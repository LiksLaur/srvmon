from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import psutil
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from srvmon.config import DEFAULT_CONFIG_DIR, SrvmonConfig, load_config
from srvmon.storage import MetricStorage


def render_doctor(console: Console | None = None, config: SrvmonConfig | None = None) -> None:
    active_console = console or Console()
    active_config = config or load_config()
    checks = [
        _python_check(),
        _srvmon_dir_check(DEFAULT_CONFIG_DIR),
        _sqlite_check(active_config.database_path),
        _process_access_check(),
        _sensors_check(),
    ]
    table = Table(title="srvmon doctor", show_lines=False)
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")
    for name, ok, details in checks:
        color = "green" if ok else "red"
        table.add_row(name, f"[{color}]{'OK' if ok else 'FAIL'}[/{color}]", details)
    active_console.print(Panel(f"config: [bold]{active_config.config_path}[/bold]", title="Diagnostics", border_style="cyan"))
    active_console.print(table)


def _python_check() -> tuple[str, bool, str]:
    version = sys.version_info
    ok = version >= (3, 10)
    return "Python version", ok, f"{version.major}.{version.minor}.{version.micro}"


def _srvmon_dir_check(path: Path) -> tuple[str, bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return "~/.srvmon permissions", True, str(path)
    except OSError as error:
        return "~/.srvmon permissions", False, str(error)


def _sqlite_check(db_path: Path) -> tuple[str, bool, str]:
    try:
        MetricStorage(db_path)
        with sqlite3.connect(db_path) as connection:
            connection.execute("SELECT 1").fetchone()
        return "SQLite access", True, str(db_path)
    except (OSError, sqlite3.Error) as error:
        return "SQLite access", False, str(error)


def _process_access_check() -> tuple[str, bool, str]:
    try:
        current = psutil.Process()
        current.as_dict(attrs=["pid", "name", "cpu_percent", "memory_percent"])
        seen = sum(1 for _ in psutil.process_iter(["pid", "name"]))
        return "Process access", True, f"{seen} processes visible"
    except (psutil.Error, OSError) as error:
        return "Process access", False, str(error)


def _sensors_check() -> tuple[str, bool, str]:
    try:
        temperatures = getattr(psutil, "sensors_temperatures", lambda: {})()
        fans = getattr(psutil, "sensors_fans", lambda: {})()
    except (AttributeError, OSError) as error:
        return "Sensors support", False, str(error)
    count = sum(len(entries) for entries in temperatures.values()) + sum(len(entries) for entries in fans.values())
    if count:
        return "Sensors support", True, f"{count} sensor entries"
    return "Sensors support", False, "no temperature/fan sensors exposed by OS"
