from __future__ import annotations

import argparse
import csv
import io
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import sqlite3
import subprocess
import sys
import time
import webbrowser
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from srvmon.config import SrvmonConfig
from srvmon.exporter import load_export_data, render_html_export
from srvmon.health import health_color, load_ratio
from srvmon.periods import REPORT_PERIODS
from srvmon.storage import DEFAULT_DATA_DIR, MetricStorage


DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8765


@dataclass(slots=True)
class WebDashboardProcess:
    process: subprocess.Popen[object]
    url: str


def start_web_dashboard(
    config: SrvmonConfig,
    *,
    host: str = DEFAULT_WEB_HOST,
    port: int = DEFAULT_WEB_PORT,
    open_browser: bool = True,
) -> WebDashboardProcess:
    selected_port = _available_port(host, port)
    command = [
        sys.executable,
        "-m",
        "srvmon.web",
        "--host",
        host,
        "--port",
        str(selected_port),
        "--db-path",
        str(config.database_path),
    ]
    if open_browser:
        command.append("--open-browser")
    log_path = Path.home() / ".srvmon" / "srvmon-web.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT)
    log_file.close()
    time.sleep(0.4)
    if process.poll() is not None:
        raise OSError(f"web dashboard exited early; see {log_path}")
    return WebDashboardProcess(process, f"http://{host}:{selected_port}")


def create_app(db_path: Path | None = None) -> object:
    from fastapi import FastAPI, Query
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    database_path = db_path or DEFAULT_DATA_DIR / "metrics.sqlite3"
    MetricStorage(database_path)
    app = FastAPI(title="srvmon dashboard")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _dashboard_html()

    @app.get("/api/overview")
    def overview(period: str = Query("-1d")) -> JSONResponse:
        return JSONResponse(_overview_payload(database_path, period))

    @app.get("/export")
    def export(period: str = Query("-1d"), format: str = Query("json")) -> Response:
        if period not in REPORT_PERIODS:
            return Response("Unsupported period", status_code=400)
        if format not in {"json", "csv", "html"}:
            return Response("Unsupported format", status_code=400)
        data = load_export_data(database_path, REPORT_PERIODS[period], None)
        if format == "json":
            return Response(
                json.dumps(data, indent=2, ensure_ascii=False),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="srvmon-{period}.json"'},
            )
        if format == "csv":
            text = _csv_text(data)
            return Response(
                text,
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="srvmon-{period}.csv"'},
            )
        return Response(
            _export_html(data, period),
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="srvmon-{period}.html"'},
        )

    return app


def run_server(host: str, port: int, db_path: Path, open_browser: bool) -> None:
    if open_browser:
        webbrowser.open(f"http://{host}:{port}", new=2)
    try:
        import uvicorn
    except ModuleNotFoundError:
        run_stdlib_server(host, port, db_path)
        return
    uvicorn.run(create_app(db_path), host=host, port=port, log_level="warning")


def run_stdlib_server(host: str, port: int, db_path: Path) -> None:
    MetricStorage(db_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if parsed.path == "/":
                self._send_text(_dashboard_html(), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/overview":
                period = params.get("period", ["-1d"])[0]
                payload = json.dumps(_overview_payload(db_path, period), ensure_ascii=False).encode("utf-8")
                self._send_bytes(payload, "application/json; charset=utf-8")
                return
            if parsed.path == "/export":
                period = params.get("period", ["-1d"])[0]
                output_format = params.get("format", ["json"])[0]
                response = _stdlib_export_response(db_path, period, output_format)
                self._send_bytes(response[0], response[1], response[2])
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_text(self, text: str, content_type: str) -> None:
            self._send_bytes(text.encode("utf-8"), content_type)

        def _send_bytes(self, payload: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            if headers:
                for key, value in headers.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def _stdlib_export_response(db_path: Path, period: str, output_format: str) -> tuple[bytes, str, dict[str, str]]:
    if period not in REPORT_PERIODS:
        return b"Unsupported period", "text/plain; charset=utf-8", {}
    data = load_export_data(db_path, REPORT_PERIODS[period], None)
    headers = {"Content-Disposition": f'attachment; filename="srvmon-{period}.{output_format}"'}
    if output_format == "json":
        return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", headers
    if output_format == "csv":
        return _csv_text(data).encode("utf-8"), "text/csv; charset=utf-8", headers
    if output_format == "html":
        return _export_html(data, period).encode("utf-8"), "text/html; charset=utf-8", headers
    return b"Unsupported format", "text/plain; charset=utf-8", {}


def main() -> None:
    parser = argparse.ArgumentParser(description="srvmon local web dashboard")
    parser.add_argument("--host", default=DEFAULT_WEB_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DATA_DIR / "metrics.sqlite3")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    run_server(args.host, args.port, args.db_path, args.open_browser)


def _overview_payload(db_path: Path, period: str) -> dict[str, object]:
    selected_period = REPORT_PERIODS.get(period, REPORT_PERIODS["-1d"])
    cutoff = time.time() - selected_period.total_seconds()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        samples = [
            dict(row)
            for row in connection.execute(
                """
                SELECT captured_at, captured_epoch, load_1, cpu_utilization_percent,
                       ram_used_percent, network_in_mbps, network_out_mbps,
                       disk_read_bps, disk_write_bps
                FROM metric_samples
                WHERE captured_epoch >= ?
                ORDER BY captured_epoch
                LIMIT 1000
                """,
                (cutoff,),
            )
        ]
        latest_sample = connection.execute(
            "SELECT * FROM metric_samples WHERE captured_epoch >= ? ORDER BY captured_epoch DESC LIMIT 1",
            (cutoff,),
        ).fetchone()
        disks = []
        processes = []
        alerts = []
        if latest_sample is not None:
            latest_id = latest_sample["id"]
            disks = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT device, mountpoint, fstype, total_bytes, used_bytes,
                           free_bytes, used_percent
                    FROM disk_usage
                    WHERE sample_id = ?
                    ORDER BY used_percent DESC
                    """,
                    (latest_id,),
                )
            ]
            alerts = _current_alerts(dict(latest_sample), disks)
            processes = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT kind, rank, pid, name, cpu_percent, memory_percent, rss_bytes, status
                    FROM top_processes
                    WHERE sample_id = ?
                    ORDER BY kind, rank
                    """,
                    (latest_id,),
                )
            ]
    return {"period": period, "samples": samples, "disks": disks, "processes": processes, "alerts": alerts}


def _current_alerts(sample: dict[str, object], disks: list[dict[str, object]]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    _append_alert(alerts, "CPU", _float(sample.get("cpu_utilization_percent")), "cpu", "%")
    _append_alert(alerts, "RAM", _float(sample.get("ram_used_percent")), "ram", "%")
    _append_alert(alerts, "Swap", _float(sample.get("swap_usage_percent")), "swap", "%")
    _append_alert(
        alerts,
        "Load 1m",
        load_ratio(_float(sample.get("load_1")), _float(sample.get("cpu_logical_cores"))),
        "load_ratio",
        "% of logical CPUs",
    )
    _append_alert(
        alerts,
        "Disk",
        max((_float(row.get("used_percent")) or 0.0 for row in disks), default=None),
        "disk",
        "%",
    )
    network_errors = sum(
        _float(sample.get(column)) or 0.0
        for column in ("network_errors_in", "network_errors_out", "network_drops_in", "network_drops_out")
    )
    _append_alert(alerts, "Network errors/drops", network_errors, "network_errors", "count")
    return alerts


def _append_alert(
    alerts: list[dict[str, str]],
    metric: str,
    value: float | None,
    threshold_key: str,
    unit: str,
) -> None:
    color = health_color(value, threshold_key)
    if color == "green":
        return
    alerts.append(
        {
            "metric": metric,
            "state": "CRITICAL" if color == "red" else "WARNING",
            "color": color,
            "value": "n/a" if value is None else f"{value:.1f} {unit}".strip(),
        }
    )


def _float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _csv_text(data: dict[str, object]) -> str:
    samples = list(data["samples"])  # type: ignore[arg-type]
    if not samples:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(samples[0].keys()))
    writer.writeheader()
    writer.writerows(samples)
    return buffer.getvalue()


def _export_html(data: dict[str, object], period: str) -> str:
    return render_html_export(data, title=f"srvmon export {period}")


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>srvmon dashboard</title>
  <style>
    :root { color-scheme: light; --bg:#f7f9fc; --panel:#ffffff; --line:#dbe4ee; --soft:#eef4fb; --text:#1f2937; --muted:#64748b; --green:#168a55; --yellow:#b7791f; --red:#d64545; --blue:#2563eb; --shadow:0 10px 28px rgba(30, 41, 59, .08); }
    body { margin:0; font-family: Inter, Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--text); }
    header { padding:18px 24px; border-bottom:1px solid var(--line); display:flex; align-items:center; justify-content:space-between; gap:16px; background:rgba(255,255,255,.92); position:sticky; top:0; z-index:2; backdrop-filter: blur(10px); }
    h1 { font-size:20px; margin:0; color:#111827; letter-spacing:0; }
    main { padding:20px 24px 32px; display:grid; gap:18px; }
    .toolbar { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    label { color:var(--muted); font-size:13px; }
    select, button, a.button { background:#fff; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:8px 10px; text-decoration:none; box-shadow:0 1px 2px rgba(15,23,42,.04); }
    button, a.button { cursor:pointer; }
    button:hover, a.button:hover, select:hover { border-color:var(--blue); background:#f8fbff; }
    .grid { display:grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap:16px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; min-width:0; box-shadow:var(--shadow); }
    .panel h2 { margin:0 0 12px; font-size:15px; color:#111827; font-weight:600; }
    canvas { width:100%; height:180px; display:block; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { padding:7px 8px; border-bottom:1px solid var(--line); text-align:right; }
    th:first-child, td:first-child { text-align:left; }
    th { color:#475569; font-weight:600; background:var(--soft); }
    tr:last-child td { border-bottom:0; }
    .muted { color:var(--muted); }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <div><h1>srvmon dashboard</h1><div class="muted" id="summary">local metrics</div></div>
    <div class="toolbar">
      <label>Period <select id="period"><option>-1h</option><option selected>-1d</option><option>-1w</option><option>-1m</option><option>-2m</option></select></label>
      <button id="refresh">Refresh</button>
      <a class="button" id="json">Export JSON</a>
      <a class="button" id="csv">Export CSV</a>
      <a class="button" id="html">Export HTML</a>
    </div>
  </header>
  <main>
    <section class="grid">
      <div class="panel"><h2>CPU %</h2><canvas id="cpu"></canvas></div>
      <div class="panel"><h2>RAM %</h2><canvas id="ram"></canvas></div>
      <div class="panel"><h2>Network MB/s</h2><canvas id="net"></canvas></div>
    </section>
    <section class="grid">
      <div class="panel"><h2>Disk I/O</h2><canvas id="diskio"></canvas></div>
      <div class="panel"><h2>Disk State</h2><table id="disks"></table></div>
      <div class="panel"><h2>Top Processes</h2><table id="processes"></table></div>
    </section>
    <section class="panel"><h2>Active Alerts</h2><table id="alerts"></table></section>
  </main>
<script>
const $ = (id) => document.getElementById(id);
function period(){ return $('period').value; }
function setLinks(){
  for (const fmt of ['json','csv','html']) $(''+fmt).href = `/export?format=${fmt}&period=${encodeURIComponent(period())}`;
}
function drawLine(canvas, series, color, maxHint){
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr; ctx.scale(dpr,dpr);
  ctx.clearRect(0,0,rect.width,rect.height);
  ctx.strokeStyle = '#dbe4ee'; ctx.lineWidth = 1;
  for(let i=0;i<4;i++){ const y=20+i*(rect.height-34)/3; ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(rect.width,y); ctx.stroke(); }
  const values = series.filter(v => Number.isFinite(v));
  if(values.length < 2){ ctx.fillStyle='#95a3b3'; ctx.fillText('waiting for data', 12, 24); return; }
  const max = Math.max(maxHint || 0, ...values, 1);
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
  values.forEach((v,i)=>{ const x=i*(rect.width-8)/(values.length-1)+4; const y=rect.height-10-(v/max)*(rect.height-26); if(i===0)ctx.moveTo(x,y); else ctx.lineTo(x,y); });
  ctx.stroke();
  ctx.fillStyle='#1f2937'; ctx.fillText(values[values.length-1].toFixed(2), 10, 16);
}
function table(el, headers, rows){
  el.innerHTML = `<thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>`;
}
async function load(){
  setLinks();
  const res = await fetch(`/api/overview?period=${encodeURIComponent(period())}`);
  const data = await res.json();
  const samples = data.samples || [];
  $('summary').textContent = `${samples.length} samples | ${samples.at(-1)?.captured_at || 'no data'}`;
  drawLine($('cpu'), samples.map(s=>s.cpu_utilization_percent), '#62b6ff', 100);
  drawLine($('ram'), samples.map(s=>s.ram_used_percent), '#48d597', 100);
  drawLine($('net'), samples.map(s=>Math.max(s.network_in_mbps||0, s.network_out_mbps||0)), '#ffd166');
  drawLine($('diskio'), samples.map(s=>Math.max((s.disk_read_bps||0)/1024/1024, (s.disk_write_bps||0)/1024/1024)), '#ff6b6b');
  table($('disks'), ['Mount','Used','Free','FS'], (data.disks||[]).map(d=>[d.mountpoint, `${Number(d.used_percent).toFixed(1)}%`, `${(d.free_bytes/1024/1024/1024).toFixed(1)} GiB`, d.fstype]));
  table($('processes'), ['Kind','PID','Name','CPU','RAM'], (data.processes||[]).slice(0,12).map(p=>[p.kind, p.pid, p.name, `${Number(p.cpu_percent).toFixed(1)}%`, `${Number(p.memory_percent).toFixed(1)}%`]));
  const alerts = data.alerts || [];
  table($('alerts'), ['State','Metric','Value'], alerts.length ? alerts.map(a=>[`<span style="color:var(--${a.color})">${a.state}</span>`, a.metric, a.value]) : [['OK','No active warnings','thresholds are within normal range']]);
}
$('period').addEventListener('change', load); $('refresh').addEventListener('click', load); setInterval(load, 5000); load();
</script>
</body>
</html>"""


def _available_port(host: str, preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    return preferred_port


if __name__ == "__main__":
    main()
