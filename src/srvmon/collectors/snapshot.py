from __future__ import annotations

from datetime import datetime, timedelta

import psutil

from srvmon.collectors.cpu import collect_cpu
from srvmon.collectors.disk import DiskCollector
from srvmon.collectors.memory import collect_memory
from srvmon.collectors.network import NetworkCollector
from srvmon.collectors.processes import collect_processes
from srvmon.collectors.system import collect_filesystem, collect_logs, collect_sensors, collect_services
from srvmon.models import MetricsSnapshot


class MetricsCollector:
    def __init__(self, monitored_disks: list[str] | None = None, monitored_interfaces: list[str] | None = None) -> None:
        self._disk = DiskCollector(monitored_disks)
        self._network = NetworkCollector(monitored_interfaces)

    def collect(self) -> MetricsSnapshot:
        now = datetime.now()
        process_rows, top_cpu, top_memory = collect_processes()
        memory_rows, process_memory = collect_memory(top_memory)
        partitions, disk_io = self._disk.collect()
        network_rows, network_rate, connections = self._network.collect()
        cpu_rows, cpu_per_core = collect_cpu()

        return MetricsSnapshot(
            captured_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            uptime=timedelta(seconds=int(now.timestamp() - psutil.boot_time())),
            cpu=cpu_rows,
            cpu_per_core=cpu_per_core,
            memory=memory_rows,
            process_memory=process_memory,
            partitions=partitions,
            disk_io=disk_io,
            network=network_rows,
            network_rate=network_rate,
            connections=connections,
            processes=process_rows,
            top_cpu=top_cpu,
            top_memory=top_memory,
            sensors=collect_sensors(),
            filesystem=collect_filesystem(),
            logs=collect_logs(),
            services=collect_services(),
        )
