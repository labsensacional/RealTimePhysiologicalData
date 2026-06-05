# Plan de testeo de métricas fisiológicas

## Estrategia general: provocar estados conocidos

En lugar de validar en condiciones naturales (donde no sabés qué debería estar pasando), inducís estados con efectos fisiológicos bien documentados y verificás que tus métricas respondan como la literatura predice.

---

## Validación por métrica

### GSR (arousal simpático)

El más fácil de testear. Estímulos con respuesta casi garantizada:

- Ruido blanco o estallido sonoro inesperado → pico nítido en 1-3 segundos
- Imagen aversiva o sorpresiva → idem
- Breath hold de 15-20 segundos → sube sostenido

Si el GSR no muestra pico limpio ante un susto, hay problema de señal. La forma del pico (rise rápido, decay exponencial lento) es también diagnóstica — si no tiene esa forma, probablemente hay artefacto de movimiento.

### FC / HRV

- Respiración pautada a 6 ciclos/min durante 2 minutos → HRV debería maximizarse notablemente (protocolo de coherencia cardíaca, muy replicado)
- Maniobra de Valsalva (empujar contra glotis cerrada) → FC baja bruscamente al soltar; es un test clínico estándar de tono vagal
- Subir escaleras o 20 sentadillas → FC sube predeciblemente; verificar latencia y magnitud

Para HRV específicamente: comparar la extracción de R-R contra una app de referencia (Elite HRV, o Polar H10 si hay acceso) en simultáneo es el test más directo.

### Temperatura cutánea

- Sumergir la mano en agua fría → baja rápido
- Relajación / calor ambiente → sube lentamente

Útil para verificar dirección y latencia, no tanto precisión absoluta.

### Acelerómetro (quietud / trance)

- Sentarse completamente quieto 2 minutos vs. moverse deliberadamente → el contraste tiene que ser enorme y limpio
- Verificar que los micro-movimientos involuntarios (respiración, pulso) se capturan pero pueden filtrarse

---

## Herramientas de debugging

**Visualización en tiempo real es no-negociable.** Sin ver la señal cruda mientras pasan las cosas, se adivina. Lo mínimo:

- Plot de señal cruda scrolling (tipo osciloscopio)
- Overlay de la métrica derivada (ej. GSR smoothed) sobre la cruda
- Marcador de eventos manual (tecla que hace stamp "ahora hice X")

Opciones: `pyqtgraph` o `matplotlib` animado en Python; Node-RED con nodos de dashboard para algo más rápido de levantar.

**Logging con timestamps y event markers** — sin esto no es posible hacer análisis post-hoc. Cada vez que se induce un estado, marcarlo. Permite hacer epoch averaging (promediar la respuesta a N repeticiones del mismo estímulo), que cancela ruido y revela la señal real.

**Separar ruido de movimiento** — el artefacto más común en wearables. Test simple: registrar sentado quieto vs. agitando el brazo. Si GSR o temperatura cambian con movimiento, hay contaminación que requiere filtrado (o fusión con acelerómetro para detectar y excluir esos segmentos).

---

## Protocolo de validación rápida (~30 min)

| Paso | Duración | Acción | Métrica a verificar |
|------|----------|--------|---------------------|
| 1 | 2 min | Baseline quieto, respiración normal | Todas (referencia) |
| 2 | ~5 min | 5 estallidos sonoros espaciados (auriculares, volumen moderado) | GSR |
| 3 | 2 min | Respiración a 6 ciclos/min | HRV |
| 4 | ~3 min | Maniobra de Valsalva x3 | FC |
| 5 | 2 min | Movimiento deliberado del brazo | Detección de artefactos |

Con esto se obtiene una imagen bastante completa de qué funciona y qué no antes de meterse en sesiones reales.
