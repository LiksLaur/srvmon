from __future__ import annotations

import psutil

from srvmon.models import MetricRow, ProcessInfo


def collect_memory(top_processes: list[ProcessInfo]) -> tuple[list[MetricRow], list[ProcessInfo]]:
    virtual = psutil.virtual_memory()
    swap = psutil.swap_memory()

    rows = [
        MetricRow("RAM total", virtual.total, "bytes"),
        MetricRow("RAM used", virtual.used, "bytes"),
        MetricRow("RAM available", virtual.available, "bytes"),
        MetricRow("RAM utilization", virtual.percent, "%"),
        MetricRow("Swap total", swap.total, "bytes"),
        MetricRow("Swap used", swap.used, "bytes"),
        MetricRow("Swap free", swap.free, "bytes"),
        MetricRow("Swap utilization", swap.percent, "%"),
        MetricRow("Buffers", getattr(virtual, "buffers", None), "bytes"),
        MetricRow("Cache", getattr(virtual, "cached", None), "bytes"),
    ]

    return rows, top_processes[:10]
