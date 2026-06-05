#!/usr/bin/env python3
"""
Microsoft Band 2 — real-time sensor stream
HR, HRV (RR intervals), GSR, Skin Temperature

Usage:
    python band2.py                        # auto-discover
    python band2.py XX:XX:XX:XX:XX:XX     # MAC directa
"""
import sys
import math
import threading
from collections import deque

import bluetooth
from libband.device import BandDevice
from libband.apps.sensors.sensor_stream import SensorStreamService
from libband.sensors import Sensor
from libband.socket import BandSocketSendError, BandSocketTimeout

RR_WINDOW = 20
rr_buffer = deque(maxlen=RR_WINDOW)
latest = {"bpm": None, "gsr": None, "temp": None, "rr": None, "rmssd": None}
lock = threading.Lock()


def calc_rmssd(rr_list):
    if len(rr_list) < 2:
        return None
    diffs = [rr_list[i+1] - rr_list[i] for i in range(len(rr_list) - 1)]
    return math.sqrt(sum(d**2 for d in diffs) / len(diffs))


def print_status():
    with lock:
        bpm   = f"{latest['bpm']:3d} bpm" if latest['bpm'] else "  - bpm"
        rmssd = f"HRV: {latest['rmssd']:.1f} ms" if latest['rmssd'] else "HRV: --.- ms"
        rr    = f"RR: {latest['rr']:.0f} ms" if latest['rr'] else "RR:  --- ms"
        gsr   = f"GSR: {latest['gsr']} kΩ" if latest['gsr'] is not None else "GSR:  ---  kΩ"
        temp  = f"Temp: {latest['temp']:.1f} °C" if latest['temp'] else "Temp:  --.- °C"

    print(f"\033[2K\r{bpm}  |  {rr}  |  {rmssd}  |  {gsr}  |  {temp}", end="", flush=True)


class LiveWrapper:
    def print(self, sensor, *args, **kwargs):
        t = sensor.subscription_type if hasattr(sensor, 'subscription_type') else None

        if t == Sensor.HeartRate:
            with lock:
                latest['bpm'] = sensor.value
        elif t == Sensor.RRInterval:
            rr_ms = sensor.value
            rr_buffer.append(rr_ms)
            with lock:
                latest['rr'] = rr_ms
                latest['rmssd'] = calc_rmssd(list(rr_buffer))
        elif t == Sensor.Gsr:
            with lock:
                latest['gsr'] = sensor.value
        elif t == Sensor.SkinTemperature:
            with lock:
                latest['temp'] = sensor.value
        print_status()

    def send(self, signal, args):
        if signal == "Status":
            print(f"\n[{signal}] {args}", flush=True)

    def atexit(self, func):
        import atexit
        atexit.register(func)


def find_band2():
    print("Buscando Microsoft Band 2... (puede tardar ~10s)")
    try:
        devices = bluetooth.discover_devices(duration=10, lookup_names=True)
        for addr, name in devices:
            if "Band" in name:
                print(f"Encontrado: {name} [{addr}]")
                return addr
        print("No encontrado. Pasá la MAC como argumento.")
    except Exception as e:
        print(f"Error en discovery: {e}")
    return None


def main():
    address = sys.argv[1] if len(sys.argv) > 1 else find_band2()
    if not address:
        return

    print(f"Conectando a {address}...")

    device = BandDevice(address)
    device.wrapper = LiveWrapper()

    stream = SensorStreamService(device)
    device.services = {"SensorStreamService": stream}

    try:
        device.connect()
    except (BandSocketTimeout, BandSocketSendError) as exc:
        print(f"\nNo se pudo inicializar el Band por RFCOMM: {exc}")
        print(
            "Si la pantalla dice \"Pairing success! Return to the Microsoft "
            "Band app to continue\", completá OOBE con la app antigua de "
            "Microsoft Band y volvé a ejecutar este script."
        )
        device.disconnect()
        return

    print("Suscribiendo a sensores...")
    stream.subscribe(Sensor.HeartRate)
    stream.subscribe(Sensor.RRInterval)
    stream.subscribe(Sensor.Gsr)
    stream.subscribe(Sensor.SkinTemperature)

    print("Leyendo datos. Ctrl+C para salir.\n")
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        print("\n\nDesconectando...")
        device.disconnect()


if __name__ == "__main__":
    main()
