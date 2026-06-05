#!/usr/bin/env python3
"""
Probe Microsoft Band 2 sensor payloads that are not fully decoded yet.

Usage:
    python3 band2_probe_missing.py 58:82:A8:CE:4E:C8 --seconds 30
"""
import argparse
import socket
import time
import uuid

from libband.sensors import Sensor, decode_sensor_reading


CARGO_PORT = 4
PUSH_PORT = 5
PUSH_SERVICE_GUID = uuid.UUID(hex="d8895bfd0461400dbd52dbe2a3c33021")


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


def print_record(record):
    sensor_id = record[0]
    missed = record[1]
    sample_size = int.from_bytes(record[2:4], "little")
    sample = record[4:]
    name = Sensor(sensor_id).name if sensor_id in Sensor._value2member_map_ else f"Unknown({sensor_id})"
    legacy_packet = b"\x01\x00" + len(record).to_bytes(4, "little") + record
    decoded = decode_sensor_reading(legacy_packet)
    fields = {
        key: value
        for key, value in vars(decoded).items()
        if not key.startswith("_")
        and key not in {"packet_length", "subscription_type", "missed_samples", "sample_size"}
    }
    print(
        f"{time.strftime('%H:%M:%S')} {name:<24} "
        f"missed={missed:<3} size={sample_size:<3} raw={sample.hex()} decoded={fields}",
        flush=True,
    )


def listen(push, seconds):
    deadline = time.monotonic() + seconds
    buffer = bytearray()
    seen = 0
    while time.monotonic() < deadline:
        try:
            chunk = push.recv(8192)
        except socket.timeout:
            continue
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
                print_record(records[offset:offset + record_length])
                seen += 1
                offset += record_length
    print(f"total_records={seen}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("address")
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()

    sensors = (
        Sensor.Gsr,
        Sensor.Gsr200MS,
        Sensor.Barometer,
        Sensor.Elevation,
        Sensor.ElevationWithDailyValues,
    )

    cargo = connect_rfcomm(args.address, CARGO_PORT)
    push = connect_rfcomm(args.address, PUSH_PORT)
    push.settimeout(1.0)
    try:
        for sensor in sensors:
            try:
                subscribe(cargo, sensor)
                print(f"subscribed {sensor.name}")
            except Exception as exc:
                print(f"subscribe failed {sensor.name}: {exc}")
        listen(push, args.seconds)
    finally:
        push.close()
        cargo.close()


if __name__ == "__main__":
    main()
