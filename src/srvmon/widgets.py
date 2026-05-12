from __future__ import annotations

from collections import deque

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import DataTable, Label, ProgressBar, Static

from srvmon.alerts import alert_summary, current_alerts
from srvmon.formatting import format_bytes, format_percent, format_rate, format_timedelta, short_text
from srvmon.health import health_color, load_ratio
from srvmon.models import MetricsSnapshot, ProcessInfo


def value_to_text(value: object, unit: str = "") -> str:
    if unit == "bytes":
        return format_bytes(value if isinstance(value, (int, float)) else None)
    if unit == "%":
        return format_percent(value if isinstance(value, (int, float)) else None)
    if value is None:
        return "n/a"
    return f"{value} {unit}".strip()


def metric_value(snapshot: MetricsSnapshot, rows_name: str, metric_name: str) -> float | None:
    rows = getattr(snapshot, rows_name)
    value = next((row.value for row in rows if row.name == metric_name), None)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def ascii_bar(value: float, maximum: float, width: int = 32) -> str:
    ratio = 0.0 if maximum <= 0 else max(0.0, min(value / maximum, 1.0))
    filled = int(round(ratio * width))
    return "#" * filled + "-" * (width - filled)


def ascii_sparkline(values: list[float], width: int = 42) -> str:
    if not values:
        return "." * width
    samples = values[-width:]
    low = min(samples)
    high = max(samples)
    alphabet = " .:-=+*#%@"
    if high <= low:
        char = alphabet[len(alphabet) // 2]
        graph = char * len(samples)
    else:
        graph = "".join(alphabet[int((value - low) / (high - low) * (len(alphabet) - 1))] for value in samples)
    return graph.rjust(width, ".")


def as_markup(markup: str) -> Text:
    return Text.from_markup(markup)


class MetricTable(DataTable):
    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True

    def set_columns(self, *columns: str) -> None:
        self.clear(columns=True)
        for column in columns:
            self.add_column(column)


class SummaryPanel(Static):
    def compose(self) -> ComposeResult:
        yield Grid(
            Label("", id="captured"),
            Label("", id="uptime"),
            Label("", id="cpu"),
            Label("", id="ram"),
            Label("", id="disk"),
            Label("", id="net"),
            Label("", id="alerts"),
            id="summary-grid",
        )

    def update_snapshot(self, snapshot: MetricsSnapshot) -> None:
        cpu = next((row.value for row in snapshot.cpu if row.name == "CPU utilization"), None)
        ram = next((row.value for row in snapshot.memory if row.name == "RAM utilization"), None)
        disk = max((partition.percent for partition in snapshot.partitions), default=0.0)
        self.query_one("#captured", Label).update(f"Captured: {snapshot.captured_at}")
        self.query_one("#uptime", Label).update(f"Uptime: {format_timedelta(snapshot.uptime)}")
        self.query_one("#cpu", Label).update(f"CPU: {format_percent(cpu if isinstance(cpu, (int, float)) else None)}")
        self.query_one("#ram", Label).update(f"RAM: {format_percent(ram if isinstance(ram, (int, float)) else None)}")
        self.query_one("#disk", Label).update(f"Max disk: {disk:.1f}%")
        self.query_one("#net", Label).update(
            f"Net: rx {format_rate(snapshot.network_rate.bytes_recv_per_sec, 's')} "
            f"tx {format_rate(snapshot.network_rate.bytes_sent_per_sec, 's')}"
        )
        notices = current_alerts(snapshot)
        if notices:
            color = "red" if any(notice.color == "red" for notice in notices) else "yellow"
            self.query_one("#alerts", Label).update(as_markup(f"[{color}]Alerts: {alert_summary(snapshot)}[/{color}]"))
        else:
            self.query_one("#alerts", Label).update(as_markup("[green]Alerts: OK[/green]"))


class LivePanel(Static):
    def __init__(self, history_size: int = 80, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.history_size = history_size
        self._history: dict[str, deque[float]] = {
            "cpu": deque(maxlen=history_size),
            "ram": deque(maxlen=history_size),
            "load": deque(maxlen=history_size),
            "disk": deque(maxlen=history_size),
            "net_in": deque(maxlen=history_size),
            "net_out": deque(maxlen=history_size),
        }

    def compose(self) -> ComposeResult:
        yield Label("", id="live-title")
        yield Static("", id="live-alert-banner")
        yield Grid(
            Static("", id="live-cpu", classes="live-card"),
            Static("", id="live-ram", classes="live-card"),
            Static("", id="live-load", classes="live-card"),
            Static("", id="live-disk", classes="live-card"),
            Static("", id="live-net-in", classes="live-card"),
            Static("", id="live-net-out", classes="live-card"),
            id="live-grid",
        )

    def update_snapshot(self, snapshot: MetricsSnapshot) -> None:
        cpu = metric_value(snapshot, "cpu", "CPU utilization") or 0.0
        ram = metric_value(snapshot, "memory", "RAM utilization") or 0.0
        load = metric_value(snapshot, "cpu", "Load average 1m") or 0.0
        logical_cores = metric_value(snapshot, "cpu", "Logical cores") or 1.0
        load_percent = min(load_ratio(load, logical_cores) or 0.0, 100)
        disk = max((partition.percent for partition in snapshot.partitions), default=0.0)
        net_in = snapshot.network_rate.bytes_recv_per_sec / 1024 / 1024
        net_out = snapshot.network_rate.bytes_sent_per_sec / 1024 / 1024

        self._history["cpu"].append(cpu)
        self._history["ram"].append(ram)
        self._history["load"].append(load_percent)
        self._history["disk"].append(disk)
        self._history["net_in"].append(net_in)
        self._history["net_out"].append(net_out)

        self.query_one("#live-title", Label).update(
            f"Live monitor | {snapshot.captured_at} | q quit | r refresh"
        )
        self.query_one("#live-cpu", Static).update(as_markup(self._percent_card("CPU", cpu, "cpu")))
        self.query_one("#live-ram", Static).update(as_markup(self._ram_card(snapshot, ram)))
        self.query_one("#live-load", Static).update(as_markup(self._load_card(snapshot, load, logical_cores, load_percent)))
        self.query_one("#live-disk", Static).update(as_markup(self._percent_card("DISK max usage", disk, "disk")))
        self.query_one("#live-net-in", Static).update(as_markup(self._rate_card("NETWORK IN", net_in, "net_in")))
        self.query_one("#live-net-out", Static).update(as_markup(self._rate_card("NETWORK OUT", net_out, "net_out")))
        self.query_one("#live-alert-banner", Static).update(as_markup(self._alerts_banner(snapshot)))

    def _percent_card(self, title: str, value: float, history_key: str) -> str:
        color = health_color(value, "cpu" if history_key == "cpu" else "disk")
        return (
            f"[b]{title}[/b]\n"
            f"[{color}]{value:6.1f}% |{ascii_bar(value, 100)}|[/{color}]\n"
            f"[bright_black]trend |{ascii_sparkline(list(self._history[history_key]))}|[/bright_black]"
        )

    def _ram_card(self, snapshot: MetricsSnapshot, ram: float) -> str:
        available = metric_value(snapshot, "memory", "RAM available")
        color = health_color(ram, "ram")
        return (
            "[b]RAM[/b]\n"
            f"[{color}]{ram:6.1f}% |{ascii_bar(ram, 100)}|[/{color}]\n"
            f"available {format_bytes(available)}\n"
            f"[bright_black]trend |{ascii_sparkline(list(self._history['ram']))}|[/bright_black]"
        )

    def _load_card(self, snapshot: MetricsSnapshot, load: float, cores: float, load_percent: float) -> str:
        load_5 = metric_value(snapshot, "cpu", "Load average 5m")
        load_15 = metric_value(snapshot, "cpu", "Load average 15m")
        color = health_color(load_percent, "load_ratio")
        return (
            "[b]LOAD AVG[/b]\n"
            f"[{color}]{load:.2f} / {cores:.0f} cores |{ascii_bar(load_percent, 100)}|[/{color}]\n"
            f"5m {load_5 if load_5 is not None else 'n/a'} | 15m {load_15 if load_15 is not None else 'n/a'}\n"
            f"[bright_black]trend |{ascii_sparkline(list(self._history['load']))}|[/bright_black]"
        )

    def _rate_card(self, title: str, value: float, history_key: str) -> str:
        maximum = max(max(self._history[history_key], default=1.0), 1.0)
        color = health_color(None, "informational")
        return (
            f"[b]{title}[/b]\n"
            f"[{color}]{value:8.2f} MB/s |{ascii_bar(value, maximum)}|[/{color}]\n"
            f"[bright_black]trend |{ascii_sparkline(list(self._history[history_key]))}|[/bright_black]"
        )

    def _alerts_banner(self, snapshot: MetricsSnapshot) -> str:
        notices = current_alerts(snapshot)
        if not notices:
            return "[b]ALERTS[/b] [green]OK[/green] [bright_black]no active warnings[/bright_black]"
        rows = []
        for notice in notices[:4]:
            rows.append(f"[{notice.color}]{notice.state}[/{notice.color}] {notice.metric}: {notice.value}")
        suffix = f" [bright_black]+{len(notices) - 4} more[/bright_black]" if len(notices) > 4 else ""
        return "[b]ALERTS[/b] " + " | ".join(rows) + suffix


class CpuPanel(Static):
    def compose(self) -> ComposeResult:
        yield MetricTable(id="cpu-table")
        yield Vertical(id="core-bars")

    def on_mount(self) -> None:
        self.query_one("#cpu-table", MetricTable).set_columns("Metric", "Value")

    def update_snapshot(self, snapshot: MetricsSnapshot) -> None:
        table = self.query_one("#cpu-table", MetricTable)
        table.clear()
        for row in snapshot.cpu:
            table.add_row(row.name, value_to_text(row.value, row.unit))

        bars = self.query_one("#core-bars", Vertical)
        bars.remove_children()
        for index, usage in enumerate(snapshot.cpu_per_core):
            progress = ProgressBar(total=100, show_eta=False)
            progress.update(progress=usage)
            bars.mount(Label(f"Core {index}: {usage:.1f}%"))
            bars.mount(progress)


class MemoryPanel(Static):
    def compose(self) -> ComposeResult:
        yield MetricTable(id="memory-table")
        yield MetricTable(id="process-memory-table")

    def on_mount(self) -> None:
        self.query_one("#memory-table", MetricTable).set_columns("Metric", "Value")
        self.query_one("#process-memory-table", MetricTable).set_columns("PID", "Name", "RAM", "RSS", "User")

    def update_snapshot(self, snapshot: MetricsSnapshot) -> None:
        table = self.query_one("#memory-table", MetricTable)
        table.clear()
        for row in snapshot.memory:
            table.add_row(row.name, value_to_text(row.value, row.unit))

        self._fill_process_table(self.query_one("#process-memory-table", MetricTable), snapshot.process_memory)

    def _fill_process_table(self, table: MetricTable, processes: list[ProcessInfo]) -> None:
        table.clear()
        for process in processes:
            table.add_row(
                str(process.pid),
                short_text(process.name, 24),
                format_percent(process.memory_percent),
                format_bytes(process.rss),
                short_text(process.username, 24),
            )


class DiskPanel(Static):
    def compose(self) -> ComposeResult:
        yield MetricTable(id="partition-table")
        yield MetricTable(id="disk-io-table")

    def on_mount(self) -> None:
        self.query_one("#partition-table", MetricTable).set_columns("Device", "Mount", "FS", "Used", "Free", "Use", "Inodes")
        self.query_one("#disk-io-table", MetricTable).set_columns("Metric", "Value")

    def update_snapshot(self, snapshot: MetricsSnapshot) -> None:
        partitions = self.query_one("#partition-table", MetricTable)
        partitions.clear()
        for partition in snapshot.partitions:
            partitions.add_row(
                short_text(partition.device, 22),
                short_text(partition.mountpoint, 22),
                partition.fstype,
                format_bytes(partition.used),
                format_bytes(partition.free),
                format_percent(partition.percent),
                format_percent(partition.inodes_used_percent),
            )

        io = snapshot.disk_io
        table = self.query_one("#disk-io-table", MetricTable)
        table.clear()
        table.add_row("Read throughput", format_rate(io.read_bps, "s"))
        table.add_row("Write throughput", format_rate(io.write_bps, "s"))
        table.add_row("Read IOPS", f"{io.read_iops:.1f}")
        table.add_row("Write IOPS", f"{io.write_iops:.1f}")
        table.add_row("Latency", f"{io.latency_ms:.2f} ms" if io.latency_ms is not None else "n/a")


class NetworkPanel(Static):
    def compose(self) -> ComposeResult:
        yield MetricTable(id="network-table")
        yield MetricTable(id="connection-table")

    def on_mount(self) -> None:
        self.query_one("#network-table", MetricTable).set_columns("Metric", "Value")
        self.query_one("#connection-table", MetricTable).set_columns("Connections", "Count")

    def update_snapshot(self, snapshot: MetricsSnapshot) -> None:
        table = self.query_one("#network-table", MetricTable)
        table.clear()
        for row in snapshot.network:
            table.add_row(row.name, value_to_text(row.value, row.unit))
        table.add_row("RX rate", format_rate(snapshot.network_rate.bytes_recv_per_sec, "s"))
        table.add_row("TX rate", format_rate(snapshot.network_rate.bytes_sent_per_sec, "s"))
        table.add_row("RX packets/s", f"{snapshot.network_rate.packets_recv_per_sec:.1f}")
        table.add_row("TX packets/s", f"{snapshot.network_rate.packets_sent_per_sec:.1f}")

        connections = self.query_one("#connection-table", MetricTable)
        connections.clear()
        for row in snapshot.connections:
            connections.add_row(row.name, str(row.value))


class ProcessesPanel(Static):
    def compose(self) -> ComposeResult:
        yield MetricTable(id="process-summary")
        yield MetricTable(id="top-cpu")
        yield MetricTable(id="top-memory")

    def on_mount(self) -> None:
        self.query_one("#process-summary", MetricTable).set_columns("Metric", "Value")
        self.query_one("#top-cpu", MetricTable).set_columns("PID", "Name", "CPU", "RAM", "Status")
        self.query_one("#top-memory", MetricTable).set_columns("PID", "Name", "CPU", "RAM", "RSS")

    def update_snapshot(self, snapshot: MetricsSnapshot) -> None:
        summary = self.query_one("#process-summary", MetricTable)
        summary.clear()
        for row in snapshot.processes:
            value = format_timedelta(snapshot.uptime) if row.name == "Uptime seconds" else value_to_text(row.value, row.unit)
            summary.add_row(row.name, value)

        self._fill_cpu_table(self.query_one("#top-cpu", MetricTable), snapshot.top_cpu)
        self._fill_memory_table(self.query_one("#top-memory", MetricTable), snapshot.top_memory)

    def _fill_cpu_table(self, table: MetricTable, processes: list[ProcessInfo]) -> None:
        table.clear()
        for process in processes:
            table.add_row(
                str(process.pid),
                short_text(process.name, 28),
                format_percent(process.cpu_percent),
                format_percent(process.memory_percent),
                process.status,
            )

    def _fill_memory_table(self, table: MetricTable, processes: list[ProcessInfo]) -> None:
        table.clear()
        for process in processes:
            table.add_row(
                str(process.pid),
                short_text(process.name, 28),
                format_percent(process.cpu_percent),
                format_percent(process.memory_percent),
                format_bytes(process.rss),
            )


class ExtrasPanel(Static):
    def compose(self) -> ComposeResult:
        yield MetricTable(id="sensor-table")
        yield MetricTable(id="service-table")
        yield MetricTable(id="log-table")

    def on_mount(self) -> None:
        self.query_one("#sensor-table", MetricTable).set_columns("Sensor", "Value")
        self.query_one("#service-table", MetricTable).set_columns("Service", "State", "Substate")
        self.query_one("#log-table", MetricTable).set_columns("Log file", "Size", "Modified")

    def update_snapshot(self, snapshot: MetricsSnapshot) -> None:
        sensors = self.query_one("#sensor-table", MetricTable)
        sensors.clear()
        for row in snapshot.sensors + snapshot.filesystem:
            sensors.add_row(row.name, value_to_text(row.value, row.unit))

        services = self.query_one("#service-table", MetricTable)
        services.clear()
        for service in snapshot.services:
            services.add_row(short_text(service.name, 40), service.state, service.substate)

        logs = self.query_one("#log-table", MetricTable)
        logs.clear()
        for log_file in snapshot.logs:
            logs.add_row(short_text(log_file.path, 48), format_bytes(log_file.size), log_file.modified)
