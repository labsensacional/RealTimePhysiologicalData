# polar_h9

Real-time HR/HRV dashboard for Polar H9 chest strap via BLE. Streams BPM, RR intervals, filtered RMSSD, and BPM derivative to a local web UI with markers, recording, and chart.

## Run
```
python3 polar_h9_dashboard.py [MAC_ADDRESS]   # auto-scans if no MAC given
# open http://127.0.0.1:8080
```

## Key files
- `polar_h9_dashboard.py` — main dashboard (HTTP server + SSE + BLE collector)
- `polar_h9.py` — original simple CLI script (terminal only)
- `recordings/` — JSON session files with samples + markers
- `analisis_hrv.md` — HRV analysis notes, arousal signatures, stress vs pleasure discrimination

## Architecture
- `PolarCollector`: BLE via bleak (asyncio in background thread), dual RR buffers (raw + filtered), bpm_rate derivative
- `DashboardHandler`: ThreadingHTTPServer, SSE at `/events`, REST at `/api/markers` and `/api/recording/*`
- RR filter: 300–2000ms range + 25% ectopic threshold

## Gotchas
- Band 2 (RFCOMM) lives in `../RealTimePhysiologicalDataWithHackedMicrosoftBand/` with its own `libband/`
- Next planned: `../fusion_dashboard/` — fuses H9 (HR/HRV) + Band 2 (skin temp, GSR, accel/gyro)
- Motion artifacts on H9 come from strap displacement, not dry electrodes — tighten strap + center module
