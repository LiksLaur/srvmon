from __future__ import annotations

from dataclasses import dataclass

from srvmon.health import health_color, health_label, load_ratio
from srvmon.models import MetricsSnapshot, MetricValue


@dataclass(frozen=True, slots=True)
class AlertNotice:
    metric: str
    value: str
    state: str
    color: str
    message: str


def current_alerts(snapshot: MetricsSnapshot) -> list[AlertNotice]:
    notices: list[AlertNotice] = []
    cpu = _metric(snapshot.cpu, "CPU utilization")
    ram = _metric(snapshot.memory, "RAM utilization")
    swap = _metric(snapshot.memory, "Swap utilization")
    load = _metric(snapshot.cpu, "Load average 1m")
    logical_cores = _metric(snapshot.cpu, "Logical cores") or 1.0
    load_percent = load_ratio(load, logical_cores)
    disk = max((partition.percent for partition in snapshot.partitions), default=None)
    network_errors = sum(
        _metric(snapshot.network, name) or 0.0
        for name in ("Errors in", "Errors out", "Drops in", "Drops out")
    )

    _append_notice(notices, "CPU", cpu, "cpu", "%", "CPU usage is above the configured threshold.")
    _append_notice(notices, "RAM", ram, "ram", "%", "RAM pressure is above the configured threshold.")
    _append_notice(notices, "Swap", swap, "swap", "%", "Swap usage is above the configured threshold.")
    _append_notice(
        notices,
        "Load 1m",
        load_percent,
        "load_ratio",
        "% of logical CPUs",
        "Load average is high for the available CPU count.",
    )
    _append_notice(notices, "Disk", disk, "disk", "%", "At least one disk is close to capacity.")
    _append_notice(
        notices,
        "Network errors/drops",
        network_errors,
        "network_errors",
        "count",
        "Network errors or dropped packets are present.",
    )
    return notices


def alert_summary(snapshot: MetricsSnapshot) -> str:
    notices = current_alerts(snapshot)
    if not notices:
        return "OK: no active warnings"
    critical = sum(1 for notice in notices if notice.color == "red")
    warning = sum(1 for notice in notices if notice.color == "yellow")
    parts = []
    if critical:
        parts.append(f"{critical} critical")
    if warning:
        parts.append(f"{warning} warning")
    return ", ".join(parts)


def _append_notice(
    notices: list[AlertNotice],
    metric: str,
    value: float | None,
    threshold_key: str,
    unit: str,
    message: str,
) -> None:
    color = health_color(value, threshold_key)
    if color == "green":
        return
    state = "CRITICAL" if color == "red" else "WARNING"
    value_text = "n/a" if value is None else f"{value:.1f} {unit}".strip()
    notices.append(
        AlertNotice(
            metric=metric,
            value=value_text,
            state=state,
            color=color,
            message=f"{health_label(value, threshold_key)} {message}",
        )
    )


def _metric(rows: list[object], name: str) -> float | None:
    raw: MetricValue | None = next((getattr(row, "value", None) for row in rows if getattr(row, "name", "") == name), None)
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    return None
