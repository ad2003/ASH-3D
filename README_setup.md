# ASH Spatial Room Designer
## 3D Room Layout + BRIR/SOFA Generator · Windows Setup Guide

---

## Übersicht

Der **ASH Room Designer** ist eine 3D-Benutzeroberfläche für ASH-Toolset.  
Du platzierst Lautsprecher per Drag & Drop im 3D-Raum, lädst SOFA-Dateien
und exportierst BRIRs / SOFA-Dateien direkt über ASH-Toolset.

```
Browser (Three.js 3D UI)
    ash_room_designer.html
         ↕ HTTP :8765
Python FastAPI Backend
    ash_backend.py
         ↕ Python import
ASH-Toolset
    ash_toolset.py
         ↕
WAV BRIRs · SOFA · Equalizer APO config
```

---

## 1. Voraussetzungen

- Windows 10 / 11
- Python 3.11+ → https://www.python.org/downloads/
- ASH-Toolset geklont: `git clone https://github.com/ShanonPearce/ASH-Toolset`
- (Optional) Equalizer APO installiert

---

## 2. Abhängigkeiten installieren

```bat
REM In der ASH-Toolset Umgebung oder einer neuen venv
pip install fastapi uvicorn python-multipart pydantic
pip install sofar scipy numpy

REM ASH-Toolset eigene Abhängigkeiten (falls noch nicht installiert)
pip install -r requirements.txt
```

---

## 3. Backend starten

```bat
REM Umgebungsvariable setzen (Pfad anpassen!)
set ASH_TOOLSET_PATH=C:\ASH-Toolset
set ASH_OUTPUT_DIR=C:\ASH-Outputs\RoomDesigner

REM Backend starten
python ash_backend.py
```

Ausgabe bei Erfolg:
```
============================================================
  ASH Room Designer Backend
============================================================
  ASH-Toolset path : C:\ASH-Toolset
  Output directory : C:\ASH-Outputs\RoomDesigner
  sofar available  : True
  scipy available  : True
  [OK]  ASH-Toolset found!

  Open in browser: ash_room_designer.html
  API docs:        http://localhost:8765/docs
============================================================
```

---

## 4. 3D UI öffnen

Einfach `ash_room_designer.html` im Browser öffnen (Chrome oder Firefox empfohlen):

```bat
start ash_room_designer.html
```

Dann oben rechts auf **[Connect]** klicken → Backend wird verbunden.

---

## 5. Bedienung

### Raum konfigurieren
- **Width / Depth / Height**: Raumdimensionen in Metern eingeben
- **Room Presets**: Schnellstart mit Cinema, Home Theater, Studio, Concert Hall

### Lautsprecher platzieren
- **Speaker Layout**: 2.0 Stereo / 5.1 / 7.1 / 7.1.4 Atmos wählen
- **Drag & Drop**: Lautsprecher direkt in der 3D-Ansicht verschieben
- **Exakte Eingabe**: Nach Klick auf einen Lautsprecher → X/Y/Z im Infopanel eingeben
- **Azimuth / Elevation / Distanz**: Werden automatisch relativ zum Listener berechnet

### Kamera
| Geste | Funktion |
|---|---|
| Linke Maustaste + Ziehen | Orbit (Drehen) |
| Rechte Maustaste + Ziehen | Panning |
| Mausrad | Zoom |
| Klick auf Objekt | Auswählen |

Camera-Buttons: ⊤ Top-View · ▣ Front-View · ◈ Perspektive · ⌖ Reset

### SOFA / HRTF laden
**Option A – Direkt (JSON):**
```bat
python sofa_to_json.py C:\path\to\my_hrtf.sofa
```
Dann die generierte `_positions.json` in der UI per Klick auf den SOFA-Bereich laden.

**Option B – Via Backend:**
Die UI lädt SOFA-Dateien automatisch über das Backend (wenn verbunden).
Messpositionen erscheinen als grüne Punkte im 3D-Raum.

### ASH-Parameter einstellen
Rechts sidebar:
- **Acoustic Space**: Raumakustik (Control Room, Cinema, Concert Hall, ...)
- **Listener / HRTF**: KEMAR, KU100, FABIAN, eigene SOFA, ...
- **Direct Sound Gain**: -10 bis +10 dB (beeinflusst wahrgenommene Distanz)
- **Room Target**: Flat, Harman, X-Curve (Cinema), ASH Target
- **Headphone Compensation**: Over-Ear / In-Ear, High/Low
- **Spatial Resolution**: Low / Medium / High
- **Low Frequency Response**: A–K (verschiedene Basscharakter)
- **Sample Rate**: 44.1 / 48 / 96 kHz
- **Bit Depth**: 24 / 32 bit

### Exportieren
- **▶ Generate BRIR / SOFA**: Sendet alles ans Backend → ASH-Toolset erzeugt WAVs + SOFA
- **⬇ Export Room Config JSON**: Speichert alle Parameter als JSON (ohne Backend)

---

## 6. Ausgabedateien

Alle Dateien landen in `C:\ASH-Outputs\RoomDesigner\YYYYMMDD_HHMMSS\`:

| Datei | Beschreibung |
|---|---|
| `room_config.json` | Vollständige Raumkonfiguration |
| `BRIR_CHL_E+000_A-030.wav` | Binaural Room IR für Kanal L |
| `BRIR_CHR_E+000_A+030.wav` | Binaural Room IR für Kanal R |
| `room_brirs.sofa` | Alle BRIRs als SOFA-Datei |
| `eqapo_config.txt` | Fertige Equalizer APO Konfiguration |

### Equalizer APO einrichten
```bat
REM eqapo_config.txt nach EqualizerAPO\config\ kopieren
copy "C:\ASH-Outputs\RoomDesigner\...\eqapo_config.txt" "C:\Program Files\EqualizerAPO\config\config.txt"
```

---

## 7. Eigenen Raum als Kino konfigurieren (Beispiel)

1. Preset **Cinema** laden → 28m × 20m × 9m, 7.1.4 Atmos Layout
2. Leinwand ist sichtbar, Sitzreihen automatisch platziert
3. Lautsprecher per Drag auf gewünschte Positionen schieben
4. Rechts: Acoustic Space → **Cinema**, Room Target → **X-Curve (Cinema)**
5. Listener (Dummy Head) auf Hörposition ziehen
6. **▶ Generate** → fertige BRIRs

---

## 8. Troubleshooting

**"Backend: Offline"**
→ `python ash_backend.py` im Terminal starten
→ Firewall-Ausnahme für Port 8765 prüfen

**"ASH-Toolset not found"**
→ `set ASH_TOOLSET_PATH=C:\pfad\zu\ASH-Toolset` setzen
→ Auch ohne ASH-Toolset: synthetische Preview-BRIRs werden generiert

**SOFA-Dateien**
→ Zusätzliche SOFA-Dateien: https://www.sofaconventions.org/mediawiki/index.php/Files
→ ASH-Toolset user SOFA-Ordner: `C:\Program Files (x86)\ASH Toolset\_internal\data\user\SOFA`

**API-Dokumentation**
→ http://localhost:8765/docs (Swagger UI, nur wenn Backend läuft)

---

## Datei-Übersicht

```
ash_room_designer.html   ← 3D UI (einfach im Browser öffnen)
ash_backend.py           ← Python Backend starten
sofa_to_json.py          ← SOFA → JSON Konverter
README_setup.md          ← Diese Anleitung
```

---

## Lizenz

ASH-Toolset ist AGPL-3.0 lizenziert. Bitte die Lizenzbedingungen beachten.
