#!/usr/bin/env python3
"""
Fusion dashboard: Polar H9 (BLE HR/HRV) + Microsoft Band 2 (skin temp, GSR, accel).

Usage:
    python3 fusion_dashboard.py [H9_MAC] [--band2 B2_MAC] [--port 8080]
    open http://127.0.0.1:8080
"""
import argparse
import asyncio
import json
import math
import queue
import socket
import sys
import threading
import time
import uuid
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

_LIBBAND_PATH = Path(__file__).resolve().parent.parent / "microsoft_band_2"
if str(_LIBBAND_PATH) not in sys.path:
    sys.path.insert(0, str(_LIBBAND_PATH))

from bleak import BleakClient, BleakScanner

try:
    from libband.sensors import Sensor, decode_sensor_reading
    BAND2_AVAILABLE = True
except ImportError:
    BAND2_AVAILABLE = False
    print("WARNING: libband not found — Band 2 support disabled")

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
RR_WINDOW = 20
BPM_RATE_WINDOW = 8
RR_MIN_MS = 300
RR_MAX_MS = 2000
RR_ECTOPIC_THRESHOLD = 0.25
MOTION_THRESHOLD_G = 1.5
CARGO_PORT = 4
PUSH_PORT = 5
PUSH_SERVICE_GUID = uuid.UUID(hex="d8895bfd0461400dbd52dbe2a3c33021")
RECONNECT_SECONDS = 5
HISTORY_SECONDS = 20 * 60
HISTORY_MAX_SAMPLES = 120000
RECORDINGS_DIR = Path(__file__).resolve().with_name("recordings")

if BAND2_AVAILABLE:
    BAND2_SENSORS = (
        Sensor.SkinTemperature,
        Sensor.Gsr200MS,
        Sensor.AccelerometerGyroscope32MS,
        Sensor.DeviceContact,
        Sensor.BatteryGauge,
    )
    ALL_BAND2_SENSORS = (
        Sensor.HeartRate, Sensor.RRInterval, Sensor.Gsr, Sensor.Gsr200MS,
        Sensor.SkinTemperature, Sensor.AccelerometerGyroscope32MS,
        Sensor.DeviceContact, Sensor.BatteryGauge,
    )


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fusion Dashboard</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #081018;
      --panel: rgba(13, 22, 32, .88);
      --panel-strong: #111b27;
      --ink: #e7eef7;
      --muted: #86a0b5;
      --line: rgba(145, 176, 206, .16);
      --blue: #49a2ff;
      --green: #3ddc97;
      --red: #ff6b6b;
      --amber: #ffbf4d;
      --purple: #b07fff;
      --teal: #47d7ff;
      --orange: #ff8c42;
      --shadow: 0 10px 30px rgba(0, 0, 0, .35);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 Inter, "Segoe UI", system-ui, sans-serif;
      background-image:
        linear-gradient(180deg, rgba(73, 162, 255, .04), transparent 28%),
        linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px),
        linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px);
      background-size: auto, 44px 44px, 44px 44px;
    }
    header {
      min-height: 64px;
      display: flex; align-items: center; justify-content: space-between;
      gap: 16px; padding: 12px 24px;
      border-bottom: 1px solid var(--line);
      background: rgba(8,16,24,.9);
      backdrop-filter: blur(18px);
      position: sticky; top: 0; z-index: 2;
      flex-wrap: wrap;
    }
    h1 { margin: 0; font-size: 17px; font-weight: 650; white-space: nowrap; }
    .devices { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
    .device-badge {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 4px 10px; border-radius: 20px;
      border: 1px solid rgba(145,176,206,.14);
      background: rgba(17,27,39,.7);
      font-size: 12px; color: var(--muted); white-space: nowrap;
    }
    .header-right {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end;
      color: var(--muted); font-size: 13px;
    }
    .status-dot {
      width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
      background: var(--amber); box-shadow: 0 0 0 3px rgba(255,191,77,.10);
    }
    .status-dot.connected { background: var(--green); box-shadow: 0 0 0 3px rgba(61,220,151,.12); }
    .status-dot.error { background: var(--red); box-shadow: 0 0 0 3px rgba(255,107,107,.12); }
    button {
      appearance: none;
      border: 1px solid rgba(145,176,206,.18);
      background: rgba(17,27,39,.8); color: var(--ink);
      border-radius: 6px; height: 34px; padding: 0 12px;
      font: inherit; cursor: pointer;
      transition: background-color .15s, border-color .15s;
    }
    button:hover:not(:disabled) { border-color: rgba(73,162,255,.35); background: rgba(20,35,51,.95); }
    button:disabled { opacity: .35; cursor: not-allowed; }
    button.primary {
      border-color: rgba(73,162,255,.45);
      background: linear-gradient(180deg, rgba(73,162,255,.98), rgba(37,112,214,.98));
      color: #f7fbff;
    }
    .recording-name {
      height: 34px; min-width: 200px; max-width: 300px;
      border: 1px solid rgba(145,176,206,.18); border-radius: 6px; padding: 0 10px;
      font: inherit; color: var(--ink); background: rgba(17,27,39,.85);
    }
    .recording-name:focus { outline: 2px solid rgba(73,162,255,.22); border-color: rgba(73,162,255,.48); }
    main { width: min(1100px, 100%); margin: 0 auto; padding: 20px 24px 28px; }
    .section-label {
      font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
      color: var(--muted); margin: 0 0 8px 2px;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(140px, 1fr));
      gap: 10px;
    }
    .metrics + .metrics { margin-top: 10px; }
    .metric {
      min-height: 104px;
      background: var(--panel); border: 1px solid rgba(145,176,206,.14);
      border-radius: 8px; box-shadow: var(--shadow);
      padding: 12px 14px;
      display: flex; flex-direction: column; justify-content: space-between;
      position: relative; overflow: hidden;
    }
    .metric::before {
      content: ""; position: absolute; inset: 0 auto auto 0;
      width: 100%; height: 2px;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,.08), transparent);
      pointer-events: none;
    }
    .metric .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; display: flex; align-items: center; gap: 5px; }
    .metric .value { font-size: clamp(24px, 3vw, 38px); line-height: 1; font-weight: 750; }
    .metric .unit { color: var(--muted); font-size: 13px; }
    .metric .subvalue { color: var(--muted); font-size: 11px; margin-top: 2px; }
    .metric .value.good, .metric .subvalue.good { color: var(--green); }
    .metric .value.warn, .metric .subvalue.warn { color: var(--amber); }
    .metric .value.bad,  .metric .subvalue.bad  { color: var(--red); }
    .metric.state-good { box-shadow: 0 0 0 1px rgba(61,220,151,.18), var(--shadow); }
    .metric.state-warn { box-shadow: 0 0 0 1px rgba(255,191,77,.18), var(--shadow); }
    .metric.state-bad  { box-shadow: 0 0 0 1px rgba(255,107,107,.18), var(--shadow); }
    .metric.state-good::before { background: linear-gradient(90deg, transparent, rgba(61,220,151,.95), transparent); }
    .metric.state-warn::before { background: linear-gradient(90deg, transparent, rgba(255,191,77,.95), transparent); }
    .metric.state-bad::before  { background: linear-gradient(90deg, transparent, rgba(255,107,107,.95), transparent); }
    .motion-badge {
      display: none; font-size: 10px; background: rgba(255,191,77,.15);
      border: 1px solid rgba(255,191,77,.35); color: var(--amber);
      padding: 1px 5px; border-radius: 10px; margin-left: 4px;
      white-space: nowrap;
    }
    .motion-badge.visible { display: inline; }
    .chart-panel {
      background: var(--panel); border: 1px solid rgba(145,176,206,.14);
      border-radius: 8px; box-shadow: var(--shadow);
      margin-top: 14px; backdrop-filter: blur(12px);
    }
    .panel-head {
      min-height: 48px; padding: 6px 14px;
      border-bottom: 1px solid rgba(145,176,206,.14);
      display: flex; align-items: center; justify-content: space-between;
      gap: 10px; flex-wrap: wrap;
    }
    .panel-title { font-weight: 700; font-size: 14px; white-space: nowrap; }
    .tabs {
      display: flex; gap: 3px; flex-wrap: wrap;
      border: 1px solid rgba(145,176,206,.14); border-radius: 6px;
      padding: 2px; background: rgba(17,27,39,.72);
    }
    .tabs button { height: 27px; padding: 0 9px; border: 0; background: transparent; color: var(--muted); font-size: 13px; }
    .tabs button.active { background: rgba(255,255,255,.06); color: var(--ink); box-shadow: inset 0 0 0 1px rgba(73,162,255,.22); }
    .raw-toggle {
      display: inline-flex; align-items: center; height: 27px; padding: 0 10px;
      border: 1px solid rgba(145,176,206,.18); border-radius: 14px;
      background: rgba(17,27,39,.72); color: var(--muted);
      font-size: 12px; cursor: pointer; gap: 5px; white-space: nowrap;
      transition: border-color .15s, color .15s;
    }
    .raw-toggle:hover { border-color: rgba(73,162,255,.35); color: var(--ink); }
    .raw-toggle.raw-active { border-color: rgba(255,191,77,.45); color: var(--amber); background: rgba(255,191,77,.06); }
    .raw-toggle .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); transition: background .15s; }
    .raw-toggle.raw-active .dot { background: var(--amber); }
    .range-controls { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
    .range-controls button { min-width: 42px; height: 27px; padding: 0 9px; border: 1px solid rgba(145,176,206,.14); background: rgba(17,27,39,.72); color: var(--muted); border-radius: 6px; font-size: 13px; }
    .range-controls button.active { background: rgba(255,255,255,.06); color: var(--ink); box-shadow: inset 0 0 0 1px rgba(71,215,255,.22); }
    canvas { display: block; width: 100%; height: 320px; cursor: crosshair; }
    #recording-status { color: var(--muted); font-size: 12px; white-space: nowrap; }
    #recording-status.active { color: var(--green); font-weight: 650; }
    .error-text { color: var(--red); margin-top: 10px; overflow-wrap: anywhere; font-size: 13px; }
    /* Marker popup */
    #marker-popup {
      display: none; position: fixed; z-index: 100;
      background: var(--panel-strong); border: 1px solid rgba(145,176,206,.22);
      border-radius: 10px; box-shadow: 0 16px 48px rgba(0,0,0,.55);
      padding: 12px 14px 10px; min-width: 220px;
      flex-direction: column; gap: 10px;
    }
    #marker-popup.open { display: flex; }
    .mp-title { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin-bottom: 2px; }
    .mp-colors { display: flex; gap: 8px; align-items: center; }
    .mp-swatch { width: 22px; height: 22px; border-radius: 50%; cursor: pointer; border: 2px solid transparent; transition: transform .12s, border-color .12s; flex-shrink: 0; }
    .mp-swatch:hover { transform: scale(1.18); }
    .mp-swatch.selected { border-color: #fff; transform: scale(1.15); }
    #marker-label-input {
      height: 32px; border: 1px solid rgba(145,176,206,.18); border-radius: 6px;
      padding: 0 9px; font: inherit; color: var(--ink);
      background: rgba(17,27,39,.85); width: 100%;
    }
    #marker-label-input:focus { outline: 2px solid rgba(73,162,255,.22); border-color: rgba(73,162,255,.48); }
    .mp-actions { display: flex; gap: 6px; justify-content: flex-end; }
    .mp-actions button { height: 28px; padding: 0 12px; font-size: 13px; }
    #marker-list { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
    .marker-chip {
      display: inline-flex; align-items: center; gap: 5px;
      height: 24px; padding: 0 8px; border-radius: 12px;
      font-size: 12px; border: 1px solid rgba(145,176,206,.14);
      background: rgba(17,27,39,.7); color: var(--ink);
    }
    .marker-chip .chip-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
    .marker-chip .chip-time { color: var(--muted); font-size: 11px; }
    .marker-chip .chip-del { background: none; border: none; color: var(--muted); font-size: 14px; line-height: 1; padding: 0; height: auto; cursor: pointer; margin-left: 2px; }
    .marker-chip .chip-del:hover { color: var(--red); }
    @media (max-width: 900px) {
      .metrics { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 600px) {
      .metrics { grid-template-columns: repeat(2, 1fr); }
      header { flex-direction: column; align-items: flex-start; padding: 12px 16px; }
      main { padding: 14px 16px 22px; }
      canvas { height: 240px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Fusion Dashboard</h1>
    <div class="devices">
      <div class="device-badge">
        <span class="status-dot" id="h9-dot"></span>
        <span id="h9-status">Polar H9</span>
      </div>
      <div class="device-badge">
        <span class="status-dot" id="b2-dot"></span>
        <span id="b2-status">Band 2</span>
      </div>
    </div>
    <div class="header-right">
      <span id="updated">–</span>
      <span id="recording-status">Not recording</span>
      <input class="recording-name" id="recording-name" type="text" value="">
      <button class="primary" id="record-start">Iniciar sesion</button>
      <button id="record-stop" disabled>Detener grabacion</button>
    </div>
  </header>

  <main>
    <div class="section-label">Polar H9 — Cardíaco</div>
    <section class="metrics" id="h9-metrics">
      <article class="metric">
        <div class="label">Heart Rate <span class="motion-badge" id="motion-badge">motion</span></div>
        <div><span class="value" id="bpm">--</span> <span class="unit">bpm</span></div>
        <div class="subvalue" id="bpm-delta-sub">Δ --</div>
      </article>
      <article class="metric">
        <div class="label">BPM Δ/s</div>
        <div><span class="value" id="bpm-rate">--</span> <span class="unit">bpm/s</span></div>
        <div class="subvalue" id="bpm-rate-hint"></div>
      </article>
      <article class="metric">
        <div class="label">RR Interval</div>
        <div><span class="value" id="rr">--</span> <span class="unit">ms</span></div>
        <div class="subvalue" id="rr-alt-sub"></div>
      </article>
      <article class="metric">
        <div class="label">HRV (RMSSD)</div>
        <div><span class="value" id="rmssd">--</span> <span class="unit">ms</span></div>
        <div class="subvalue" id="rmssd-alt-sub"></div>
      </article>
    </section>

    <div class="section-label" style="margin-top:16px">Microsoft Band 2 — Piel &amp; Movimiento</div>
    <section class="metrics" id="b2-metrics">
      <article class="metric">
        <div class="label">Skin Temp</div>
        <div><span class="value" id="skin-temp">--</span> <span class="unit">°C</span></div>
        <div class="subvalue" id="skin-temp-sub"></div>
      </article>
      <article class="metric">
        <div class="label">GSR (200ms)</div>
        <div><span class="value" id="gsr">--</span> <span class="unit">kΩ</span></div>
        <div class="subvalue" id="gsr-sub"></div>
      </article>
      <article class="metric">
        <div class="label">Accel Mag</div>
        <div><span class="value" id="accel">--</span> <span class="unit">g</span></div>
        <div class="subvalue" id="accel-axes">x -- · y -- · z --</div>
      </article>
      <article class="metric">
        <div class="label">Battery / Wearing</div>
        <div><span class="value" id="battery">--</span> <span class="unit">%</span></div>
        <div class="subvalue" id="wearing-sub">wearing --</div>
      </article>
    </section>

    <article class="chart-panel">
      <div class="panel-head">
        <div class="panel-title" id="chart-title">Heart Rate</div>
        <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; justify-content:flex-end;">
          <div class="tabs">
            <button class="active" data-series="bpm">BPM</button>
            <button data-series="bpm_rate">ΔBPM</button>
            <button data-series="rr_ms">RR</button>
            <button data-series="rmssd_ms">HRV</button>
            <button data-series="skin_temp_c">Temp</button>
            <button data-series="gsr_kohm">GSR</button>
            <button data-series="accel_mag_g">Accel</button>
            <button data-series="gyro_mag_dps">Gyro</button>
          </div>
          <button class="raw-toggle" id="raw-toggle">
            <span class="dot"></span>
            <span id="raw-toggle-label">Filtrado</span>
          </button>
          <div class="range-controls">
            <button class="active" data-window="10">10s</button>
            <button data-window="30">30s</button>
            <button data-window="60">1m</button>
            <button data-window="600">10m</button>
            <button data-window="0">All</button>
          </div>
        </div>
      </div>
      <canvas id="chart" width="900" height="320"></canvas>
    </article>

    <div id="marker-list"></div>
    <p class="error-text" id="error"></p>
  </main>

  <!-- Marker popup -->
  <div id="marker-popup">
    <div>
      <div class="mp-title">Marca</div>
      <div class="mp-colors" id="mp-colors"></div>
    </div>
    <input id="marker-label-input" type="text" placeholder="Label (opcional)" maxlength="60" autocomplete="off">
    <div class="mp-actions">
      <button id="mp-cancel">Cancelar</button>
      <button class="primary" id="mp-confirm">Agregar</button>
    </div>
  </div>

  <script>
    const MARKER_COLORS = [
      { name: "blue",   hex: "#49a2ff" },
      { name: "green",  hex: "#3ddc97" },
      { name: "amber",  hex: "#ffbf4d" },
      { name: "red",    hex: "#ff6b6b" },
      { name: "purple", hex: "#b07fff" },
    ];
    const MOTION_THRESHOLD = 1.5;

    // Fixed Y ranges + zone bands for series with known physiological meaning.
    // Bands are intentionally subtle — direction indicators, not clinical thresholds.
    const ZONES = {
      bpm: {
        yMin: 40, yMax: 180,
        bands: [
          { from: 140, to: 180, fill: "rgba(255,107,107,.07)", label: "alto",    lc: "rgba(255,107,107,.45)" },
          { from: 100, to: 140, fill: "rgba(255,191,77,.07)",  label: "elevado", lc: "rgba(255,191,77,.45)"  },
          { from:  60, to: 100, fill: "rgba(61,220,151,.06)",  label: "normal",  lc: "rgba(61,220,151,.45)"  },
          { from:  40, to:  60, fill: "rgba(73,162,255,.07)",  label: "bajo",    lc: "rgba(73,162,255,.45)"  },
        ]
      },
      rr_ms: {
        yMin: 300, yMax: 1500,
        bands: [
          { from: 1000, to: 1500, fill: "rgba(73,162,255,.06)",  label: "baja FC",  lc: "rgba(73,162,255,.4)"  },
          { from:  600, to: 1000, fill: "rgba(61,220,151,.06)",  label: "normal",   lc: "rgba(61,220,151,.4)"  },
          { from:  430, to:  600, fill: "rgba(255,191,77,.06)",  label: "elevada",  lc: "rgba(255,191,77,.4)"  },
          { from:  300, to:  430, fill: "rgba(255,107,107,.07)", label: "alta FC",  lc: "rgba(255,107,107,.4)" },
        ]
      },
      rr_ms_raw: {
        yMin: 300, yMax: 1500,
        bands: [
          { from: 1000, to: 1500, fill: "rgba(73,162,255,.06)",  label: "baja FC",  lc: "rgba(73,162,255,.4)"  },
          { from:  600, to: 1000, fill: "rgba(61,220,151,.06)",  label: "normal",   lc: "rgba(61,220,151,.4)"  },
          { from:  430, to:  600, fill: "rgba(255,191,77,.06)",  label: "elevada",  lc: "rgba(255,191,77,.4)"  },
          { from:  300, to:  430, fill: "rgba(255,107,107,.07)", label: "alta FC",  lc: "rgba(255,107,107,.4)" },
        ]
      },
      rmssd_ms: {
        yMin: 0, yMax: 100,
        bands: [
          { from: 50, to: 100, fill: "rgba(73,162,255,.06)",  label: "alto",   lc: "rgba(73,162,255,.4)"  },
          { from: 20, to:  50, fill: "rgba(61,220,151,.05)",  label: "normal", lc: "rgba(61,220,151,.4)"  },
          { from:  0, to:  20, fill: "rgba(255,107,107,.06)", label: "bajo",   lc: "rgba(255,107,107,.4)" },
        ]
      },
      rmssd_ms_raw: {
        yMin: 0, yMax: 100,
        bands: [
          { from: 50, to: 100, fill: "rgba(73,162,255,.06)",  label: "alto",   lc: "rgba(73,162,255,.4)"  },
          { from: 20, to:  50, fill: "rgba(61,220,151,.05)",  label: "normal", lc: "rgba(61,220,151,.4)"  },
          { from:  0, to:  20, fill: "rgba(255,107,107,.06)", label: "bajo",   lc: "rgba(255,107,107,.4)" },
        ]
      },
      skin_temp_c: {
        yMin: 28, yMax: 38,
        bands: [
          { from: 34, to: 38, fill: "rgba(255,107,107,.06)", label: "cálido", lc: "rgba(255,107,107,.4)" },
          { from: 31, to: 34, fill: "rgba(61,220,151,.06)",  label: "normal", lc: "rgba(61,220,151,.4)"  },
          { from: 28, to: 31, fill: "rgba(73,162,255,.07)",  label: "frío",   lc: "rgba(73,162,255,.4)"  },
        ]
      },
      accel_mag_g: {
        yMin: 0, yMax: 4,
        bands: [
          { from: 1.5, to: 4,   fill: "rgba(255,191,77,.06)", label: "mov",    lc: "rgba(255,191,77,.4)"  },
          { from: 0,   to: 1.5, fill: "rgba(61,220,151,.05)", label: "quieto", lc: "rgba(61,220,151,.35)" },
        ]
      },
    };

    const state = {
      series: "bpm",
      windowSeconds: 10,
      history: [],
      markers: [],
      rawMode: false,
      recordingActive: false,
      recordingFile: null,
      labels: {
        bpm:          ["Heart Rate (H9)",          "bpm",   "#49a2ff"],
        bpm_rate:     ["BPM Δ/s (H9)",             "bpm/s", "#b07fff"],
        rr_ms:        ["RR Interval (filtrado)",    "ms",    "#3ddc97"],
        rr_ms_raw:    ["RR Interval (raw)",         "ms",    "#3ddc97"],
        rmssd_ms:     ["HRV RMSSD (filtrado)",      "ms",    "#ffbf4d"],
        rmssd_ms_raw: ["HRV RMSSD (raw)",           "ms",    "#ffbf4d"],
        skin_temp_c:  ["Skin Temperature (Band2)",  "°C",   "#ff8c42"],
        gsr_kohm:     ["GSR (Band2)",               "kΩ",   "#47d7ff"],
        accel_mag_g:  ["Accel Magnitude (Band2)",   "g",    "#e879f9"],
        gyro_mag_dps: ["Gyro Magnitude (Band2)",    "dps",  "#f472b6"],
      }
    };

    let pendingMarker = null;

    const $ = id => document.getElementById(id);
    const fmt0 = v => Number.isFinite(v) ? Math.round(v).toString() : "--";
    const fmt1 = v => Number.isFinite(v) ? v.toFixed(1) : "--";
    const fmt2 = v => Number.isFinite(v) ? v.toFixed(2) : "--";
    const fmtRate = v => Number.isFinite(v) ? (v >= 0 ? "+" : "") + v.toFixed(2) : "--";
    const CLIENT_HISTORY_SECONDS = 20 * 60;
    const MAX_CLIENT_HISTORY = 50000;
    const MAX_PLOT_POINTS = 2500;
    const PAD = { left: 54, right: 18, top: 22, bottom: 44 };

    function defaultRecordingName() {
      const now = new Date();
      const pad = n => String(n).padStart(2, "0");
      return `fusion-${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}-${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}.json`;
    }
    $("recording-name").value = defaultRecordingName();

    function trimHistory() {
      if (!state.history.length) return;
      const newest = state.history[state.history.length - 1];
      const cutoff = Number.isFinite(newest && newest.t)
        ? newest.t - CLIENT_HISTORY_SECONDS
        : (Date.now() / 1000) - CLIENT_HISTORY_SECONDS;
      while (state.history.length && Number.isFinite(state.history[0].t) && state.history[0].t < cutoff)
        state.history.shift();
      if (state.history.length > MAX_CLIENT_HISTORY)
        state.history = state.history.slice(-MAX_CLIENT_HISTORY);
    }

    function appendSample(sample) {
      if (!sample || !Number.isFinite(sample.t)) return;
      const last = state.history[state.history.length - 1];
      if (!last || last.t !== sample.t) { state.history.push(sample); trimHistory(); }
    }

    function setMetricTone(id, tone) {
      const el = $(id);
      if (!el) return;
      const card = el.closest(".metric");
      el.classList.remove("good", "warn", "bad");
      if (card) card.classList.remove("state-good", "state-warn", "state-bad");
      if (!tone) return;
      el.classList.add(tone);
      if (card) card.classList.add(`state-${tone}`);
    }

    function toneHR(bpm) {
      if (!Number.isFinite(bpm)) return null;
      if (bpm < 50) return "bad";
      if (bpm < 60) return "warn";
      if (bpm <= 100) return "good";
      if (bpm <= 120) return "warn";
      return "bad";
    }
    function toneBpmRate(r) {
      if (!Number.isFinite(r)) return null;
      if (Math.abs(r) < 0.3) return "good";
      return r > 0 ? (r > 1.5 ? "bad" : "warn") : "good";
    }
    function toneSkinTemp(t) {
      if (!Number.isFinite(t)) return null;
      if (t >= 31 && t <= 34) return "good";
      if (t >= 29 && t <= 36) return "warn";
      return "bad";
    }
    function toneBattery(p) {
      if (!Number.isFinite(p)) return null;
      if (p >= 50) return "good";
      if (p >= 20) return "warn";
      return "bad";
    }
    function toneAccel(g) {
      if (!Number.isFinite(g)) return null;
      if (g < 1.2) return "good";
      if (g < MOTION_THRESHOLD) return "warn";
      return "bad";
    }

    function rrKey()    { return state.rawMode ? "rr_ms_raw"    : "rr_ms"; }
    function rmssdKey() { return state.rawMode ? "rmssd_ms_raw" : "rmssd_ms"; }

    function updateDeviceStatus(dotId, labelId, connectionState, prefix) {
      const dot = $(dotId);
      dot.className = "status-dot";
      const cs = connectionState || "unknown";
      if (cs === "connected") dot.classList.add("connected");
      else if (cs === "reconnecting" || cs === "error") dot.classList.add("error");
      $(labelId).textContent = `${prefix}: ${cs}`;
    }

    function render(data) {
      updateDeviceStatus("h9-dot", "h9-status", data.h9_state, "H9");
      updateDeviceStatus("b2-dot", "b2-status", data.b2_state, "Band2");
      $("error").textContent = data.error || "";

      // H9 metrics
      $("bpm").textContent = fmt0(data.bpm);
      setMetricTone("bpm", toneHR(data.bpm));
      const rate = data.bpm_rate;
      $("bpm-rate").textContent = fmtRate(rate);
      setMetricTone("bpm-rate", toneBpmRate(rate));
      $("bpm-rate-hint").textContent = Number.isFinite(rate)
        ? (rate > 0.5 ? "subiendo" : rate < -0.5 ? "bajando" : "estable") : "";
      $("bpm-delta-sub").textContent = Number.isFinite(rate) ? `Δ ${fmtRate(rate)} bpm/s` : "Δ --";

      const rrVal = state.rawMode ? data.rr_ms_raw : data.rr_ms;
      const rrAlt = state.rawMode ? data.rr_ms : data.rr_ms_raw;
      $("rr").textContent = fmt0(rrVal);
      $("rr-alt-sub").textContent = Number.isFinite(rrAlt)
        ? `${state.rawMode ? "filtrado" : "raw"}: ${fmt0(rrAlt)} ms` : "";

      const rmssdVal = state.rawMode ? data.rmssd_ms_raw : data.rmssd_ms;
      const rmssdAlt = state.rawMode ? data.rmssd_ms : data.rmssd_ms_raw;
      $("rmssd").textContent = fmt1(rmssdVal);
      $("rmssd-alt-sub").textContent = Number.isFinite(rmssdAlt)
        ? `${state.rawMode ? "filtrado" : "raw"}: ${fmt1(rmssdAlt)} ms` : "";

      // Motion flag
      const motionFlag = data.h9_motion_flag === true;
      $("motion-badge").classList.toggle("visible", motionFlag);

      // Band2 metrics
      $("skin-temp").textContent = fmt1(data.skin_temp_c);
      setMetricTone("skin-temp", toneSkinTemp(data.skin_temp_c));
      $("skin-temp-sub").textContent = Number.isFinite(data.skin_temp_c)
        ? (data.skin_temp_c >= 31 && data.skin_temp_c <= 34 ? "normal" : "fuera de rango") : "";

      $("gsr").textContent = Number.isFinite(data.gsr_kohm) ? fmt0(data.gsr_kohm) : "--";
      $("gsr-sub").textContent = "";

      const accelMag = data.accel_mag_g;
      $("accel").textContent = fmt2(accelMag);
      setMetricTone("accel", toneAccel(accelMag));
      $("accel-axes").textContent = [data.accel_x_g, data.accel_y_g, data.accel_z_g]
        .map((v, i) => `${"xyz"[i]} ${fmt2(v)}`).join(" · ") +
        (Number.isFinite(data.gyro_mag_dps) ? `  ω ${fmt0(data.gyro_mag_dps)}dps` : "");

      $("battery").textContent = fmt0(data.battery_pct);
      setMetricTone("battery", toneBattery(data.battery_pct));
      $("wearing-sub").textContent = data.wearing === true ? "wearing: yes"
        : data.wearing === false ? "wearing: no" : "wearing: --";

      // Recording
      state.recordingActive = !!data.recording_active;
      state.recordingFile = data.recording_file || null;
      $("recording-status").textContent = state.recordingActive
        ? `REC ${state.recordingFile || ""}`.trim() : "Not recording";
      $("recording-status").classList.toggle("active", state.recordingActive);
      $("record-start").disabled = state.recordingActive;
      $("record-stop").disabled = !state.recordingActive;

      if (data.updated_at) $("updated").textContent = new Date(data.updated_at * 1000).toLocaleTimeString();

      if (Array.isArray(data.history)) { state.history = data.history; trimHistory(); }
      if (data.sample && Number.isFinite(data.sample.t)) appendSample(data.sample);
      if (Array.isArray(data.markers)) { state.markers = data.markers; renderMarkerChips(); }

      drawChart();
    }

    // Raw/Filtered toggle
    $("raw-toggle").addEventListener("click", () => {
      state.rawMode = !state.rawMode;
      $("raw-toggle").classList.toggle("raw-active", state.rawMode);
      $("raw-toggle-label").textContent = state.rawMode ? "Raw" : "Filtrado";
      if (state.series === "rr_ms" || state.series === "rr_ms_raw") state.series = rrKey();
      if (state.series === "rmssd_ms" || state.series === "rmssd_ms_raw") state.series = rmssdKey();
      drawChart();
    });

    // Chart
    function chartTimeRange() {
      const now = Date.now() / 1000;
      if (state.windowSeconds > 0) return { xStart: now - state.windowSeconds, xEnd: now };
      if (!state.history.length) return { xStart: now - 10, xEnd: now };
      return { xStart: state.history[0].t, xEnd: state.history[state.history.length - 1].t };
    }

    function drawChart() {
      const canvas = $("chart");
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width  = Math.max(600, Math.floor(rect.width  * dpr));
      canvas.height = Math.max(220, Math.floor(rect.height * dpr));
      ctx.scale(dpr, dpr);
      const w = rect.width, h = rect.height;
      ctx.clearRect(0, 0, w, h);

      let seriesKey = state.series;
      if (seriesKey === "rr_ms"    || seriesKey === "rr_ms_raw")    seriesKey = rrKey();
      if (seriesKey === "rmssd_ms" || seriesKey === "rmssd_ms_raw") seriesKey = rmssdKey();

      const [title, unit, color] = state.labels[seriesKey];
      $("chart-title").textContent = `${title} · ${formatWindowLabel(state.windowSeconds)}`;

      const { xStart, xEnd } = chartTimeRange();
      let plot = state.windowSeconds > 0
        ? state.history.filter(s => Number.isFinite(s.t) && s.t >= xStart)
        : state.history.slice();
      if (plot.length > MAX_PLOT_POINTS) {
        const step = Math.ceil(plot.length / MAX_PLOT_POINTS);
        plot = plot.filter((_, i) => i % step === 0 || i === plot.length - 1);
      }

      // segments
      let segments = [], segment = [];
      plot.forEach(sample => {
        const v = sample[seriesKey];
        if (!Number.isFinite(v)) return;
        const prev = segment[segment.length - 1];
        if (segment.length && Number.isFinite(prev.t) && Math.abs(sample.t - prev.t) > 2.5) {
          segments.push(segment); segment = [];
        }
        segment.push(sample);
      });
      if (segment.length) segments.push(segment);
      const values = segments.flatMap(seg => seg.map(s => s[seriesKey]).filter(v => Number.isFinite(v)));

      // Y range: fixed for known series, dynamic otherwise
      const zoneConfig = ZONES[seriesKey];
      let yMin, yMax;
      if (zoneConfig) {
        yMin = zoneConfig.yMin;
        yMax = zoneConfig.yMax;
      } else {
        if (values.length < 2) {
          ctx.fillStyle = "#86a0b5"; ctx.font = "13px system-ui, sans-serif";
          ctx.fillText("Waiting for samples…", PAD.left + 12, PAD.top + 24);
          drawMarkersOnChart(ctx, w, h, xStart, xEnd, null, null);
          return;
        }
        const vmin = Math.min(...values), vmax = Math.max(...values);
        const span = Math.max(1, vmax - vmin);
        yMin = vmin - span * 0.12; yMax = vmax + span * 0.12;
      }

      // axes
      ctx.strokeStyle = "rgba(145,176,206,.18)"; ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(PAD.left, PAD.top); ctx.lineTo(PAD.left, h - PAD.bottom);
      ctx.lineTo(w - PAD.right, h - PAD.bottom); ctx.stroke();

      // zone backgrounds + labels
      if (zoneConfig && zoneConfig.bands.length) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(PAD.left + 1, PAD.top, w - PAD.left - PAD.right - 1, h - PAD.top - PAD.bottom);
        ctx.clip();
        zoneConfig.bands.forEach(band => {
          const yTop = valToY(Math.min(band.to, yMax), yMin, yMax, h);
          const yBot = valToY(Math.max(band.from, yMin), yMin, yMax, h);
          ctx.fillStyle = band.fill;
          ctx.fillRect(PAD.left + 1, yTop, w - PAD.left - PAD.right - 1, yBot - yTop);
        });
        ctx.restore();
        // labels on right edge, after clip is released
        ctx.font = "10px system-ui, sans-serif";
        ctx.textAlign = "right"; ctx.textBaseline = "middle";
        zoneConfig.bands.forEach(band => {
          const yTop = Math.max(valToY(Math.min(band.to, yMax), yMin, yMax, h), PAD.top);
          const yBot = Math.min(valToY(Math.max(band.from, yMin), yMin, yMax, h), h - PAD.bottom);
          if (yBot - yTop < 10) return;
          ctx.fillStyle = band.lc;
          ctx.fillText(band.label, w - PAD.right - 5, (yTop + yBot) / 2);
        });
        ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      }

      // waiting message (for fixed-range series with no data yet)
      if (values.length < 2) {
        ctx.fillStyle = "#86a0b5"; ctx.font = "13px system-ui, sans-serif";
        ctx.fillText("Waiting for samples…", PAD.left + 12, PAD.top + 24);
        drawMarkersOnChart(ctx, w, h, xStart, xEnd, yMin, yMax);
        return;
      }

      // zero line for bpm_rate
      if (seriesKey === "bpm_rate" && yMin < 0 && yMax > 0) {
        const y0 = valToY(0, yMin, yMax, h);
        ctx.strokeStyle = "rgba(145,176,206,.25)"; ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.moveTo(PAD.left, y0); ctx.lineTo(w - PAD.right, y0); ctx.stroke();
        ctx.setLineDash([]);
      }

      // y-axis labels
      ctx.fillStyle = "#86a0b5"; ctx.font = "12px system-ui, sans-serif";
      const topLabel = seriesKey === "bpm_rate" ? fmtRate(yMax) : `${Math.round(yMax)}`;
      const botLabel = seriesKey === "bpm_rate" ? fmtRate(yMin) : `${Math.round(yMin)}`;
      ctx.fillText(`${topLabel} ${unit}`, 4, PAD.top + 4);
      ctx.fillText(`${botLabel} ${unit}`, 4, h - PAD.bottom);

      // data line
      if (seriesKey === "bpm_rate") {
        segments.forEach(seg => {
          ctx.lineWidth = 2;
          for (let i = 1; i < seg.length; i++) {
            const a = seg[i-1], b = seg[i];
            ctx.strokeStyle = b[seriesKey] >= 0 ? "#ff9f9f" : "#3ddc97";
            ctx.beginPath();
            ctx.moveTo(tsToX(a.t, xStart, xEnd, w), valToY(a[seriesKey], yMin, yMax, h));
            ctx.lineTo(tsToX(b.t, xStart, xEnd, w), valToY(b[seriesKey], yMin, yMax, h));
            ctx.stroke();
          }
        });
      } else {
        ctx.strokeStyle = color; ctx.lineWidth = 2;
        segments.forEach(seg => {
          ctx.beginPath();
          seg.forEach((sample, i) => {
            const x = tsToX(sample.t, xStart, xEnd, w);
            const y = valToY(sample[seriesKey], yMin, yMax, h);
            i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
          });
          ctx.stroke();
        });
      }

      // x-axis ticks
      ctx.strokeStyle = "rgba(145,176,206,.18)"; ctx.fillStyle = "#86a0b5";
      ctx.font = "11px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "top";
      for (let i = 0; i < 5; i++) {
        const frac = i / 4;
        const ts = xStart + (xEnd - xStart) * frac;
        const x = PAD.left + frac * (w - PAD.left - PAD.right);
        ctx.beginPath(); ctx.moveTo(x, h - PAD.bottom); ctx.lineTo(x, h - PAD.bottom + 6); ctx.stroke();
        ctx.fillText(fmtTime(ts), x, h - PAD.bottom + 8);
      }
      ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      drawMarkersOnChart(ctx, w, h, xStart, xEnd, yMin, yMax);
    }

    function tsToX(t, xStart, xEnd, w) {
      return PAD.left + ((t - xStart) / Math.max(1, xEnd - xStart)) * (w - PAD.left - PAD.right);
    }
    function valToY(v, yMin, yMax, h) {
      return PAD.top + (1 - (v - yMin) / (yMax - yMin)) * (h - PAD.top - PAD.bottom);
    }

    function drawMarkersOnChart(ctx, w, h, xStart, xEnd, yMin, yMax) {
      const all = pendingMarker ? [...state.markers, pendingMarker] : state.markers;
      all.forEach(m => {
        const x = tsToX(m.t, xStart, xEnd, w);
        if (x < PAD.left - 1 || x > w - PAD.right + 1) return;
        const c = m.color || "#ffffff";
        const isPending = m === pendingMarker;
        ctx.save();
        ctx.strokeStyle = c; ctx.lineWidth = 1.5;
        ctx.globalAlpha = isPending ? 0.5 : 0.85;
        ctx.setLineDash([5, 4]);
        ctx.beginPath(); ctx.moveTo(x, PAD.top); ctx.lineTo(x, h - PAD.bottom); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = c; ctx.globalAlpha = isPending ? 0.5 : 1;
        ctx.beginPath(); ctx.arc(x, PAD.top + 6, 4, 0, Math.PI * 2); ctx.fill();
        if (m.label) {
          ctx.font = "bold 11px system-ui, sans-serif";
          ctx.textAlign = "center"; ctx.textBaseline = "bottom";
          const tw = ctx.measureText(m.label).width;
          const px = 4, py = 2;
          ctx.globalAlpha = isPending ? 0.3 : 0.55;
          ctx.fillStyle = "#0d1620";
          ctx.beginPath();
          ctx.roundRect(x - tw/2 - px, PAD.top + 14 - py, tw + px*2, 13 + py*2, 4); ctx.fill();
          ctx.globalAlpha = isPending ? 0.5 : 1;
          ctx.fillStyle = c;
          ctx.fillText(m.label, x, PAD.top + 27);
        }
        ctx.restore();
      });
    }

    function fmtTime(ts) {
      return new Date(ts * 1000).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }
    function formatWindowLabel(s) {
      if (!s) return "All";
      if (s < 60) return `${s}s`;
      if (s % 60 === 0) return `${s / 60}m`;
      return `${s}s`;
    }

    // Marker popup
    let selectedColor = MARKER_COLORS[0].hex;
    const colorContainer = $("mp-colors");
    MARKER_COLORS.forEach(c => {
      const el = document.createElement("div");
      el.className = "mp-swatch" + (c.hex === selectedColor ? " selected" : "");
      el.style.background = c.hex; el.title = c.name;
      el.addEventListener("click", () => {
        selectedColor = c.hex;
        colorContainer.querySelectorAll(".mp-swatch").forEach(s => s.classList.remove("selected"));
        el.classList.add("selected");
        if (pendingMarker) { pendingMarker.color = selectedColor; drawChart(); }
      });
      colorContainer.appendChild(el);
    });

    function openMarkerPopup(clientX, clientY, t) {
      pendingMarker = { t, label: "", color: selectedColor };
      $("marker-label-input").value = "";
      const popup = $("marker-popup");
      popup.classList.add("open");
      const pw = 240, ph = 130;
      let left = clientX + 12, top = clientY - 20;
      if (left + pw > window.innerWidth - 8) left = clientX - pw - 12;
      if (top + ph > window.innerHeight - 8) top = window.innerHeight - ph - 8;
      if (top < 8) top = 8;
      popup.style.left = left + "px"; popup.style.top = top + "px";
      drawChart();
      setTimeout(() => $("marker-label-input").focus(), 30);
    }
    function closeMarkerPopup() { $("marker-popup").classList.remove("open"); pendingMarker = null; drawChart(); }
    function confirmMarker() {
      if (!pendingMarker) return;
      const marker = { t: pendingMarker.t, label: $("marker-label-input").value.trim(), color: selectedColor };
      $("marker-popup").classList.remove("open"); pendingMarker = null;
      fetch("/api/markers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(marker),
      }).then(r => r.json()).then(d => {
        if (d.ok && Array.isArray(d.markers)) { state.markers = d.markers; renderMarkerChips(); drawChart(); }
      });
    }
    $("mp-cancel").addEventListener("click", closeMarkerPopup);
    $("mp-confirm").addEventListener("click", confirmMarker);
    $("marker-label-input").addEventListener("keydown", e => {
      if (e.key === "Enter") confirmMarker();
      if (e.key === "Escape") closeMarkerPopup();
    });
    $("chart").addEventListener("click", e => {
      if ($("marker-popup").classList.contains("open")) return;
      const rect = $("chart").getBoundingClientRect();
      const x = e.clientX - rect.left;
      if (x < PAD.left || x > rect.width - PAD.right) return;
      const { xStart, xEnd } = chartTimeRange();
      const frac = (x - PAD.left) / (rect.width - PAD.left - PAD.right);
      openMarkerPopup(e.clientX, e.clientY, xStart + frac * (xEnd - xStart));
    });
    document.addEventListener("click", e => {
      if (!$("marker-popup").classList.contains("open")) return;
      if (!$("marker-popup").contains(e.target) && e.target !== $("chart")) closeMarkerPopup();
    });

    function renderMarkerChips() {
      const container = $("marker-list");
      container.innerHTML = "";
      state.markers.forEach((m, idx) => {
        const chip = document.createElement("div");
        chip.className = "marker-chip";
        chip.innerHTML = `
          <span class="chip-dot" style="background:${m.color || "#fff"}"></span>
          ${m.label ? `<span>${m.label}</span>` : ""}
          <span class="chip-time">${fmtTime(m.t)}</span>
          <button class="chip-del" title="Eliminar">&#x2715;</button>`;
        chip.querySelector(".chip-del").addEventListener("click", () => deleteMarker(idx));
        container.appendChild(chip);
      });
    }
    function deleteMarker(idx) {
      fetch(`/api/markers/${idx}`, { method: "DELETE" })
        .then(r => r.json())
        .then(d => { if (d.ok && Array.isArray(d.markers)) { state.markers = d.markers; renderMarkerChips(); drawChart(); } });
    }

    document.querySelectorAll(".tabs button").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tabs button").forEach(b => b.classList.remove("active"));
        btn.classList.add("active"); state.series = btn.dataset.series; drawChart();
      });
    });
    document.querySelectorAll(".range-controls button").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".range-controls button").forEach(b => b.classList.remove("active"));
        btn.classList.add("active"); state.windowSeconds = Number(btn.dataset.window); drawChart();
      });
    });

    $("record-start").addEventListener("click", () => {
      fetch("/api/recording/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: $("recording-name").value.trim() || defaultRecordingName() }),
      });
    });
    $("record-stop").addEventListener("click", () => fetch("/api/recording/stop", { method: "POST" }));

    window.addEventListener("resize", drawChart);
    setInterval(drawChart, 1000);

    const events = new EventSource("/events");
    events.onmessage = e => render(JSON.parse(e.data));
    events.onerror = () => {
      $("h9-status").textContent = "H9: disconnected";
      $("h9-dot").className = "status-dot error";
    };
  </script>
</body>
</html>
"""


def calc_rmssd(values):
    if len(values) < 2:
        return None
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    return math.sqrt(sum(d * d for d in diffs) / len(diffs))


def parse_hr_measurement(data: bytearray):
    flags = data[0]
    hr_16bit = flags & 0x01
    energy_present = (flags >> 3) & 0x01
    rr_present = (flags >> 4) & 0x01
    offset = 1
    if hr_16bit:
        bpm = int.from_bytes(data[offset:offset + 2], "little")
        offset += 2
    else:
        bpm = data[offset]; offset += 1
    if energy_present:
        offset += 2
    rr_intervals = []
    if rr_present:
        while offset + 1 < len(data):
            raw = int.from_bytes(data[offset:offset + 2], "little")
            rr_intervals.append(raw * 1000 / 1024)
            offset += 2
    return bpm, rr_intervals


def connect_rfcomm(address, port, timeout=8.0):
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(timeout)
    sock.connect((address, port))
    return sock


def read_exact(sock, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("Bluetooth socket closed")
        chunks.extend(chunk)
    return bytes(chunks)


def make_packet(command, data_length=0):
    return b"\xf9\x2e" + command.to_bytes(2, "little") + data_length.to_bytes(4, "little")


def cargo_command(cargo, packet, response_length=0, transfer=None):
    cargo.send(bytes([len(packet)]) + packet)
    if transfer is not None:
        cargo.send(transfer)
    response = read_exact(cargo, response_length) if response_length else b""
    status = read_exact(cargo, 6)
    if status[:2] != b"\xfe\xa6":
        raise RuntimeError(f"unexpected status packet: {status.hex()}")
    code = int.from_bytes(status[2:6], "little")
    if code:
        raise RuntimeError(f"command {packet.hex()} failed with status 0x{code:08x}")
    return response


def subscribe_sensor(cargo, sensor):
    transfer = bytes([int(sensor)]) + b"\x00\x00\x00\x00" + PUSH_SERVICE_GUID.bytes_le
    cargo_command(cargo, make_packet(0x8F07, data_length=len(transfer)), transfer=transfer)


def unsubscribe_sensor(cargo, sensor):
    transfer = bytes([int(sensor)]) + b"\x00\x00\x00\x00" + PUSH_SERVICE_GUID.bytes_le
    cargo_command(cargo, make_packet(0x8F08, data_length=len(transfer)), transfer=transfer)


class FusionCollector:
    def __init__(self, h9_address=None, b2_address=None):
        self.h9_address = h9_address
        self.b2_address = b2_address
        self.lock = threading.RLock()
        self.clients = []
        # H9 RR buffers
        self.rr_buffer = deque(maxlen=RR_WINDOW)
        self.rr_buffer_clean = deque(maxlen=RR_WINDOW)
        self.bpm_history = deque(maxlen=BPM_RATE_WINDOW)
        # History + recording
        self.history = deque()
        self.markers = []
        self.recording_active = False
        self.recording_file = None
        self.recording_started_at = None
        self.recording_samples = []
        self.recording_markers = []
        self.state = {
            "h9_state": "stopped",
            "h9_address": h9_address,
            "b2_state": "stopped" if b2_address else "disabled",
            "b2_address": b2_address,
            "error": None,
            # H9
            "bpm": None,
            "bpm_rate": None,
            "rr_ms": None,
            "rr_ms_raw": None,
            "rmssd_ms": None,
            "rmssd_ms_raw": None,
            "rr_count": 0,
            "h9_motion_flag": False,
            # Band2
            "skin_temp_c": None,
            "gsr_kohm": None,
            "accel_x_g": None,
            "accel_y_g": None,
            "accel_z_g": None,
            "accel_mag_g": None,
            "gyro_x_dps": None,
            "gyro_y_dps": None,
            "gyro_z_dps": None,
            "gyro_mag_dps": None,
            "wearing": None,
            "battery_pct": None,
            # meta
            "updated_at": None,
            "recording_active": False,
            "recording_file": None,
        }

    def _is_valid_rr(self, rr_ms):
        if rr_ms < RR_MIN_MS or rr_ms > RR_MAX_MS:
            return False
        if len(self.rr_buffer_clean) >= 3:
            recent = list(self.rr_buffer_clean)[-5:]
            median = sorted(recent)[len(recent) // 2]
            if median > 0 and abs(rr_ms - median) / median > RR_ECTOPIC_THRESHOLD:
                return False
        return True

    def update_h9(self, bpm, rr_intervals):
        now = time.time()
        with self.lock:
            self.state["bpm"] = bpm

            # motion flag from Band2 accel
            accel = self.state["accel_mag_g"]
            self.state["h9_motion_flag"] = (accel is not None and accel > MOTION_THRESHOLD_G)

            if rr_intervals:
                self.rr_buffer.extend(rr_intervals)
                self.state["rr_ms_raw"] = rr_intervals[-1]
                self.state["rmssd_ms_raw"] = calc_rmssd(list(self.rr_buffer))
                last_valid = None
                for rr in rr_intervals:
                    if self._is_valid_rr(rr):
                        self.rr_buffer_clean.append(rr)
                        last_valid = rr
                if last_valid is not None:
                    self.state["rr_ms"] = last_valid
                self.state["rmssd_ms"] = calc_rmssd(list(self.rr_buffer_clean))
                self.state["rr_count"] = len(self.rr_buffer_clean)

            self.bpm_history.append((now, bpm))
            if len(self.bpm_history) >= 2:
                t0, b0 = self.bpm_history[0]; t1, b1 = self.bpm_history[-1]
                dt = t1 - t0
                self.state["bpm_rate"] = round((b1 - b0) / dt, 3) if dt > 0 else 0.0

            self.state["updated_at"] = now
            sample = self._build_sample(now)
            self.history.append(sample)
            self._prune_history_locked(now)
            if self.recording_active:
                self.recording_samples.append(dict(sample))

        self.broadcast(sample)

    def update_b2_sensor(self, sensor):
        if not BAND2_AVAILABLE:
            return
        now = time.time()
        with self.lock:
            sensor_type = sensor.subscription_type
            if sensor_type == Sensor.SkinTemperature:
                if hasattr(sensor, "value"):
                    self.state["skin_temp_c"] = sensor.value
            elif sensor_type == Sensor.Gsr200MS:
                if hasattr(sensor, "value"):
                    self.state["gsr_kohm"] = sensor.value
            elif sensor_type in (Sensor.AccelerometerGyroscope32MS,):
                if hasattr(sensor, "acceleration_x"):
                    ax = sensor.acceleration_x
                    ay = sensor.acceleration_y
                    az = sensor.acceleration_z
                    gx = sensor.velocity_x
                    gy = sensor.velocity_y
                    gz = sensor.velocity_z
                    self.state["accel_x_g"] = ax
                    self.state["accel_y_g"] = ay
                    self.state["accel_z_g"] = az
                    self.state["accel_mag_g"] = math.sqrt(ax*ax + ay*ay + az*az)
                    self.state["gyro_x_dps"] = gx
                    self.state["gyro_y_dps"] = gy
                    self.state["gyro_z_dps"] = gz
                    self.state["gyro_mag_dps"] = math.sqrt(gx*gx + gy*gy + gz*gz)
            elif sensor_type == Sensor.DeviceContact:
                if hasattr(sensor, "value"):
                    self.state["wearing"] = sensor.value
            elif sensor_type == Sensor.BatteryGauge:
                if hasattr(sensor, "value"):
                    self.state["battery_pct"] = sensor.value
            else:
                return
            self.state["updated_at"] = now

        self.broadcast()

    def set_h9_state(self, status, error=None):
        with self.lock:
            self.state["h9_state"] = status
            if error:
                self.state["error"] = error
        self.broadcast()

    def set_b2_state(self, status, error=None):
        with self.lock:
            self.state["b2_state"] = status
            if error:
                self.state["error"] = error
        self.broadcast()

    def _build_sample(self, t):
        return {
            "t": t,
            "bpm": self.state["bpm"],
            "bpm_rate": self.state["bpm_rate"],
            "rr_ms": self.state["rr_ms"],
            "rr_ms_raw": self.state["rr_ms_raw"],
            "rmssd_ms": self.state["rmssd_ms"],
            "rmssd_ms_raw": self.state["rmssd_ms_raw"],
            "h9_motion_flag": self.state["h9_motion_flag"],
            "skin_temp_c": self.state["skin_temp_c"],
            "gsr_kohm": self.state["gsr_kohm"],
            "accel_mag_g": self.state["accel_mag_g"],
            "gyro_mag_dps": self.state["gyro_mag_dps"],
            "accel_x_g": self.state["accel_x_g"],
            "accel_y_g": self.state["accel_y_g"],
            "accel_z_g": self.state["accel_z_g"],
            "wearing": self.state["wearing"],
            "battery_pct": self.state["battery_pct"],
        }

    def _prune_history_locked(self, now):
        cutoff = now - HISTORY_SECONDS
        while self.history and self.history[0]["t"] < cutoff:
            self.history.popleft()
        while len(self.history) > HISTORY_MAX_SAMPLES:
            self.history.popleft()

    def snapshot(self, include_history=True, sample=None):
        with self.lock:
            data = dict(self.state)
            if include_history:
                data["history"] = list(self.history)
            if sample is not None:
                data["sample"] = sample
            data["recording_active"] = self.recording_active
            data["recording_file"] = self.recording_file
            data["markers"] = list(self.markers)
            return data

    def add_marker(self, marker):
        with self.lock:
            entry = {
                "t": float(marker.get("t", time.time())),
                "label": str(marker.get("label", ""))[:60],
                "color": str(marker.get("color", "#ffffff"))[:20],
            }
            self.markers.append(entry)
            self.markers.sort(key=lambda m: m["t"])
            if self.recording_active:
                self.recording_markers = list(self.markers)
            markers = list(self.markers)
        self.broadcast()
        return markers

    def delete_marker(self, idx):
        with self.lock:
            if idx < 0 or idx >= len(self.markers):
                return None
            self.markers.pop(idx)
            if self.recording_active:
                self.recording_markers = list(self.markers)
            markers = list(self.markers)
        self.broadcast()
        return markers

    def start_recording(self, requested_name=None):
        with self.lock:
            if self.recording_active:
                return False, self.recording_file
            RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
            started_at = time.time()
            if requested_name:
                file_name = requested_name.strip()
            else:
                stamp = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime(started_at))
                file_name = f"fusion-{stamp}.json"
            if not file_name.endswith(".json"):
                file_name += ".json"
            file_name = file_name.replace("/", "_")
            self.recording_active = True
            self.recording_started_at = started_at
            self.recording_file = file_name
            self.recording_samples = [self._build_sample(started_at)]
            self.recording_markers = []
            self.state["recording_active"] = True
            self.state["recording_file"] = file_name
        self.broadcast()
        return True, file_name

    def stop_recording(self, reason="manual"):
        with self.lock:
            if not self.recording_active:
                return None
            ended_at = time.time()
            payload = {
                "version": 1,
                "h9_address": self.h9_address,
                "b2_address": self.b2_address,
                "started_at": self.recording_started_at,
                "ended_at": ended_at,
                "reason": reason,
                "markers": list(self.recording_markers),
                "samples": list(self.recording_samples),
            }
            path = RECORDINGS_DIR / self.recording_file
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            self.recording_active = False
            self.recording_samples = []
            self.recording_markers = []
            self.state["recording_active"] = False
        self.broadcast()
        return path

    def add_client(self):
        client = queue.Queue(maxsize=16)
        with self.lock:
            self.clients.append(client)
        client.put(self.snapshot(include_history=True))
        return client

    def remove_client(self, client):
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)

    def broadcast(self, sample=None):
        data = self.snapshot(include_history=False, sample=sample)
        with self.lock:
            clients = list(self.clients)
        for client in clients:
            try:
                if client.full():
                    client.get_nowait()
                client.put_nowait(data)
            except queue.Full:
                pass


class H9Thread:
    def __init__(self, collector: FusionCollector):
        self.collector = collector
        self._stop = threading.Event()
        self._loop = None
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_loop(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connect_loop())
        finally:
            loop.close()

    async def _connect_loop(self):
        while not self._stop.is_set():
            try:
                await self._connect_once()
            except Exception as exc:
                if not self._stop.is_set():
                    self.collector.set_h9_state("reconnecting", str(exc))
                    await asyncio.sleep(RECONNECT_SECONDS)
            if self._stop.is_set():
                break

    async def _connect_once(self):
        addr = self.collector.h9_address
        if not addr:
            self.collector.set_h9_state("scanning")
            devices = await BleakScanner.discover(timeout=10)
            for d in devices:
                if d.name and "Polar H9" in d.name:
                    addr = d.address
                    self.collector.h9_address = addr
                    with self.collector.lock:
                        self.collector.state["h9_address"] = addr
                    break
            if not addr:
                raise RuntimeError("Polar H9 not found during scan")

        self.collector.set_h9_state("connecting")

        def hr_handler(sender, data):
            bpm, rr_intervals = parse_hr_measurement(bytearray(data))
            self.collector.update_h9(bpm, rr_intervals)

        async with BleakClient(addr) as client:
            self.collector.set_h9_state("connected")
            await client.start_notify(HR_UUID, hr_handler)
            while not self._stop.is_set() and client.is_connected:
                await asyncio.sleep(1)
            await client.stop_notify(HR_UUID)

        if not self._stop.is_set():
            raise ConnectionError("BLE client disconnected")


class Band2Thread:
    def __init__(self, collector: FusionCollector):
        self.collector = collector
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def process_push_buffer(self, buffer):
        while len(buffer) >= 6:
            if buffer[:2] != b"\x01\x00":
                start = buffer.find(b"\x01\x00", 1)
                if start == -1:
                    buffer.clear(); break
                del buffer[:start]
                if len(buffer) < 6:
                    break

            packet_length = int.from_bytes(buffer[2:6], "little")
            frame_length = 2 + 4 + packet_length
            if packet_length <= 0 or packet_length > 512:
                del buffer[0]; continue
            if len(buffer) < frame_length:
                break

            packet = bytes(buffer[:frame_length])
            del buffer[:frame_length]

            records = packet[6:]
            offset = 0
            while offset + 4 <= len(records):
                sample_size = int.from_bytes(records[offset + 2:offset + 4], "little")
                record_length = 4 + sample_size
                if sample_size <= 0 or offset + record_length > len(records):
                    break
                record = records[offset:offset + record_length]
                legacy_packet = packet[:2] + record_length.to_bytes(4, "little") + record
                offset += record_length
                sensor = decode_sensor_reading(legacy_packet)
                self.collector.update_b2_sensor(sensor)

    def run(self):
        addr = self.collector.b2_address
        while not self._stop.is_set():
            cargo = push = None
            try:
                self.collector.set_b2_state("connecting")
                cargo = connect_rfcomm(addr, CARGO_PORT)
                push = connect_rfcomm(addr, PUSH_PORT)
                push.settimeout(1.0)

                for sensor in ALL_BAND2_SENSORS:
                    try:
                        unsubscribe_sensor(cargo, sensor)
                    except Exception:
                        pass
                for sensor in BAND2_SENSORS:
                    try:
                        subscribe_sensor(cargo, sensor)
                    except Exception:
                        pass

                self.collector.set_b2_state("connected")
                buffer = bytearray()
                while not self._stop.is_set():
                    try:
                        chunk = push.recv(8192)
                    except (TimeoutError, socket.timeout):
                        continue
                    if not chunk:
                        raise ConnectionError("push socket closed")
                    buffer.extend(chunk)
                    self.process_push_buffer(buffer)
            except Exception as exc:
                if not self._stop.is_set():
                    self.collector.set_b2_state("reconnecting", str(exc))
            finally:
                for sock in (push, cargo):
                    if sock:
                        try:
                            sock.close()
                        except OSError:
                            pass
            if self._stop.is_set():
                break
            time.sleep(RECONNECT_SECONDS)


class DashboardHandler(BaseHTTPRequestHandler):
    collector = None

    def log_message(self, fmt, *args):
        return

    def send_json(self, data, status=HTTPStatus.OK):
        payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            payload = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/api/current":
            self.send_json(self.collector.snapshot())
            return

        if path == "/events":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            client = self.collector.add_client()
            try:
                while True:
                    try:
                        data = client.get(timeout=15)
                        payload = f"data: {json.dumps(data, separators=(',', ':'))}\n\n"
                    except queue.Empty:
                        payload = ": keepalive\n\n"
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                self.collector.remove_client(client)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/markers/"):
            try:
                idx = int(path.split("/")[-1])
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            markers = self.collector.delete_marker(idx)
            if markers is None:
                self.send_json({"ok": False, "error": "index out of range"}, HTTPStatus.BAD_REQUEST)
            else:
                self.send_json({"ok": True, "markers": markers})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/markers":
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length else b"{}"
            try:
                marker = json.loads(body.decode("utf-8"))
            except Exception:
                marker = {}
            self.send_json({"ok": True, "markers": self.collector.add_marker(marker)})
            return

        if path == "/api/recording/start":
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                payload = {}
            started, file_name = self.collector.start_recording(payload.get("name"))
            self.send_json({"ok": True, "started": started, "file": file_name})
            return

        if path == "/api/recording/stop":
            p = self.collector.stop_recording()
            self.send_json({"ok": True, "file": p.name if p else None})
            return

        self.send_error(HTTPStatus.NOT_FOUND)


def main():
    parser = argparse.ArgumentParser(description="Fusion dashboard: Polar H9 + Microsoft Band 2")
    parser.add_argument("h9_address", nargs="?", default="A0:9E:1A:DD:B3:D7", help="Polar H9 BLE MAC")
    parser.add_argument("--band2", metavar="MAC", default="58:82:A8:CE:4E:C8", help="Microsoft Band 2 Bluetooth MAC")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.band2 and not BAND2_AVAILABLE:
        print("ERROR: --band2 specified but libband could not be imported.")
        print(f"       Expected at: {_LIBBAND_PATH}")
        sys.exit(1)

    collector = FusionCollector(h9_address=args.h9_address, b2_address=args.band2)
    DashboardHandler.collector = collector

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)

    print(f"Dashboard: http://{args.host}:{args.port}")
    print(f"Polar H9:  {args.h9_address}")
    print(f"Band 2:    {args.band2}")

    h9_thread = H9Thread(collector)
    h9_thread.start()

    b2_thread = None
    if args.band2:
        b2_thread = Band2Thread(collector)
        b2_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        h9_thread.stop()
        if b2_thread:
            b2_thread.stop()
        server.server_close()


if __name__ == "__main__":
    main()
