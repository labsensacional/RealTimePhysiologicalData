#!/usr/bin/env python3
"""
Completa el OOBE del Microsoft Band 2 sin la app oficial.
Usa socket raw — sin esperar respuestas que nunca llegan en OOBE mode.
"""
import sys
import errno
import struct
import time
import bluetooth
from libband.commands import OOBE_FINALIZE, CARGO_SYSTEM_SETTINGS_OOBE_COMPLETED_SET

ADDRESS = sys.argv[1] if len(sys.argv) > 1 else "58:82:A8:CE:4E:C8"
PORT = 4   # CARGO_SERVICE_PORT
MAGIC = 12025

def make_packet(command, args=b''):
    packet = bytes([8 + len(args)])
    packet += struct.pack("<H", MAGIC)
    packet += struct.pack("<H", command)
    packet += struct.pack("<I", 0)  # data_stage_size = 0
    packet += args
    return packet

print(f"Conectando a {ADDRESS} port {PORT}...")
sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
sock.connect((ADDRESS, PORT))
print("Conectado.")
time.sleep(0.2)  # dar tiempo a que el link se estabilice

print("Enviando OOBE_FINALIZE...")
try:
    sock.send(make_packet(OOBE_FINALIZE))
except OSError as exc:
    if getattr(exc, "errno", None) == errno.EFAULT:
        print(
            "El Band aceptó la conexión, pero rechazó el send() con EFAULT. "
            "En este estado no se puede completar OOBE desde RFCOMM raw; "
            "usá la app antigua de Microsoft Band para terminar el setup."
        )
        sock.close()
        sys.exit(1)
    raise
time.sleep(0.3)

print("Enviando OOBE_COMPLETED_SET...")
try:
    sock.send(make_packet(CARGO_SYSTEM_SETTINGS_OOBE_COMPLETED_SET, struct.pack("<I", 1)))
except OSError as exc:
    if getattr(exc, "errno", None) == errno.EFAULT:
        print(
            "El Band rechazó OOBE_COMPLETED_SET con EFAULT. Terminá OOBE "
            "desde la app antigua de Microsoft Band y luego corré band2.py."
        )
        sock.close()
        sys.exit(1)
    raise
time.sleep(0.5)

# Intentar leer respuesta sin bloquear
sock.setblocking(False)
try:
    resp = sock.recv(256)
    print(f"Respuesta: {resp.hex()}")
except:
    print("Sin respuesta (normal en OOBE mode)")

sock.close()
print("\nListo. Ahora corré desde microsoft_band/: python3 band2.py 58:82:A8:CE:4E:C8")
