from __future__ import annotations

import os

import psutil

from srvmon.models import MetricRow


def collect_cpu() -> tuple[list[MetricRow], list[float]]:
    load_avg = os.getloadavg() if hasattr(os, "getloadavg") else None
    stats = psutil.cpu_stats()
    freq = psutil.cpu_freq()

    rows = [
        MetricRow("CPU utilization", psutil.cpu_percent(interval=None), "%"),
        MetricRow("Physical cores", psutil.cpu_count(logical=False) or "n/a"),
        MetricRow("Logical cores", psutil.cpu_count(logical=True) or "n/a"),
        MetricRow("Context switches", stats.ctx_switches),
        MetricRow("Interrupts", stats.interrupts),
        MetricRow("Soft interrupts", getattr(stats, "soft_interrupts", "n/a")),
        MetricRow("Syscalls", getattr(stats, "syscalls", "n/a")),
    ]

    if load_avg is not None:
        rows.extend(
            [
                MetricRow("Load average 1m", f"{load_avg[0]:.2f}"),
                MetricRow("Load average 5m", f"{load_avg[1]:.2f}"),
                MetricRow("Load average 15m", f"{load_avg[2]:.2f}"),
            ]
        )
    else:
        rows.append(MetricRow("Load average", "n/a"))

    if freq is not None:
        rows.append(MetricRow("CPU frequency", f"{freq.current:.0f}", "MHz"))

    return rows, psutil.cpu_percent(interval=None, percpu=True)
