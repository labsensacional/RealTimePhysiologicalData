#!/usr/bin/env python3
"""
Microsoft Band 2 live physiological stream over Bluetooth RFCOMM.

Uses msband-lib-9th command framing for subscriptions and the local libband
sensor decoder for push packets.
"""
import math
import socket
import sys
import threading
import uuid
from collections import deque

from libband.sensors import Sensor, decode_sensor_reading


ADDRESS = sys.argv[1] if len(sys.argv) > 1 else "58:82:A8:CE:4E:C8"
CARGO_PORT = 4
PUSH_PORT = 5
RR_WINDOW = 20
PUSH_SERVICE_GUID = uuid.UUID(hex="d8895bfd0461400dbd52dbe2a3c33021")

latest = {"bpm": None, "rr": None, "rmssd": None, "gsr": None, "temp": None}
rr_buffer = deque(maxlen=RR_WINDOW)
lock = threading.Lock()


def calc_rmssd(values):
    if len(values) < 2:
        return None
    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    return math.sqrt(sum(diff * diff for diff in diffs) / len(diffs))


def print_status():
    with lock:
        bpm = f"{latest['bpm']:3d} bpm" if latest["bpm"] else "  - bpm"
        rr = f"RR: {latest['rr']:.0f} ms" if latest["rr"] else "RR:  --- ms"
        rmssd = f"HRV: {latest['rmssd']:.1f} ms" if latest["rmssd"] else "HRV: --.- ms"
        gsr = f"GSR: {latest['gsr']} kOhm" if latest["gsr"] is not None else "GSR:  ---  kOhm"
        temp = f"Temp: {latest['temp']:.1f} C" if latest["temp"] else "Temp:  --.- C"
    print(f"\033[2K\r{bpm}  |  {rr}  |  {rmssd}  |  {gsr}  |  {temp}", end="", flush=True)


def connect_rfcomm(port, timeout=8.0):
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(timeout)
    sock.connect((ADDRESS, port))
    return sock


def read_exact(sock, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("Bluetooth socket closed")
        chunks.extend(chunk)
    return bytes(chunks)


def command(cargo, packet, response_length=0, transfer=None):
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


def make_packet(command, data_length=0):
    return b"\xf9\x2e" + command.to_bytes(2, "little") + data_length.to_bytes(4, "little")


def subscribe(cargo, sensor):
    # LibraryRemoteSubscription.SubscribeId = facility 0x8f, code 7.
    # Register the sensor against the push service, otherwise the Band may
    # stream an existing/default subscription instead of the requested sensor.
    transfer = bytes([int(sensor)]) + b"\x00\x00\x00\x00" + PUSH_SERVICE_GUID.bytes_le
    packet = make_packet(0x8F07, data_length=len(transfer))
    command(cargo, packet, transfer=transfer)


def listen_push(push):
    buffer = bytearray()
    while True:
        chunk = push.recv(8192)
        if not chunk:
            raise ConnectionError("push socket closed")
        buffer.extend(chunk)

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
            if packet_length <= 0 or packet_length > 256:
                del buffer[0]
                continue
            if len(buffer) < frame_length:
                break

            packet = bytes(buffer[:frame_length])
            del buffer[:frame_length]

            packet_type = int.from_bytes(packet[:2], "little")
            if packet_type != 1:
                continue

            records = packet[6:]
            offset = 0
            while offset + 4 <= len(records):
                sample_size = int.from_bytes(records[offset + 2:offset + 4], "little")
                record_length = 4 + sample_size
                if sample_size <= 0 or offset + record_length > len(records):
                    break

                record = records[offset:offset + record_length]
                legacy_packet = (
                    packet[:2]
                    + record_length.to_bytes(4, "little")
                    + record
                )
                offset += record_length

                sensor = decode_sensor_reading(legacy_packet)
                sensor_type = sensor.subscription_type
                if not hasattr(sensor, "value"):
                    continue

                with lock:
                    if sensor_type == Sensor.HeartRate:
                        latest["bpm"] = sensor.value
                    elif sensor_type == Sensor.RRInterval:
                        rr_buffer.append(sensor.value)
                        latest["rr"] = sensor.value
                        latest["rmssd"] = calc_rmssd(list(rr_buffer))
                    elif sensor_type == Sensor.Gsr:
                        latest["gsr"] = sensor.value
                    elif sensor_type == Sensor.SkinTemperature:
                        latest["temp"] = sensor.value

                print_status()


def main():
    print(f"Connecting cargo RFCOMM {ADDRESS}:{CARGO_PORT}...")
    cargo = connect_rfcomm(CARGO_PORT)

    print(f"Connecting push RFCOMM {ADDRESS}:{PUSH_PORT}...")
    push = connect_rfcomm(PUSH_PORT)

    print("Subscribing sensors...")
    for sensor in (Sensor.HeartRate, Sensor.RRInterval, Sensor.Gsr, Sensor.SkinTemperature):
        subscribe(cargo, sensor)
        print(f"  subscribed {sensor.name}")

    print("Reading live data. Ctrl+C to stop.\n")
    try:
        listen_push(push)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        cargo.close()
        push.close()


if __name__ == "__main__":
    main()
