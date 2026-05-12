from __future__ import annotations

import csv
import html
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import TextIO

from srvmon.periods import REPORT_PERIODS
from srvmon.storage import DEFAULT_DATA_DIR, MetricStorage


EXPORT_FORMATS = {"json", "csv", "html"}
DEFAULT_EXPORT_DIR = Path.home() / ".srvmon" / "reports"


def export_metrics(
    *,
    db_path: Path | None,
    period: timedelta,
    output_format: str,
    limit: int | None = None,
    output_path: Path | None = None,
    period_label: str = "-1d",
) -> Path:
    if output_format not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format {output_format!r}. Use: json, csv, html.")

    path = db_path or DEFAULT_DATA_DIR / "metrics.sqlite3"
    MetricStorage(path)
    data = load_export_data(path, period, limit)
    auto_output = output_path is None
    if output_path is None:
        output_path = default_export_path(output_format, period_label)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as file:
            _write_export(file, output_format, data)
    except OSError:
        if not auto_output:
            raise
        output_path = fallback_export_path(output_format, period_label)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as file:
            _write_export(file, output_format, data)
    return output_path


def default_export_path(output_format: str, period_label: str) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_period = period_label.replace("-", "last-")
    filename = f"srvmon-{safe_period}-{timestamp}.{output_format}"
    try:
        DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_EXPORT_DIR / filename
    except OSError:
        return fallback_export_path(output_format, period_label)


def fallback_export_path(output_format: str, period_label: str) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_period = period_label.replace("-", "last-")
    filename = f"srvmon-{safe_period}-{timestamp}.{output_format}"
    return Path.cwd() / ".srvmon" / "reports" / filename


def load_export_data(db_path: Path, period: timedelta, limit: int | None = None) -> dict[str, object]:
    cutoff = time.time() - period.total_seconds()
    limit_clause = "" if limit is None else "LIMIT ?"
    params: tuple[object, ...] = (cutoff,) if limit is None else (cutoff, limit)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        samples = [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM metric_samples WHERE captured_epoch >= ? ORDER BY captured_epoch {limit_clause}",
                params,
            )
        ]
        sample_ids = [sample["id"] for sample in samples]
        if not sample_ids:
            return {"samples": [], "disk_usage": [], "top_processes": []}
        placeholders = ",".join("?" for _ in sample_ids)
        disk_usage = [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM disk_usage WHERE sample_id IN ({placeholders}) ORDER BY sample_id, mountpoint",
                sample_ids,
            )
        ]
        top_processes = [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM top_processes WHERE sample_id IN ({placeholders}) ORDER BY sample_id, kind, rank",
                sample_ids,
            )
        ]
    return {"samples": samples, "disk_usage": disk_usage, "top_processes": top_processes}


def _write_export(file: TextIO, output_format: str, data: dict[str, object]) -> None:
    if output_format == "json":
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")
    elif output_format == "csv":
        _write_csv(file, data)
    else:
        file.write(render_html_export(data))


def _write_csv(file: TextIO, data: dict[str, object]) -> None:
    samples = list(data["samples"])  # type: ignore[arg-type]
    if not samples:
        file.write("")
        return
    writer = csv.DictWriter(file, fieldnames=list(samples[0].keys()))
    writer.writeheader()
    writer.writerows(samples)


def render_html_export(data: dict[str, object], title: str = "srvmon export") -> str:
    samples = list(data["samples"])  # type: ignore[arg-type]
    disks = list(data["disk_usage"])  # type: ignore[arg-type]
    processes = list(data["top_processes"])  # type: ignore[arg-type]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Inter, Segoe UI, Arial, sans-serif; margin: 32px; color: #17202a; }}
    table {{ border-collapse: collapse; width: 100%; margin: 18px 0 32px; font-size: 13px; }}
    th, td {{ border: 1px solid #d8dee9; padding: 6px 8px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: #eef3f8; }}
    h1, h2 {{ margin-bottom: 8px; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>Samples: {len(samples)} | Disk rows: {len(disks)} | Process rows: {len(processes)}</p>
  <h2>Metric samples</h2>
  {_html_table(samples[:200])}
  <h2>Disk usage</h2>
  {_html_table(disks[:200])}
  <h2>Top processes</h2>
  {_html_table(processes[:200])}
</body>
</html>
"""


def _html_table(rows: list[object]) -> str:
    if not rows:
        return "<p>No data.</p>"
    dict_rows = [dict(row) for row in rows]  # type: ignore[arg-type]
    columns = list(dict_rows[0].keys())
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row.get(column, '')))}</td>" for column in columns) + "</tr>"
        for row in dict_rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def period_from_label(label: str | None) -> timedelta:
    return REPORT_PERIODS[label or "-1d"]
