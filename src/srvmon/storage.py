from __future__ import annotations

import sqlite3
import time
import json
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any

from srvmon.health import Threshold
from srvmon.models import MetricsSnapshot, MetricValue, ProcessInfo


DEFAULT_DATA_DIR = Path.home() / ".srvmon" / "data"
DEFAULT_RETENTION = timedelta(days=62)


class MetricStorage:
    def __init__(self, db_path: Path | None = None, retention: timedelta = DEFAULT_RETENTION) -> None:
        self.db_path = db_path or DEFAULT_DATA_DIR / "metrics.sqlite3"
        self.retention = retention
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_snapshot(self, snapshot: MetricsSnapshot) -> None:
        captured_epoch = time.time()
        with closing(self._connect()) as connection:
            sample_id = self._insert_sample(connection, snapshot, captured_epoch)
            self._insert_disk_usage(connection, sample_id, snapshot)
            self._insert_processes(connection, sample_id, "cpu", snapshot.top_cpu)
            self._insert_processes(connection, sample_id, "memory", snapshot.top_memory)
            self.prune(connection, captured_epoch)
            connection.commit()

    def save_config_metadata(
        self,
        *,
        config_path: Path,
        database_path: Path,
        live_refresh_seconds: float,
        collection_seconds: float,
        retention_days: int,
        thresholds: dict[str, Threshold],
        disks: list[str],
        interfaces: list[str],
    ) -> None:
        payload = {
            "config_path": str(config_path),
            "database_path": str(database_path),
            "live_refresh_seconds": live_refresh_seconds,
            "collection_seconds": collection_seconds,
            "retention_days": retention_days,
            "thresholds": {
                key: {"warning": value.warning, "critical": value.critical}
                for key, value in thresholds.items()
            },
            "disks": disks,
            "interfaces": interfaces,
        }
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO srvmon_config (id, updated_at, config_json)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    config_json = excluded.config_json
                """,
                (time.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(payload, sort_keys=True)),
            )
            connection.commit()

    def prune(self, connection: sqlite3.Connection | None = None, now_epoch: float | None = None) -> int:
        owns_connection = connection is None
        active_connection = connection or self._connect()
        cutoff = (now_epoch or time.time()) - self.retention.total_seconds()
        try:
            before = active_connection.execute(
                "SELECT COUNT(*) FROM metric_samples WHERE captured_epoch < ?",
                (cutoff,),
            ).fetchone()[0]
            active_connection.execute("DELETE FROM metric_samples WHERE captured_epoch < ?", (cutoff,))
            active_connection.commit()
            return int(before)
        finally:
            if owns_connection:
                active_connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metric_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    captured_epoch REAL NOT NULL,
                    load_1 REAL,
                    load_5 REAL,
                    load_15 REAL,
                    cpu_logical_cores INTEGER,
                    cpu_utilization_percent REAL,
                    ram_used_percent REAL,
                    ram_available_bytes INTEGER,
                    swap_usage_percent REAL,
                    disk_read_bps REAL,
                    disk_write_bps REAL,
                    network_in_mbps REAL,
                    network_out_mbps REAL,
                    network_errors_in INTEGER,
                    network_errors_out INTEGER,
                    network_drops_in INTEGER,
                    network_drops_out INTEGER
                );

                CREATE TABLE IF NOT EXISTS disk_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_id INTEGER NOT NULL REFERENCES metric_samples(id) ON DELETE CASCADE,
                    device TEXT NOT NULL,
                    mountpoint TEXT NOT NULL,
                    fstype TEXT NOT NULL,
                    total_bytes INTEGER NOT NULL,
                    used_bytes INTEGER NOT NULL,
                    free_bytes INTEGER NOT NULL,
                    used_percent REAL NOT NULL,
                    inodes_used_percent REAL
                );

                CREATE TABLE IF NOT EXISTS top_processes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_id INTEGER NOT NULL REFERENCES metric_samples(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK (kind IN ('cpu', 'memory')),
                    rank INTEGER NOT NULL,
                    pid INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    username TEXT NOT NULL,
                    cpu_percent REAL NOT NULL,
                    memory_percent REAL NOT NULL,
                    rss_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS srvmon_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    updated_at TEXT NOT NULL,
                    config_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_metric_samples_epoch
                    ON metric_samples(captured_epoch);
                CREATE INDEX IF NOT EXISTS idx_disk_usage_sample
                    ON disk_usage(sample_id);
                CREATE INDEX IF NOT EXISTS idx_top_processes_sample_kind
                    ON top_processes(sample_id, kind);
                """
            )
            self._migrate(connection)
            connection.commit()

    def _migrate(self, connection: sqlite3.Connection) -> None:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(metric_samples)").fetchall()
        }
        if "cpu_logical_cores" not in columns:
            connection.execute("ALTER TABLE metric_samples ADD COLUMN cpu_logical_cores INTEGER")

    def _insert_sample(self, connection: sqlite3.Connection, snapshot: MetricsSnapshot, captured_epoch: float) -> int:
        cpu = _metric_map(snapshot.cpu)
        memory = _metric_map(snapshot.memory)
        network = _metric_map(snapshot.network)
        values = {
            "load_1": _as_float(cpu.get("Load average 1m")),
            "load_5": _as_float(cpu.get("Load average 5m")),
            "load_15": _as_float(cpu.get("Load average 15m")),
            "cpu_logical_cores": _as_int(cpu.get("Logical cores")),
            "cpu_utilization_percent": _as_float(cpu.get("CPU utilization")),
            "ram_used_percent": _as_float(memory.get("RAM utilization")),
            "ram_available_bytes": _as_int(memory.get("RAM available")),
            "swap_usage_percent": _as_float(memory.get("Swap utilization")),
            "disk_read_bps": snapshot.disk_io.read_bps,
            "disk_write_bps": snapshot.disk_io.write_bps,
            "network_in_mbps": snapshot.network_rate.bytes_recv_per_sec / 1024 / 1024,
            "network_out_mbps": snapshot.network_rate.bytes_sent_per_sec / 1024 / 1024,
            "network_errors_in": _as_int(network.get("Errors in")),
            "network_errors_out": _as_int(network.get("Errors out")),
            "network_drops_in": _as_int(network.get("Drops in")),
            "network_drops_out": _as_int(network.get("Drops out")),
        }
        cursor = connection.execute(
            """
            INSERT INTO metric_samples (
                captured_at, captured_epoch, load_1, load_5, load_15, cpu_logical_cores,
                cpu_utilization_percent, ram_used_percent, ram_available_bytes,
                swap_usage_percent, disk_read_bps, disk_write_bps, network_in_mbps,
                network_out_mbps, network_errors_in, network_errors_out,
                network_drops_in, network_drops_out
            ) VALUES (
                :captured_at, :captured_epoch, :load_1, :load_5, :load_15, :cpu_logical_cores,
                :cpu_utilization_percent, :ram_used_percent, :ram_available_bytes,
                :swap_usage_percent, :disk_read_bps, :disk_write_bps, :network_in_mbps,
                :network_out_mbps, :network_errors_in, :network_errors_out,
                :network_drops_in, :network_drops_out
            )
            """,
            {"captured_at": snapshot.captured_at, "captured_epoch": captured_epoch, **values},
        )
        return int(cursor.lastrowid)

    def _insert_disk_usage(self, connection: sqlite3.Connection, sample_id: int, snapshot: MetricsSnapshot) -> None:
        connection.executemany(
            """
            INSERT INTO disk_usage (
                sample_id, device, mountpoint, fstype, total_bytes, used_bytes,
                free_bytes, used_percent, inodes_used_percent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    sample_id,
                    partition.device,
                    partition.mountpoint,
                    partition.fstype,
                    partition.total,
                    partition.used,
                    partition.free,
                    partition.percent,
                    partition.inodes_used_percent,
                )
                for partition in snapshot.partitions
            ],
        )

    def _insert_processes(
        self,
        connection: sqlite3.Connection,
        sample_id: int,
        kind: str,
        processes: list[ProcessInfo],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO top_processes (
                sample_id, kind, rank, pid, name, username, cpu_percent,
                memory_percent, rss_bytes, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    sample_id,
                    kind,
                    rank,
                    process.pid,
                    process.name,
                    process.username,
                    process.cpu_percent,
                    process.memory_percent,
                    process.rss,
                    process.status,
                )
                for rank, process in enumerate(processes[:10], start=1)
            ],
        )


def _metric_map(rows: list[Any]) -> dict[str, MetricValue]:
    return {row.name: row.value for row in rows}


def _as_float(value: MetricValue) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_int(value: MetricValue) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def data_dir_status() -> str:
    return str(DEFAULT_DATA_DIR)
