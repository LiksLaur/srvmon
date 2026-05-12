from __future__ import annotations

from datetime import timedelta


REPORT_PERIODS = {
    "-1h": timedelta(hours=1),
    "-1d": timedelta(days=1),
    "-1w": timedelta(weeks=1),
    "-1m": timedelta(days=31),
    "-2m": timedelta(days=62),
}


def parse_report_period(token: str | None) -> tuple[str, timedelta]:
    if token is None:
        token = "-1d"
    if token not in REPORT_PERIODS:
        allowed = ", ".join(REPORT_PERIODS)
        raise ValueError(f"Unsupported report period {token!r}. Use one of: {allowed}.")
    return token, REPORT_PERIODS[token]
