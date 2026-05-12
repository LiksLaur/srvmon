from __future__ import annotations

from datetime import datetime

import psutil

from srvmon.models import MetricRow, ProcessInfo


IDLE_PROCESS_NAMES = {"system idle process", "idle"}


def collect_processes(limit: int = 10) -> tuple[list[MetricRow], list[ProcessInfo], list[ProcessInfo]]:
    processes: list[ProcessInfo] = []
    zombies = 0
    logical_cpus = psutil.cpu_count(logical=True) or 1

    for process in psutil.process_iter(
        ["pid", "name", "username", "cpu_percent", "memory_percent", "memory_info", "status"]
    ):
        try:
            info = process.info
            status = info.get("status") or "unknown"
            if status == psutil.STATUS_ZOMBIE:
                zombies += 1
            memory_info = info.get("memory_info")
            name = info.get("name") or "unknown"
            if name.lower() in IDLE_PROCESS_NAMES:
                continue
            raw_cpu_percent = float(info.get("cpu_percent") or 0.0)
            normalized_cpu_percent = min(raw_cpu_percent / logical_cpus, 100.0)
            processes.append(
                ProcessInfo(
                    pid=info["pid"],
                    name=name,
                    username=info.get("username") or "n/a",
                    cpu_percent=normalized_cpu_percent,
                    memory_percent=float(info.get("memory_percent") or 0.0),
                    rss=getattr(memory_info, "rss", 0),
                    status=status,
                )
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

    users = psutil.users()
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime_seconds = (datetime.now() - boot_time).total_seconds()

    rows = [
        MetricRow("Processes", len(processes)),
        MetricRow("Zombie processes", zombies),
        MetricRow("Logged in users", len(users)),
        MetricRow("Uptime seconds", int(uptime_seconds)),
    ]

    top_cpu = sorted(processes, key=lambda item: item.cpu_percent, reverse=True)[:limit]
    top_memory = sorted(processes, key=lambda item: item.memory_percent, reverse=True)[:limit]
    return rows, top_cpu, top_memory
