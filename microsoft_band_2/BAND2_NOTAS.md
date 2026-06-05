# Microsoft Band 2 desde Linux — Notas técnicas

## Objetivo
Leer en tiempo real HR, HRV (RR intervals), GSR (galvanic skin response) y temperatura de piel desde una Microsoft Band 2, conectado a Ubuntu 24.04 (Python 3.12).

---

## Hardware
- **Dispositivo:** Microsoft Band 2, MAC `58:82:A8:CE:4E:C8`
- **Protocolo:** Bluetooth Classic RFCOMM (no BLE)
- **Puertos RFCOMM:**
  - Port 4 = CARGO (comandos)
  - Port 5 = PUSH SERVICE (stream de datos de sensores)

---

## Librería base
`libmsftband` — https://github.com/ksiazkowicz/libmsftband  
No tiene `setup.py`, se usa copiando `libband/` directamente al proyecto.

**Dependencias:**
```bash
sudo apt install python3-bluetooth
pip install unidecode geocoder --break-system-packages
```

**Fix requerido para Python 3.12** en `libband/versions.py`:
```python
# Cambiar esto:
from dataclasses import dataclass

@dataclass
class DeviceVersion:
    bootloader: FirmwareVersion = FirmwareVersion()
    ...

# Por esto:
from dataclasses import dataclass, field

@dataclass
class DeviceVersion:
    bootloader: FirmwareVersion = field(default_factory=FirmwareVersion)
    application: FirmwareVersion = field(default_factory=FirmwareVersion)
    updater: FirmwareVersion = field(default_factory=FirmwareVersion)
```

---

## Estado actual: OOBE incompleto detectado correctamente
El Band 2 fue factory-reseteado y está en estado OOBE (Out Of Box Experience), pantalla: **"Pairing success! Return to the Microsoft Band app to continue"**.

**Síntoma original:** `device.connect()` llama a `get_firmware_version()` internamente, que espera respuesta del cargo socket. En OOBE mode el Band acepta la conexión RFCOMM en port 4 pero nunca responde a comandos → `receive()` hacía timeout → reconectaba → loop infinito. Nunca se llegaba a enviar comandos ni suscribirse a sensores.

**Resuelto en código:** `libband/socket.py` ahora corta con `BandSocketTimeout` cuando no hay respuesta y con `BandSocketSendError` cuando `send()` falla con `EFAULT`. `band2.py` captura esos casos y muestra la instrucción correcta en lugar de quedar bloqueado.

**Intento de bypass:** Enviar `OOBE_FINALIZE` y `CARGO_SYSTEM_SETTINGS_OOBE_COMPLETED_SET` vía socket raw → falla con `EFAULT (errno 14)` al hacer `sock.send()`. El Band acepta el `connect()` pero rechaza el send.

**Solución operativa:** Completar OOBE instalando el APK viejo de la app de Microsoft Band (v1.3.31002.2) desde APKMirror en un Android. Una vez que el Band salga del estado OOBE, el cargo socket acepta comandos normalmente.

**Fixes adicionales aplicados:**
- `RRInterval` ahora se expone en milisegundos, como espera `band2.py` para calcular RMSSD.
- `Gsr` ahora se expone como entero en lugar de tupla.
- `oobe_bypass.py` informa explícitamente el caso `EFAULT` y termina con código de error.

---

## Código en `/home/mathigatti/Desktop/domotica/microsoft_band/`
- `band2.py` — script principal con display en tiempo real de BPM, RR, HRV (RMSSD), GSR, temperatura
- `band2_live.py` — lector Bluetooth RFCOMM confirmado para datos fisiológicos en vivo
- `band2_dashboard.py` — dashboard web local con métricas en vivo
- `oobe_bypass.py` — intento de bypass OOBE vía socket raw (fallido por ahora)
- `msband_oobe_usb.py` — bypass OOBE por USB usando `msband-lib-9th`
- `BAND2_OOBE_USB_RUNBOOK.md` — procedimiento reproducible completo
- `libband/` — librería copiada localmente con el fix de Python 3.12 ya aplicado

## Resultado OOBE USB confirmado
El bypass programático por USB funcionó con `msband_oobe_usb.py --unlock`.

Estado antes:
```text
WhoAmI: 3
OOBE complete: False
OOBE stage: 4
```

Estado después:
```text
WhoAmI: 3
OOBE complete: True
OOBE stage: NotInOobe
```

Verificación posterior por Bluetooth RFCOMM con `msband-lib-9th`:
```text
CoreModuleWhoAmI -> 3
SystemSettingsOobeCompleteGet -> True
CoreModuleGetVersion -> App 2.0.5301, PCBId 26
```

Lectura fisiológica en vivo confirmada con `band2_live.py`:
```text
59 bpm | RR: 1095 ms | HRV: 176.6 ms | GSR: 8637 kOhm | Temp: 28.6 C
58 bpm | RR: 913 ms  | HRV: 167.9 ms | GSR: 8637 kOhm | Temp: 28.6 C
```

---

## Sensores disponibles (una vez que OOBE esté completo)
Todos implementados y listos en `libband/sensors.py`:

| Sensor | ID | Estado |
|--------|----|--------|
| HeartRate | 16 | ✅ |
| RRInterval | 26 | ✅ |
| Gsr | 15 | ✅ |
| SkinTemperature | 20 | ✅ |
| Accelerometer (16/32/128ms) | 0,1,48 | ✅ |
| AccelerometerGyroscope | 4,5,49 | ✅ |
| UV, AmbientLight, BatteryGauge | varios | ✅ |
| Gsr200MS, Barometer, Elevation | varios | ✅ |

## Métricas confirmadas
Estas son las métricas que hoy se pueden leer desde la Band 2 y que el dashboard
web muestra o calcula en vivo:

| Grupo | Métrica | Unidad / formato | Fuente |
|-------|---------|------------------|--------|
| Cardiovascular | Frecuencia cardíaca | bpm | `HeartRate` |
| Cardiovascular | RR interval | ms | `RRInterval` |
| Cardiovascular | HRV / RMSSD | ms | Calculado desde ventana reciente de RR |
| Conductancia | GSR | kOhm | `Gsr` |
| Conductancia | GSR rápido | kOhm | `Gsr200MS` |
| Temperatura | Temperatura de piel | C | `SkinTemperature` |
| Movimiento | Acelerómetro X/Y/Z | g | `Accelerometer` |
| Movimiento | Magnitud de aceleración | g | Calculado desde X/Y/Z |
| Movimiento | Giroscopio X/Y/Z | deg/s | `AccelerometerGyroscope` |
| Movimiento | Magnitud de giro | deg/s | Calculado desde X/Y/Z |
| Actividad | Pasos totales | pasos | `Pedometer` |
| Actividad | Pasos del día | pasos | `Pedometer` |
| Actividad | Distancia | m | `Distance` |
| Actividad | Velocidad | m/s | `Distance` |
| Actividad | Ritmo | min/km | Calculado desde velocidad |
| Actividad | Movimiento detectado | texto | `Distance` |
| Energía | Calorías | kcal | `Calories` |
| Ambiente | UV | nivel textual | `UV` |
| Ambiente | Luz ambiente | lx | `AmbientLightWithDailyValues` |
| Ambiente | Presión barométrica | hPa | `Barometer` |
| Ambiente | Temperatura barométrica | C | `Barometer` |
| Elevación | Altitud estimada | m | `Elevation` |
| Elevación | Ganancia/pérdida acumulada | m | `Elevation`, `ElevationWithDailyValues` |
| Elevación | Ganancia por pasos | m | `Elevation`, `ElevationWithDailyValues` |
| Elevación | Pasos/pisos ascendidos y descendidos | conteos | `Elevation`, `ElevationWithDailyValues` |
| Elevación | Rate vertical | cm/s | `Elevation`, `ElevationWithDailyValues` |
| Dispositivo | Batería | %, mV, alertas | `BatteryGauge` |
| Dispositivo | Estado de contacto | booleano / texto | `Contact` |

Notas:
- `AmbientLightWithDailyValues` devuelve la luz ambiente en lux. Durante una
  prueba inicial quedaba vacío porque el decoder esperaba 6 bytes, pero la
  muestra real llegaba con 2 bytes; ya está corregido.
- `HRV / RMSSD`, magnitudes de acelerómetro/giroscopio y ritmo no vienen como
  campos directos de la Band: se calculan en `band2_dashboard.py`.
- `Gsr200MS` usa el mismo payload que `Gsr`, pero llega con mayor frecuencia
  cuando se suscribe ese sensor.
- `Barometer` quedó validado contra payload real: presión en hPa y temperatura
  del sensor barométrico en C.
- `Elevation` y `ElevationWithDailyValues` emiten datos reales. El decoder
  expone altitud estimada, ganancia/pérdida acumulada, ganancia/pérdida por
  pasos, pasos/pisos y rate vertical. El layout fue inferido desde payloads
  reales y nombres de campos del SDK, así que conviene revalidarlo si aparecen
  valores no nulos de pisos/pasos en actividad real.

Última muestra confirmada desde `band2_dashboard.py`:

```text
barometer_hpa = 1019.18
barometer_temp_c = 28.30
elevation_altitude_m = 26.72
elevation_gain_m = 20.50
elevation_loss_m = 12.46
elevation_rate_cms = 0.23
gsr_200ms_kohm = 4075
```
