from __future__ import annotations

import time

import psutil

from srvmon.models import DiskIoRate, PartitionUsage
from srvmon.platforms import get_inode_percent


class DiskCollector:
    def __init__(self, monitored_disks: list[str] | None = None) -> None:
        self.monitored_disks = set(monitored_disks or [])
        self._previous = psutil.disk_io_counters()
        self._previous_time = time.monotonic()

    def collect(self) -> tuple[list[PartitionUsage], DiskIoRate]:
        partitions: list[PartitionUsage] = []
        for partition in psutil.disk_partitions(all=False):
            if self.monitored_disks and not self._matches_partition(partition):
                continue
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (OSError, PermissionError):
                continue
            partitions.append(
                PartitionUsage(
                    device=partition.device,
                    mountpoint=partition.mountpoint,
                    fstype=partition.fstype,
                    total=usage.total,
                    used=usage.used,
                    free=usage.free,
                    percent=usage.percent,
                    inodes_used_percent=get_inode_percent(partition.mountpoint),
                )
            )

        now = time.monotonic()
        current = psutil.disk_io_counters()
        elapsed = max(now - self._previous_time, 0.001)
        rate = DiskIoRate()

        if current and self._previous:
            read_delta = current.read_bytes - self._previous.read_bytes
            write_delta = current.write_bytes - self._previous.write_bytes
            read_count_delta = current.read_count - self._previous.read_count
            write_count_delta = current.write_count - self._previous.write_count
            busy_delta = getattr(current, "busy_time", 0) - getattr(self._previous, "busy_time", 0)
            op_delta = read_count_delta + write_count_delta

            rate = DiskIoRate(
                read_bps=max(read_delta, 0) / elapsed,
                write_bps=max(write_delta, 0) / elapsed,
                read_iops=max(read_count_delta, 0) / elapsed,
                write_iops=max(write_count_delta, 0) / elapsed,
                latency_ms=(busy_delta / op_delta) if op_delta > 0 and busy_delta > 0 else None,
            )

        self._previous = current
        self._previous_time = now
        return partitions, rate

    def _matches_partition(self, partition: object) -> bool:
        values = {
            getattr(partition, "device", ""),
            getattr(partition, "mountpoint", ""),
        }
        return bool(values & self.monitored_disks)
