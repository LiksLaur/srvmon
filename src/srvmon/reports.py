from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from srvmon.analytics import DeepAnalysis, analyze_history
from srvmon.formatting import format_bytes
from srvmon.health import health_color, health_label, load_ratio, score_from_colors, threshold_rows
from srvmon.periods import REPORT_PERIODS, parse_report_period
from srvmon.storage import DEFAULT_DATA_DIR, MetricStorage


DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "metrics.sqlite3"
@dataclass(slots=True)
class MetricStats:
    first: float | None
    latest: float | None
    minimum: float | None
    maximum: float | None
    average: float | None

    @property
    def delta(self) -> float | None:
        if self.first is None or self.latest is None:
            return None
        return self.latest - self.first


@dataclass(slots=True)
class ReportData:
    period_label: str
    sample_count: int
    first_seen: str | None
    last_seen: str | None
    metrics: dict[str, MetricStats]
    disk_usage: list[dict[str, object]]
    top_cpu: list[dict[str, object]]
    top_memory: list[dict[str, object]]
    deep_analysis: DeepAnalysis | None = None


def build_report_data(db_path: Path | None = None, period: timedelta = REPORT_PERIODS["-1d"], label: str = "-1d") -> ReportData:
    path = db_path or DEFAULT_DB_PATH
    MetricStorage(path)
    if not path.exists():
        return ReportData(label, 0, None, None, {}, [], [], [])

    cutoff = time.time() - period.total_seconds()
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        summary = connection.execute(
            """
            SELECT COUNT(*) AS sample_count,
                   MIN(captured_at) AS first_seen,
                   MAX(captured_at) AS last_seen
            FROM metric_samples
            WHERE captured_epoch >= ?
            """,
            (cutoff,),
        ).fetchone()
        sample_count = int(summary["sample_count"] or 0)
        if sample_count == 0:
            return ReportData(label, 0, None, None, {}, [], [], [])

        sample_ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM metric_samples WHERE captured_epoch >= ? ORDER BY captured_epoch",
                (cutoff,),
            )
        ]
        first_id = sample_ids[0]
        latest_id = sample_ids[-1]

        columns = {
            "Load 1m": "load_1",
            "Load 5m": "load_5",
            "Load 15m": "load_15",
            "Logical CPUs": "cpu_logical_cores",
            "CPU utilization": "cpu_utilization_percent",
            "RAM used": "ram_used_percent",
            "RAM available": "ram_available_bytes",
            "Swap usage": "swap_usage_percent",
            "Disk read": "disk_read_bps",
            "Disk write": "disk_write_bps",
            "Network in": "network_in_mbps",
            "Network out": "network_out_mbps",
            "Errors in": "network_errors_in",
            "Errors out": "network_errors_out",
            "Drops in": "network_drops_in",
            "Drops out": "network_drops_out",
        }
        metrics = {
            name: _column_stats(connection, column, cutoff, first_id, latest_id)
            for name, column in columns.items()
        }

        disk_usage = _disk_usage(connection, cutoff)
        top_cpu = _top_processes(connection, cutoff, "cpu")
        top_memory = _top_processes(connection, cutoff, "memory")

    return ReportData(
        period_label=label,
        sample_count=sample_count,
        first_seen=summary["first_seen"],
        last_seen=summary["last_seen"],
        metrics=metrics,
        disk_usage=disk_usage,
        top_cpu=top_cpu,
        top_memory=top_memory,
        deep_analysis=analyze_history(path, period),
    )


def render_report(report: ReportData, console: Console | None = None) -> None:
    active_console = console or Console()
    if report.sample_count == 0:
        active_console.print(
            Panel(
                "[yellow]No stored metrics found for this period.[/yellow]\n"
                "Start collection with [bold]srvmon live[/bold], then run the report again.",
                title=f"srvmon report {report.period_label}",
                border_style="yellow",
            )
        )
        return

    active_console.print(
        Panel(
            f"samples: [bold]{report.sample_count}[/bold]\n"
            f"from: [bold]{report.first_seen}[/bold]\n"
            f"to:   [bold]{report.last_seen}[/bold]\n"
            f"{_health_score_markup(report)}",
            title=f"srvmon report {report.period_label}",
            border_style="cyan",
        )
    )
    active_console.print(_health_table(report))
    active_console.print(_disk_table(report))
    active_console.print(_process_table("Top CPU processes", report.top_cpu, "avg_cpu"))
    active_console.print(_process_table("Top RAM processes", report.top_memory, "avg_memory"))
    active_console.print(_analysis_panel(report))
    if report.deep_analysis is not None:
        active_console.print(_deep_analysis_panel(report.deep_analysis))
        active_console.print(_anomaly_table(report.deep_analysis))
        active_console.print(_warn_crit_table(report.deep_analysis))
        active_console.print(_disk_growth_table(report.deep_analysis))
    active_console.print(_threshold_table())


def _column_stats(
    connection: sqlite3.Connection,
    column: str,
    cutoff: float,
    first_id: int,
    latest_id: int,
) -> MetricStats:
    row = connection.execute(
        f"""
        SELECT
            (SELECT {column} FROM metric_samples WHERE id = ?) AS first_value,
            (SELECT {column} FROM metric_samples WHERE id = ?) AS latest_value,
            MIN({column}) AS min_value,
            MAX({column}) AS max_value,
            AVG({column}) AS avg_value
        FROM metric_samples
        WHERE captured_epoch >= ?
        """,
        (first_id, latest_id, cutoff),
    ).fetchone()
    return MetricStats(
        first=_optional_float(row["first_value"]),
        latest=_optional_float(row["latest_value"]),
        minimum=_optional_float(row["min_value"]),
        maximum=_optional_float(row["max_value"]),
        average=_optional_float(row["avg_value"]),
    )


def _disk_usage(connection: sqlite3.Connection, cutoff: float) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            d.device,
            d.mountpoint,
            MAX(d.used_percent) AS max_used,
            AVG(d.used_percent) AS avg_used,
            (
                SELECT latest.used_percent
                FROM disk_usage latest
                JOIN metric_samples latest_sample ON latest_sample.id = latest.sample_id
                WHERE latest.device = d.device AND latest.mountpoint = d.mountpoint
                ORDER BY latest_sample.captured_epoch DESC
                LIMIT 1
            ) AS latest_used
        FROM disk_usage d
        JOIN metric_samples s ON s.id = d.sample_id
        WHERE s.captured_epoch >= ?
        GROUP BY d.device, d.mountpoint
        ORDER BY max_used DESC
        LIMIT 12
        """,
        (cutoff,),
    )
    return [dict(row) for row in rows]


def _top_processes(connection: sqlite3.Connection, cutoff: float, kind: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT
            name,
            MAX(pid) AS pid,
            COUNT(*) AS samples,
            AVG(cpu_percent) AS avg_cpu,
            MAX(cpu_percent) AS max_cpu,
            AVG(memory_percent) AS avg_memory,
            MAX(memory_percent) AS max_memory,
            MAX(rss_bytes) AS max_rss
        FROM top_processes p
        JOIN metric_samples s ON s.id = p.sample_id
        WHERE s.captured_epoch >= ?
          AND p.kind = ?
          AND LOWER(name) NOT IN ('system idle process', 'idle')
        GROUP BY name
        ORDER BY
            CASE WHEN ? = 'cpu' THEN AVG(cpu_percent) ELSE AVG(memory_percent) END DESC
        LIMIT 10
        """,
        (cutoff, kind, kind),
    )
    return [dict(row) for row in rows]


def _health_table(report: ReportData) -> Table:
    table = Table(title="System metrics", show_lines=False)
    table.add_column("Metric")
    table.add_column("Latest", justify="right")
    table.add_column("Avg", justify="right")
    table.add_column("Max", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("State")

    metric_specs = [
        ("Load 1m", "load_ratio", _plain, _load_health_value),
        ("Load 5m", "load_ratio", _plain, _load_health_value),
        ("Load 15m", "load_ratio", _plain, _load_health_value),
        ("CPU utilization", "cpu", _percent, _max_value),
        ("RAM used", "ram", _percent, _max_value),
        ("RAM available", "informational", _bytes, _max_value),
        ("Swap usage", "swap", _percent, _max_value),
        ("Disk read", "informational", _bytes_rate, _max_value),
        ("Disk write", "informational", _bytes_rate, _max_value),
        ("Network in", "informational", _mbps, _max_value),
        ("Network out", "informational", _mbps, _max_value),
    ]
    for name, threshold_key, formatter, health_getter in metric_specs:
        stats = report.metrics.get(name)
        if stats is None:
            continue
        health_value = health_getter(report, name)
        color = health_color(health_value, threshold_key)
        table.add_row(
            name,
            _colored(formatter(stats.latest), color),
            formatter(stats.average),
            formatter(stats.maximum),
            _signed_delta(stats.delta, formatter),
            health_label(health_value, threshold_key),
        )

    for name in ("Errors in", "Errors out", "Drops in", "Drops out"):
        stats = report.metrics.get(name)
        if stats is None:
            continue
        delta = stats.delta or 0
        color = health_color(delta, "network_errors")
        table.add_row(
            name,
            _colored(_plain(stats.latest), color),
            _plain(stats.average),
            _plain(stats.maximum),
            _colored(f"+{delta:.0f}", color),
            health_label(delta, "network_errors"),
        )
    return table


def _disk_table(report: ReportData) -> Table:
    table = Table(title="Disk usage by mount", show_lines=False)
    table.add_column("Device")
    table.add_column("Mount")
    table.add_column("Latest", justify="right")
    table.add_column("Avg", justify="right")
    table.add_column("Max", justify="right")
    table.add_column("State")
    for row in report.disk_usage:
        latest = _optional_float(row["latest_used"])
        avg = _optional_float(row["avg_used"])
        maximum = _optional_float(row["max_used"])
        color = health_color(maximum, "disk")
        table.add_row(
            str(row["device"]),
            str(row["mountpoint"]),
            _colored(_percent(latest), color),
            _percent(avg),
            _percent(maximum),
            health_label(maximum, "disk"),
        )
    return table


def _process_table(title: str, rows: list[dict[str, object]], sort_metric: str) -> Table:
    table = Table(title=title, show_lines=False)
    table.add_column("Process")
    table.add_column("PID", justify="right")
    table.add_column("Samples", justify="right")
    table.add_column("Avg CPU", justify="right")
    table.add_column("Max CPU", justify="right")
    table.add_column("Avg RAM", justify="right")
    table.add_column("Max RSS", justify="right")
    for row in rows:
        threshold_key = "process_cpu" if sort_metric == "avg_cpu" else "process_ram"
        health_value = _optional_float(row[sort_metric])
        color = health_color(health_value, threshold_key)
        table.add_row(
            _colored(str(row["name"]), color),
            str(row["pid"]),
            str(row["samples"]),
            _percent(_optional_float(row["avg_cpu"])),
            _percent(_optional_float(row["max_cpu"])),
            _percent(_optional_float(row["avg_memory"])),
            format_bytes(_optional_float(row["max_rss"])),
        )
    return table


def _analysis_panel(report: ReportData) -> Panel:
    lines = []
    cpu_max = report.metrics["CPU utilization"].maximum
    ram_max = report.metrics["RAM used"].maximum
    swap_max = report.metrics["Swap usage"].maximum
    error_delta = sum((report.metrics[name].delta or 0) for name in ("Errors in", "Errors out", "Drops in", "Drops out"))
    disk_max = max((_optional_float(row["max_used"]) or 0 for row in report.disk_usage), default=0)

    lines.append(_advice("CPU", cpu_max, "cpu", "CPU load stayed healthy.", "CPU was close to saturation.", "CPU reached critical utilization."))
    lines.append(_advice("RAM", ram_max, "ram", "Memory pressure is normal.", "RAM usage is elevated.", "RAM usage reached critical levels."))
    lines.append(_advice("Swap", swap_max, "swap", "Swap usage is low.", "Swap activity is noticeable.", "Swap usage is critical."))
    lines.append(_advice("Disk", disk_max, "disk", "Disk space looks fine.", "Some disks are getting full.", "At least one disk is critically full."))
    error_color = health_color(error_delta, "network_errors")
    if error_color != "green":
        lines.append(f"[{error_color}]Network errors/drops increased during the period.[/{error_color}]")
    else:
        lines.append("[green]No network error/drop growth recorded.[/green]")

    return Panel("\n".join(lines), title="Brief analysis", border_style="cyan")


def _health_score_markup(report: ReportData) -> str:
    colors = [
        health_color(_load_health_value(report, "Load 1m"), "load_ratio"),
        health_color(report.metrics["CPU utilization"].maximum, "cpu"),
        health_color(report.metrics["RAM used"].maximum, "ram"),
        health_color(report.metrics["Swap usage"].maximum, "swap"),
        health_color(max((_optional_float(row["max_used"]) or 0 for row in report.disk_usage), default=0), "disk"),
        health_color(
            sum((report.metrics[name].delta or 0) for name in ("Errors in", "Errors out", "Drops in", "Drops out")),
            "network_errors",
        ),
    ]
    score = score_from_colors(colors)
    return f"Health Score: [{score.color}]{score.score}/100[/] | Status: [{score.color}]{score.status}[/]"


def _threshold_table() -> Table:
    table = Table(title="Color thresholds", show_lines=False)
    table.add_column("Metric")
    table.add_column("Green")
    table.add_column("Yellow")
    table.add_column("Red")
    for metric, green, yellow, red in threshold_rows():
        table.add_row(metric, f"[green]{green}[/green]", f"[yellow]{yellow}[/yellow]", f"[red]{red}[/red]")
    return table


def _deep_analysis_panel(analysis: DeepAnalysis) -> Panel:
    lines = []
    if analysis.heaviest_cpu_process:
        lines.append(
            "Heaviest CPU process: "
            f"[bold]{analysis.heaviest_cpu_process['name']}[/bold] "
            f"avg {_percent(_optional_float(analysis.heaviest_cpu_process['avg_cpu']))}, "
            f"max {_percent(_optional_float(analysis.heaviest_cpu_process['max_cpu']))}"
        )
    if analysis.heaviest_memory_process:
        lines.append(
            "Heaviest RAM process: "
            f"[bold]{analysis.heaviest_memory_process['name']}[/bold] "
            f"avg {_percent(_optional_float(analysis.heaviest_memory_process['avg_memory']))}, "
            f"max RSS {format_bytes(_optional_float(analysis.heaviest_memory_process['max_rss']))}"
        )
    lines.append(f"Peak load 1m: {_plain(analysis.peak_load)}")
    lines.append(f"Peak CPU: {_percent(analysis.peak_cpu)}")
    lines.append("")
    lines.append("[bold]Recommendations[/bold]")
    lines.extend(f"- {recommendation}" for recommendation in analysis.recommendations)
    return Panel("\n".join(lines), title="Deep analysis", border_style="cyan")


def _anomaly_table(analysis: DeepAnalysis) -> Table:
    table = Table(title="Anomaly detection", show_lines=False)
    table.add_column("Metric")
    table.add_column("Latest", justify="right")
    table.add_column("Baseline", justify="right")
    table.add_column("z-score", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("Message")
    if not analysis.anomalies:
        table.add_row("n/a", "n/a", "n/a", "n/a", "n/a", "No strong anomalies detected")
        return table
    for anomaly in analysis.anomalies:
        table.add_row(
            anomaly.metric,
            f"{anomaly.latest:.2f}",
            f"{anomaly.baseline:.2f}",
            f"{anomaly.z_score:.1f}",
            f"{anomaly.percent_change:.0f}%",
            anomaly.message,
        )
    return table


def _warn_crit_table(analysis: DeepAnalysis) -> Table:
    table = Table(title="Time in WARN/CRIT", show_lines=False)
    table.add_column("Metric")
    table.add_column("WARN", justify="right")
    table.add_column("CRIT", justify="right")
    for metric, values in analysis.warn_crit_seconds.items():
        table.add_row(metric, _duration(values["warning"]), _duration(values["critical"]))
    return table


def _disk_growth_table(analysis: DeepAnalysis) -> Table:
    table = Table(title="Disk growth", show_lines=False)
    table.add_column("Mount")
    table.add_column("Device")
    table.add_column("First", justify="right")
    table.add_column("Latest", justify="right")
    table.add_column("Delta", justify="right")
    if not analysis.disk_growth:
        table.add_row("n/a", "n/a", "n/a", "n/a", "No disk growth data")
        return table
    for row in analysis.disk_growth:
        table.add_row(
            str(row["mountpoint"]),
            str(row["device"]),
            _percent(_optional_float(row["first"])),
            _percent(_optional_float(row["latest"])),
            f"{_optional_float(row['delta']) or 0:+.1f}%",
        )
    return table


def _duration(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _advice(name: str, value: float | None, threshold_key: str, ok: str, warning: str, bad: str) -> str:
    color = health_color(value, threshold_key)
    message = {"green": ok, "yellow": warning, "red": bad}[color]
    return f"[{color}]{name}: {message}[/{color}]"


def _colored(value: str, color: str) -> str:
    return f"[{color}]{value}[/{color}]"


def _max_value(report: ReportData, metric_name: str) -> float | None:
    return report.metrics[metric_name].maximum


def _load_health_value(report: ReportData, metric_name: str) -> float | None:
    return load_ratio(report.metrics[metric_name].maximum, report.metrics["Logical CPUs"].latest)


def _signed_delta(delta: float | None, formatter: object) -> str:
    if delta is None:
        return "n/a"
    sign = "+" if delta >= 0 else ""
    if formatter in (_bytes, _bytes_rate):
        return f"{sign}{format_bytes(delta)}"
    return f"{sign}{delta:.2f}"


def _plain(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{min(value, 100.0):.1f}%"


def _bytes(value: float | None) -> str:
    return format_bytes(value)


def _bytes_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{format_bytes(value)}/s"


def _mbps(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} MB/s"


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
