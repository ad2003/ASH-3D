# ASH Spatial Room Designer

3D Room Layout + BRIR / SOFA Generator

⚠️ SUPER PROTOTYPE — Experimental / Vibecoded Tool

This project is an experimental prototype UI for the ASH Toolset.

It was built quickly for experimentation and prototyping and comes with no guarantees, no support, and no stability promises.

Use at your own risk.

This tool allows you to visually design loudspeaker layouts in a 3D room environment, load SOFA / HRTF datasets, and generate BRIRs and SOFA files using the ASH Toolset backend.

---

# Overview

The ASH Room Designer is a 3D browser interface for the ASH Toolset.

You can:

• place speakers via drag & drop in a 3D room  
• load SOFA / HRTF datasets  
• generate BRIR WAV files and SOFA files  
• export configurations for Equalizer APO  

Architecture:

Browser (Three.js 3D UI)

ash_room_designer.html  
↕ HTTP :8765  

Python FastAPI Backend  

ash_backend.py  
↕ Python import  

ASH Toolset  

ash_toolset.py  

Outputs  

WAV BRIRs  
SOFA  
Equalizer APO config

---

# 1. Requirements

• Windows 10 / 11  
• Python 3.11+  

Download Python:  
https://www.python.org/downloads/

Clone the ASH Toolset:

git clone https://github.com/ShanonPearce/ASH-Toolset

Optional:

Equalizer APO installed

---

# 2. Install Dependencies

Inside the ASH Toolset environment or a new virtual environment:

pip install fastapi uvicorn python-multipart pydantic  
pip install sofar scipy numpy  

Install ASH Toolset dependencies if not already installed:

pip install -r requirements.txt

---

# 3. Start Backend

Set environment variables (adjust paths):

set ASH_TOOLSET_PATH=C:\ASH-Toolset  
set ASH_OUTPUT_DIR=C:\ASH-Outputs\RoomDesigner  

Start backend:

python ash_backend.py

Successful startup output:

============================================================
ASH Room Designer Backend
============================================================
ASH-Toolset path : C:\ASH-Toolset
Output directory : C:\ASH-Outputs\RoomDesigner
sofar available  : True
scipy available  : True
[OK] ASH-Toolset found!

Open in browser: ash_room_designer.html  
API docs: http://localhost:8765/docs
============================================================

---

# 4. Open the 3D UI

Open the UI directly in your browser:

start ash_room_designer.html

Recommended browsers:

Chrome  
Firefox  

Click **Connect** in the top-right corner to connect to the backend.

---

# 5. Usage

## Configure Room

Set room dimensions in meters:

Width  
Depth  
Height  

Room presets available:

Cinema  
Home Theater  
Studio  
Concert Hall  

---

## Place Speakers

Choose a layout:

2.0 Stereo  
5.1  
7.1  
7.1  
7.1.4 Atmos  

Speakers can be moved via drag & drop in the 3D scene.

You can also enter exact coordinates (X/Y/Z) after selecting a speaker.

The UI automatically calculates:

Azimuth  
Elevation  
Distance relative to the listener

---

## Camera Controls

Left mouse drag → Orbit  
Right mouse drag → Pan  
Mouse wheel → Zoom  
Click object → Select  

Camera buttons:

Top View  
Front View  
Perspective  
Reset  

---

# Loading SOFA / HRTF

Option A — Direct JSON

Convert SOFA to JSON:

python sofa_to_json.py C:\path\to\my_hrtf.sofa

Load the generated `_positions.json` inside the UI.

---

Option B — Backend

If the backend is connected, SOFA files can be loaded automatically.

Measurement positions appear as green points in the 3D room.

---

# ASH Parameters

Right sidebar parameters include:

Acoustic Space  
(Control Room, Cinema, Concert Hall)

Listener / HRTF  
(KEMAR, KU100, FABIAN, custom SOFA)

Direct Sound Gain  
-10 to +10 dB

Room Target

Flat  
Harman  
X-Curve (Cinema)  
ASH Target

Headphone Compensation

Over-Ear  
In-Ear

Spatial Resolution

Low  
Medium  
High

Low Frequency Response

Profiles A–K (different bass characteristics)

Sample Rate

44.1 kHz  
48 kHz  
96 kHz  

Bit Depth

24 bit  
32 bit  

---

# Export

Generate BRIR / SOFA

Sends the configuration to the backend → ASH Toolset generates WAV + SOFA files.

Export Room Config JSON

Exports the room configuration locally without the backend.

---

# Output Files

Generated files appear in:

C:\ASH-Outputs\RoomDesigner\YYYYMMDD_HHMMSS\

Files:

room_config.json  
Full room configuration

BRIR_CHL_E+000_A-030.wav  
Binaural room IR for left channel

BRIR_CHR_E+000_A+030.wav  
Binaural room IR for right channel

room_brirs.sofa  
All BRIRs as SOFA file

eqapo_config.txt  
Ready-to-use Equalizer APO configuration

---

# Equalizer APO Setup

Copy the config file:

copy "C:\ASH-Outputs\RoomDesigner\...\eqapo_config.txt" "C:\Program Files\EqualizerAPO\config\config.txt"

---

# Example: Configure a Cinema Room

Load the Cinema preset.

Room size:

28m × 20m × 9m

Layout:

7.1.4 Atmos

Steps:

1. Move speakers to desired positions
2. Set Acoustic Space → Cinema
3. Set Room Target → X-Curve
4. Place listener (dummy head) at listening position
5. Click Generate

ASH Toolset generates the final BRIRs.

---

# Troubleshooting

Backend Offline

Start backend:

python ash_backend.py

Check firewall access for port 8765.

---

ASH Toolset Not Found

Set environment variable correctly:

set ASH_TOOLSET_PATH=C:\path\to\ASH-Toolset

Without ASH Toolset the backend may generate synthetic preview BRIRs only.

---

SOFA Files

Additional SOFA datasets:

https://www.sofaconventions.org/mediawiki/index.php/Files

ASH Toolset user SOFA folder:

C:\Program Files (x86)\ASH Toolset\_internal\data\user\SOFA

---

API Documentation

Available when backend is running:

http://localhost:8765/docs

(FastAPI Swagger UI)

---

# File Overview

ash_room_designer.html  
3D UI (open in browser)

ash_backend.py  
Python backend

sofa_to_json.py  
SOFA → JSON converter

README.md  
This documentation

---

# License

The ASH Toolset is licensed under AGPL-3.0.

Please respect the license terms when using or modifying this project.