from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import timedelta
from io import StringIO
from pathlib import Path

from rich.console import Console

from srvmon.alerts import current_alerts
from srvmon.app import ServerMonitorApp, _parse_cleanup_args, _parse_export_args, _parse_live_args, _parse_report_args
from srvmon.config import DEFAULT_CONFIG_TEXT, load_config
from srvmon.collectors import MetricsCollector
from srvmon.daemon import ServiceState
from srvmon.doctor import render_doctor
from srvmon.exporter import export_metrics
from srvmon.periods import parse_report_period
from srvmon.reports import build_report_data, render_report
from srvmon.status import render_status
from srvmon.storage import MetricStorage


TEMP_DIR = Path(__file__).resolve().parents[1] / ".tmp"


def test_collector_smoke() -> None:
    snapshot = MetricsCollector().collect()
    assert snapshot.cpu
    assert snapshot.memory
    assert snapshot.network


def test_textual_composition_smoke() -> None:
    async def run() -> None:
        TEMP_DIR.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEMP_DIR) as directory:
            app = ServerMonitorApp(
                refresh_interval=999,
                storage_interval=0,
                storage_path=Path(directory) / "metrics.sqlite3",
                web_enabled=False,
            )
            async with app.run_test() as pilot:
                await pilot.pause(0.1)

    asyncio.run(run())


def test_storage_smoke() -> None:
    TEMP_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as directory:
        db_path = Path(directory) / "metrics.sqlite3"
        storage = MetricStorage(db_path=db_path, retention=timedelta(days=62))
        snapshot = MetricsCollector().collect()
        storage.save_snapshot(snapshot)

        with closing(sqlite3.connect(db_path)) as connection:
            samples = connection.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0]
            disks = connection.execute("SELECT COUNT(*) FROM disk_usage").fetchone()[0]
            processes = connection.execute("SELECT COUNT(*) FROM top_processes").fetchone()[0]
            config_table = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='srvmon_config'"
            ).fetchone()[0]

        assert samples == 1
        assert disks >= 1
        assert processes >= 1
        assert config_table == 1


def test_config_smoke() -> None:
    TEMP_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as directory:
        config_path = Path(directory) / "config.toml"
        config_path.write_text(
            DEFAULT_CONFIG_TEXT.replace("disks = []", 'disks = ["C:\\\\"]').replace(
                "interfaces = []",
                'interfaces = ["Ethernet"]',
            ),
            encoding="utf-8",
        )
        config = load_config(config_path)
        assert config.disks == ["C:\\"]
        assert config.interfaces == ["Ethernet"]


def test_storage_retention_smoke() -> None:
    TEMP_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as directory:
        db_path = Path(directory) / "metrics.sqlite3"
        storage = MetricStorage(db_path=db_path, retention=timedelta(days=62))
        old_epoch = time.time() - timedelta(days=90).total_seconds()

        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute(
                "INSERT INTO metric_samples (captured_at, captured_epoch) VALUES (?, ?)",
                ("old", old_epoch),
            )
            connection.commit()

        storage.save_snapshot(MetricsCollector().collect())

        with closing(sqlite3.connect(db_path)) as connection:
            samples = connection.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0]
            oldest = connection.execute("SELECT MIN(captured_epoch) FROM metric_samples").fetchone()[0]

        assert samples == 1
        assert oldest > old_epoch


def test_live_interval_parser_smoke() -> None:
    args, warning = _parse_live_args([])
    assert args.refresh_interval == 2.0
    assert warning is None
    assert args.no_web is False

    args, warning = _parse_live_args(["-0.5s"])
    assert args.refresh_interval == 0.5
    assert warning is None

    args, warning = _parse_live_args(["-0.1s"])
    assert args.refresh_interval == 0.5
    assert warning is not None

    args, warning = _parse_live_args(["-20s"])
    assert args.refresh_interval == 10.0
    assert warning is not None


def test_report_smoke() -> None:
    TEMP_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as directory:
        db_path = Path(directory) / "metrics.sqlite3"
        storage = MetricStorage(db_path=db_path, retention=timedelta(days=62))
        storage.save_snapshot(MetricsCollector().collect())

        label, period = parse_report_period("-1d")
        report = build_report_data(db_path, period, label)
        assert report.sample_count == 1
        assert "CPU utilization" in report.metrics

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        render_report(report, console)
        text = output.getvalue()
        assert "System metrics" in text
        assert "Deep analysis" in text
        assert "Anomaly detection" in text


def test_export_smoke() -> None:
    TEMP_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as directory:
        root = Path(directory)
        db_path = root / "metrics.sqlite3"
        storage = MetricStorage(db_path=db_path, retention=timedelta(days=62))
        storage.save_snapshot(MetricsCollector().collect())
        for output_format in ("json", "csv", "html"):
            output_path = root / f"export.{output_format}"
            saved_path = export_metrics(
                db_path=db_path,
                period=timedelta(days=1),
                output_format=output_format,
                limit=10,
                output_path=output_path,
            )
            assert saved_path == output_path
            assert output_path.exists()
            assert output_path.read_text(encoding="utf-8") != ""
        auto_path = export_metrics(
            db_path=db_path,
            period=timedelta(days=1),
            output_format="json",
            limit=1,
            output_path=None,
            period_label="-1d",
        )
        assert auto_path.exists()


def test_current_alerts_smoke() -> None:
    snapshot = MetricsCollector().collect()
    cpu = next(row for row in snapshot.cpu if row.name == "CPU utilization")
    cpu.value = 95
    notices = current_alerts(snapshot)
    assert any(notice.metric == "CPU" and notice.state == "CRITICAL" for notice in notices)


def test_report_parser_smoke() -> None:
    args = _parse_report_args([])
    assert args.period == "-1d"

    args = _parse_report_args(["-1h"])
    assert args.period == "-1h"

    args = _parse_report_args(["-2m"])
    assert args.period == "-2m"


def test_export_parser_smoke() -> None:
    args = _parse_export_args(["--format", "json", "-1d", "--limit", "5"])
    assert args.period == "-1d"
    assert args.format == "json"
    assert args.limit == 5


def test_status_smoke() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=120)
    render_status(console, service_state=ServiceState(False))
    text = output.getvalue()
    assert "Current health" in text
    assert "Color thresholds" in text
    assert "service" in text


def test_doctor_smoke() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=120)
    render_doctor(console)
    text = output.getvalue()
    assert "srvmon doctor" in text
    assert "Python version" in text


def test_cleanup_parser_smoke() -> None:
    args = _parse_cleanup_args([])
    assert args.storage_path is None


if __name__ == "__main__":
    test_collector_smoke()
    test_textual_composition_smoke()
    test_storage_smoke()
    test_config_smoke()
    test_storage_retention_smoke()
    test_live_interval_parser_smoke()
    test_report_smoke()
    test_export_smoke()
    test_current_alerts_smoke()
    test_report_parser_smoke()
    test_export_parser_smoke()
    test_status_smoke()
    test_doctor_smoke()
    test_cleanup_parser_smoke()
    print("smoke ok")
