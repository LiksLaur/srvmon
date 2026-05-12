from __future__ import annotations

from datetime import datetime

import psutil

from srvmon.models import LogFileInfo, MetricRow, ServiceInfo
from srvmon.platforms import command_exists, get_log_paths, run_command


def collect_sensors() -> list[MetricRow]:
    rows: list[MetricRow] = []
    temperatures = getattr(psutil, "sensors_temperatures", lambda: {})()
    fans = getattr(psutil, "sensors_fans", lambda: {})()

    for chip, entries in temperatures.items():
        for entry in entries:
            label = entry.label or chip
            rows.append(MetricRow(f"Temp {label}", entry.current, "C"))

    for chip, entries in fans.items():
        for entry in entries:
            label = entry.label or chip
            rows.append(MetricRow(f"Fan {label}", entry.current, "RPM"))

    if not rows:
        rows.append(MetricRow("Sensors", "n/a"))
    return rows


def collect_filesystem() -> list[MetricRow]:
    rows = [
        MetricRow("Disk partitions", len(psutil.disk_partitions(all=False))),
    ]
    return rows


def collect_logs() -> list[LogFileInfo]:
    log_files: list[LogFileInfo] = []
    for path in get_log_paths():
        try:
            stat = path.stat()
        except OSError:
            continue
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        log_files.append(LogFileInfo(str(path), stat.st_size, modified))
    return log_files


def collect_services(limit: int = 12) -> list[ServiceInfo]:
    if not command_exists("systemctl"):
        return [ServiceInfo("systemd", "n/a", "systemctl unavailable")]

    output = run_command(
        ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--plain", "--no-legend"],
        timeout=3.0,
    )
    if not output:
        return [ServiceInfo("systemd", "n/a", "no data")]

    services: list[ServiceInfo] = []
    for line in output.splitlines()[:limit]:
        parts = line.split(None, 4)
        if len(parts) >= 4:
            services.append(ServiceInfo(parts[0], parts[2], parts[3]))
    return services
