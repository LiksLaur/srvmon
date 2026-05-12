from __future__ import annotations

import math
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from srvmon.health import health_color, load_ratio
from srvmon.periods import REPORT_PERIODS
from srvmon.storage import DEFAULT_DATA_DIR, MetricStorage


@dataclass(slots=True)
class Anomaly:
    metric: str
    latest: float
    baseline: float
    z_score: float
    percent_change: float
    message: str


@dataclass(slots=True)
class DeepAnalysis:
    anomalies: list[Anomaly] = field(default_factory=list)
    heaviest_cpu_process: dict[str, object] | None = None
    heaviest_memory_process: dict[str, object] | None = None
    peak_load: float | None = None
    peak_cpu: float | None = None
    disk_growth: list[dict[str, object]] = field(default_factory=list)
    warn_crit_seconds: dict[str, dict[str, float]] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)


def analyze_history(db_path: Path | None = None, period: timedelta = REPORT_PERIODS["-1d"]) -> DeepAnalysis:
    path = db_path or DEFAULT_DATA_DIR / "metrics.sqlite3"
    MetricStorage(path)
    cutoff = time.time() - period.total_seconds()
    baseline_cutoff = time.time() - timedelta(days=7).total_seconds()
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        samples = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM metric_samples WHERE captured_epoch >= ? ORDER BY captured_epoch",
                (cutoff,),
            )
        ]
        baseline = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM metric_samples WHERE captured_epoch >= ? ORDER BY captured_epoch",
                (baseline_cutoff,),
            )
        ]
        disk_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT s.captured_epoch, d.device, d.mountpoint, d.used_percent
                FROM disk_usage d
                JOIN metric_samples s ON s.id = d.sample_id
                WHERE s.captured_epoch >= ?
                ORDER BY d.mountpoint, s.captured_epoch
                """,
                (cutoff,),
            )
        ]
        top_cpu = _top_process(connection, cutoff, "cpu")
        top_memory = _top_process(connection, cutoff, "memory")

    analysis = DeepAnalysis()
    analysis.anomalies = _anomalies(samples, baseline)
    analysis.heaviest_cpu_process = top_cpu
    analysis.heaviest_memory_process = top_memory
    analysis.peak_load = max((_optional_float(row.get("load_1")) or 0 for row in samples), default=None)
    analysis.peak_cpu = max((_optional_float(row.get("cpu_utilization_percent")) or 0 for row in samples), default=None)
    analysis.disk_growth = _disk_growth(disk_rows)
    analysis.warn_crit_seconds = _warn_crit_time(samples, disk_rows)
    analysis.recommendations = _recommendations(analysis)
    return analysis


def _anomalies(samples: list[dict[str, object]], baseline: list[dict[str, object]]) -> list[Anomaly]:
    if not samples or len(baseline) < 3:
        return []
    specs = {
        "CPU utilization": "cpu_utilization_percent",
        "RAM used": "ram_used_percent",
        "Swap usage": "swap_usage_percent",
        "Network out": "network_out_mbps",
        "Network in": "network_in_mbps",
        "Disk read": "disk_read_bps",
        "Disk write": "disk_write_bps",
    }
    anomalies = []
    latest = samples[-1]
    for name, column in specs.items():
        values = [_optional_float(row.get(column)) for row in baseline]
        clean = [value for value in values if value is not None]
        latest_value = _optional_float(latest.get(column))
        if latest_value is None or len(clean) < 3:
            continue
        mean = sum(clean) / len(clean)
        variance = sum((value - mean) ** 2 for value in clean) / len(clean)
        stdev = math.sqrt(variance)
        if stdev <= 0:
            continue
        z_score = (latest_value - mean) / stdev
        percent_change = ((latest_value - mean) / mean * 100) if mean else 0.0
        if z_score >= 2.0 and percent_change > 50:
            anomalies.append(
                Anomaly(
                    metric=name,
                    latest=latest_value,
                    baseline=mean,
                    z_score=z_score,
                    percent_change=percent_change,
                    message=f"{name} is above typical behavior by {percent_change:.0f}% (z={z_score:.1f}).",
                )
            )
    return anomalies


def _top_process(connection: sqlite3.Connection, cutoff: float, kind: str) -> dict[str, object] | None:
    order_column = "cpu_percent" if kind == "cpu" else "memory_percent"
    row = connection.execute(
        f"""
        SELECT name, MAX(pid) AS pid, AVG(cpu_percent) AS avg_cpu,
               MAX(cpu_percent) AS max_cpu, AVG(memory_percent) AS avg_memory,
               MAX(memory_percent) AS max_memory, MAX(rss_bytes) AS max_rss,
               COUNT(*) AS samples
        FROM top_processes p
        JOIN metric_samples s ON s.id = p.sample_id
        WHERE s.captured_epoch >= ? AND p.kind = ?
        GROUP BY name
        ORDER BY AVG({order_column}) DESC
        LIMIT 1
        """,
        (cutoff, kind),
    ).fetchone()
    return dict(row) if row else None


def _disk_growth(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault((str(row["device"]), str(row["mountpoint"])), []).append(row)
    growth = []
    for (device, mountpoint), items in grouped.items():
        if len(items) < 2:
            continue
        first = _optional_float(items[0]["used_percent"]) or 0
        latest = _optional_float(items[-1]["used_percent"]) or 0
        delta = latest - first
        growth.append({"device": device, "mountpoint": mountpoint, "first": first, "latest": latest, "delta": delta})
    return sorted(growth, key=lambda row: row["delta"], reverse=True)[:10]


def _warn_crit_time(samples: list[dict[str, object]], disk_rows: list[dict[str, object]]) -> dict[str, dict[str, float]]:
    result = {
        "Load 1m": {"warning": 0.0, "critical": 0.0},
        "CPU": {"warning": 0.0, "critical": 0.0},
        "RAM": {"warning": 0.0, "critical": 0.0},
        "Swap": {"warning": 0.0, "critical": 0.0},
        "Disk": {"warning": 0.0, "critical": 0.0},
    }
    for previous, current in zip(samples, samples[1:]):
        duration = max((_optional_float(current["captured_epoch"]) or 0) - (_optional_float(previous["captured_epoch"]) or 0), 0)
        _add_state_time(result["Load 1m"], health_color(load_ratio(_optional_float(previous["load_1"]), _optional_float(previous["cpu_logical_cores"])), "load_ratio"), duration)
        _add_state_time(result["CPU"], health_color(_optional_float(previous["cpu_utilization_percent"]), "cpu"), duration)
        _add_state_time(result["RAM"], health_color(_optional_float(previous["ram_used_percent"]), "ram"), duration)
        _add_state_time(result["Swap"], health_color(_optional_float(previous["swap_usage_percent"]), "swap"), duration)

    by_epoch: dict[float, float] = {}
    for row in disk_rows:
        epoch = _optional_float(row["captured_epoch"]) or 0
        by_epoch[epoch] = max(by_epoch.get(epoch, 0), _optional_float(row["used_percent"]) or 0)
    epochs = sorted(by_epoch)
    for previous, current in zip(epochs, epochs[1:]):
        _add_state_time(result["Disk"], health_color(by_epoch[previous], "disk"), max(current - previous, 0))
    return result


def _add_state_time(target: dict[str, float], color: str, duration: float) -> None:
    if color == "yellow":
        target["warning"] += duration
    elif color == "red":
        target["critical"] += duration


def _recommendations(analysis: DeepAnalysis) -> list[str]:
    recommendations = []
    if (analysis.peak_cpu or 0) >= 90:
        recommendations.append("CPU reached critical levels: inspect top CPU processes and consider workload limits.")
    if analysis.heaviest_memory_process and (_optional_float(analysis.heaviest_memory_process.get("avg_memory")) or 0) >= 10:
        recommendations.append("A single process uses a large RAM share: review memory leaks or service sizing.")
    if any((_optional_float(row["delta"]) or 0) > 5 for row in analysis.disk_growth):
        recommendations.append("Disk usage is growing quickly: check logs, caches, backups and retention policies.")
    if any(anomaly.metric.startswith("Network") for anomaly in analysis.anomalies):
        recommendations.append("Network traffic is anomalous: verify backups, replication jobs, or unexpected outbound traffic.")
    if not recommendations:
        recommendations.append("No strong optimization action is required from the collected period.")
    return recommendations


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
