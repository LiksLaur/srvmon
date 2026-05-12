from __future__ import annotations

from datetime import timedelta


def format_bytes(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(size) < 1024 or unit == "PiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PiB"


def format_rate(value: float | int | None, suffix: str) -> str:
    if value is None:
        return "n/a"
    return f"{format_bytes(value)}/{suffix}"


def format_percent(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}%"


def format_timedelta(value: timedelta | None) -> str:
    if value is None:
        return "n/a"
    total_seconds = int(value.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def short_text(value: object, max_len: int = 40) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 1]}..."
