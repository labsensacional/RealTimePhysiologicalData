#!/usr/bin/env python3
"""
Local web dashboard for Microsoft Band 2 live physiological data.

Usage:
    python3 band2_dashboard.py 58:82:A8:CE:4E:C8
    open http://127.0.0.1:8000
"""
import argparse
import json
import math
import queue
import socket
import threading
import time
import uuid
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from libband.sensors import Sensor, decode_sensor_reading


CARGO_PORT = 4
PUSH_PORT = 5
RR_WINDOW = 20
RECONNECT_SECONDS = 5
HISTORY_SECONDS = 20 * 60
HISTORY_MAX_SAMPLES = 120000
PUSH_SERVICE_GUID = uuid.UUID(hex="d8895bfd0461400dbd52dbe2a3c33021")
RECORDINGS_DIR = Path(__file__).resolve().with_name("recordings")

ALL_SENSORS = (
    Sensor.HeartRate,
    Sensor.RRInterval,
    Sensor.Gsr,
    Sensor.Gsr200MS,
    Sensor.SkinTemperature,
    Sensor.Accelerometer32MS,
    Sensor.AccelerometerGyroscope32MS,
    Sensor.DeviceContact,
    Sensor.BatteryGauge,
    Sensor.Pedometer,
    Sensor.PedometerWithDailyValues,
    Sensor.Distance,
    Sensor.DistanceWithDailyValues,
    Sensor.Calories1S,
    Sensor.UV,
    Sensor.AmbientLight,
    Sensor.AmbientLightWithDailyValues,
    Sensor.Barometer,
    Sensor.Elevation,
    Sensor.ElevationWithDailyValues,
)
SENSOR_BY_NAME = {s.name: s for s in ALL_SENSORS}


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Band 2 Live</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #081018;
      --bg2: #0d1620;
      --panel: rgba(13, 22, 32, .88);
      --panel-strong: #111b27;
      --ink: #e7eef7;
      --muted: #86a0b5;
      --line: rgba(145, 176, 206, .16);
      --blue: #49a2ff;
      --blue-soft: rgba(73, 162, 255, .14);
      --green: #3ddc97;
      --green-soft: rgba(61, 220, 151, .16);
      --red: #ff6b6b;
      --red-soft: rgba(255, 107, 107, .16);
      --amber: #ffbf4d;
      --amber-soft: rgba(255, 191, 77, .16);
      --teal: #47d7ff;
      --teal-soft: rgba(71, 215, 255, .16);
      --shadow: 0 10px 30px rgba(0, 0, 0, .35);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 Inter, "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background-image:
        linear-gradient(180deg, rgba(73, 162, 255, .04), transparent 28%),
        linear-gradient(90deg, rgba(255, 255, 255, .03) 1px, transparent 1px),
        linear-gradient(rgba(255, 255, 255, .03) 1px, transparent 1px);
      background-size: auto, 44px 44px, 44px 44px;
    }

    header {
      min-height: 64px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 24px;
      border-bottom: 1px solid var(--line);
      background: rgba(8, 16, 24, .9);
      backdrop-filter: blur(18px);
      position: sticky;
      top: 0;
      z-index: 2;
    }

    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }

    .header-meta {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
      color: var(--muted);
      font-size: 13px;
    }

    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--amber);
      box-shadow: 0 0 0 4px rgba(255, 191, 77, .08);
    }

    .status-dot.connected {
      background: var(--green);
      box-shadow: 0 0 0 4px rgba(61, 220, 151, .10);
    }

    .status-dot.error {
      background: var(--red);
      box-shadow: 0 0 0 4px rgba(255, 107, 107, .10);
    }

    button {
      appearance: none;
      border: 1px solid rgba(145, 176, 206, .18);
      background: rgba(17, 27, 39, .8);
      color: var(--ink);
      border-radius: 6px;
      height: 34px;
      padding: 0 12px;
      font: inherit;
      cursor: pointer;
      transition: background-color .15s ease, border-color .15s ease, color .15s ease, transform .15s ease;
    }

    button:hover {
      border-color: rgba(73, 162, 255, .35);
      background: rgba(20, 35, 51, .95);
    }

    button.primary {
      border-color: rgba(73, 162, 255, .45);
      background: linear-gradient(180deg, rgba(73, 162, 255, .98), rgba(37, 112, 214, .98));
      color: #f7fbff;
      box-shadow: 0 0 0 4px rgba(73, 162, 255, .10);
    }

    .recording-name {
      height: 34px;
      min-width: 240px;
      max-width: 360px;
      border: 1px solid rgba(145, 176, 206, .18);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
      color: var(--ink);
      background: rgba(17, 27, 39, .85);
    }

    .recording-name:focus {
      outline: 2px solid rgba(73, 162, 255, .22);
      border-color: rgba(73, 162, 255, .48);
    }

    main {
      width: min(1440px, 100%);
      margin: 0 auto;
      padding: 22px 24px 28px;
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(6, minmax(140px, 1fr));
      gap: 12px;
    }

    .metric {
      min-height: 112px;
      background: var(--panel);
      border: 1px solid rgba(145, 176, 206, .14);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 14px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
      overflow: hidden;
    }

    .metric::before {
      content: "";
      position: absolute;
      inset: 0 auto auto 0;
      width: 100%;
      height: 2px;
      background: linear-gradient(90deg, transparent, rgba(255, 255, 255, .08), transparent);
      pointer-events: none;
    }

    .metric .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
      white-space: nowrap;
    }

    .metric .value {
      font-size: clamp(28px, 4vw, 44px);
      line-height: 1;
      font-weight: 750;
      letter-spacing: 0;
      overflow-wrap: anywhere;
      color: var(--ink);
    }

    .metric .unit {
      color: var(--muted);
      font-size: 13px;
    }

    .metric .subvalue {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .content {
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) minmax(320px, .9fr);
      gap: 14px;
      margin-top: 14px;
      align-items: start;
    }

    .panel {
      background: var(--panel);
      border: 1px solid rgba(145, 176, 206, .14);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
      backdrop-filter: blur(12px);
    }

    .panel-head {
      height: 48px;
      padding: 0 14px;
      border-bottom: 1px solid rgba(145, 176, 206, .14);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .panel-title {
      font-weight: 700;
      font-size: 14px;
    }

    .tabs {
      display: flex;
      gap: 4px;
      border: 1px solid rgba(145, 176, 206, .14);
      border-radius: 6px;
      padding: 2px;
      background: rgba(17, 27, 39, .72);
    }

    .tabs button {
      height: 28px;
      padding: 0 10px;
      border: 0;
      background: transparent;
      color: var(--muted);
    }

    .tabs button.active {
      background: rgba(255, 255, 255, .06);
      color: var(--ink);
      box-shadow: inset 0 0 0 1px rgba(73, 162, 255, .22);
    }

    .range-controls {
      display: flex;
      gap: 4px;
      flex-wrap: wrap;
      align-items: center;
      justify-content: flex-end;
    }

    .range-controls button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 54px;
      height: 28px;
      padding: 0 12px;
      border: 1px solid rgba(145, 176, 206, .14);
      background: rgba(17, 27, 39, .72);
      color: var(--muted);
      border-radius: 6px;
      cursor: pointer;
    }

    .range-controls button.active {
      background: rgba(255, 255, 255, .06);
      color: var(--ink);
      box-shadow: inset 0 0 0 1px rgba(71, 215, 255, .22);
    }

    canvas {
      display: block;
      width: 100%;
      height: 360px;
    }

    .error-text {
      color: var(--red);
      max-width: 720px;
      overflow-wrap: anywhere;
      margin-top: 12px;
    }

    #recording-status {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }

    #recording-status.active {
      color: var(--green);
      font-weight: 650;
    }

    .metric .value.good,
    .metric .subvalue.good { color: var(--green); }

    .metric .value.warn,
    .metric .subvalue.warn { color: var(--amber); }

    .metric .value.bad,
    .metric .subvalue.bad { color: var(--red); }

    .metric.state-good { box-shadow: 0 0 0 1px rgba(61, 220, 151, .18), var(--shadow); }
    .metric.state-warn { box-shadow: 0 0 0 1px rgba(255, 191, 77, .18), var(--shadow); }
    .metric.state-bad { box-shadow: 0 0 0 1px rgba(255, 107, 107, .18), var(--shadow); }

    .metric.state-good::before { background: linear-gradient(90deg, transparent, rgba(61, 220, 151, .95), transparent); }
    .metric.state-warn::before { background: linear-gradient(90deg, transparent, rgba(255, 191, 77, .95), transparent); }
    .metric.state-bad::before { background: linear-gradient(90deg, transparent, rgba(255, 107, 107, .95), transparent); }

    @media (max-width: 980px) {
      .metrics { grid-template-columns: repeat(2, minmax(150px, 1fr)); }
      .content { grid-template-columns: 1fr; }
    }

    @media (max-width: 560px) {
      header { align-items: flex-start; flex-direction: column; padding: 14px 16px; }
      .header-meta { justify-content: flex-start; }
      main { padding: 14px 16px 22px; }
      .metrics { grid-template-columns: 1fr; }
      canvas { height: 300px; }
    }

    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, .72);
      z-index: 200;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .modal {
      background: var(--panel-strong);
      border: 1px solid rgba(145, 176, 206, .2);
      border-radius: 10px;
      box-shadow: 0 24px 64px rgba(0, 0, 0, .55);
      width: min(540px, calc(100vw - 32px));
      max-height: calc(100vh - 64px);
      display: flex;
      flex-direction: column;
    }

    .modal-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
    }

    .modal-head h2 {
      margin: 0;
      font-size: 15px;
      font-weight: 700;
    }

    .modal-head button {
      height: 28px;
      width: 28px;
      padding: 0;
      border-color: transparent;
    }

    .modal-body {
      overflow-y: auto;
      padding: 14px 18px;
      flex: 1;
    }

    .sensor-group { margin-bottom: 14px; }

    .sensor-group-title {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: var(--muted);
      margin-bottom: 6px;
    }

    .sensor-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 5px 0;
      border-bottom: 1px solid rgba(145, 176, 206, .06);
    }

    .sensor-row label { cursor: pointer; flex: 1; font-size: 13px; }

    .sensor-row input[type=checkbox] {
      accent-color: var(--blue);
      width: 15px;
      height: 15px;
      cursor: pointer;
    }

    .modal-footer {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 18px;
      border-top: 1px solid var(--line);
      justify-content: flex-end;
    }
  </style>
</head>
<body>
  <header>
    <h1>Microsoft Band 2 Live</h1>
    <div class="header-meta">
      <span class="status-dot" id="dot"></span>
      <span id="status">Connecting</span>
      <span id="updated">No samples yet</span>
      <span id="recording-status">Not recording</span>
      <input class="recording-name" id="recording-name" type="text" value="">
      <button id="connect-btn">Iniciar conexion</button>
      <button class="primary" id="record-start">Iniciar sesion</button>
      <button id="record-stop">Detener grabacion</button>
    </div>
  </header>

  <main>
    <section class="metrics">
      <article class="metric" data-sensors="HeartRate">
        <div class="label">Heart Rate</div>
        <div><span class="value" id="bpm">--</span> <span class="unit">bpm</span></div>
      </article>
      <article class="metric" data-sensors="RRInterval">
        <div class="label">RR Interval</div>
        <div><span class="value" id="rr">--</span> <span class="unit">ms</span></div>
      </article>
      <article class="metric" data-sensors="RRInterval">
        <div class="label">RMSSD</div>
        <div><span class="value" id="rmssd">--</span> <span class="unit">ms</span></div>
      </article>
      <article class="metric" data-sensors="Gsr">
        <div class="label">GSR</div>
        <div><span class="value" id="gsr">--</span> <span class="unit">kOhm</span></div>
      </article>
      <article class="metric" data-sensors="SkinTemperature">
        <div class="label">Skin Temp</div>
        <div><span class="value" id="temp">--</span> <span class="unit">C</span></div>
      </article>
      <article class="metric" data-sensors="Accelerometer32MS,AccelerometerGyroscope32MS">
        <div class="label">Motion</div>
        <div><span class="value" id="accel">--</span> <span class="unit">g</span></div>
        <div class="subvalue" id="accel-axes">x -- · y -- · z --</div>
      </article>
      <article class="metric" data-sensors="AccelerometerGyroscope32MS">
        <div class="label">Gyro</div>
        <div><span class="value" id="gyro">--</span> <span class="unit">dps</span></div>
        <div class="subvalue" id="gyro-axes">x -- · y -- · z --</div>
      </article>
      <article class="metric" data-sensors="DeviceContact">
        <div class="label">Wearing</div>
        <div><span class="value" id="contact">--</span></div>
      </article>
      <article class="metric" data-sensors="DeviceContact">
        <div class="label">Band Conn</div>
        <div><span class="value" id="band-connected">--</span></div>
      </article>
      <article class="metric" data-sensors="BatteryGauge">
        <div class="label">Battery</div>
        <div><span class="value" id="battery">--</span> <span class="unit">%</span></div>
        <div class="subvalue" id="battery-voltage">-- mV</div>
      </article>
      <article class="metric" data-sensors="Pedometer,PedometerWithDailyValues">
        <div class="label">Steps</div>
        <div><span class="value" id="steps">--</span></div>
        <div class="subvalue" id="steps-today">today --</div>
      </article>
      <article class="metric" data-sensors="Distance,DistanceWithDailyValues">
        <div class="label">Distance</div>
        <div><span class="value" id="distance">--</span> <span class="unit">m</span></div>
        <div class="subvalue" id="pace">pace -- · speed --</div>
      </article>
      <article class="metric" data-sensors="Calories1S">
        <div class="label">Calories</div>
        <div><span class="value" id="calories">--</span> <span class="unit">cal</span></div>
      </article>
      <article class="metric" data-sensors="UV">
        <div class="label">UV</div>
        <div><span class="value" id="uv">--</span></div>
      </article>
      <article class="metric" data-sensors="AmbientLight,AmbientLightWithDailyValues">
        <div class="label">Ambient</div>
        <div><span class="value" id="ambient">--</span> <span class="unit">lx</span></div>
      </article>
      <article class="metric" data-sensors="Barometer">
        <div class="label">Barometer</div>
        <div><span class="value" id="pressure">--</span> <span class="unit">hPa</span></div>
        <div class="subvalue" id="baro-temp">temp -- C</div>
      </article>
      <article class="metric" data-sensors="Elevation,ElevationWithDailyValues">
        <div class="label">Elevation</div>
        <div><span class="value" id="elevation">--</span> <span class="unit">m gain</span></div>
        <div class="subvalue" id="elevation-detail">loss -- · rate --</div>
      </article>
      <article class="metric" data-sensors="Gsr200MS">
        <div class="label">GSR 200ms</div>
        <div><span class="value" id="gsr-fast">--</span> <span class="unit">kOhm</span></div>
      </article>
    </section>

    <section class="content">
      <article class="panel">
        <div class="panel-head">
          <div class="panel-title" id="chart-title">Heart Rate</div>
          <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; justify-content:flex-end;">
            <div class="tabs">
              <button class="active" data-series="bpm" data-sensors="HeartRate">BPM</button>
              <button data-series="rr_ms" data-sensors="RRInterval">RR</button>
              <button data-series="rmssd_ms" data-sensors="RRInterval">HRV</button>
              <button data-series="gsr_kohm" data-sensors="Gsr">GSR</button>
              <button data-series="skin_temp_c" data-sensors="SkinTemperature">Temp</button>
              <button data-series="accel_mag_g" data-sensors="Accelerometer32MS,AccelerometerGyroscope32MS">Accel</button>
              <button data-series="gyro_mag_dps" data-sensors="AccelerometerGyroscope32MS">Gyro</button>
              <button data-series="steps" data-sensors="Pedometer,PedometerWithDailyValues">Steps</button>
              <button data-series="battery_pct" data-sensors="BatteryGauge">Battery</button>
              <button data-series="ambient_light" data-sensors="AmbientLight,AmbientLightWithDailyValues">Light</button>
              <button data-series="barometer_hpa" data-sensors="Barometer">Pressure</button>
              <button data-series="elevation_gain_m" data-sensors="Elevation,ElevationWithDailyValues">Elev</button>
              <button data-series="gsr_200ms_kohm" data-sensors="Gsr200MS">GSR 200</button>
            </div>
            <div class="range-controls">
              <button class="active" data-window="10">10s</button>
              <button data-window="30">30s</button>
              <button data-window="60">1m</button>
              <button data-window="600">10m</button>
              <button data-window="0">All</button>
            </div>
          </div>
        </div>
        <canvas id="chart" width="900" height="360"></canvas>
      </article>
    </section>
    <p class="error-text" id="error"></p>
  </main>

  <div id="sensor-modal" class="modal-overlay" style="display:none">
    <div class="modal">
      <div class="modal-head">
        <h2>Sensores a suscribir</h2>
        <button id="modal-close">&#x2715;</button>
      </div>
      <div class="modal-body" id="sensor-list"></div>
      <div class="modal-footer">
        <button id="select-all-btn">Todos</button>
        <button id="select-none-btn">Ninguno</button>
        <button class="primary" id="modal-start-btn">Iniciar</button>
      </div>
    </div>
  </div>

  <script>
    const SENSOR_OPTIONS = [
      { name: "HeartRate", label: "Heart Rate", group: "Cardíaco" },
      { name: "RRInterval", label: "RR Interval (HRV)", group: "Cardíaco" },
      { name: "Gsr", label: "GSR (galvánico)", group: "Piel" },
      { name: "Gsr200MS", label: "GSR 200ms", group: "Piel" },
      { name: "SkinTemperature", label: "Temperatura cutánea", group: "Piel" },
      { name: "Accelerometer32MS", label: "Acelerómetro", group: "Movimiento" },
      { name: "AccelerometerGyroscope32MS", label: "Accel + Giroscopio", group: "Movimiento" },
      { name: "DeviceContact", label: "Contacto con piel", group: "Dispositivo" },
      { name: "BatteryGauge", label: "Batería", group: "Dispositivo" },
      { name: "Pedometer", label: "Podómetro", group: "Actividad" },
      { name: "PedometerWithDailyValues", label: "Podómetro (diario)", group: "Actividad" },
      { name: "Distance", label: "Distancia", group: "Actividad" },
      { name: "DistanceWithDailyValues", label: "Distancia (diaria)", group: "Actividad" },
      { name: "Calories1S", label: "Calorías", group: "Actividad" },
      { name: "UV", label: "UV", group: "Ambiente" },
      { name: "AmbientLight", label: "Luz ambiente", group: "Ambiente" },
      { name: "AmbientLightWithDailyValues", label: "Luz ambiente (diaria)", group: "Ambiente" },
      { name: "Barometer", label: "Barómetro", group: "Ambiente" },
      { name: "Elevation", label: "Elevación", group: "Ambiente" },
      { name: "ElevationWithDailyValues", label: "Elevación (diaria)", group: "Ambiente" },
    ];

    const state = {
      series: "bpm",
      windowSeconds: 10,
      history: [],
      wearingGaps: [],
      recordingActive: false,
      recordingFile: null,
      wearing: null,
      bandConnected: null,
      selectedSensors: null,
      labels: {
        bpm: ["Heart Rate", "bpm", "#1f6feb"],
        rr_ms: ["RR Interval", "ms", "#087f8c"],
        rmssd_ms: ["RMSSD", "ms", "#21845a"],
        gsr_kohm: ["GSR", "kOhm", "#a66b00"],
        skin_temp_c: ["Skin Temperature", "C", "#c44536"],
        accel_mag_g: ["Acceleration Magnitude", "g", "#6f42c1"],
        gyro_mag_dps: ["Gyroscope Magnitude", "dps", "#805ad5"],
        steps: ["Steps", "steps", "#2f855a"],
        battery_pct: ["Battery", "%", "#2b6cb0"],
        ambient_light: ["Ambient Light", "lx", "#975a16"],
        barometer_hpa: ["Air Pressure", "hPa", "#4a5568"],
        elevation_gain_m: ["Elevation Gain", "m", "#276749"],
        gsr_200ms_kohm: ["GSR 200ms", "kOhm", "#744210"]
      }
    };

    const $ = (id) => document.getElementById(id);
    const fmt0 = (v) => Number.isFinite(v) ? Math.round(v).toString() : "--";
    const fmt1 = (v) => Number.isFinite(v) ? v.toFixed(1) : "--";
    const fmt2 = (v) => Number.isFinite(v) ? v.toFixed(2) : "--";
    const CLIENT_HISTORY_SECONDS = 20 * 60;
    const MAX_CLIENT_HISTORY = 50000;
    const MAX_PLOT_POINTS = 2500;

    function defaultRecordingName() {
      const now = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      return `sesion-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}.json`;
    }

    $("recording-name").value = defaultRecordingName();

    function trimHistory() {
      if (!state.history.length) return;
      const newest = state.history[state.history.length - 1];
      const cutoff = Number.isFinite(newest && newest.t)
        ? newest.t - CLIENT_HISTORY_SECONDS
        : (Date.now() / 1000) - CLIENT_HISTORY_SECONDS;
      while (state.history.length && Number.isFinite(state.history[0].t) && state.history[0].t < cutoff) {
        state.history.shift();
      }
      if (state.history.length > MAX_CLIENT_HISTORY) {
        state.history = state.history.slice(-MAX_CLIENT_HISTORY);
      }
    }

    function appendSample(sample) {
      if (!sample || !Number.isFinite(sample.t)) return;
      const last = state.history[state.history.length - 1];
      if (!last || last.t !== sample.t) {
        state.history.push(sample);
        trimHistory();
      }
    }

    function setMetricTone(id, tone) {
      const value = $(id);
      if (!value) return;
      const card = value.closest(".metric");
      const tones = ["good", "warn", "bad"];
      value.classList.remove(...tones);
      if (card) card.classList.remove("state-good", "state-warn", "state-bad");
      if (!tone) return;
      value.classList.add(tone);
      if (card) card.classList.add(`state-${tone}`);
    }

    function toneHeartRate(bpm) {
      if (!Number.isFinite(bpm)) return null;
      if (bpm < 50) return "bad";
      if (bpm < 60) return "warn";
      if (bpm <= 100) return "good";
      if (bpm <= 120) return "warn";
      return "bad";
    }

    function toneBattery(pct) {
      if (!Number.isFinite(pct)) return null;
      if (pct >= 50) return "good";
      if (pct >= 20) return "warn";
      return "bad";
    }

    function toneSkinTemp(temp) {
      if (!Number.isFinite(temp)) return null;
      if (temp >= 31 && temp <= 34) return "good";
      if (temp >= 29 && temp <= 35) return "warn";
      return "bad";
    }

    function toneContact(v) {
      if (v === true) return "good";
      if (v === false) return "bad";
      return null;
    }

    function toneUV(level) {
      if (!level || level === "NoUV" || level === "Low") return "good";
      if (level === "Medium") return "warn";
      return "bad";
    }

    function toneBandConnected(v) {
      if (v === true) return "good";
      if (v === false) return "bad";
      return null;
    }

    function normalizeWearing(value) {
      if (value === true || value === 1 || value === "true" || value === "True" || value === "Yes" || value === "yes") return true;
      if (value === false || value === 0 || value === "false" || value === "False" || value === "No" || value === "no") return false;
      return null;
    }

    function isBiometricSeries(series) {
      return [
        "bpm",
        "rr_ms",
        "rmssd_ms",
        "gsr_kohm",
        "skin_temp_c",
        "accel_mag_g",
        "gyro_mag_dps",
        "gsr_200ms_kohm",
      ].includes(series);
    }

    function crossesWearingGap(prevT, currT) {
      if (!Number.isFinite(prevT) || !Number.isFinite(currT) || currT <= prevT) return false;
      if (!Array.isArray(state.wearingGaps) || !state.wearingGaps.length) return false;
      return state.wearingGaps.some((gap) => {
        const start = Number(gap && gap.start);
        const endRaw = gap && gap.end;
        const end = endRaw == null ? Infinity : Number(endRaw);
        if (!Number.isFinite(start)) return false;
        if (!Number.isFinite(end)) return false;
        return prevT < end && currT > start;
      });
    }

    function updateStatus(data) {
      const dot = $("dot");
      dot.className = "status-dot";
      const connectionState = data.connection_state || data.status || "unknown";
      if (connectionState === "connected") dot.classList.add("connected");
      if (connectionState === "reconnecting" || connectionState === "error") dot.classList.add("error");
      $("status").textContent = connectionState;
      $("error").textContent = data.error || "";
    }

    function render(data) {
      updateStatus(data);
      const wearing = normalizeWearing(data.wearing);
      const biometricsVisible = data.band_connected !== false;
      $("bpm").textContent = biometricsVisible ? fmt0(data.bpm) : "--";
      setMetricTone("bpm", biometricsVisible ? toneHeartRate(data.bpm) : null);
      $("rr").textContent = biometricsVisible ? fmt0(data.rr_ms) : "--";
      $("rmssd").textContent = biometricsVisible ? fmt1(data.rmssd_ms) : "--";
      $("gsr").textContent = biometricsVisible ? fmt0(data.gsr_kohm) : "--";
      $("temp").textContent = biometricsVisible ? fmt1(data.skin_temp_c) : "--";
      setMetricTone("temp", biometricsVisible ? toneSkinTemp(data.skin_temp_c) : null);
      $("accel").textContent = biometricsVisible ? fmt2(data.accel_mag_g) : "--";
      $("accel-axes").textContent = biometricsVisible
        ? `x ${fmt2(data.accel_x_g)} · y ${fmt2(data.accel_y_g)} · z ${fmt2(data.accel_z_g)}`
        : "x -- · y -- · z --";
      $("gyro").textContent = biometricsVisible ? fmt0(data.gyro_mag_dps) : "--";
      $("gyro-axes").textContent = biometricsVisible
        ? `x ${fmt0(data.gyro_x_dps)} · y ${fmt0(data.gyro_y_dps)} · z ${fmt0(data.gyro_z_dps)}`
        : "x -- · y -- · z --";
      $("contact").textContent = wearing === true ? "Yes" : wearing === false ? "No" : "--";
      setMetricTone("contact", toneContact(wearing));
      $("band-connected").textContent = data.band_connected === true ? "Yes" : data.band_connected === false ? "No" : "--";
      setMetricTone("band-connected", toneBandConnected(data.band_connected));
      $("battery").textContent = fmt0(data.battery_pct);
      setMetricTone("battery", toneBattery(data.battery_pct));
      $("battery-voltage").textContent = Number.isFinite(data.battery_voltage_mv) ? `${fmt0(data.battery_voltage_mv)} mV` : "-- mV";
      $("steps").textContent = fmt0(data.steps);
      $("steps-today").textContent = Number.isFinite(data.steps_today) ? `today ${fmt0(data.steps_today)}` : "today --";
      $("distance").textContent = Number.isFinite(data.distance_m) ? fmt1(data.distance_m) : "--";
      $("pace").textContent = `pace ${fmt0(data.pace)} · speed ${fmt1(data.speed_mps)}`;
      $("calories").textContent = fmt0(data.calories);
      $("uv").textContent = data.uv_level || "--";
      setMetricTone("uv", toneUV(data.uv_level));
      $("ambient").textContent = fmt0(data.ambient_light);
      $("pressure").textContent = fmt1(data.barometer_hpa);
      $("baro-temp").textContent = Number.isFinite(data.barometer_temp_c) ? `temp ${fmt1(data.barometer_temp_c)} C` : "temp -- C";
      $("elevation").textContent = fmt1(data.elevation_gain_m);
      $("elevation-detail").textContent = `loss ${fmt1(data.elevation_loss_m)} m · rate ${fmt1(data.elevation_rate_cms)} cm/s`;
      $("gsr-fast").textContent = biometricsVisible ? fmt0(data.gsr_200ms_kohm) : "--";
      setMetricTone("gsr", null);
      setMetricTone("gsr-fast", null);
      state.wearing = wearing;
      state.bandConnected = data.band_connected === true ? true : data.band_connected === false ? false : null;
      if (Array.isArray(data.wearing_gaps)) {
        state.wearingGaps = data.wearing_gaps;
      }
      state.recordingActive = !!data.recording_active;
      state.recordingFile = data.recording_file || null;
      $("recording-status").textContent = state.recordingActive
        ? `Recording ${state.recordingFile || ""}`.trim()
        : "Not recording";
      $("recording-status").classList.toggle("active", state.recordingActive);

      if (data.updated_at) {
        $("updated").textContent = new Date(data.updated_at * 1000).toLocaleTimeString();
      }

      if (Array.isArray(data.selected_sensors)) {
        state.selectedSensors = data.selected_sensors;
        updateSensorVisibility();
      }

      if (Array.isArray(data.history)) {
        state.history = data.history;
        trimHistory();
      }
      if (data.sample && Number.isFinite(data.sample.t)) {
        appendSample(data.sample);
      }
      drawChart();
    }

    function drawChart() {
      const canvas = $("chart");
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(600, Math.floor(rect.width * dpr));
      canvas.height = Math.max(260, Math.floor(rect.height * dpr));
      ctx.scale(dpr, dpr);

      const w = rect.width;
      const h = rect.height;
      ctx.clearRect(0, 0, w, h);

      const [title, unit, color] = state.labels[state.series];
      $("chart-title").textContent = `${title} · ${formatWindowLabel(state.windowSeconds)}`;

      const pad = { left: 48, right: 18, top: 22, bottom: 44 };
      const now = Date.now() / 1000;
      const cutoff = state.windowSeconds > 0 ? now - state.windowSeconds : 0;
      const windowEnd = state.windowSeconds > 0 ? now : 0;
      let plot = state.windowSeconds > 0
        ? state.history.filter((sample) => Number.isFinite(sample.t) && sample.t >= cutoff)
        : state.history.slice();
      if (plot.length > MAX_PLOT_POINTS) {
        const step = Math.ceil(plot.length / MAX_PLOT_POINTS);
        plot = plot.filter((_, index) => index % step === 0 || index === plot.length - 1);
      }
      const gapSeconds = 2.5;

      ctx.strokeStyle = "rgba(145, 176, 206, .18)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top);
      ctx.lineTo(pad.left, h - pad.bottom);
      ctx.lineTo(w - pad.right, h - pad.bottom);
      ctx.stroke();

      let segments = [];
      let segment = [];
      plot.forEach((sample) => {
        const v = sample[state.series];
        if (!Number.isFinite(v)) return;
        if (isBiometricSeries(state.series) && (sample.band_connected === false || sample.wearing === false)) {
          if (segment.length) {
            segments.push(segment);
            segment = [];
          }
          return;
        }
        const prev = segment[segment.length - 1];
        const breaksByTime = segment.length && Number.isFinite(prev.t) && Math.abs(sample.t - prev.t) > gapSeconds;
        if (breaksByTime) {
          segments.push(segment);
          segment = [];
        }
        segment.push(sample);
      });
      if (segment.length) segments.push(segment);

      const values = segments.flatMap((series) => series.map((s) => s[state.series]).filter((v) => Number.isFinite(v)));
      if (!segments.length) {
        ctx.fillStyle = "#86a0b5";
        ctx.fillText(
          state.bandConnected === false && isBiometricSeries(state.series) ? "Band disconnected" : "Waiting for samples",
          pad.left + 12,
          pad.top + 24
        );
        return;
      }
      const plotStart = segments[0][0].t;
      const plotEnd = segments[segments.length - 1][segments[segments.length - 1].length - 1].t;

      if (values.length < 2) {
        ctx.fillStyle = "#86a0b5";
        ctx.fillText(
          state.bandConnected === false && isBiometricSeries(state.series) ? "Band disconnected" : "Waiting for samples",
          pad.left + 12,
          pad.top + 24
        );
        return;
      }

      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = Math.max(1, max - min);
      const yMin = min - span * 0.12;
      const yMax = max + span * 0.12;
      ctx.fillStyle = "#86a0b5";
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText(`${Math.round(yMax)} ${unit}`, 8, pad.top + 4);
      ctx.fillText(`${Math.round(yMin)} ${unit}`, 8, h - pad.bottom);

      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      segments.forEach((series) => {
        ctx.beginPath();
        series.forEach((sample, i) => {
          const v = sample[state.series];
          const xStart = state.windowSeconds > 0 ? cutoff : plotStart;
          const xEnd = state.windowSeconds > 0 ? windowEnd : plotEnd;
          const x = pad.left + ((sample.t - xStart) / Math.max(1, xEnd - xStart)) * (w - pad.left - pad.right);
          const y = pad.top + (1 - (v - yMin) / (yMax - yMin)) * (h - pad.top - pad.bottom);
          if (i === 0) {
            ctx.moveTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
        });
        ctx.stroke();
      });

      if (plot.length > 1) {
        const ticks = 5;
        const start = state.windowSeconds > 0 ? cutoff : plot[0].t;
        const end = state.windowSeconds > 0 ? windowEnd : plot[plot.length - 1].t;
        const span = Math.max(1, end - start);
        ctx.strokeStyle = "rgba(145, 176, 206, .18)";
        ctx.fillStyle = "#86a0b5";
        ctx.font = "11px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        for (let i = 0; i < ticks; i += 1) {
          const fraction = ticks === 1 ? 0 : i / (ticks - 1);
          const ts = start + span * fraction;
          const x = pad.left + fraction * (w - pad.left - pad.right);
          ctx.beginPath();
          ctx.moveTo(x, h - pad.bottom);
          ctx.lineTo(x, h - pad.bottom + 6);
          ctx.stroke();
          ctx.fillText(formatAxisTime(ts), x, h - pad.bottom + 8);
        }
        ctx.textAlign = "left";
        ctx.textBaseline = "alphabetic";
      }
    }

    function formatAxisTime(timestampSeconds) {
      const d = new Date(timestampSeconds * 1000);
      return d.toLocaleTimeString([], {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      });
    }

    function formatWindowLabel(seconds) {
      if (!seconds) return "All";
      if (seconds < 60) return `${seconds}s`;
      if (seconds < 3600 && seconds % 60 === 0) return `${seconds / 60}m`;
      return `${seconds}s`;
    }

    function updateSensorVisibility() {
      const selected = state.selectedSensors;
      if (!Array.isArray(selected)) return;
      document.querySelectorAll(".metric[data-sensors]").forEach((card) => {
        const sensors = card.dataset.sensors.split(",");
        card.style.display = sensors.some((s) => selected.includes(s)) ? "" : "none";
      });
      let activeVisible = false;
      let firstVisible = null;
      document.querySelectorAll(".tabs button[data-sensors]").forEach((btn) => {
        const sensors = btn.dataset.sensors.split(",");
        const visible = sensors.some((s) => selected.includes(s));
        btn.style.display = visible ? "" : "none";
        if (visible && !firstVisible) firstVisible = btn;
        if (btn.classList.contains("active") && visible) activeVisible = true;
      });
      if (!activeVisible && firstVisible) {
        document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
        firstVisible.classList.add("active");
        state.series = firstVisible.dataset.series;
      }
    }

    document.querySelectorAll(".tabs button").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.series = btn.dataset.series;
        drawChart();
      });
    });

    document.querySelectorAll(".range-controls button").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".range-controls button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.windowSeconds = Number(btn.dataset.window);
        drawChart();
      });
    });

    function openSensorModal() {
      const selected = state.selectedSensors || SENSOR_OPTIONS.map((s) => s.name);
      const groups = {};
      SENSOR_OPTIONS.forEach((s) => {
        if (!groups[s.group]) groups[s.group] = [];
        groups[s.group].push(s);
      });
      const container = $("sensor-list");
      container.innerHTML = "";
      Object.entries(groups).forEach(([group, sensors]) => {
        const div = document.createElement("div");
        div.className = "sensor-group";
        const title = document.createElement("div");
        title.className = "sensor-group-title";
        title.textContent = group;
        div.appendChild(title);
        sensors.forEach((s) => {
          const row = document.createElement("div");
          row.className = "sensor-row";
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.id = `sensor-${s.name}`;
          cb.name = s.name;
          cb.checked = selected.includes(s.name);
          const lbl = document.createElement("label");
          lbl.htmlFor = `sensor-${s.name}`;
          lbl.textContent = s.label;
          row.appendChild(cb);
          row.appendChild(lbl);
          div.appendChild(row);
        });
        container.appendChild(div);
      });
      $("sensor-modal").style.display = "flex";
    }

    function closeSensorModal() {
      $("sensor-modal").style.display = "none";
    }

    function getCheckedSensors() {
      return SENSOR_OPTIONS
        .filter((s) => { const el = document.getElementById(`sensor-${s.name}`); return el && el.checked; })
        .map((s) => s.name);
    }

    $("connect-btn").addEventListener("click", openSensorModal);
    $("modal-close").addEventListener("click", closeSensorModal);
    $("sensor-modal").addEventListener("click", (e) => { if (e.target === $("sensor-modal")) closeSensorModal(); });
    $("select-all-btn").addEventListener("click", () => {
      SENSOR_OPTIONS.forEach((s) => { const el = document.getElementById(`sensor-${s.name}`); if (el) el.checked = true; });
    });
    $("select-none-btn").addEventListener("click", () => {
      SENSOR_OPTIONS.forEach((s) => { const el = document.getElementById(`sensor-${s.name}`); if (el) el.checked = false; });
    });
    $("modal-start-btn").addEventListener("click", () => {
      const sensors = getCheckedSensors();
      fetch("/api/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sensors })
      });
      closeSensorModal();
    });

    $("record-start").addEventListener("click", () => {
      fetch("/api/recording/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: $("recording-name").value.trim() || defaultRecordingName() })
      });
    });
    $("record-stop").addEventListener("click", () => fetch("/api/recording/stop", { method: "POST" }));
    window.addEventListener("resize", drawChart);
    setInterval(drawChart, 1000);

    const events = new EventSource("/events");
    events.onmessage = (event) => render(JSON.parse(event.data));
    events.onerror = () => {
      $("status").textContent = "event stream disconnected";
      $("dot").className = "status-dot error";
    };

    openSensorModal();
  </script>
</body>
</html>
"""


def calc_rmssd(values):
    if len(values) < 2:
        return None
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    return math.sqrt(sum(diff * diff for diff in diffs) / len(diffs))


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


def subscribe(cargo, sensor):
    transfer = bytes([int(sensor)]) + b"\x00\x00\x00\x00" + PUSH_SERVICE_GUID.bytes_le
    packet = make_packet(0x8F07, data_length=len(transfer))
    cargo_command(cargo, packet, transfer=transfer)


def unsubscribe(cargo, sensor):
    transfer = bytes([int(sensor)]) + b"\x00\x00\x00\x00" + PUSH_SERVICE_GUID.bytes_le
    packet = make_packet(0x8F08, data_length=len(transfer))
    cargo_command(cargo, packet, transfer=transfer)


class BandCollector:
    def __init__(self, address):
        self.address = address
        self.selected_sensors = list(ALL_SENSORS)
        self.lock = threading.RLock()
        self.clients = []
        self.thread = None
        self.stop_event = threading.Event()
        self.rr_buffer = deque(maxlen=RR_WINDOW)
        self.history = deque()
        self.recording_active = False
        self.recording_started_at = None
        self.recording_ended_at = None
        self.recording_reason = None
        self.recording_error = None
        self.recording_file = None
        self.recording_samples = []
        self.recording_gaps = []
        self.recording_gap_started_at = None
        self.band_connection_gaps = []
        self.band_connection_gap_started_at = None
        self.wearing_gaps = []
        self.wearing_gap_started_at = None
        self.state = {
            "status": "stopped",
            "error": None,
            "bpm": None,
            "rr_ms": None,
            "rmssd_ms": None,
            "gsr_kohm": None,
            "skin_temp_c": None,
            "accel_x_g": None,
            "accel_y_g": None,
            "accel_z_g": None,
            "accel_mag_g": None,
            "gyro_x_dps": None,
            "gyro_y_dps": None,
            "gyro_z_dps": None,
            "gyro_mag_dps": None,
            "wearing": None,
            "band_connected": None,
            "battery_pct": None,
            "battery_voltage_mv": None,
            "battery_alerts": None,
            "steps": None,
            "steps_today": None,
            "distance_m": None,
            "speed_mps": None,
            "pace": None,
            "motion": None,
            "calories": None,
            "uv_level": None,
            "ambient_light": None,
            "barometer_hpa": None,
            "barometer_temp_c": None,
            "elevation_altitude_m": None,
            "elevation_gain_m": None,
            "elevation_loss_m": None,
            "elevation_stepping_gain_m": None,
            "elevation_stepping_loss_m": None,
            "elevation_steps_ascended": None,
            "elevation_steps_descended": None,
            "elevation_rate_cms": None,
            "elevation_flights_ascended": None,
            "elevation_flights_descended": None,
            "elevation_flights_ascended_today": None,
            "elevation_gain_today_m": None,
            "gsr_200ms_kohm": None,
            "recording_active": False,
            "recording_file": None,
            "connection_state": "stopped",
            "updated_at": None,
            "address": self.address,
        }

    def snapshot(self, include_history=True, sample=None):
        with self.lock:
            data = dict(self.state)
            if include_history:
                data["history"] = list(self.history)
            if sample is not None:
                data["sample"] = sample
            data["recording_active"] = self.recording_active
            data["recording_file"] = self.recording_file
            data["recording_started_at"] = self.recording_started_at
            data["recording_ended_at"] = self.recording_ended_at
            data["recording_reason"] = self.recording_reason
            data["recording_error"] = self.recording_error
            data["recording_gaps"] = list(self.recording_gaps)
            data["band_connection_gaps"] = list(self.band_connection_gaps)
            data["wearing_gaps"] = list(self.wearing_gaps)
            data["connection_state"] = self.state["connection_state"]
            data["selected_sensors"] = [s.name for s in self.selected_sensors]
            return data

    def set_status(self, status, error=None):
        with self.lock:
            self.state["status"] = status
            self.state["error"] = error
        self.broadcast()

    def set_connection_state(self, status, error=None):
        with self.lock:
            self.state["connection_state"] = status
            self.state["status"] = status
            self.state["error"] = error
        self.broadcast()

    def _build_sample(self, timestamp):
        return {
            "t": timestamp,
            "bpm": self.state["bpm"],
            "rr_ms": self.state["rr_ms"],
            "rmssd_ms": self.state["rmssd_ms"],
            "gsr_kohm": self.state["gsr_kohm"],
            "skin_temp_c": self.state["skin_temp_c"],
            "accel_x_g": self.state["accel_x_g"],
            "accel_y_g": self.state["accel_y_g"],
            "accel_z_g": self.state["accel_z_g"],
            "accel_mag_g": self.state["accel_mag_g"],
            "gyro_x_dps": self.state["gyro_x_dps"],
            "gyro_y_dps": self.state["gyro_y_dps"],
            "gyro_z_dps": self.state["gyro_z_dps"],
            "gyro_mag_dps": self.state["gyro_mag_dps"],
            "wearing": self.state["wearing"],
            "band_connected": self.state["band_connected"],
            "battery_pct": self.state["battery_pct"],
            "steps": self.state["steps"],
            "steps_today": self.state["steps_today"],
            "distance_m": self.state["distance_m"],
            "speed_mps": self.state["speed_mps"],
            "pace": self.state["pace"],
            "calories": self.state["calories"],
            "uv_level": self.state["uv_level"],
            "ambient_light": self.state["ambient_light"],
            "barometer_hpa": self.state["barometer_hpa"],
            "barometer_temp_c": self.state["barometer_temp_c"],
            "elevation_altitude_m": self.state["elevation_altitude_m"],
            "elevation_gain_m": self.state["elevation_gain_m"],
            "elevation_loss_m": self.state["elevation_loss_m"],
            "elevation_rate_cms": self.state["elevation_rate_cms"],
            "elevation_gain_today_m": self.state["elevation_gain_today_m"],
            "gsr_200ms_kohm": self.state["gsr_200ms_kohm"],
        }

    def _prune_history_locked(self, now=None):
        if now is None:
            now = time.time()
        cutoff = now - HISTORY_SECONDS
        while self.history and self.history[0]["t"] < cutoff:
            self.history.popleft()
        while len(self.history) > HISTORY_MAX_SAMPLES:
            self.history.popleft()

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
                file_name = f"sesion-{stamp}.json"
            if not file_name.endswith(".json"):
                file_name += ".json"
            file_name = file_name.replace("/", "_")
            self.recording_active = True
            self.recording_started_at = started_at
            self.recording_ended_at = None
            self.recording_reason = None
            self.recording_error = None
            self.recording_file = file_name
            self.recording_gaps = []
            self.recording_gap_started_at = None
            self.band_connection_gaps = []
            self.band_connection_gap_started_at = None
            self.wearing_gaps = []
            self.wearing_gap_started_at = None
            self._update_band_connection_locked()
            if self.state["band_connected"] is False:
                self.recording_samples = []
                self.band_connection_gap_started_at = started_at
                self.band_connection_gaps.append({
                    "start": started_at,
                    "end": None,
                    "reason": self._band_disconnect_reason_locked(),
                })
            else:
                self.recording_samples = [self._build_sample(started_at)]
            self.state["recording_active"] = True
            self.state["recording_file"] = self.recording_file
            file_return = self.recording_file
        self.broadcast()
        return True, file_return

    def _finish_recording_locked(self, reason, error=None):
        if not self.recording_active:
            return None
        ended_at = time.time()
        if self.recording_gaps and self.recording_gaps[-1].get("end") is None:
            self.recording_gaps[-1]["end"] = ended_at
        if self.band_connection_gaps and self.band_connection_gaps[-1].get("end") is None:
            self.band_connection_gaps[-1]["end"] = ended_at
        if self.wearing_gaps and self.wearing_gaps[-1].get("end") is None:
            self.wearing_gaps[-1]["end"] = ended_at
        payload = {
            "version": 1,
            "address": self.address,
            "started_at": self.recording_started_at,
            "ended_at": ended_at,
            "reason": reason,
            "error": error,
            "gaps": list(self.recording_gaps),
            "band_connection_gaps": list(self.band_connection_gaps),
            "wearing_gaps": list(self.wearing_gaps),
            "samples": list(self.recording_samples),
            "final_state": self._build_sample(ended_at),
        }
        path = RECORDINGS_DIR / self.recording_file
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        self.recording_active = False
        self.recording_ended_at = ended_at
        self.recording_reason = reason
        self.recording_error = error
        self.recording_samples = []
        self.recording_gap_started_at = None
        self.band_connection_gap_started_at = None
        self.wearing_gap_started_at = None
        self.state["recording_active"] = False
        self.state["recording_file"] = self.recording_file
        return path

    def stop_recording(self, reason="manual", error=None):
        with self.lock:
            path = self._finish_recording_locked(reason, error)
        if path is not None:
            self.broadcast()
        return path

    def mark_disconnect_start(self, reason=None):
        with self.lock:
            if not self.recording_active:
                return
            if self.recording_gap_started_at is not None:
                return
            self.recording_gap_started_at = time.time()
            if reason:
                self.recording_error = reason
            self.recording_gaps.append({
                "start": self.recording_gap_started_at,
                "end": None,
                "reason": reason,
            })
        self.broadcast()

    def mark_reconnect(self):
        with self.lock:
            if not self.recording_active or self.recording_gap_started_at is None:
                return
            ended_at = time.time()
            if self.recording_gaps:
                gap = self.recording_gaps[-1]
                if gap.get("end") is None:
                    gap["end"] = ended_at
            self.recording_gap_started_at = None
            self.recording_error = None
        self.broadcast()

    def _effective_gsr_locked(self):
        if self.state["gsr_200ms_kohm"] is not None:
            return self.state["gsr_200ms_kohm"]
        return self.state["gsr_kohm"]

    def _band_disconnect_reason_locked(self):
        if self.state["wearing"] is False:
            return "not_wearing"
        gsr = self._effective_gsr_locked()
        if gsr is not None and gsr >= 50000:
            return "gsr_spike"
        return "unknown"

    def _infer_band_connected_locked(self):
        wearing = self.state["wearing"]
        gsr = self._effective_gsr_locked()
        previous = self.state["band_connected"]
        if wearing is False:
            return False
        if gsr is None:
            return True if wearing is True else previous
        if previous is False:
            if wearing is True and gsr <= 20000:
                return True
            return False
        if gsr >= 50000:
            return False
        if wearing is True:
            return True
        if gsr <= 20000:
            return True
        return previous

    def _update_band_connection_locked(self):
        previous = self.state["band_connected"]
        current = self._infer_band_connected_locked()
        if current == previous:
            return current
        self.state["band_connected"] = current
        if self.recording_active:
            if current is False:
                if self.band_connection_gap_started_at is None:
                    self.band_connection_gap_started_at = time.time()
                    self.band_connection_gaps.append({
                        "start": self.band_connection_gap_started_at,
                        "end": None,
                        "reason": self._band_disconnect_reason_locked(),
                    })
            elif previous is False and current is True:
                ended_at = time.time()
                if self.band_connection_gaps:
                    gap = self.band_connection_gaps[-1]
                    if gap.get("end") is None:
                        gap["end"] = ended_at
                self.band_connection_gap_started_at = None
        return current

    def mark_band_connection_lost(self):
        with self.lock:
            if not self.recording_active or self.band_connection_gap_started_at is not None:
                return
            self.band_connection_gap_started_at = time.time()
            self.band_connection_gaps.append({
                "start": self.band_connection_gap_started_at,
                "end": None,
                "reason": self._band_disconnect_reason_locked(),
            })
        self.broadcast()

    def mark_band_connection_restored(self):
        with self.lock:
            if not self.recording_active or self.band_connection_gap_started_at is None:
                return
            ended_at = time.time()
            if self.band_connection_gaps:
                gap = self.band_connection_gaps[-1]
                if gap.get("end") is None:
                    gap["end"] = ended_at
            self.band_connection_gap_started_at = None
        self.broadcast()

    def mark_wearing_lost(self):
        with self.lock:
            if not self.recording_active:
                return
            if self.wearing_gap_started_at is not None:
                return
            self.wearing_gap_started_at = time.time()
            self.wearing_gaps.append({
                "start": self.wearing_gap_started_at,
                "end": None,
                "reason": "not_wearing",
            })
        self.broadcast()

    def mark_wearing_restored(self):
        with self.lock:
            if not self.recording_active or self.wearing_gap_started_at is None:
                return
            ended_at = time.time()
            if self.wearing_gaps:
                gap = self.wearing_gaps[-1]
                if gap.get("end") is None:
                    gap["end"] = ended_at
            self.wearing_gap_started_at = None
        self.broadcast()

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

    def start(self, sensors=None):
        if sensors is not None:
            with self.lock:
                self.selected_sensors = list(sensors)
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def _reset_sensor_state_locked(self):
        for key in [
            "bpm", "rr_ms", "rmssd_ms", "gsr_kohm", "skin_temp_c",
            "accel_x_g", "accel_y_g", "accel_z_g", "accel_mag_g",
            "gyro_x_dps", "gyro_y_dps", "gyro_z_dps", "gyro_mag_dps",
            "wearing", "band_connected", "battery_pct", "battery_voltage_mv",
            "battery_alerts", "steps", "steps_today", "distance_m",
            "speed_mps", "pace", "motion", "calories", "uv_level",
            "ambient_light", "barometer_hpa", "barometer_temp_c",
            "elevation_altitude_m", "elevation_gain_m", "elevation_loss_m",
            "elevation_stepping_gain_m", "elevation_stepping_loss_m",
            "elevation_steps_ascended", "elevation_steps_descended",
            "elevation_rate_cms", "elevation_flights_ascended",
            "elevation_flights_descended", "elevation_flights_ascended_today",
            "elevation_gain_today_m", "gsr_200ms_kohm", "updated_at",
        ]:
            self.state[key] = None
        self.rr_buffer.clear()
        self.history.clear()

    def _do_restart(self, sensors=None):
        if sensors is not None:
            with self.lock:
                self.selected_sensors = list(sensors)
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=8)
        self.stop_event.clear()
        with self.lock:
            self.state["connection_state"] = "stopped"
            self.state["status"] = "stopped"
            self._reset_sensor_state_locked()
            data = self.snapshot(include_history=True)
            clients = list(self.clients)
        for client in clients:
            try:
                if client.full():
                    client.get_nowait()
                client.put_nowait(data)
            except queue.Full:
                pass
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def restart(self, sensors=None):
        t = threading.Thread(target=self._do_restart, args=(sensors,), daemon=True)
        t.start()

    def stop(self):
        self.stop_event.set()
        self.set_status("stopped")
        self.set_connection_state("stopped")

    def update_sensor(self, sensor):
        now = time.time()
        sensor_type = sensor.subscription_type
        suppress_sample = False

        with self.lock:
            if sensor_type == Sensor.HeartRate:
                if not hasattr(sensor, "value"):
                    return
                self.state["bpm"] = sensor.value
            elif sensor_type == Sensor.RRInterval:
                if not hasattr(sensor, "value"):
                    return
                self.rr_buffer.append(sensor.value)
                self.state["rr_ms"] = sensor.value
                self.state["rmssd_ms"] = calc_rmssd(list(self.rr_buffer))
            elif sensor_type == Sensor.Gsr:
                if not hasattr(sensor, "value"):
                    return
                self.state["gsr_kohm"] = sensor.value
                self._update_band_connection_locked()
                suppress_sample = self.state["band_connected"] is False
            elif sensor_type == Sensor.Gsr200MS:
                if not hasattr(sensor, "value"):
                    return
                self.state["gsr_200ms_kohm"] = sensor.value
                self._update_band_connection_locked()
                suppress_sample = self.state["band_connected"] is False
            elif sensor_type == Sensor.SkinTemperature:
                if not hasattr(sensor, "value"):
                    return
                self.state["skin_temp_c"] = sensor.value
            elif sensor_type in (
                Sensor.Accelerometer32MS,
                Sensor.Accelerometer128MS,
                Sensor.Accelerometer16MS,
            ):
                if not hasattr(sensor, "acceleration_x"):
                    return
                x = sensor.acceleration_x
                y = sensor.acceleration_y
                z = sensor.acceleration_z
                self.state["accel_x_g"] = x
                self.state["accel_y_g"] = y
                self.state["accel_z_g"] = z
                self.state["accel_mag_g"] = math.sqrt(x * x + y * y + z * z)
            elif sensor_type in (
                Sensor.AccelerometerGyroscope32MS,
                Sensor.AccelerometerGyroscope128MS,
                Sensor.AccelerometerGyroscope16MS,
            ):
                if not hasattr(sensor, "acceleration_x"):
                    return
                ax = sensor.acceleration_x
                ay = sensor.acceleration_y
                az = sensor.acceleration_z
                gx = sensor.velocity_x
                gy = sensor.velocity_y
                gz = sensor.velocity_z
                self.state["accel_x_g"] = ax
                self.state["accel_y_g"] = ay
                self.state["accel_z_g"] = az
                self.state["accel_mag_g"] = math.sqrt(ax * ax + ay * ay + az * az)
                self.state["gyro_x_dps"] = gx
                self.state["gyro_y_dps"] = gy
                self.state["gyro_z_dps"] = gz
                self.state["gyro_mag_dps"] = math.sqrt(gx * gx + gy * gy + gz * gz)
            elif sensor_type == Sensor.DeviceContact:
                if not hasattr(sensor, "value"):
                    return
                previous_wearing = self.state["wearing"]
                self.state["wearing"] = sensor.value
                self._update_band_connection_locked()
                if self.recording_active:
                    if sensor.value is False:
                        self.mark_wearing_lost()
                    elif previous_wearing is False and sensor.value is True:
                        self.mark_wearing_restored()
                suppress_sample = self.state["band_connected"] is False
            elif sensor_type == Sensor.BatteryGauge:
                if not hasattr(sensor, "value"):
                    return
                self.state["battery_pct"] = sensor.value
                self.state["battery_voltage_mv"] = sensor.filtered_voltage
                self.state["battery_alerts"] = sensor.battery_gauge_alerts
            elif sensor_type == Sensor.Pedometer:
                if not hasattr(sensor, "value"):
                    return
                self.state["steps"] = sensor.value
            elif sensor_type == Sensor.PedometerWithDailyValues:
                if not hasattr(sensor, "value"):
                    return
                self.state["steps"] = sensor.value
                self.state["steps_today"] = sensor.value_today
            elif sensor_type == Sensor.Distance:
                if not hasattr(sensor, "value"):
                    return
                self.state["distance_m"] = sensor.value / 100.0
                self.state["speed_mps"] = sensor.speed / 100.0
                self.state["pace"] = sensor.pace
            elif sensor_type == Sensor.DistanceWithDailyValues:
                if not hasattr(sensor, "value"):
                    return
                self.state["distance_m"] = sensor.value / 100.0
                self.state["speed_mps"] = sensor.speed / 100.0
                self.state["pace"] = sensor.pace
                self.state["motion"] = getattr(sensor.current_motion, "name", str(sensor.current_motion))
            elif sensor_type == Sensor.Calories1S:
                if not hasattr(sensor, "value"):
                    return
                self.state["calories"] = sensor.value
            elif sensor_type in (Sensor.UV, Sensor.UVFast):
                if not hasattr(sensor, "value"):
                    return
                self.state["uv_level"] = getattr(sensor.value, "name", str(sensor.value))
            elif sensor_type in (Sensor.AmbientLight, Sensor.AmbientLightWithDailyValues):
                if not hasattr(sensor, "value"):
                    return
                self.state["ambient_light"] = sensor.value
            elif sensor_type == Sensor.Barometer:
                if not hasattr(sensor, "air_pressure_hpa"):
                    return
                self.state["barometer_hpa"] = sensor.air_pressure_hpa
                self.state["barometer_temp_c"] = sensor.temperature_c
            elif sensor_type in (Sensor.Elevation, Sensor.ElevationWithDailyValues):
                if not hasattr(sensor, "total_gain_cm"):
                    return
                self.state["elevation_altitude_m"] = getattr(sensor, "altitude_m", self.state["elevation_altitude_m"])
                self.state["elevation_gain_m"] = sensor.total_gain_cm / 100.0
                self.state["elevation_loss_m"] = sensor.total_loss_cm / 100.0
                self.state["elevation_stepping_gain_m"] = sensor.stepping_gain_cm / 100.0
                self.state["elevation_stepping_loss_m"] = sensor.stepping_loss_cm / 100.0
                self.state["elevation_steps_ascended"] = sensor.steps_ascended
                self.state["elevation_steps_descended"] = sensor.steps_descended
                self.state["elevation_rate_cms"] = sensor.rate_cms
                self.state["elevation_flights_ascended"] = sensor.flights_ascended
                self.state["elevation_flights_descended"] = sensor.flights_descended
                self.state["elevation_flights_ascended_today"] = getattr(sensor, "flights_ascended_today", None)
                self.state["elevation_gain_today_m"] = (
                    sensor.total_gain_today_cm / 100.0
                    if hasattr(sensor, "total_gain_today_cm")
                    else self.state["elevation_gain_today_m"]
                )
            else:
                return

            self.state["updated_at"] = now
            sample = self._build_sample(now)
            if not suppress_sample and self.state["band_connected"] is not False:
                self.history.append(sample)
                self._prune_history_locked(now)
            if self.recording_active and not suppress_sample and self.state["band_connected"] is not False:
                self.recording_samples.append(dict(sample))
        self.broadcast(sample)

    def process_push_buffer(self, buffer):
        while len(buffer) >= 6:
            if buffer[:2] != b"\x01\x00":
                start = buffer.find(b"\x01\x00", 1)
                if start == -1:
                    buffer.clear()
                    break
                del buffer[:start]
                if len(buffer) < 6:
                    break

            packet_length = int.from_bytes(buffer[2:6], "little")
            frame_length = 2 + 4 + packet_length
            if packet_length <= 0 or packet_length > 512:
                del buffer[0]
                continue
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
                self.update_sensor(decode_sensor_reading(legacy_packet))

    def run(self):
        while not self.stop_event.is_set():
            cargo = None
            push = None
            failure = None
            with self.lock:
                sensors = list(self.selected_sensors)
            try:
                self.set_connection_state("connecting")
                cargo = connect_rfcomm(self.address, CARGO_PORT)
                push = connect_rfcomm(self.address, PUSH_PORT)
                push.settimeout(1.0)

                for sensor in ALL_SENSORS:
                    try:
                        unsubscribe(cargo, sensor)
                    except Exception:
                        pass
                for sensor in sensors:
                    try:
                        subscribe(cargo, sensor)
                    except Exception:
                        pass

                self.set_connection_state("connected")
                self.mark_reconnect()
                buffer = bytearray()
                while not self.stop_event.is_set():
                    try:
                        chunk = push.recv(8192)
                    except TimeoutError:
                        continue
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise ConnectionError("push socket closed")
                    buffer.extend(chunk)
                    self.process_push_buffer(buffer)
            except Exception as exc:
                failure = str(exc)
                if not self.stop_event.is_set():
                    self.set_connection_state("reconnecting", failure)
                    self.mark_disconnect_start(failure)
            finally:
                for sock in (push, cargo):
                    if sock:
                        try:
                            sock.close()
                        except OSError:
                            pass
            if self.stop_event.is_set():
                break
            time.sleep(RECONNECT_SECONDS)

        with self.lock:
            if self.recording_active and self.recording_gap_started_at is not None:
                self.recording_gaps[-1]["end"] = time.time()


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

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/start":
            content_length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(content_length) if content_length else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                payload = {}
            sensor_names = payload.get("sensors")
            sensors = None
            if sensor_names is not None:
                sensors = [SENSOR_BY_NAME[n] for n in sensor_names if n in SENSOR_BY_NAME]
            self.collector.restart(sensors=sensors)
            self.send_json({"ok": True})
            return
        if path == "/api/stop":
            self.collector.stop()
            self.send_json({"ok": True})
            return
        if path == "/api/recording/start":
            content_length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(content_length) if content_length else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                payload = {}
            started, file_name = self.collector.start_recording(payload.get("name"))
            self.send_json({"ok": True, "started": started, "file": file_name})
            return
        if path == "/api/recording/stop":
            path = self.collector.stop_recording()
            self.send_json({"ok": True, "file": path.name if path else None})
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("address", nargs="?", default="58:82:A8:CE:4E:C8")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    collector = BandCollector(args.address)
    DashboardHandler.collector = collector
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)

    print(f"Dashboard: http://{args.host}:{args.port}")
    print(f"Band: {args.address}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        server.server_close()


if __name__ == "__main__":
    main()
