# Microsoft Band 2: desbloquear OOBE por USB y leer sensores desde Linux

Este runbook documenta el procedimiento probado en Ubuntu 24.04 / Python 3.12
para sacar una Microsoft Band 2 de OOBE sin usar la app de Android, y dejarla
lista para leer datos fisiologicos por Bluetooth RFCOMM.

## Estado inicial observado

- Dispositivo: Microsoft Band 2.
- MAC Bluetooth usada: `58:82:A8:CE:4E:C8`.
- USB ID observado: `045e:02d6 Microsoft Corp. Microsoft Band 2`.
- Pantalla del Band antes del unlock:
  `"Pairing success! Return to the Microsoft Band app to continue"`.
- Ese estado corresponde a OOBE stage `4` (`PairingSuccess`).
- El Band acepta conexion Bluetooth RFCOMM al puerto cargo, pero en OOBE no
  permite completar el flujo simple desde `libmsftband`.

## Que es OOBE

OOBE significa "Out Of Box Experience": el setup inicial del dispositivo despues
de un factory reset. No alcanza con que Bluetooth este emparejado; el firmware
todavia espera que una app/host complete configuracion, hora, zona horaria,
perfil y stage final.

Mientras OOBE no esta completo, no se pueden usar normalmente los comandos para
suscribirse a sensores.

## Hallazgo clave

La libreria local vieja (`libband/`, derivada de `libmsftband`) solo trae
constantes OOBE basicas. Intentar mandar directamente:

- `OOBE_FINALIZE`
- `CARGO_SYSTEM_SETTINGS_OOBE_COMPLETED_SET`

por Bluetooth RFCOMM no alcanzo. En estado OOBE el Band llego a aceptar
`connect()`, pero rechazo `send()` con `EFAULT`.

La solucion programatica encontrada fue usar el proyecto:

```text
https://github.com/MicrosoftBandDev/msband-lib-9th
```

Ese proyecto incluye `examples/escape_oobe.py` y soporta explicitamente
"Escaping OOBE (on Band 2)". El metodo probado funciona por USB.

## Secuencia OOBE programatica

La secuencia real no es solo finalizar OOBE. El flujo de `msband-lib-9th` hace:

1. Verificar `CoreModuleWhoAmI == FirmwareApp.App`.
2. Verificar `SystemSettingsOobeCompleteGet == False`.
3. Leer el perfil con `ProfileGetDataApp`.
4. Leer stage con `OobeGetStage`.
5. Setear stages:
   - `CheckingForUpdate`
   - `StartingUpdate`
   - `UpdateComplete`
6. Setear hora UTC con `TimeSetUtcTime`.
7. Navegar a pantalla `OobeBoot`.
8. Escribir ephemeris dummy de 130 bytes.
9. Setear timezone (`GMT` en el script usado).
10. Setear stage `WaitingOnPhoneToCompleteOobe`.
11. Actualizar perfil:
    - `DeviceName = "Liberated Band"`
    - `Telemetry = False`
    - `LastSync` y timestamps de cambios con fecha/hora actual UTC.
12. Guardar perfil con `ProfileSetDataApp`.
13. Ejecutar `OobeFinalize`.

## Dependencias usadas

Todo el codigo de Microsoft Band vive en:

```text
/home/mathigatti/Desktop/domotica/microsoft_band
```

Entrar a esa carpeta antes de correr los comandos:

```bash
cd /home/mathigatti/Desktop/domotica/microsoft_band
```

Se creo un venv local dentro de esa carpeta:

```bash
python3 -m venv .venv-msband
.venv-msband/bin/python -m pip install construct construct-typing pyusb pillow
```

Se clono la libreria nueva en `/tmp`:

```bash
git clone --depth 1 https://github.com/MicrosoftBandDev/msband-lib-9th.git /tmp/msband-lib-9th
```

## Permisos USB

Con el Band conectado por USB, verificar que Linux lo vea:

```bash
lsusb | rg '045e|Band|Microsoft'
```

Salida observada:

```text
Bus 001 Device 104: ID 045e:02d6 Microsoft Corp. Microsoft Band 2
```

PyUSB fallo inicialmente con:

```text
usb.core.USBError: [Errno 13] Access denied (insufficient permissions)
```

El nodo observado era:

```text
/dev/bus/usb/001/104
crw-rw-r-- 1 root root ...
```

Solucion rapida usada:

```bash
sudo chmod a+rw /dev/bus/usb/001/104
```

Nota: el numero `104` cambia al reconectar. Para una solucion permanente,
crear una regla udev para vendor `045e`, product `02d6`.

## Script local creado

Se agrego:

```text
msband_oobe_usb.py
```

Uso:

```bash
.venv-msband/bin/python msband_oobe_usb.py --status
.venv-msband/bin/python msband_oobe_usb.py --unlock
```

El script importa `msband-lib-9th` desde:

```text
/tmp/msband-lib-9th/src
```

Se puede cambiar con:

```bash
MSBAND_LIB_PATH=/ruta/a/msband-lib-9th/src .venv-msband/bin/python msband_oobe_usb.py --status
```

## Resultado confirmado

Antes del unlock:

```text
WhoAmI: 3
OOBE complete: False
OOBE stage: 4
```

Despues de ejecutar:

```bash
.venv-msband/bin/python msband_oobe_usb.py --unlock
```

Salida final:

```text
WhoAmI: 3
OOBE complete: True
OOBE stage: (b'\xff\xff', <Status.NotInOobe: ...>)
```

Ese `NotInOobe` al leer stage despues del unlock es esperado: el dispositivo ya
no esta en modo OOBE.

## Verificacion Bluetooth posterior

Con `msband-lib-9th` por Bluetooth RFCOMM se verifico:

```text
CoreModuleWhoAmI -> 3
SystemSettingsOobeCompleteGet -> True
CoreModuleGetVersion -> App 2.0.5301, PCBId 26
```

Esto confirma que:

- El Band salio de OOBE.
- Bluetooth RFCOMM cargo responde comandos.
- El siguiente paso es desconectar USB, ponerse la pulsera y leer sensores en
  vivo por Bluetooth.

## Lectura fisiologica en vivo confirmada

Se agrego y probo:

```text
band2_live.py
```

Este script usa Bluetooth RFCOMM:

- Puerto 4: cargo/comandos.
- Puerto 5: push stream.

Hallazgo importante: para sensores en vivo conviene usar
`RemoteSubscriptionSubscribeId` (`0x8f07`) con el GUID del push service:

```text
d8895bfd-0461-400d-bd52-dbe2a3c33021
```

Usar `RemoteSubscriptionSubscribe` simple (`0x8f00`) puede terminar recibiendo
una subscripcion default/existente, por ejemplo acelerometro tipo `0`.

El push stream puede agrupar multiples lecturas dentro de un solo frame. El
formato observado:

```text
packet_type: 2 bytes, little endian, 1 = RemoteSubscription
packet_length: 4 bytes, little endian, longitud de records agregados
records:
  sensor_type: 1 byte
  missed_samples: 1 byte
  sample_size: 2 bytes, little endian
  sample: sample_size bytes
```

`band2_live.py` parsea esos subregistros y reutiliza los decoders de
`libband/sensors.py`.

Ejecucion:

```bash
python3 band2_live.py 58:82:A8:CE:4E:C8
```

Salida real confirmada con la pulsera puesta:

```text
59 bpm | RR: 1095 ms | HRV: 176.6 ms | GSR: 8637 kOhm | Temp: 28.6 C
58 bpm | RR: 913 ms  | HRV: 167.9 ms | GSR: 8637 kOhm | Temp: 28.6 C
```

Metricas adicionales confirmadas en el dashboard:

```text
accel_x/y/z, accel_mag
gyro_x/y/z, gyro_mag
wearing/contact = true
battery = 86%
steps = 94
distance = 75.36 m
calories = 69
uv = NoUV
```

`AmbientLight` esta implementado y suscripto, pero durante la prueba quedo sin
muestra (`null`). Puede depender de firmware/estado de pantalla/condiciones de
luz.

## Cambios hechos en la libreria local

Se corrigio `libband/versions.py` para Python 3.12 usando
`field(default_factory=...)` en dataclasses.

Se corrigio `libband/sensors.py`:

- `RRInterval` ahora se expone en milisegundos.
- `Gsr` ahora devuelve entero, no tupla.
- Algunos `struct.unpack` nativos (`L`, `H`, etc.) se cambiaron a formatos
  little-endian explicitos para evitar errores en Linux 64-bit.

Se corrigio `libband/socket.py`:

- Ya no queda en loop infinito en timeout OOBE.
- Agrega `BandSocketTimeout`.
- Agrega `BandSocketSendError` para `EFAULT`.

Se corrigio `band2.py`:

- Captura errores OOBE y muestra instruccion clara.
- Ya no intenta finalizar OOBE por Bluetooth en cada arranque.

## Flujo final recomendado

1. Si el Band esta en factory reset/OOBE:

```bash
cd /home/mathigatti/Desktop/domotica/microsoft_band
git clone --depth 1 https://github.com/MicrosoftBandDev/msband-lib-9th.git /tmp/msband-lib-9th
python3 -m venv .venv-msband
.venv-msband/bin/python -m pip install construct construct-typing pyusb pillow
lsusb | rg '045e|Band|Microsoft'
sudo chmod a+rw /dev/bus/usb/XXX/YYY
.venv-msband/bin/python msband_oobe_usb.py --status
.venv-msband/bin/python msband_oobe_usb.py --unlock
```

2. Desconectar USB.
3. Ponerse la Band ajustada en la muneca.
4. Ejecutar lector Bluetooth:

```bash
cd /home/mathigatti/Desktop/domotica/microsoft_band
python3 band2.py 58:82:A8:CE:4E:C8
```

Dashboard web:

```bash
cd /home/mathigatti/Desktop/domotica/microsoft_band
python3 band2_dashboard.py 58:82:A8:CE:4E:C8
```

Abrir:

```text
http://127.0.0.1:8000
```

## Notas de cuidado

- El unlock USB modifica estado interno del dispositivo. No interrumpirlo a la
  mitad.
- Si el Band esta en stage `PreStateLanguageSelect`, seleccionar idioma en la
  Band antes de correr unlock.
- Si el Band esta en `PreStateCharging`, ponerlo en pantalla de setup antes de
  correr unlock.
- Si se reconecta USB, el nodo `/dev/bus/usb/...` cambia y hay que volver a dar
  permisos o usar una regla udev.
