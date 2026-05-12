from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback.
    import tomli as tomllib  # type: ignore[no-redef]

from srvmon.health import THRESHOLDS, Threshold
from srvmon.storage import DEFAULT_DATA_DIR


DEFAULT_CONFIG_DIR = Path.home() / ".srvmon"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "metrics.sqlite3"

DEFAULT_CONFIG_TEXT = """# srvmon configuration

[intervals]
# How often srvmon live refreshes the screen when no CLI interval is provided.
live_refresh_seconds = 2.0
# How often background/collector-style code should collect metrics.
collection_seconds = 2.0

[storage]
database_path = "~/.srvmon/data/metrics.sqlite3"
retention_days = 62

[thresholds.load_ratio]
warning = 70
critical = 100

[thresholds.cpu]
warning = 70
critical = 90

[thresholds.ram]
warning = 75
critical = 90

[thresholds.swap]
warning = 20
critical = 50

[thresholds.disk]
warning = 80
critical = 90

[thresholds.disk_latency]
warning = 20
critical = 50

[thresholds.network_errors]
warning = 1
critical = 10

[thresholds.process_cpu]
warning = 70
critical = 90

[thresholds.process_ram]
warning = 10
critical = 25

[monitoring]
# Empty lists mean: monitor everything visible to the current user.
disks = []
interfaces = []
"""


@dataclass(slots=True)
class SrvmonConfig:
    config_path: Path = DEFAULT_CONFIG_PATH
    live_refresh_seconds: float = 2.0
    collection_seconds: float = 2.0
    database_path: Path = DEFAULT_DB_PATH
    retention_days: int = 62
    thresholds: dict[str, Threshold] = field(default_factory=lambda: dict(THRESHOLDS))
    disks: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)


def load_config(path: Path | None = None) -> SrvmonConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    if not ensure_config_file(config_path):
        return SrvmonConfig(config_path=config_path)
    data = _read_toml(config_path)
    return _config_from_mapping(config_path, data)


def ensure_config_file(path: Path = DEFAULT_CONFIG_PATH) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    except OSError:
        return False
    return True


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _config_from_mapping(path: Path, data: dict[str, Any]) -> SrvmonConfig:
    intervals = _section(data, "intervals")
    storage = _section(data, "storage")
    monitoring = _section(data, "monitoring")
    thresholds_data = _section(data, "thresholds")

    config = SrvmonConfig(
        config_path=path,
        live_refresh_seconds=_float(intervals.get("live_refresh_seconds"), 2.0),
        collection_seconds=_float(intervals.get("collection_seconds"), 2.0),
        database_path=_path(storage.get("database_path"), DEFAULT_DB_PATH),
        retention_days=int(_float(storage.get("retention_days"), 62)),
        disks=_string_list(monitoring.get("disks")),
        interfaces=_string_list(monitoring.get("interfaces")),
    )
    config.thresholds = _thresholds(thresholds_data)
    return config


def _thresholds(data: dict[str, Any]) -> dict[str, Threshold]:
    configured = dict(THRESHOLDS)
    for key, default in THRESHOLDS.items():
        section = data.get(key)
        if not isinstance(section, dict):
            continue
        warning = _optional_float(section.get("warning"), default.warning)
        critical = _optional_float(section.get("critical"), default.critical)
        configured[key] = Threshold(warning, critical, default.unit, default.description)
    return configured


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def _float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _optional_float(value: object, default: float | None) -> float | None:
    if value is None:
        return default
    return _float(value, default or 0.0)


def _path(value: object, default: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        return default
    return Path(value).expanduser()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]
