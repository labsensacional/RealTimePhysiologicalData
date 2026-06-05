# Análisis HRV — Polar H9

## Hallazgos de la sesión 2026-06-02

Datos: ~18 min, arousal sentado → parado → eyaculación → recuperación.

### Patrones observados (con RR filtrado 300–2000ms, ectópicos <25%)

| Fase | BPM | RMSSD |
|------|-----|-------|
| Baseline | 63 | 46 ms |
| Arousal temprano | 65 | 44 ms |
| Plateau (~90 bpm) | 90 | 32 ms |
| Pre-orgasmo ascenso | 61→120 | 72 ms → 29 ms |
| Post-orgasmo inmediato | 110→80 | **8–13 ms** |
| Recuperación (8 min) | 73 | 43 ms |

### Firma del orgasmo
- RMSSD **sube** durante el ascenso de BPM (RSA amplificada por respiración profunda)
- Colapsa bruscamente cuando BPM cruza ~100 (~20–25s antes)
- Mínimo absoluto (8–13ms) en los primeros segundos post-orgasmo
- Recuperación limpia en ~30s — patrón único, sin equivalente en estrés

### Artefactos de movimiento
- "Brazo intenso": solo 16% de RR válidos → movimiento lateral del strap, no electrodos secos
- Solución parcial: strap más tenso, módulo centrado, subir un par de cm sobre el pectoral

---

## Diferenciación arousal estrés vs arousal placentero

### Por qué es difícil
El SNA no distingue causa, solo intensidad. BPM sube y RMSSD cae en ambos casos.

### Discriminadores útiles

**RMSSD basal antes del evento**
- Estrés: ya viene bajo antes del estímulo
- Placer: basal normal/alto, la caída ocurre durante la activación

**Forma del arco de BPM**
- Estrés agudo: pico brusco → caída lenta
- Arousal sexual: subida gradual → pico → caída muy brusca post-orgasmo
- Estrés crónico: BPM elevado sostenido sin pico claro

**Recuperación post-evento**
- Post-orgasmo: RMSSD y BPM vuelven a baseline en 5–10 min
- Post-estrés: recuperación lenta, RMSSD puede quedar bajo por horas

**Trayectoria como clasificador (no valor puntual)**
```
RMSSD alto + BPM subiendo lento  → arousal placentero temprano
RMSSD cae + BPM cruza ~100       → climax o estrés intenso (ambiguos)
RMSSD colapsa + BPM cae rápido   → orgasmo (firma única)
RMSSD bajo sostenido sin pico    → estrés crónico / ansiedad
```

### HRV en frecuencia (requiere RR limpios, ventanas 2–5 min)
- Ratio LF/HF: arousal placentero conserva más potencia HF (parasimpático presente)
- Estrés suprime HF casi completamente

---

## Para mejorar el análisis

1. **Más sesiones de arousal** para calibrar umbrales personales
2. **Sesiones de referencia de estrés** (tarea cognitiva, película de terror) con mismo dispositivo
3. **Segundo canal** — temperatura cutánea o GSR discrimina mucho mejor estrés vs placer
4. Con solo H9: falsos positivos posibles (ejercicio intenso ≈ plateau sexual en BPM+RMSSD)

---

## Predicción de proximidad al orgasmo

- BPM solo: ~15–30s de anticipación cuando cruza ~100 bpm
- RMSSD filtrado suma un segundo canal de confirmación
- Con 5–10 sesiones se podría calibrar threshold personal
