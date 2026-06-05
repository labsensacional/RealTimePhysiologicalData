# Physiological Sensing — Microsoft Band 2 + Polar H9

Workspace for real-time physiological data acquisition from Microsoft Band 2 (RFCOMM Bluetooth) and Polar H9 (BLE), with a combined fusion dashboard.

## Repository layout

```
.
├── microsoft_band_2/       ← Band 2 scripts, libband, recordings
│   ├── band2.py            ← lower-level Band access helpers
│   ├── band2_dashboard.py  ← live dashboard + session recorder (port 8000)
│   ├── band2_live.py       ← live sensor streaming
│   ├── band2_probe_missing.py
│   ├── oobe_bypass.py      ← OOBE bypass via Bluetooth (fails on EFAULT in OOBE)
│   ├── msband_oobe_usb.py  ← OOBE bypass via USB ← USE THIS
│   ├── libband/            ← libmsftband, patched for Python 3.12
│   ├── recordings/         ← session JSON files
│   ├── BAND2_NOTAS.md      ← technical notes, sensor table, status
│   └── BAND2_OOBE_USB_RUNBOOK.md
│
├── polar_h9/               ← Polar H9 BLE scripts
│   ├── polar_h9.py         ← lower-level BLE helper
│   ├── polar_h9_dashboard.py ← live dashboard (port 8080)
│   ├── analisis_hrv.md     ← HRV analysis notes
│   ├── CLAUDE.md
│   └── recordings/         ← session JSON files
│
├── fusion_dashboard/       ← Combined H9 + Band 2 dashboard
│   ├── fusion_dashboard.py ← main entry point (port 8080)
│   └── recordings/         ← session JSON files
│
├── emotions_inference_with_microsoft_band/  ← original reference repo
│   ├── band.py
│   └── bayesian-model.ipynb
│
└── plan_testeo_metricas.md ← validation protocol for each sensor
```

## Quick start

### Fusion dashboard (H9 + Band 2 together) — recommended

```bash
cd fusion_dashboard
python3 fusion_dashboard.py [H9_MAC] [--band2 B2_MAC] [--port 8080]
# defaults: H9=A0:9E:1A:DD:B3:D7  Band2=58:82:A8:CE:4E:C8
```
Open: http://127.0.0.1:8080

Shows: BPM, RR, HRV (RMSSD), BPM rate, skin temp, GSR, accelerometer, gyro, battery, wearing state. Supports recording to JSON and time markers.

### Band 2 standalone dashboard

```bash
cd microsoft_band_2
python3 band2_dashboard.py 58:82:A8:CE:4E:C8
```
Open: http://127.0.0.1:8000

### Polar H9 standalone dashboard

```bash
cd polar_h9
python3 polar_h9_dashboard.py [MAC_ADDRESS]
```
Open: http://127.0.0.1:8080

## Devices

| Device | MAC | Protocol |
|---|---|---|
| Microsoft Band 2 | `58:82:A8:CE:4E:C8` | Bluetooth Classic RFCOMM (ports 4+5) |
| Polar H9 | `A0:9E:1A:DD:B3:D7` | BLE (GATT HR service) |

## Dependencies

```bash
# Band 2
sudo apt install python3-bluetooth
pip install unidecode geocoder --break-system-packages

# Polar H9 + fusion dashboard
pip install bleak --break-system-packages
```

`libband/` is vendored locally (from [libmsftband](https://github.com/ksiazkowicz/libmsftband)) with Python 3.12 fix already applied — see `BAND2_NOTAS.md`.

## Sensor metrics (Band 2)

Skin temperature, GSR (200ms), AccelerometerGyroscope (32ms), DeviceContact, BatteryGauge — all confirmed working. See `microsoft_band_2/BAND2_NOTAS.md` for full sensor table.

## Recordings format

All dashboards write JSON with `{version, started_at, ended_at, samples[], markers[]}`. Each sample has a Unix timestamp `t` plus all active sensor fields.
