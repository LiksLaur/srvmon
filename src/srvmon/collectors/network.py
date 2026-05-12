from __future__ import annotations

from collections import Counter
import time

import psutil

from srvmon.models import MetricRow, NetworkRate


class NetworkCollector:
    def __init__(self, monitored_interfaces: list[str] | None = None) -> None:
        self.monitored_interfaces = set(monitored_interfaces or [])
        self._previous = self._counters()
        self._previous_time = time.monotonic()

    def collect(self) -> tuple[list[MetricRow], NetworkRate, list[MetricRow]]:
        now = time.monotonic()
        current = self._counters()
        elapsed = max(now - self._previous_time, 0.001)

        rows = [
            MetricRow("Bytes sent", current.bytes_sent, "bytes"),
            MetricRow("Bytes received", current.bytes_recv, "bytes"),
            MetricRow("Packets sent", current.packets_sent),
            MetricRow("Packets received", current.packets_recv),
            MetricRow("Errors in", current.errin),
            MetricRow("Errors out", current.errout),
            MetricRow("Drops in", current.dropin),
            MetricRow("Drops out", current.dropout),
        ]

        rate = NetworkRate(
            bytes_sent_per_sec=max(current.bytes_sent - self._previous.bytes_sent, 0) / elapsed,
            bytes_recv_per_sec=max(current.bytes_recv - self._previous.bytes_recv, 0) / elapsed,
            packets_sent_per_sec=max(current.packets_sent - self._previous.packets_sent, 0) / elapsed,
            packets_recv_per_sec=max(current.packets_recv - self._previous.packets_recv, 0) / elapsed,
        )

        connections = self._connection_rows()
        self._previous = current
        self._previous_time = now
        return rows, rate, connections

    def _counters(self) -> object:
        if not self.monitored_interfaces:
            return psutil.net_io_counters()

        per_interface = psutil.net_io_counters(pernic=True)
        selected = [
            counters
            for name, counters in per_interface.items()
            if name in self.monitored_interfaces
        ]
        if not selected:
            return psutil.net_io_counters()

        base = selected[0]
        values = {
            field: sum(getattr(item, field) for item in selected)
            for field in base._fields
        }
        return type(base)(**values)

    def _connection_rows(self) -> list[MetricRow]:
        try:
            connections = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            return [MetricRow("Open connections", "access denied")]

        proto_counts = Counter("TCP" if connection.type.name == "SOCK_STREAM" else "UDP" for connection in connections)
        status_counts = Counter(connection.status for connection in connections if connection.status)
        rows = [
            MetricRow("Open connections", len(connections)),
            MetricRow("TCP connections", proto_counts.get("TCP", 0)),
            MetricRow("UDP connections", proto_counts.get("UDP", 0)),
        ]
        for status, count in status_counts.most_common(6):
            rows.append(MetricRow(f"TCP {status}", count))
        return rows
