from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from srvmon.collectors import MetricsCollector
from srvmon.config import SrvmonConfig, load_config
from srvmon.daemon import ServiceState
from srvmon.formatting import format_bytes, format_rate, format_timedelta
from srvmon.health import configure_thresholds
from srvmon.health import health_color, health_label, load_ratio, score_from_colors, threshold_rows
from srvmon.models import MetricsSnapshot


def render_status(
    console: Console | None = None,
    config: SrvmonConfig | None = None,
    service_state: ServiceState | None = None,
) -> None:
    active_console = console or Console()
    active_config = config or load_config()
    configure_thresholds(active_config.thresholds)
    snapshot = MetricsCollector(active_config.disks, active_config.interfaces).collect()
    health_score = _health_score(snapshot)
    active_console.print(
        Panel(
            f"captured: [bold]{snapshot.captured_at}[/bold]\n"
            f"uptime:   [bold]{format_timedelta(snapshot.uptime)}[/bold]\n"
            f"Health Score: [{health_score.color}]{health_score.score}/100[/] | "
            f"Status: [{health_score.color}]{health_score.status}[/]\n"
            f"service:  [bold]{_service_text(service_state)}[/bold]\n"
            f"config:   [bold]{active_config.config_path}[/bold]\n"
            f"database: [bold]{active_config.database_path}[/bold]",
            title="srvmon status",
            border_style="cyan",
        )
    )
    active_console.print(_overview_table(snapshot))
    active_console.print(_process_table("Top CPU now", snapshot.top_cpu, "cpu"))
    active_console.print(_process_table("Top RAM now", snapshot.top_memory, "ram"))
    active_console.print(_threshold_table())


def _overview_table(snapshot: MetricsSnapshot) -> Table:
    cpu = _metric(snapshot, "cpu", "CPU utilization")
    ram = _metric(snapshot, "memory", "RAM utilization")
    swap = _metric(snapshot, "memory", "Swap utilization")
    load_1 = _metric(snapshot, "cpu", "Load average 1m")
    load_5 = _metric(snapshot, "cpu", "Load average 5m")
    load_15 = _metric(snapshot, "cpu", "Load average 15m")
    logical = _metric(snapshot, "cpu", "Logical cores")
    max_disk = max((partition.percent for partition in snapshot.partitions), default=None)
    disk_latency = snapshot.disk_io.latency_ms
    error_drop_total = sum(
        int(_metric(snapshot, "network", name) or 0)
        for name in ("Errors in", "Errors out", "Drops in", "Drops out")
    )

    table = Table(title="Current health", show_lines=False)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_column("State")
    _add_row(table, "Load 1/5/15", f"{_num(load_1)} / {_num(load_5)} / {_num(load_15)}", load_ratio(load_1, logical), "load_ratio")
    _add_row(table, "CPU", _percent(cpu), cpu, "cpu")
    _add_row(table, "RAM", _percent(ram), ram, "ram")
    _add_row(table, "Swap", _percent(swap), swap, "swap")
    _add_row(table, "Disk max usage", _percent(max_disk), max_disk, "disk")
    _add_row(table, "Disk read", format_rate(snapshot.disk_io.read_bps, "s"), None, "informational")
    _add_row(table, "Disk write", format_rate(snapshot.disk_io.write_bps, "s"), None, "informational")
    _add_row(table, "Disk latency", _latency(disk_latency), disk_latency, "disk_latency")
    _add_row(table, "Network in", f"{snapshot.network_rate.bytes_recv_per_sec / 1024 / 1024:.2f} MB/s", None, "informational")
    _add_row(table, "Network out", f"{snapshot.network_rate.bytes_sent_per_sec / 1024 / 1024:.2f} MB/s", None, "informational")
    _add_row(table, "Network errors/drops", str(error_drop_total), float(error_drop_total), "network_errors")
    return table


def _service_text(service_state: ServiceState | None) -> str:
    if service_state is None:
        return "unknown"
    if service_state.running:
        return f"running pid={service_state.pid}"
    return "stopped"


def _health_score(snapshot: MetricsSnapshot) -> object:
    cpu = _metric(snapshot, "cpu", "CPU utilization")
    ram = _metric(snapshot, "memory", "RAM utilization")
    swap = _metric(snapshot, "memory", "Swap utilization")
    load_1 = _metric(snapshot, "cpu", "Load average 1m")
    logical = _metric(snapshot, "cpu", "Logical cores")
    max_disk = max((partition.percent for partition in snapshot.partitions), default=None)
    error_drop_total = sum(
        int(_metric(snapshot, "network", name) or 0)
        for name in ("Errors in", "Errors out", "Drops in", "Drops out")
    )
    colors = [
        health_color(load_ratio(load_1, logical), "load_ratio"),
        health_color(cpu, "cpu"),
        health_color(ram, "ram"),
        health_color(swap, "swap"),
        health_color(max_disk, "disk"),
        health_color(float(error_drop_total), "network_errors"),
    ]
    return score_from_colors(colors)


def _process_table(title: str, processes: object, kind: str) -> Table:
    table = Table(title=title, show_lines=False)
    table.add_column("PID", justify="right")
    table.add_column("Process")
    table.add_column("CPU", justify="right")
    table.add_column("RAM", justify="right")
    table.add_column("RSS", justify="right")
    for process in list(processes)[:10]:
        value = process.cpu_percent if kind == "cpu" else process.memory_percent
        threshold = "process_cpu" if kind == "cpu" else "process_ram"
        color = health_color(value, threshold)
        table.add_row(
            str(process.pid),
            f"[{color}]{process.name}[/{color}]",
            _percent(process.cpu_percent),
            _percent(process.memory_percent),
            format_bytes(process.rss),
        )
    return table


def _threshold_table() -> Table:
    table = Table(title="Color thresholds", show_lines=False)
    table.add_column("Metric")
    table.add_column("Green")
    table.add_column("Yellow")
    table.add_column("Red")
    for metric, green, yellow, red in threshold_rows():
        table.add_row(metric, f"[green]{green}[/green]", f"[yellow]{yellow}[/yellow]", f"[red]{red}[/red]")
    return table


def _add_row(table: Table, name: str, value: str, health_value: float | None, threshold: str) -> None:
    color = health_color(health_value, threshold)
    table.add_row(name, f"[{color}]{value}[/{color}]", health_label(health_value, threshold))


def _metric(snapshot: MetricsSnapshot, collection: str, name: str) -> float | None:
    rows = getattr(snapshot, collection)
    value = next((row.value for row in rows if row.name == name), None)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{min(value, 100.0):.1f}%"


def _num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _latency(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f} ms"
