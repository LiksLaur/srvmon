from __future__ import annotations

import argparse
import re
import time
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, TabbedContent, TabPane

from srvmon.collectors import MetricsCollector
from srvmon.config import SrvmonConfig, load_config
from srvmon.daemon import service_status, start_service, stop_service
from srvmon.doctor import render_doctor
from srvmon.exporter import export_metrics
from srvmon.health import configure_thresholds
from srvmon.models import MetricsSnapshot
from srvmon.periods import parse_report_period
from srvmon.reports import build_report_data, render_report
from srvmon.status import render_status
from srvmon.storage import MetricStorage
from srvmon.web import DEFAULT_WEB_PORT, WebDashboardProcess, start_web_dashboard
from srvmon.widgets import CpuPanel, DiskPanel, ExtrasPanel, LivePanel, MemoryPanel, NetworkPanel, ProcessesPanel, SummaryPanel


MIN_REFRESH_INTERVAL = 0.5
MAX_REFRESH_INTERVAL = 10.0
DEFAULT_REFRESH_INTERVAL = 2.0


class ServerMonitorApp(App[None]):
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]
    TITLE = "srvmon"
    SUB_TITLE = "server metrics"

    def __init__(
        self,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
        storage_interval: float | None = None,
        storage_path: Path | None = None,
        storage_enabled: bool = True,
        interval_warning: str | None = None,
        config: SrvmonConfig | None = None,
        web_enabled: bool = True,
        web_port: int = DEFAULT_WEB_PORT,
    ) -> None:
        super().__init__()
        self.config = config or load_config()
        configure_thresholds(self.config.thresholds)
        self.refresh_interval = refresh_interval
        self.storage_interval = storage_interval if storage_interval is not None else refresh_interval
        self.interval_warning = interval_warning
        self.web_enabled = web_enabled
        self.web_port = web_port
        self.web_dashboard: WebDashboardProcess | None = None
        self.collector = MetricsCollector(self.config.disks, self.config.interfaces)
        self.storage: MetricStorage | None = None
        self._startup_storage_error: Exception | None = None
        if storage_enabled:
            try:
                self.storage = MetricStorage(
                    storage_path or self.config.database_path,
                    retention=timedelta(days=self.config.retention_days),
                )
                self._save_config_metadata()
            except (OSError, sqlite3.Error) as error:
                self._startup_storage_error = error
        self._last_stored_at = 0.0
        self._storage_error_reported = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main"):
            yield SummaryPanel(id="summary")
            with TabbedContent(id="tabs"):
                yield TabPane("Live", LivePanel(id="live-panel"), id="live")
                yield TabPane("CPU", CpuPanel(id="cpu-panel"), id="cpu")
                yield TabPane("Memory", MemoryPanel(id="memory-panel"), id="memory")
                yield TabPane("Disk", DiskPanel(id="disk-panel"), id="disk")
                yield TabPane("Network", NetworkPanel(id="network-panel"), id="network")
                yield TabPane("Processes", ProcessesPanel(id="processes-panel"), id="processes")
                yield TabPane("Extras", ExtrasPanel(id="extras-panel"), id="extras")
        yield Footer()

    def on_mount(self) -> None:
        if self.web_enabled:
            self._start_web_dashboard()
        if self.interval_warning is not None:
            self.notify(self.interval_warning, severity="warning")
        if self._startup_storage_error is not None:
            self._report_storage_error(self._startup_storage_error)
        self.set_interval(self.refresh_interval, self.refresh_metrics)
        self.refresh_metrics()

    def on_unmount(self) -> None:
        if self.web_dashboard is not None:
            self.web_dashboard.process.terminate()

    def action_refresh(self) -> None:
        self.refresh_metrics()

    def refresh_metrics(self) -> None:
        snapshot = self.collector.collect()
        self._store_snapshot(snapshot)
        self._update_panels(snapshot)

    def _store_snapshot(self, snapshot: MetricsSnapshot) -> None:
        if self.storage is None:
            return
        now = time.monotonic()
        if self._last_stored_at and now - self._last_stored_at < self.storage_interval:
            return
        try:
            self.storage.save_snapshot(snapshot)
        except (OSError, sqlite3.Error) as error:
            self._report_storage_error(error)
        self._last_stored_at = now

    def _report_storage_error(self, error: Exception) -> None:
        if self._storage_error_reported:
            return
        self._storage_error_reported = True
        self.notify(f"Metric storage disabled for this run: {error}", severity="warning")

    def _start_web_dashboard(self) -> None:
        try:
            self.web_dashboard = start_web_dashboard(self.config, port=self.web_port)
        except OSError as error:
            self.notify(f"Web dashboard unavailable: {error}", severity="warning")
            return
        self.sub_title = f"web {self.web_dashboard.url}"
        self.notify(f"Web dashboard: {self.web_dashboard.url}", title="srvmon live")

    def _save_config_metadata(self) -> None:
        if self.storage is None:
            return
        self.storage.save_config_metadata(
            config_path=self.config.config_path,
            database_path=self.storage.db_path,
            live_refresh_seconds=self.config.live_refresh_seconds,
            collection_seconds=self.config.collection_seconds,
            retention_days=self.config.retention_days,
            thresholds=self.config.thresholds,
            disks=self.config.disks,
            interfaces=self.config.interfaces,
        )

    def _update_panels(self, snapshot: MetricsSnapshot) -> None:
        self.query_one("#summary", SummaryPanel).update_snapshot(snapshot)
        self.query_one("#live-panel", LivePanel).update_snapshot(snapshot)
        self.query_one("#cpu-panel", CpuPanel).update_snapshot(snapshot)
        self.query_one("#memory-panel", MemoryPanel).update_snapshot(snapshot)
        self.query_one("#disk-panel", DiskPanel).update_snapshot(snapshot)
        self.query_one("#network-panel", NetworkPanel).update_snapshot(snapshot)
        self.query_one("#processes-panel", ProcessesPanel).update_snapshot(snapshot)
        self.query_one("#extras-panel", ExtrasPanel).update_snapshot(snapshot)


def main() -> None:
    argv = sys.argv[1:]
    config = load_config()
    configure_thresholds(config.thresholds)
    if not argv:
        _build_root_parser().print_help()
        return
    if argv[0] in {"-h", "--help"}:
        _build_root_parser().print_help()
        return

    command = argv[0]
    if command == "start":
        state = start_service(config)
        print(state.message)
        return

    if command == "stop":
        state = stop_service()
        print(state.message)
        return

    if command == "status":
        render_status(config=config, service_state=service_status())
        return

    if command == "doctor":
        render_doctor(config=config)
        return

    if command == "cleanup":
        args = _parse_cleanup_args(argv[1:])
        storage = MetricStorage(args.storage_path or config.database_path, retention=timedelta(days=config.retention_days))
        _save_cli_config_metadata(storage, config)
        deleted = storage.prune()
        print(f"Cleaned up {deleted} old metric sample(s).")
        return

    if command == "report":
        args = _parse_report_args(argv[1:])
        try:
            label, period = parse_report_period(args.period)
        except ValueError as error:
            parser = _build_root_parser()
            parser.error(str(error))
        render_report(build_report_data(args.storage_path or config.database_path, period, label))
        return

    if command == "export":
        args = _parse_export_args(argv[1:])
        try:
            label, period = parse_report_period(args.period)
        except ValueError as error:
            parser = _build_root_parser()
            parser.error(str(error))
        output_path = export_metrics(
            db_path=args.storage_path or config.database_path,
            period=period,
            output_format=args.format,
            limit=args.limit,
            output_path=args.output,
            period_label=label,
        )
        print(f"Export saved: {output_path}")
        return

    if command != "live":
        parser = _build_root_parser()
        parser.error(
            f"unknown command: {command!r}. Use 'srvmon start', 'srvmon stop', "
            "'srvmon live', 'srvmon report', 'srvmon export', "
            "'srvmon status', 'srvmon doctor' or 'srvmon cleanup'."
        )

    args, interval_warning = _parse_live_args(argv[1:])

    ServerMonitorApp(
        refresh_interval=args.refresh_interval,
        storage_interval=0.0,
        storage_path=args.storage_path or config.database_path,
        storage_enabled=not args.no_storage,
        interval_warning=interval_warning,
        config=config,
        web_enabled=not args.no_web,
        web_port=args.web_port,
    ).run()


def _build_root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Textual server metrics monitor")
    subparsers = parser.add_subparsers(dest="command")
    live = subparsers.add_parser("live", help="start live real-time monitoring")
    live.add_argument("interval", nargs="?", help="refresh speed, for example -0.5s, -5s or 2s")
    live.add_argument("--storage-path", type=Path, default=None, help="SQLite database path")
    live.add_argument("--no-storage", action="store_true", help="Disable local metric storage")
    live.add_argument("--no-web", action="store_true", help="Disable local web dashboard")
    live.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Preferred local web dashboard port")
    subparsers.add_parser("start", help="start background metric collection service")
    subparsers.add_parser("stop", help="stop background metric collection service")
    report = subparsers.add_parser("report", help="print a metrics report from local history")
    report.add_argument("period", nargs="?", help="period: -1h, -1d, -1w, -1m, -2m")
    report.add_argument("--storage-path", type=Path, default=None, help="SQLite database path")
    export = subparsers.add_parser("export", help="export stored metrics")
    export.add_argument("period", nargs="?", help="period: -1h, -1d, -1w, -1m, -2m")
    export.add_argument("--format", choices=["json", "csv", "html"], default="json")
    export.add_argument("--limit", type=int, default=None, help="max metric samples to export")
    export.add_argument("--output", type=Path, default=None, help="write export to file")
    export.add_argument("--storage-path", type=Path, default=None, help="SQLite database path")
    subparsers.add_parser("status", help="print a quick current health overview")
    subparsers.add_parser("doctor", help="diagnose srvmon runtime environment")
    cleanup = subparsers.add_parser("cleanup", help="delete stored metrics older than the retention window")
    cleanup.add_argument("--storage-path", type=Path, default=None, help="SQLite database path")
    return parser


def _parse_live_args(argv: list[str]) -> tuple[argparse.Namespace, str | None]:
    interval_token: str | None = None
    remaining: list[str] = []
    for token in argv:
        if interval_token is None and _looks_like_interval(token):
            interval_token = token
        else:
            remaining.append(token)

    parser = argparse.ArgumentParser(prog="srvmon live", description="Start live real-time monitoring")
    parser.add_argument("--storage-path", type=Path, default=None, help="SQLite database path")
    parser.add_argument("--no-storage", action="store_true", help="Disable local metric storage")
    parser.add_argument("--no-web", action="store_true", help="Disable local web dashboard")
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT, help="Preferred local web dashboard port")
    args = parser.parse_args(remaining)
    config = load_config()
    interval, warning = _normalize_interval(interval_token, config.live_refresh_seconds)
    args.refresh_interval = interval
    return args, warning


def _parse_report_args(argv: list[str]) -> argparse.Namespace:
    period_token: str | None = None
    remaining: list[str] = []
    for token in argv:
        if period_token is None and re.fullmatch(r"-[12][hdwm]", token):
            period_token = token
        else:
            remaining.append(token)

    parser = argparse.ArgumentParser(prog="srvmon report", description="Print a metrics report from local history")
    parser.add_argument("--storage-path", type=Path, default=None, help="SQLite database path")
    args = parser.parse_args(remaining)
    args.period = period_token or "-1d"
    return args


def _parse_export_args(argv: list[str]) -> argparse.Namespace:
    period_token, remaining = _extract_period(argv)
    parser = argparse.ArgumentParser(prog="srvmon export", description="Export stored metrics")
    parser.add_argument("--format", choices=["json", "csv", "html"], default="json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--storage-path", type=Path, default=None)
    args = parser.parse_args(remaining)
    args.period = period_token or "-1d"
    return args


def _extract_period(argv: list[str]) -> tuple[str | None, list[str]]:
    period_token: str | None = None
    remaining: list[str] = []
    for token in argv:
        if period_token is None and re.fullmatch(r"-[12][hdwm]", token):
            period_token = token
        else:
            remaining.append(token)
    return period_token, remaining


def _parse_cleanup_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="srvmon cleanup", description="Delete old metric history")
    parser.add_argument("--storage-path", type=Path, default=None, help="SQLite database path")
    return parser.parse_args(argv)


def _looks_like_interval(token: str) -> bool:
    return re.fullmatch(r"-?\d+(?:\.\d+)?s", token) is not None


def _normalize_interval(token: str | None, default: float = DEFAULT_REFRESH_INTERVAL) -> tuple[float, str | None]:
    if token is None:
        return default, None

    raw_value = token.removesuffix("s").lstrip("-")
    try:
        value = float(raw_value)
    except ValueError:
        return default, f"Invalid refresh interval {token!r}; using {default:g}s."

    if value < MIN_REFRESH_INTERVAL:
        return MIN_REFRESH_INTERVAL, (
            f"Refresh interval {value:g}s is below the minimum; using {MIN_REFRESH_INTERVAL:g}s."
        )
    if value > MAX_REFRESH_INTERVAL:
        return MAX_REFRESH_INTERVAL, (
            f"Refresh interval {value:g}s is above the maximum; using {MAX_REFRESH_INTERVAL:g}s."
        )
    return value, None


def _save_cli_config_metadata(storage: MetricStorage, config: SrvmonConfig) -> None:
    storage.save_config_metadata(
        config_path=config.config_path,
        database_path=storage.db_path,
        live_refresh_seconds=config.live_refresh_seconds,
        collection_seconds=config.collection_seconds,
        retention_days=config.retention_days,
        thresholds=config.thresholds,
        disks=config.disks,
        interfaces=config.interfaces,
    )
