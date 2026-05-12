from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Threshold:
    warning: float | None
    critical: float | None
    unit: str
    description: str


@dataclass(frozen=True, slots=True)
class HealthScore:
    score: int
    status: str
    color: str


THRESHOLDS = {
    "load_ratio": Threshold(70.0, 100.0, "% of logical CPUs", "Load average divided by logical CPU count"),
    "cpu": Threshold(70.0, 90.0, "%", "CPU utilization"),
    "ram": Threshold(75.0, 90.0, "%", "RAM used"),
    "swap": Threshold(20.0, 50.0, "%", "Swap used"),
    "disk": Threshold(80.0, 90.0, "%", "Disk used on any mounted partition"),
    "disk_latency": Threshold(20.0, 50.0, "ms", "Disk I/O latency when available"),
    "network_errors": Threshold(1.0, 10.0, "count", "Network errors or drops increased over the period"),
    "process_cpu": Threshold(70.0, 90.0, "%", "Single process CPU share normalized to whole host"),
    "process_ram": Threshold(10.0, 25.0, "%", "Single process RAM share"),
    "informational": Threshold(None, None, "", "Informational metric without a universal threshold"),
}

_active_thresholds = dict(THRESHOLDS)


def configure_thresholds(thresholds: dict[str, Threshold] | None) -> None:
    global _active_thresholds
    _active_thresholds = dict(THRESHOLDS)
    if thresholds:
        _active_thresholds.update(thresholds)


def health_color(value: float | None, key: str) -> str:
    threshold = _active_thresholds[key]
    if value is None or threshold.warning is None or threshold.critical is None:
        return "green"
    if value >= threshold.critical:
        return "red"
    if value >= threshold.warning:
        return "yellow"
    return "green"


def health_label(value: float | None, key: str) -> str:
    labels = {"green": "OK", "yellow": "WARN", "red": "CRIT"}
    color = health_color(value, key)
    return f"[{color}]{labels[color]}[/{color}]"


def score_from_colors(colors: list[str]) -> HealthScore:
    if not colors:
        return HealthScore(100, "OK", "green")
    penalties = {"green": 0, "yellow": 10, "red": 25}
    score = max(0, 100 - sum(penalties.get(color, 0) for color in colors))
    if any(color == "red" for color in colors) or score < 60:
        return HealthScore(score, "CRITICAL", "red")
    if any(color == "yellow" for color in colors) or score < 85:
        return HealthScore(score, "WARNING", "yellow")
    return HealthScore(score, "OK", "green")


def load_ratio(load: float | None, logical_cpus: float | None) -> float | None:
    if load is None:
        return None
    cores = logical_cpus or 1.0
    return load / max(cores, 1.0) * 100.0


def threshold_rows() -> list[tuple[str, str, str, str]]:
    rows = []
    for name, threshold in THRESHOLDS.items():
        threshold = _active_thresholds.get(name, threshold)
        if name == "informational":
            continue
        warning = f">= {threshold.warning:g} {threshold.unit}" if threshold.warning is not None else "n/a"
        critical = f">= {threshold.critical:g} {threshold.unit}" if threshold.critical is not None else "n/a"
        rows.append((threshold.description, "below warning", warning, critical))
    rows.append(("Disk/Network throughput", "informational", "no universal threshold", "errors/latency decide"))
    return rows
