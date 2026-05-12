from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta


MetricValue = str | int | float | None


@dataclass(slots=True)
class MetricRow:
    name: str
    value: MetricValue
    unit: str = ""


@dataclass(slots=True)
class PartitionUsage:
    device: str
    mountpoint: str
    fstype: str
    total: int
    used: int
    free: int
    percent: float
    inodes_used_percent: float | None = None


@dataclass(slots=True)
class DiskIoRate:
    read_bps: float = 0.0
    write_bps: float = 0.0
    read_iops: float = 0.0
    write_iops: float = 0.0
    latency_ms: float | None = None


@dataclass(slots=True)
class NetworkRate:
    bytes_sent_per_sec: float = 0.0
    bytes_recv_per_sec: float = 0.0
    packets_sent_per_sec: float = 0.0
    packets_recv_per_sec: float = 0.0


@dataclass(slots=True)
class ProcessInfo:
    pid: int
    name: str
    username: str
    cpu_percent: float
    memory_percent: float
    rss: int
    status: str


@dataclass(slots=True)
class ServiceInfo:
    name: str
    state: str
    substate: str = ""


@dataclass(slots=True)
class LogFileInfo:
    path: str
    size: int
    modified: str


@dataclass(slots=True)
class MetricsSnapshot:
    captured_at: str
    uptime: timedelta | None
    cpu: list[MetricRow] = field(default_factory=list)
    cpu_per_core: list[float] = field(default_factory=list)
    memory: list[MetricRow] = field(default_factory=list)
    process_memory: list[ProcessInfo] = field(default_factory=list)
    partitions: list[PartitionUsage] = field(default_factory=list)
    disk_io: DiskIoRate = field(default_factory=DiskIoRate)
    network: list[MetricRow] = field(default_factory=list)
    network_rate: NetworkRate = field(default_factory=NetworkRate)
    connections: list[MetricRow] = field(default_factory=list)
    processes: list[MetricRow] = field(default_factory=list)
    top_cpu: list[ProcessInfo] = field(default_factory=list)
    top_memory: list[ProcessInfo] = field(default_factory=list)
    sensors: list[MetricRow] = field(default_factory=list)
    filesystem: list[MetricRow] = field(default_factory=list)
    logs: list[LogFileInfo] = field(default_factory=list)
    services: list[ServiceInfo] = field(default_factory=list)
