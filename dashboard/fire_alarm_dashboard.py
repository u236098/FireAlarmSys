#!/usr/bin/env python3
import argparse
import json
import threading
import csv
import time
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

try:
    import serial
except ImportError as exc:
    raise SystemExit("Install pyserial first: python3 -m pip install pyserial") from exc


latest = {
    "raw": None,
    "json": None,
    "lines": [],
    "history": [],
    "port_warning": None,
    "command_status": None,
    "last_command": None,
}

# CSV logging setup
csv_lock = threading.Lock()
csv_file = None
csv_writer = None
history_lock = threading.Lock()
serial_conn = None
serial_lock = threading.Lock()
# Keep a long dashboard timeline for the current run.
# With a 2 second sensing cycle, 10800 samples is about 6 hours.
HISTORY_LIMIT = 10800
demo_mode = False
demo_buzzer_manual = False
demo_buzzer_state = False

VALID_BUZZER_COMMANDS = {
    "on": "BUZZER_ON",
    "off": "BUZZER_OFF",
    "auto": "BUZZER_AUTO",
}


def record_sensor_data(data, payload):
    latest["json"] = data
    latest["raw"] = payload

    sample = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "id": data.get("id"),
        "temp": data.get("temp"),
        "hum": data.get("hum"),
        "gas": data.get("gas"),
        "alert": data.get("alert", 0),
        "buzzer": data.get("buzzer", 0),
        "buzzer_manual": data.get("buzzer_manual", 0),
    }
    with history_lock:
        latest["history"] = (latest["history"] + [sample])[-HISTORY_LIMIT:]

    if not csv_writer:
        return

    alert_cause = "none"
    if data.get("temp_alert") == 1 and data.get("gas_alert") == 1:
        alert_cause = "temperature+gas"
    elif data.get("temp_alert") == 1:
        alert_cause = "temperature"
    elif data.get("gas_alert") == 1:
        alert_cause = "gas"

    row = {
        "timestamp": datetime.now().isoformat(),
        "packet_id": data.get("id", ""),
        "temperature": data.get("temp") if data.get("temp") is not None else "N/A",
        "humidity": data.get("hum") if data.get("hum") is not None else "N/A",
        "gas": data.get("gas", ""),
        "alert": data.get("alert", 0),
        "temp_alert": data.get("temp_alert", 0),
        "gas_alert": data.get("gas_alert", 0),
        "buzzer": data.get("buzzer", 0),
        "buzzer_manual": data.get("buzzer_manual", 0),
        "alert_cause": alert_cause
    }
    with csv_lock:
        csv_writer.writerow(row)
        csv_file.flush()


def serial_reader(port, baud):
    global csv_writer, serial_conn
    try:
        with serial.Serial(port, baud, timeout=1) as ser:
            with serial_lock:
                serial_conn = ser
            while True:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                latest["lines"] = (latest["lines"] + [line])[-30:]

                marker = "DASHBOARD_JSON:"
                if marker in line:
                    latest["port_warning"] = None
                    payload = line.split(marker, 1)[1].strip()
                    latest["raw"] = payload
                    try:
                        # Replace ? with null for valid JSON parsing
                        json_payload = payload.replace('"temp":?', '"temp":null')
                        json_payload = json_payload.replace('"hum":?', '"hum":null')
                        data = json.loads(json_payload)
                        record_sensor_data(data, payload)
                    except (json.JSONDecodeError, ValueError):
                        latest["json"] = None
                elif "[INFO: uart-client]" in line or "Arduino line:" in line or "ACK from root" in line:
                    latest["port_warning"] = (
                        "This looks like the sender Zolertia serial log. "
                        "Use the receiver/root Zolertia port, which prints DASHBOARD_JSON lines."
                    )
    finally:
        with serial_lock:
            serial_conn = None


def demo_reader():
    count = 0
    while True:
        count += 1
        temp = round(24.0 + (count % 18) * 0.35, 1)
        hum = 42 + (count % 12)
        gas = 150 + (count * 9) % 220
        temp_alert = 1 if temp > 30 else 0
        gas_alert = 1 if gas > 330 else 0
        alert = 1 if temp_alert or gas_alert else 0
        with serial_lock:
            buzzer_manual = demo_buzzer_manual
            buzzer = demo_buzzer_state if demo_buzzer_manual else alert
        buzzer = 1 if buzzer else 0
        data = {
            "id": count,
            "temp": temp,
            "hum": hum,
            "gas": gas,
            "alert": alert,
            "temp_alert": temp_alert,
            "gas_alert": gas_alert,
            "buzzer": buzzer,
            "buzzer_manual": 1 if buzzer_manual else 0,
        }
        payload = json.dumps(data, separators=(",", ":"))
        line = f"DASHBOARD_JSON:{payload}"
        latest["port_warning"] = None
        latest["lines"] = (latest["lines"] + [line])[-30:]
        record_sensor_data(data, payload)
        time.sleep(2)


def send_dashboard_command(command):
    global demo_buzzer_manual, demo_buzzer_state
    if demo_mode:
        with serial_lock:
            if command == "BUZZER_ON":
                demo_buzzer_manual = True
                demo_buzzer_state = True
            elif command == "BUZZER_OFF":
                demo_buzzer_manual = True
                demo_buzzer_state = False
            elif command == "BUZZER_AUTO":
                demo_buzzer_manual = False

        latest["last_command"] = command
        latest["command_status"] = f"Demo mode accepted {command}."
        return True, latest["command_status"]

    with serial_lock:
        if serial_conn is None or not serial_conn.is_open:
            latest["command_status"] = "Serial port is not ready."
            return False, latest["command_status"]

        line = f"DASHBOARD_CMD:{command}\n"
        serial_conn.write(line.encode("ascii"))
        serial_conn.flush()

    latest["last_command"] = command
    latest["command_status"] = f"Sent {command} to receiver/root Zolertia."
    return True, latest["command_status"]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/latest":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(latest).encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE.encode("utf-8"))

    def do_POST(self):
        if self.path != "/api/buzzer":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        content_type = self.headers.get("Content-Type", "")
        state = None

        if raw_body:
            try:
                if "application/json" in content_type:
                    state = json.loads(raw_body).get("state")
                else:
                    state = parse_qs(raw_body).get("state", [None])[0]
            except (json.JSONDecodeError, ValueError):
                state = None

        command = VALID_BUZZER_COMMANDS.get(str(state or "").lower())
        if command is None:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Use state on, off, or auto."}).encode("utf-8"))
            return

        ok, message = send_dashboard_command(command)
        self.send_response(200 if ok else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": ok, "message": message, "command": command}).encode("utf-8"))

    def log_message(self, fmt, *args):
        return


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fire Alarm Dashboard</title>
  <style>
    body { margin: 0; font: 16px system-ui, sans-serif; background: #0f1419; color: #e6edf3; }
    main { max-width: 1200px; margin: 0 auto; padding: 32px 20px; }
    h1 { font-size: 32px; margin: 0 0 8px; color: #f85149; }
    .subtitle { color: #7d8590; margin-bottom: 24px; }
    .warning { display: none; margin: 0 0 16px; padding: 12px 16px; border: 1px solid #d29922; border-radius: 8px; background: #332a12; color: #ffdf8b; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 16px; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; transition: all 0.3s; }
    .card.alert { background: #3d1f1f; border-color: #f85149; animation: pulse 2s infinite; }
    @keyframes pulse {
      0%, 100% { box-shadow: 0 0 0 0 rgba(248, 81, 73, 0.4); }
      50% { box-shadow: 0 0 20px 5px rgba(248, 81, 73, 0.2); }
    }
    .label { color: #7d8590; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }
    .value { font-size: 36px; font-weight: 700; margin-top: 8px; }
    .status-badge {
      display: inline-block;
      padding: 6px 16px;
      border-radius: 20px;
      font-size: 14px;
      font-weight: 600;
      margin-top: 12px;
    }
    .status-normal { background: #1f6f3c; color: #2ea043; }
    .status-alert { background: #78191b; color: #ff7b72; animation: blink 1s infinite; }
    .controls { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
    button { border: 1px solid #30363d; border-radius: 6px; background: #21262d; color: #e6edf3; padding: 10px 14px; font: inherit; cursor: pointer; }
    button:hover { background: #30363d; }
    button.on { border-color: #2ea043; }
    button.off { border-color: #f85149; }
    button.auto { border-color: #58a6ff; }
    .command-status { margin-top: 12px; min-height: 20px; color: #7d8590; font-size: 14px; }
    .charts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }
    .history-panel { margin-top: 16px; }
    .history-controls { display: grid; grid-template-columns: 180px 1fr 120px; gap: 12px; align-items: center; margin-top: 12px; }
    select, input[type="range"] { width: 100%; }
    select { border: 1px solid #30363d; border-radius: 6px; background: #21262d; color: #e6edf3; padding: 8px 10px; font: inherit; }
    .history-meta { color: #7d8590; font-size: 13px; }
    .toggle { display: inline-flex; align-items: center; gap: 8px; color: #e6edf3; font-size: 14px; }
    .chart-title { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
    .chart-value { color: #e6edf3; font-size: 14px; font-weight: 600; }
    canvas { display: block; width: 100%; height: 180px; background: #0d1117; border-radius: 6px; }
    @keyframes blink {
      0%, 50%, 100% { opacity: 1; }
      25%, 75% { opacity: 0.5; }
    }
    pre { overflow: auto; white-space: pre-wrap; background: #0d1117; padding: 12px; border-radius: 6px; font-size: 13px; }
    .icon { font-size: 24px; margin-right: 8px; }
    @media (max-width: 900px) { .grid, .charts { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 650px) { .grid, .charts, .history-controls { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<main>
  <h1>🔥 Fire Alarm Monitoring System</h1>
  <div class="subtitle">Wireless Sensor Network - Real-time Fire Detection</div>
  <div class="warning" id="port-warning"></div>

  <section class="grid">
    <div class="card" id="card-temp">
      <div class="label">🌡️ Temperature</div>
      <div class="value" id="temp">-</div>
    </div>
    <div class="card" id="card-hum">
      <div class="label">💧 Humidity</div>
      <div class="value" id="hum">-</div>
    </div>
    <div class="card" id="card-gas">
      <div class="label">☁️ Gas Level</div>
      <div class="value" id="gas">-</div>
    </div>
    <div class="card" id="card-status">
      <div class="label">📊 System Status</div>
      <div class="status-badge" id="status">Waiting...</div>
      <div style="font-size: 12px; color: #7d8590; margin-top: 12px;">
        Packet ID: <span id="id">-</span>
      </div>
    </div>
  </section>

  <section class="card">
    <div class="label">Buzzer Control</div>
    <div class="controls">
      <button class="on" type="button" onclick="setBuzzer('on')">Buzzer ON</button>
      <button class="off" type="button" onclick="setBuzzer('off')">Buzzer OFF</button>
      <button class="auto" type="button" onclick="setBuzzer('auto')">Auto</button>
    </div>
    <div class="command-status" id="command-status"></div>
  </section>

  <section class="card" style="margin-top:16px">
    <div class="label">📦 Latest Payload</div>
    <pre id="raw">Waiting for data...</pre>
  </section>

  <section class="card history-panel">
    <div class="label">Timeline History</div>
    <div class="history-controls">
      <select id="history-window">
        <option value="60">Last 2 minutes</option>
        <option value="150">Last 5 minutes</option>
        <option value="450">Last 15 minutes</option>
        <option value="1800">Last 1 hour</option>
        <option value="all">All current run</option>
      </select>
      <input id="history-slider" type="range" min="0" max="0" value="0">
      <label class="toggle">
        <input id="history-live" type="checkbox" checked>
        Live
      </label>
    </div>
    <div class="history-meta" id="history-meta">Waiting for history...</div>
  </section>

  <section class="charts">
    <div class="card">
      <div class="chart-title">
        <div class="label">Temperature Plot</div>
        <div class="chart-value" id="temp-now">-</div>
      </div>
      <canvas id="chart-temp"></canvas>
    </div>
    <div class="card">
      <div class="chart-title">
        <div class="label">Humidity Plot</div>
        <div class="chart-value" id="hum-now">-</div>
      </div>
      <canvas id="chart-hum"></canvas>
    </div>
    <div class="card">
      <div class="chart-title">
        <div class="label">Gas Plot</div>
        <div class="chart-value" id="gas-now">-</div>
      </div>
      <canvas id="chart-gas"></canvas>
    </div>
    <div class="card">
      <div class="chart-title">
        <div class="label">Alert And Buzzer Plot</div>
        <div class="chart-value" id="state-now">-</div>
      </div>
      <canvas id="chart-state"></canvas>
    </div>
  </section>

  <section class="card" style="margin-top:16px">
    <div class="label">📝 Serial Log (Last 30 lines)</div>
    <pre id="lines"></pre>
  </section>
</main>
<script>
function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width: rect.width, height: rect.height };
}

function drawEmptyChart(canvas, text) {
  const { ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#7d8590';
  ctx.font = '13px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(text, width / 2, height / 2);
}

function drawAxes(ctx, width, height, min, max, firstLabel, lastLabel) {
  const pad = { left: 44, right: 14, top: 16, bottom: 28 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;

  ctx.strokeStyle = '#30363d';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, pad.top + plotHeight);
  ctx.lineTo(pad.left + plotWidth, pad.top + plotHeight);
  ctx.stroke();

  ctx.fillStyle = '#7d8590';
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'right';
  ctx.fillText(max.toFixed(max >= 100 ? 0 : 1), pad.left - 7, pad.top + 4);
  ctx.fillText(min.toFixed(min >= 100 ? 0 : 1), pad.left - 7, pad.top + plotHeight);

  ctx.textAlign = 'left';
  ctx.fillText(firstLabel || '', pad.left, height - 8);
  ctx.textAlign = 'right';
  ctx.fillText(lastLabel || '', pad.left + plotWidth, height - 8);

  return { pad, plotWidth, plotHeight };
}

function drawLineChart(canvasId, history, field, color, options = {}) {
  const canvas = document.getElementById(canvasId);
  const values = history
    .map((point) => Number(point[field]))
    .filter((value) => Number.isFinite(value));

  if (values.length < 2) {
    drawEmptyChart(canvas, 'Waiting for more data...');
    return;
  }

  const { ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);

  let min = options.min ?? Math.min(...values);
  let max = options.max ?? Math.max(...values);
  if (min === max) {
    const pad = Math.max(1, Math.abs(max) * 0.05);
    min -= pad;
    max += pad;
  }

  const firstLabel = history[0]?.time || '';
  const lastLabel = history[history.length - 1]?.time || '';
  const { pad, plotWidth, plotHeight } = drawAxes(ctx, width, height, min, max, firstLabel, lastLabel);
  const xStep = plotWidth / Math.max(1, history.length - 1);

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  let started = false;

  history.forEach((point, index) => {
    const value = Number(point[field]);
    if (!Number.isFinite(value)) {
      return;
    }
    const x = pad.left + index * xStep;
    const y = pad.top + plotHeight - ((value - min) / (max - min)) * plotHeight;
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
}

function drawStateChart(history) {
  const canvas = document.getElementById('chart-state');
  if (history.length < 2) {
    drawEmptyChart(canvas, 'Waiting for more data...');
    return;
  }

  const { ctx, width, height } = setupCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const firstLabel = history[0]?.time || '';
  const lastLabel = history[history.length - 1]?.time || '';
  const { pad, plotWidth, plotHeight } = drawAxes(ctx, width, height, 0, 1, firstLabel, lastLabel);
  const xStep = plotWidth / Math.max(1, history.length - 1);

  function drawDigital(field, color, offset) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    history.forEach((point, index) => {
      const value = Number(point[field]) === 1 ? 1 : 0;
      const x = pad.left + index * xStep;
      const y = pad.top + plotHeight - value * (plotHeight - 10) - offset;
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  }

  drawDigital('alert', '#f85149', 0);
  drawDigital('buzzer', '#58a6ff', 10);

  ctx.fillStyle = '#f85149';
  ctx.fillRect(width - 122, 16, 10, 10);
  ctx.fillStyle = '#7d8590';
  ctx.font = '12px system-ui, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText('Alert', width - 106, 25);
  ctx.fillStyle = '#58a6ff';
  ctx.fillRect(width - 62, 16, 10, 10);
  ctx.fillStyle = '#7d8590';
  ctx.fillText('Buzzer', width - 46, 25);
}

function getVisibleHistory(history) {
  const windowEl = document.getElementById('history-window');
  const slider = document.getElementById('history-slider');
  const live = document.getElementById('history-live');
  const meta = document.getElementById('history-meta');

  if (!history.length) {
    slider.max = 0;
    slider.value = 0;
    meta.textContent = 'Waiting for history...';
    return [];
  }

  const selected = windowEl.value;
  const windowSize = selected === 'all'
    ? history.length
    : Math.min(history.length, Number(selected));
  const maxStart = Math.max(0, history.length - windowSize);

  slider.max = maxStart;
  slider.disabled = maxStart === 0;

  if (live.checked) {
    slider.value = maxStart;
  } else if (Number(slider.value) > maxStart) {
    slider.value = maxStart;
  }

  const start = Number(slider.value);
  const end = Math.min(history.length, start + windowSize);
  const visible = history.slice(start, end);
  const first = visible[0]?.time || '-';
  const last = visible[visible.length - 1]?.time || '-';

  meta.textContent = `Showing samples ${start + 1}-${end} of ${history.length} (${first} to ${last})`;
  return visible;
}

function updatePlots(history, payload) {
  document.getElementById('temp-now').textContent = payload.temp !== undefined ? payload.temp + '°C' : '-';
  document.getElementById('hum-now').textContent = payload.hum !== undefined ? payload.hum + '%' : '-';
  document.getElementById('gas-now').textContent = payload.gas ?? '-';
  document.getElementById('state-now').textContent = `Alert ${payload.alert ?? 0} / Buzzer ${payload.buzzer ?? 0}`;

  drawLineChart('chart-temp', history, 'temp', '#ff7b72');
  drawLineChart('chart-hum', history, 'hum', '#58a6ff', { min: 0, max: 100 });
  drawLineChart('chart-gas', history, 'gas', '#d29922');
  drawStateChart(history);
}

async function refresh() {
  const res = await fetch('/api/latest');
  const data = await res.json();
  const payload = data.json || {};
  const history = data.history || [];
  const visibleHistory = getVisibleHistory(history);
  const warning = data.port_warning || '';
  const warningEl = document.getElementById('port-warning');
  warningEl.textContent = warning;
  warningEl.style.display = warning ? 'block' : 'none';

  // Update values
  document.getElementById('id').textContent = payload.id ?? '-';
  document.getElementById('temp').textContent = payload.temp !== undefined ? payload.temp + '°C' : '-';
  document.getElementById('hum').textContent = payload.hum !== undefined ? payload.hum + '%' : '-';
  document.getElementById('gas').textContent = payload.gas ?? '-';
  document.getElementById('command-status').textContent = data.command_status || '';

  // Update alert status with specific sensor highlighting
  const isAlert = payload.alert === 1;
  const tempAlert = payload.temp_alert === 1;
  const gasAlert = payload.gas_alert === 1;
  const statusEl = document.getElementById('status');
  const cardTemp = document.getElementById('card-temp');
  const cardGas = document.getElementById('card-gas');

  // Remove all alerts first
  cardTemp.classList.remove('alert');
  cardGas.classList.remove('alert');

  if (isAlert) {
    // Determine alert message based on cause
    if (tempAlert && gasAlert) {
      statusEl.textContent = '🚨 ALERT: Temp + Gas!';
      cardTemp.classList.add('alert');
      cardGas.classList.add('alert');
    } else if (tempAlert) {
      statusEl.textContent = '🚨 ALERT: High Temp!';
      cardTemp.classList.add('alert');
    } else if (gasAlert) {
      statusEl.textContent = '🚨 ALERT: Gas Detected!';
      cardGas.classList.add('alert');
    } else {
      statusEl.textContent = '🚨 ALERT!';
    }
    statusEl.className = 'status-badge status-alert';
  } else {
    statusEl.textContent = '✓ Normal';
    statusEl.className = 'status-badge status-normal';
  }

  // Update raw payload
  document.getElementById('raw').textContent = data.raw || 'Waiting for data...';
  document.getElementById('lines').textContent = (data.lines || []).join('\\n');
  updatePlots(visibleHistory, payload);
}

async function setBuzzer(state) {
  const res = await fetch('/api/buzzer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ state })
  });
  const data = await res.json();
  document.getElementById('command-status').textContent = data.message || data.error || 'Command failed';
  refresh();
}

setInterval(refresh, 1000);
document.getElementById('history-slider').addEventListener('input', () => {
  document.getElementById('history-live').checked = false;
  refresh();
});
document.getElementById('history-window').addEventListener('change', refresh);
document.getElementById('history-live').addEventListener('change', refresh);
refresh();
</script>
</body>
</html>
"""


def main():
    global csv_file, csv_writer, demo_mode

    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", help="Serial port for Zolertia receiver, e.g. /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--csv-dir", default="./data", help="Directory to store CSV logs")
    parser.add_argument("--demo", action="store_true", help="Run with simulated sensor data and no serial device")
    args = parser.parse_args()
    demo_mode = args.demo

    if not args.demo and not args.port:
        parser.error("port is required unless --demo is used")

    # Setup CSV logging
    csv_dir = Path(args.csv_dir)
    csv_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = csv_dir / f"fire_alarm_{timestamp}.csv"

    csv_file = open(csv_path, "w", newline="")
    fieldnames = [
        "timestamp",
        "packet_id",
        "temperature",
        "humidity",
        "gas",
        "alert",
        "temp_alert",
        "gas_alert",
        "buzzer",
        "buzzer_manual",
        "alert_cause",
    ]
    csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    csv_writer.writeheader()
    csv_file.flush()

    if args.demo:
        thread = threading.Thread(target=demo_reader, daemon=True)
    else:
        thread = threading.Thread(target=serial_reader, args=(args.port, args.baud), daemon=True)
    thread.start()

    server = ThreadingHTTPServer((args.host, args.http_port), Handler)
    print(f"🔥 Fire Alarm Dashboard: http://{args.host}:{args.http_port}")
    if args.demo:
        print("📡 Demo mode: generating simulated sensor data")
    else:
        print(f"📡 Reading serial: {args.port} at {args.baud}")
    print(f"💾 Logging data to: {csv_path}")

    try:
        server.serve_forever()
    finally:
        if csv_file:
            csv_file.close()


if __name__ == "__main__":
    main()
