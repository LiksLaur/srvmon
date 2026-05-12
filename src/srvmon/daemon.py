from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import psutil

from srvmon.collectors import MetricsCollector
from srvmon.config import DEFAULT_CONFIG_DIR, SrvmonConfig, load_config
from srvmon.health import configure_thresholds
from srvmon.storage import MetricStorage


PID_PATH = DEFAULT_CONFIG_DIR / "srvmon.pid"
LOG_PATH = DEFAULT_CONFIG_DIR / "srvmon.log"
MIN_COLLECTION_INTERVAL = 0.5


@dataclass(slots=True)
class ServiceState:
    running: bool
    pid: int | None = None
    message: str = ""


def start_service(config: SrvmonConfig | None = None) -> ServiceState:
    active_config = config or load_config()
    state = service_status()
    if state.running:
        return ServiceState(True, state.pid, f"srvmon service is already running with PID {state.pid}.")

    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH.open("a", encoding="utf-8")
    command = [sys.executable, "-m", "srvmon.daemon"]
    creationflags = 0
    start_new_session = True
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        start_new_session = False

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        close_fds=os.name != "nt",
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    log_file.close()
    _write_pid(process.pid, active_config)
    return ServiceState(True, process.pid, f"srvmon service started with PID {process.pid}.")


def stop_service(timeout: float = 8.0) -> ServiceState:
    pid = _read_pid()
    if pid is None:
        return ServiceState(False, None, "srvmon service is not running.")

    if not psutil.pid_exists(pid):
        _remove_pid()
        return ServiceState(False, pid, "stale PID file removed; service was not running.")

    process = psutil.Process(pid)
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)
    _remove_pid()
    return ServiceState(False, pid, f"srvmon service stopped (PID {pid}).")


def service_status() -> ServiceState:
    pid = _read_pid()
    if pid is None:
        return ServiceState(False, None, "srvmon service is not running.")
    if psutil.pid_exists(pid):
        return ServiceState(True, pid, f"srvmon service is running with PID {pid}.")
    _remove_pid()
    return ServiceState(False, pid, "stale PID file removed; service was not running.")


def run_daemon(config: SrvmonConfig | None = None) -> None:
    active_config = config or load_config()
    configure_thresholds(active_config.thresholds)
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _write_pid(os.getpid(), active_config)

    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, stop)

    storage = MetricStorage(
        active_config.database_path,
        retention=timedelta(days=active_config.retention_days),
    )
    storage.save_config_metadata(
        config_path=active_config.config_path,
        database_path=storage.db_path,
        live_refresh_seconds=active_config.live_refresh_seconds,
        collection_seconds=active_config.collection_seconds,
        retention_days=active_config.retention_days,
        thresholds=active_config.thresholds,
        disks=active_config.disks,
        interfaces=active_config.interfaces,
    )
    collector = MetricsCollector(active_config.disks, active_config.interfaces)
    interval = max(active_config.collection_seconds, MIN_COLLECTION_INTERVAL)

    _log(f"service started pid={os.getpid()} interval={interval}s db={storage.db_path}")
    try:
        while running:
            started_at = time.monotonic()
            try:
                storage.save_snapshot(collector.collect())
            except Exception as error:  # noqa: BLE001 - daemon must keep collecting after transient failures.
                _log(f"collection error: {error!r}")
            elapsed = time.monotonic() - started_at
            time.sleep(max(interval - elapsed, MIN_COLLECTION_INTERVAL))
    finally:
        _log("service stopped")
        if _read_pid() == os.getpid():
            _remove_pid()


def main() -> None:
    run_daemon()


def _write_pid(pid: int, config: SrvmonConfig) -> None:
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": pid,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config_path": str(config.config_path),
        "database_path": str(config.database_path),
        "collection_seconds": config.collection_seconds,
    }
    PID_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_pid() -> int | None:
    try:
        payload = json.loads(PID_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pid = payload.get("pid")
    return int(pid) if isinstance(pid, int) else None


def _remove_pid() -> None:
    try:
        PID_PATH.unlink()
    except FileNotFoundError:
        pass


def _log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


if __name__ == "__main__":
    main()
