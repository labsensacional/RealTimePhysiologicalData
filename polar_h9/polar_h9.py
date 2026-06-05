#!/usr/bin/env python3
import asyncio
import math
import sys
from collections import deque
from bleak import BleakClient, BleakScanner

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
RR_WINDOW = 20  # RR intervals para calcular RMSSD

rr_buffer = deque(maxlen=RR_WINDOW)


def parse_hr_measurement(data: bytearray):
    flags = data[0]
    hr_16bit = flags & 0x01
    energy_present = (flags >> 3) & 0x01
    rr_present = (flags >> 4) & 0x01

    offset = 1
    if hr_16bit:
        bpm = int.from_bytes(data[offset:offset+2], "little")
        offset += 2
    else:
        bpm = data[offset]
        offset += 1

    if energy_present:
        offset += 2

    rr_intervals = []
    if rr_present:
        while offset + 1 < len(data):
            raw = int.from_bytes(data[offset:offset+2], "little")
            rr_intervals.append(raw * 1000 / 1024)  # convertir a ms
            offset += 2

    return bpm, rr_intervals


def calc_rmssd(rr_list):
    if len(rr_list) < 2:
        return None
    diffs = [rr_list[i+1] - rr_list[i] for i in range(len(rr_list)-1)]
    return math.sqrt(sum(d**2 for d in diffs) / len(diffs))


def handle_hr(sender, data):
    bpm, rr_intervals = parse_hr_measurement(bytearray(data))
    rr_buffer.extend(rr_intervals)

    rmssd = calc_rmssd(list(rr_buffer))

    rr_str = f"RR: {[f'{r:.0f}' for r in rr_intervals]} ms" if rr_intervals else "RR: -"
    rmssd_str = f"HRV (RMSSD): {rmssd:.1f} ms" if rmssd else "HRV (RMSSD): calculando..."

    print(f"\033[2K\rBPM: {bpm:3d}  |  {rmssd_str}  |  {rr_str}", end="", flush=True)


async def find_h9():
    print("Buscando Polar H9... (poné el sensor en contacto con la piel)")
    devices = await BleakScanner.discover(timeout=10)
    for d in devices:
        if d.name and "Polar H9" in d.name:
            print(f"\nEncontrado: {d.name} [{d.address}]")
            return d.address
    return None


async def main():
    address = sys.argv[1] if len(sys.argv) > 1 else None

    if not address:
        address = await find_h9()
        if not address:
            print("\nNo se encontró el H9. Pasá la MAC como argumento: python polar_h9.py XX:XX:XX:XX:XX:XX")
            return

    print(f"Conectando a {address}...")
    async with BleakClient(address) as client:
        print("Conectado. Leyendo datos...\n")
        await client.start_notify(HR_UUID, handle_hr)
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n\nListo.")


asyncio.run(main())
