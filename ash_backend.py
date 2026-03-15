"""
ash_backend.py  –  ASH Room Designer Backend v3
================================================
Optimiert für:
  - Windows SourceForge-Installation: C:\\Program Files\\ASH Toolset
  - Dolby Atmos 7.1.4 über Kopfhörer
  - Equalizer APO direkte Konfiguration
  - SOFA-Dateien für eigene HRTF
  - BRIR-Export für Roon, JRiver, HeSuVi

Starten:  python ash_backend.py
Port:     8765
"""

import sys, os, json, math, asyncio, logging, subprocess, shutil, re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

# ── Auto-install fehlende Pakete ─────────────────────────────────────
def _pip(pkg):
    try:
        __import__(pkg.split("[")[0].replace("-","_"))
    except ImportError:
        print(f"  [SETUP] pip install {pkg} ...")
        subprocess.check_call([sys.executable,"-m","pip","install",pkg,"-q"])

for _p in ["fastapi","uvicorn","python-multipart","pydantic","numpy","scipy","sofar","soundfile","h5py"]:
    _pip(_p)

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import numpy as np
import uvicorn

try:
    import sofar as sf_lib; SOFAR=True
except Exception: SOFAR=False; print("[WARN] sofar unavailable")

try:
    from scipy.signal import butter, sosfilt, fftconvolve
    import scipy.io.wavfile as wavfile; SCIPY=True
except Exception: SCIPY=False; print("[WARN] scipy unavailable")

# ═══════════════════════════════════════════════════════════════════
# PFADE  –  SourceForge-Struktur erkennen
# ═══════════════════════════════════════════════════════════════════

# SourceForge installiert nach:
#   C:\Program Files\ASH Toolset\
#     ash_toolset.exe           ← GUI launcher
#     _internal\                ← Python-Quellen + Daten
#       ash_toolset.py
#       ash_toolset\            ← Package
#       data\
#         user\SOFA\            ← User-SOFA hier ablegen
#         ...
#
# GitHub-Klon installiert nach:
#   C:\ASH-Toolset\
#     ash_toolset.py
#     ash_toolset\
#     data\

ASH_SF_ROOTS = [
    r"C:\Program Files\ASH Toolset",
    r"C:\Program Files (x86)\ASH Toolset",
]
ASH_GH_ROOTS = [
    os.environ.get("ASH_TOOLSET_PATH",""),
    r"C:\ASH-Toolset",
    r"C:\Tools\ASH-Toolset",
    str(Path.home()/"ASH-Toolset"),
    str(Path.home()/"Documents"/"ASH-Toolset"),
]

def _find_ash() -> Tuple[Optional[Path], str]:
    """Returns (python_root, install_type) where python_root contains ash_toolset.py"""
    # SourceForge: _internal sub-folder
    for root in ASH_SF_ROOTS:
        internal = Path(root) / "_internal"
        if (internal / "ash_toolset.py").exists():
            return internal, "sourceforge"
    # GitHub clone
    for root in ASH_GH_ROOTS:
        if root and (Path(root) / "ash_toolset.py").exists():
            return Path(root), "github"
    return None, "none"

ASH_ROOT, ASH_TYPE = _find_ash()

# Data + SOFA paths
if ASH_ROOT:
    ASH_DATA = ASH_ROOT / "data"
    ASH_USER_SOFA = ASH_DATA / "user" / "SOFA"
    ASH_USER_SOFA.mkdir(parents=True, exist_ok=True)
else:
    ASH_DATA = None
    ASH_USER_SOFA = None

# Equalizer APO
def _find_eqapo() -> Optional[Path]:
    for p in [
        r"C:\Program Files\EqualizerAPO",
        r"C:\Program Files (x86)\EqualizerAPO",
        os.environ.get("EQAPO_PATH",""),
    ]:
        if p and (Path(p)/"config").exists():
            return Path(p)
    return None

EQAPO = _find_eqapo()
EQAPO_CFG = EQAPO/"config" if EQAPO else None

# Output dirs
OUT_DIR  = Path(os.environ.get("ASH_OUTPUT_DIR", str(Path.home()/"ASH-Outputs"/"RoomDesigner")))
TEMP_DIR = Path(os.environ.get("TEMP", r"C:\Temp")) / "ASH-Room-Designer"
PORT     = int(os.environ.get("ASH_PORT","8765"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ash")

# ═══════════════════════════════════════════════════════════════════
# DATENMODELLE
# ═══════════════════════════════════════════════════════════════════

class Vec3(BaseModel):
    x:float=0.; y:float=0.; z:float=0.

class SpeakerPos(BaseModel):
    x:float; y:float; z:float
    azimuth:float=0.; elevation:float=0.; distance:float=1.

class ASHParams(BaseModel):
    acoustic_space:     str  = "Listening Room A"
    listener_type:      str  = "Dummy Head"
    dataset:            str  = "KEMAR"
    direct_sound_gain:  int  = 0
    room_target:        str  = "Harman Target"
    headphone_comp:     str  = "over-high"
    spatial_resolution: str  = "Medium"
    lf_response:        str  = "A"
    lf_crossover:       int  = 0
    hp_rolloff_comp:    bool = False
    fwd_bwd_filter:     bool = False
    sample_rate:        int  = 48000
    bit_depth:          int  = 24
    # Export targets
    export_sofa:        bool = True
    export_wav_brir:    bool = True
    export_hesuvi:      bool = False
    export_jriver:      bool = False
    write_eqapo:        bool = True
    eqapo_channel_cfg:  str  = "7.1"   # Windows channel config for APO

class RoomConfig(BaseModel):
    room:       Dict[str,float]        = {"w":5,"d":7,"h":2.8}
    layout:     str                    = "7.1.4"
    listener:   Vec3                   = Vec3()
    speakers:   Dict[str,SpeakerPos]   = {}
    ash_params: ASHParams              = ASHParams()

class ProcessResult(BaseModel):
    status:        str
    message:       str
    output_path:   Optional[str] = None
    files:         List[str]     = []
    eqapo_written: bool          = False
    eqapo_path:    Optional[str] = None
    warnings:      List[str]     = []
    duration_s:    float         = 0.

# ═══════════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════════
app = FastAPI(title="ASH Room Designer", version="3.0.0",
              description="Bridge: 3D UI ↔ ASH-Toolset ↔ EQ APO / SOFA / BRIRs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ═══════════════════════════════════════════════════════════════════
# ROUTEN
# ═══════════════════════════════════════════════════════════════════

@app.get("/status")
async def status():
    sofa_dir_files = []
    if ASH_USER_SOFA and ASH_USER_SOFA.exists():
        sofa_dir_files = [f.name for f in ASH_USER_SOFA.glob("*.sofa")]
    return {
        "status":"ok", "version":"3.0.0",
        "ash_root":      str(ASH_ROOT) if ASH_ROOT else None,
        "ash_type":      ASH_TYPE,
        "ash_available": ASH_ROOT is not None,
        "ash_data":      str(ASH_DATA) if ASH_DATA else None,
        "user_sofa_dir": str(ASH_USER_SOFA) if ASH_USER_SOFA else None,
        "user_sofa_files": sofa_dir_files,
        "eqapo_root":    str(EQAPO) if EQAPO else None,
        "eqapo_cfg_dir": str(EQAPO_CFG) if EQAPO_CFG else None,
        "eqapo_available": EQAPO is not None,
        "sofar":   SOFAR,
        "scipy":   SCIPY,
        "output_dir": str(OUT_DIR),
    }


@app.get("/ash-spaces")
async def ash_spaces():
    """Liest verfügbare Acoustic Spaces direkt aus ASH-Toolset-Daten."""
    spaces = _load_ash_spaces()
    return {"spaces": spaces, "count": len(spaces)}


@app.get("/ash-listeners")
async def ash_listeners():
    """Gibt verfügbare Listener/HRTF-Datensätze zurück."""
    listeners = _load_ash_listeners()
    sofa_files = []
    if ASH_USER_SOFA and ASH_USER_SOFA.exists():
        sofa_files = [{"name":f.stem,"file":f.name,"path":str(f)}
                      for f in sorted(ASH_USER_SOFA.glob("*.sofa"))]
    return {"datasets": listeners, "user_sofa": sofa_files}


@app.post("/process", response_model=ProcessResult)
async def process(cfg: RoomConfig):
    t0  = datetime.now()
    ts  = t0.strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / ts
    out.mkdir(parents=True, exist_ok=True)
    warnings = []

    log.info(f"Processing {cfg.layout} | {len(cfg.speakers)} ch | {cfg.ash_params.acoustic_space}")
    (out/"room_config.json").write_text(cfg.model_dump_json(indent=2), encoding="utf-8")

    files = []

    # ── 1. BRIR Generation ──────────────────────────────────────────
    if ASH_ROOT:
        try:
            f, w = await _ash_generate(cfg, out)
            files.extend(f); warnings.extend(w)
        except Exception as e:
            log.warning(f"ASH call failed: {e} – synthetic fallback")
            warnings.append(f"ASH-Toolset call failed ({e}), synthetic BRIRs used")
            f = await _synthetic_brirs(cfg, out)
            files.extend(f)
    else:
        warnings.append("ASH-Toolset not found – synthetic preview BRIRs generated")
        f = await _synthetic_brirs(cfg, out)
        files.extend(f)

    # ── 2. SOFA Export ──────────────────────────────────────────────
    if cfg.ash_params.export_sofa and SOFAR:
        try:
            sp = _write_sofa(cfg, out)
            files.append(str(sp))
        except Exception as e:
            warnings.append(f"SOFA export: {e}")

    # ── 3. HeSuVi Export ────────────────────────────────────────────
    if cfg.ash_params.export_hesuvi:
        try:
            hf = _write_hesuvi_config(cfg, out)
            files.append(str(hf))
        except Exception as e:
            warnings.append(f"HeSuVi export: {e}")

    # ── 4. JRiver / Roon M3U manifest ───────────────────────────────
    if cfg.ash_params.export_jriver:
        try:
            jf = _write_jriver_manifest(cfg, out)
            files.append(str(jf))
        except Exception as e:
            warnings.append(f"JRiver manifest: {e}")

    # ── 5. Equalizer APO ────────────────────────────────────────────
    apo_file  = out / "config.txt"
    _write_eqapo(cfg, out, apo_file)
    files.append(str(apo_file))

    eqapo_written = False
    eqapo_dest    = None
    if EQAPO_CFG and cfg.ash_params.write_eqapo:
        try:
            # WAV-Ordner anlegen
            wav_dest = EQAPO_CFG / "ASH-RoomDesigner"
            wav_dest.mkdir(exist_ok=True)
            for wf in out.glob("BRIR_*.wav"):
                shutil.copy2(wf, wav_dest/wf.name)
            # config.txt schreiben (mit korrekten absoluten Pfaden)
            _write_eqapo(cfg, wav_dest, EQAPO_CFG/"config.txt", absolute=True)
            eqapo_written = True
            eqapo_dest    = str(EQAPO_CFG/"config.txt")
            log.info(f"EQ APO written: {eqapo_dest}")
        except PermissionError:
            warnings.append("EQ APO: permission denied – run as Administrator to write config.txt")
        except Exception as e:
            warnings.append(f"EQ APO write failed: {e}")

    dur = (datetime.now()-t0).total_seconds()
    return ProcessResult(
        status="ok",
        message=f"{len(files)} files generated in {dur:.1f}s",
        output_path=str(out),
        files=[Path(f).name for f in files],
        eqapo_written=eqapo_written,
        eqapo_path=eqapo_dest,
        warnings=warnings,
        duration_s=round(dur,2)
    )


@app.post("/sofa-upload")
async def sofa_upload(file: UploadFile = File(...)):
    """
    SOFA-Datei hochladen → ins ASH user/SOFA-Verzeichnis kopieren
    + Positionen für 3D-Visualisierung zurückgeben.
    """
    if not file.filename or not file.filename.lower().endswith(".sofa"):
        raise HTTPException(400, "Nur .sofa Dateien erlaubt")

    data = await file.read()

    # Ins ASH-Userverzeichnis kopieren wenn verfügbar
    saved_to = None
    if ASH_USER_SOFA:
        dest = ASH_USER_SOFA / file.filename
        dest.write_bytes(data)
        saved_to = str(dest)
        log.info(f"SOFA saved to ASH user dir: {dest}")

    # Für Visualisierung parsen
    positions = []
    convention = "unknown"
    if SOFAR:
        tmp = TEMP_DIR / f"up_{datetime.now().strftime('%f')}.sofa"
        tmp.write_bytes(data)
        try:
            sofa = sf_lib.read_sofa(str(tmp))
            convention = getattr(sofa,"GLOBAL_SOFAConventions","unknown")
            for i,row in enumerate(sofa.SourcePosition):
                if len(row)>=3:
                    positions.append({"index":i,"azimuth":float(row[0]),
                                       "elevation":float(row[1]),"radius":float(row[2])})
        finally:
            tmp.unlink(missing_ok=True)

    return {
        "filename":   file.filename,
        "saved_to":   saved_to,
        "convention": convention,
        "num_positions": len(positions),
        "positions":  positions,
        "message": f"Saved to ASH user SOFA dir" if saved_to else "Backend has no ASH path"
    }


@app.post("/sofa-parse")
async def sofa_parse(file: UploadFile = File(...)):
    """Nur parsen (nicht speichern) – für Visualisierung."""
    if not SOFAR:
        raise HTTPException(503, "sofar not installed: pip install sofar")
    data = await file.read()
    tmp  = TEMP_DIR / f"parse_{datetime.now().strftime('%f')}.sofa"
    tmp.write_bytes(data)
    try:
        sofa = sf_lib.read_sofa(str(tmp))
        positions = []
        for i,row in enumerate(sofa.SourcePosition):
            if len(row)>=3:
                positions.append([float(row[0]),float(row[1]),float(row[2])])
        return {"filename":file.filename,
                "convention":getattr(sofa,"GLOBAL_SOFAConventions","?"),
                "num_positions":len(positions),"positions":positions}
    finally:
        tmp.unlink(missing_ok=True)


@app.get("/output/{session}/{fname}")
async def get_output(session:str, fname:str):
    p = OUT_DIR/session/fname
    if not p.exists(): raise HTTPException(404)
    return FileResponse(str(p))


@app.post("/open-output-folder")
async def open_output_folder():
    import subprocess as sp
    try:
        sp.Popen(["explorer", str(OUT_DIR)])
        return {"status":"ok","path":str(OUT_DIR)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/latest-session")
async def latest_session():
    sessions = sorted([d for d in OUT_DIR.iterdir() if d.is_dir()], reverse=True)
    if not sessions:
        return {"session":None,"files":[]}
    latest = sessions[0]
    files  = [{"name":f.name,"size":f.stat().st_size,"type":f.suffix}
              for f in sorted(latest.iterdir()) if f.is_file() and not f.name.startswith('_')]
    return {"session":latest.name,"path":str(latest),"files":files}


@app.get("/acoustic-space-positions")
async def acoustic_space_positions(name: str):
    """
    Liest IR-Quellpositionen eines Acoustic Space aus den ASH-Metadaten-CSVs.
    ASH speichert diese nach dem Import in:
      _internal/data/interim/reverberation/user/<SpaceName>/*metadata*.csv
    """
    import csv as csv_mod

    if not ASH_DATA:
        raise HTTPException(503, "ASH data path not found")

    search_bases = [
        ASH_DATA / "interim" / "reverberation" / "user",
        ASH_DATA / "interim" / "reverberation",
        ASH_DATA / "user",
    ]

    positions = []
    meta_found = None
    name_norm  = name.lower().replace(' ','').replace('_','')

    for base in search_bases:
        if not base.exists():
            continue
        for folder in base.iterdir():
            if not folder.is_dir():
                continue
            folder_norm = folder.name.lower().replace(' ','').replace('_','')
            if name_norm not in folder_norm and folder_norm not in name_norm:
                continue
            for csv_file in list(folder.glob("*metadata*.csv")) + list(folder.glob("*asi*.csv")):
                try:
                    with open(csv_file, newline='', encoding='utf-8', errors='replace') as f:
                        reader = csv_mod.DictReader(f)
                        for row in reader:
                            az   = float(row.get('azimuth',   row.get('az',  0)))
                            el   = float(row.get('elevation', row.get('el',  0)))
                            dist = float(row.get('distance',  row.get('r',  2.0)))
                            positions.append({"azimuth":az,"elevation":el,"distance":dist})
                    meta_found = str(csv_file)
                    break
                except Exception as e:
                    log.warning(f"CSV error {csv_file}: {e}")
            if positions:
                break
        if positions:
            break

    rt60 = next((s["rt60"] for s in SPACES if s["name"]==name), 500)

    if not positions:
        return {
            "name": name, "rt60": rt60, "positions": [],
            "source": "not_found",
            "message": f"Keine CSV-Metadaten für '{name}' gefunden."
        }

    return {"name":name,"rt60":rt60,"positions":positions,
            "count":len(positions),"source":meta_found}


# ═══════════════════════════════════════════════════════════════════
# ASH-TOOLSET INTEGRATION
# ═══════════════════════════════════════════════════════════════════

async def _ash_generate(cfg:RoomConfig, out:Path) -> Tuple[List[str],List[str]]:
    """
    Ruft ASH-Toolset über seinen internen Python-Code auf.
    Die SourceForge-Installation enthält in _internal/ alles nötige.
    """
    p  = cfg.ash_params
    warnings = []

    # Headphone comp mapping
    hp_type_map = {
        "over-high": ("Over-Ear/On-Ear Headphones","high strength"),
        "over-low":  ("Over-Ear/On-Ear Headphones","low strength"),
        "iem-high":  ("In-Ear Headphones","high strength"),
        "iem-low":   ("In-Ear Headphones","low strength"),
        "none":      ("None",""),
    }
    hp_type, hp_strength = hp_type_map.get(p.headphone_comp,("Over-Ear/On-Ear Headphones","high strength"))

    res_map = {"Low":0,"Medium":1,"High":2}

    # Listener type mapping für SourceForge-Version
    listener_map = {
        "Dummy Head":       "Dummy Head / Head & Torso Simulator",
        "Human Listener":   "Human Listener",
        "User SOFA Input":  "User SOFA Input",
    }

    script_body = f"""
import sys, json, os
sys.path.insert(0, r"{ASH_ROOT}")

# Unterdrücke GUI-Imports
import unittest.mock
sys.modules['dearpygui'] = unittest.mock.MagicMock()
sys.modules['dearpygui.dearpygui'] = unittest.mock.MagicMock()
sys.modules['dearpygui_ext'] = unittest.mock.MagicMock()
sys.modules['dearpygui_extend'] = unittest.mock.MagicMock()

os.chdir(r"{ASH_ROOT}")

try:
    # Versuche direkten Import des Core-Moduls
    from ash_toolset import process as ash_process
    
    result = ash_process.generate_binaural_dataset(
        acoustic_space   = "{p.acoustic_space}",
        listener_type    = "{listener_map.get(p.listener_type, p.listener_type)}",
        dataset          = "{p.dataset}",
        direct_gain      = {p.direct_sound_gain},
        room_target      = "{p.room_target}",
        hp_type          = "{hp_type}",
        hp_strength      = "{hp_strength}",
        resolution       = {res_map.get(p.spatial_resolution,1)},
        lf_response      = "{p.lf_response}",
        lf_crossover     = {p.lf_crossover if p.lf_crossover > 0 else 'None'},
        hp_rolloff_comp  = {p.hp_rolloff_comp},
        fwd_bwd          = {p.fwd_bwd_filter},
        sample_rate      = {p.sample_rate},
        bit_depth        = {p.bit_depth},
        output_dir       = r"{out}",
        export_wav       = {p.export_wav_brir},
        export_sofa      = False,  # we handle SOFA separately
    )
    print(json.dumps({{"status":"ok","files":result if isinstance(result,list) else []}}))

except AttributeError:
    # ASH module structure differs – try alternative entry point
    try:
        from ash_toolset import constants
        from ash_toolset import dataset_export
        result = dataset_export.run({{
            "acoustic_space": "{p.acoustic_space}",
            "listener_type":  "{p.listener_type}",
            "dataset":        "{p.dataset}",
            "output_dir":     r"{out}",
            "sample_rate":    {p.sample_rate},
            "bit_depth":      {p.bit_depth},
        }})
        print(json.dumps({{"status":"ok","files":result or []}}))
    except Exception as e2:
        print(json.dumps({{"status":"fallback","reason":str(e2)}}))
        sys.exit(2)

except ImportError as e:
    print(json.dumps({{"status":"fallback","reason":str(e)}}))
    sys.exit(2)

except Exception as e:
    import traceback
    print(json.dumps({{"status":"error","reason":str(e),
                        "trace":traceback.format_exc()[-800:]}}))
    sys.exit(1)
"""
    runner = out/"_runner.py"
    runner.write_text(script_body, encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(runner),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(ASH_ROOT)
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("ASH-Toolset timed out after 5 minutes")

    # Parse output
    last_json = None
    for line in reversed(stdout.decode(errors="replace").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try: last_json = json.loads(line); break
            except: pass

    if last_json:
        if last_json.get("status") == "ok":
            files = last_json.get("files",[])
            log.info(f"ASH generated {len(files)} files")
            return files, warnings
        elif last_json.get("status") == "fallback":
            raise RuntimeError("fallback: "+last_json.get("reason",""))
        else:
            raise RuntimeError(last_json.get("reason","unknown"))

    if proc.returncode == 2:
        raise RuntimeError("fallback")

    err = stderr.decode(errors="replace")[:300]
    raise RuntimeError(err or "no output from ASH runner")


# ═══════════════════════════════════════════════════════════════════
# SYNTHETISCHE BRIRS  (Fallback / Preview)
# ═══════════════════════════════════════════════════════════════════

async def _synthetic_brirs(cfg:RoomConfig, out:Path) -> List[str]:
    if not SCIPY: return []
    p   = cfg.ash_params
    sr  = p.sample_rate
    dur = int(0.512*sr)
    gen = []

    for ch, spk in cfg.speakers.items():
        az=spk.azimuth; el=spk.elevation; d=max(0.5,spk.distance)
        hl,hr = _make_hrir(az,el,d,sr,dur,p)
        hl,hr = _apply_target(hl,hr,p.room_target,sr)
        hl,hr = _apply_reverb_tail(hl,hr,sr,p.acoustic_space,d)
        if p.hp_rolloff_comp: hl,hr = _hp_rolloff(hl,hr,sr)
        pk = max(np.max(np.abs(hl)),np.max(np.abs(hr)),1e-9)
        hl/=pk*1.05; hr/=pk*1.05

        name = f"BRIR_{ch}_E{int(round(el)):+04d}_A{int(round(az)):+04d}.wav"
        path = out/name
        stereo = np.stack([hl,hr],axis=-1)
        maxv = 8388607 if p.bit_depth==24 else 2147483647
        wavfile.write(str(path), sr, (np.clip(stereo,-1,1)*maxv).astype(np.int32))
        gen.append(str(path))
        log.info(f"  Synthetic BRIR: {name}")
    return gen

def _make_hrir(az,el,d,sr,dur,p):
    azR=math.radians(az); elR=math.radians(el)
    R=0.0875; c=343.; atten=1./d
    # Woodworth ITD
    itd_n = min(dur-1, int(abs((R/c)*(azR+math.sin(azR)))*sr))
    # ILD (broadband approximation)
    ild = max(0.15, 0.5+0.5*math.cos(azR)) * max(0.4, math.cos(elR)**0.5)

    hl=np.zeros(dur); hr=np.zeros(dur)
    dn=min(dur-1, int(d/c*sr))
    if az>=0:
        hr[dn]             = atten
        hl[min(dur-1,dn+itd_n)] = atten*ild
    else:
        hl[dn]             = atten
        hr[min(dur-1,dn+itd_n)] = atten*ild

    # Elevation pinna notch
    if abs(el)>5:
        pf = 8000+el*60
        hl,hr = _notch(hl,hr,pf,sr, depth=3+abs(el)/20)

    return hl,hr

def _apply_target(l,r,target,sr):
    if target in ("Harman Target","ASH Target"):
        sos = butter(2,120,'lowpass',fs=sr,output='sos')
        l += sosfilt(sos,l)*0.2; r += sosfilt(sos,r)*0.2
    elif "X-Curve" in target:
        sos = butter(2,[60,10000],'bandpass',fs=sr,output='sos')
        l=sosfilt(sos,l); r=sosfilt(sos,r)
    return l,r

def _apply_reverb_tail(l,r,sr,space,d):
    rt60 = next((s["rt60"]/1000 for s in SPACES if s["name"]==space), 0.45)
    n=len(l); t=np.arange(n)/sr
    rng=np.random.default_rng(seed=42)
    env=np.exp(-6.91/max(rt60,0.05)*t)
    rev=rng.standard_normal(n)*0.08*env
    gain = min(0.25, 0.12/d)
    return l+rev*gain, r+rev*gain

def _hp_rolloff(l,r,sr):
    sos=butter(1,80,'lowpass',fs=sr,output='sos')
    return l+sosfilt(sos,l)*0.3, r+sosfilt(sos,r)*0.3

def _notch(l,r,freq,sr,depth=4):
    f1=max(20,freq*0.7); f2=min(sr/2-100,freq*1.3)
    if f1>=f2: return l,r
    sos=butter(2,[f1,f2],'bandstop',fs=sr,output='sos')
    fac=10**(-depth/20)
    nl=l*(1-fac)+sosfilt(sos,l)*fac
    nr=r*(1-fac)+sosfilt(sos,r)*fac
    return nl,nr


# ═══════════════════════════════════════════════════════════════════
# SOFA EXPORT
# ═══════════════════════════════════════════════════════════════════

def _write_sofa(cfg:RoomConfig, out:Path) -> Path:
    sofa = sf_lib.Sofa("SimpleFreeFieldHRIR")
    p    = cfg.ash_params
    sr   = p.sample_rate; dur = int(0.512*sr)
    rows = [[s.azimuth,s.elevation,s.distance] for s in cfg.speakers.values()]
    n    = len(rows)
    sofa.GLOBAL_Title            = f"ASH Room Designer – {cfg.layout}"
    sofa.GLOBAL_DateCreated      = datetime.now().isoformat()
    sofa.GLOBAL_AuthorContact    = "ASH Room Designer"
    sofa.GLOBAL_Comment          = (f"Room {cfg.room} | Space:{p.acoustic_space} | "
                                    f"HRTF:{p.dataset} | Target:{p.room_target}")
    sofa.Data_SamplingRate       = float(sr)
    sofa.Data_SamplingRate_Units = "hertz"
    sofa.SourcePosition          = np.array(rows, dtype=float)
    sofa.ReceiverPosition        = np.array([[0,0,0],[0,0,0]], dtype=float)
    sofa.Data_IR                 = np.zeros((n,2,dur), dtype=float)
    # Fill IR data from written WAVs if present
    for i,(ch,spk) in enumerate(cfg.speakers.items()):
        az=int(round(spk.azimuth)); el=int(round(spk.elevation))
        wav = out/f"BRIR_{ch}_E{el:+04d}_A{az:+04d}.wav"
        if wav.exists():
            rate, data = wavfile.read(str(wav))
            d2 = np.array(data, dtype=float)/np.iinfo(data.dtype).max
            n_copy = min(dur, d2.shape[0])
            sofa.Data_IR[i,0,:n_copy] = d2[:n_copy,0]
            sofa.Data_IR[i,1,:n_copy] = d2[:n_copy,1]
    path = out/"room_brirs.sofa"
    sf_lib.write_sofa(str(path), sofa)
    log.info(f"SOFA: {path}")
    return path


# ═══════════════════════════════════════════════════════════════════
# EQUALIZER APO CONFIG
# ═══════════════════════════════════════════════════════════════════

# Windows surround channel labels (standard 7.1 speaker layout)
_WIN_CH = {
    "L":"L","R":"R","C":"C","LFE":"Sub",
    "LS":"RL","RS":"RR","LB":"SBL","RB":"SBR",
    "LTF":"TFL","RTF":"TFR","LTR":"TBL","RTR":"TBR",
}

def _write_eqapo(cfg:RoomConfig, wav_dir:Path, out_path:Path, absolute:bool=False):
    p  = cfg.ash_params
    hp_labels = {
        "over-high":"Over-Ear High","over-low":"Over-Ear Low",
        "iem-high":"In-Ear High","iem-low":"In-Ear Low","none":"None",
    }

    def wav_path(ch,spk):
        az=int(round(spk.azimuth)); el=int(round(spk.elevation))
        name = f"BRIR_{ch}_E{el:+04d}_A{az:+04d}.wav"
        return str(wav_dir/name) if absolute else name

    lines = [
        "# ══════════════════════════════════════════════════",
        "#  ASH Room Designer – Equalizer APO Configuration",
        f"#  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"#  Layout    : {cfg.layout}  ({len(cfg.speakers)} channels)",
        f"#  Room      : {cfg.room.get('w',0):.1f} × {cfg.room.get('d',0):.1f} × {cfg.room.get('h',0):.1f} m",
        f"#  Space     : {p.acoustic_space}",
        f"#  HRTF      : {p.dataset}  ({p.listener_type})",
        f"#  HP Comp   : {hp_labels.get(p.headphone_comp,p.headphone_comp)}",
        f"#  Target    : {p.room_target}",
        f"#  Sample Rate: {p.sample_rate} Hz / {p.bit_depth} bit",
        "# ══════════════════════════════════════════════════",
        "",
        "Device: all",
        "",
        f"# Preamp to avoid clipping from {len(cfg.speakers)}-channel convolution",
        f"Preamp: -{max(6, len(cfg.speakers))}dB",
        "",
        "# Channel Config – set to match your Windows sound device",
        f"# (Device must support {p.eqapo_channel_cfg} in Windows sound settings)",
        "",
    ]

    if p.hp_rolloff_comp:
        lines += [
            "# Headphone bass roll-off compensation (+4 dB @ 20 Hz)",
            "Filter: ON LSC Fc 80 Hz Gain 4.0 dB Q 0.707",
            "",
        ]

    if p.fwd_bwd_filter:
        lines += ["# Forward-Backward filter applied at generation time",""]

    # One Convolution block per channel
    for ch, spk in cfg.speakers.items():
        win = _WIN_CH.get(ch, ch)
        lines += [
            f"# ── {ch}: {SPEAKER_NAMES.get(ch,ch)}  "
            f"Az={spk.azimuth:+.0f}°  El={spk.elevation:+.0f}°  D={spk.distance:.1f}m",
            f"Channel: {win}",
            f'Convolution: "{wav_path(ch,spk)}"',
            "",
        ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"EQ APO: {out_path}")


# ═══════════════════════════════════════════════════════════════════
# HESUVI EXPORT
# ═══════════════════════════════════════════════════════════════════

def _write_hesuvi_config(cfg:RoomConfig, out:Path) -> Path:
    """
    Schreibt eine HeSuVi-kompatible Kanalzuordnung.
    HeSuVi erwartet 14 Kanäle in einem WAV: FL FR C LFE RL RR SBL SBR ...
    """
    p = cfg.ash_params
    # HeSuVi channel order
    hesuvi_order = ["L","R","C","LFE","LS","RS","LB","RB","LTF","RTF","LTR","RTR"]
    available    = [ch for ch in hesuvi_order if ch in cfg.speakers]

    lines = [
        f"; HeSuVi compatible filter list",
        f"; Generated by ASH Room Designer – {datetime.now().strftime('%Y-%m-%d')}",
        f"; Space: {p.acoustic_space} | HRTF: {p.dataset}",
        f"; Room: {cfg.room}",
        ";",
        "; Load these BRIRs in HeSuVi → Virtualisation tab",
        "",
    ]
    for ch in available:
        spk = cfg.speakers[ch]
        az=int(round(spk.azimuth)); el=int(round(spk.elevation))
        wav = f"BRIR_{ch}_E{el:+04d}_A{az:+04d}.wav"
        lines.append(f"{ch}: {wav}")

    path = out/"hesuvi_config.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"HeSuVi config: {path}")
    return path


# ═══════════════════════════════════════════════════════════════════
# JRIVER / ROON MANIFEST
# ═══════════════════════════════════════════════════════════════════

def _write_jriver_manifest(cfg:RoomConfig, out:Path) -> Path:
    """
    Schreibt eine JSON-Manifestdatei mit allen BRIR-Dateien
    und deren Kanalzuordnung, kompatibel mit JRiver DSP Studio
    und Roon Convolution Engine.
    """
    p = cfg.ash_params
    channels = []
    for ch, spk in cfg.speakers.items():
        az=int(round(spk.azimuth)); el=int(round(spk.elevation))
        channels.append({
            "channel":   ch,
            "name":      SPEAKER_NAMES.get(ch,ch),
            "azimuth":   spk.azimuth,
            "elevation": spk.elevation,
            "distance":  spk.distance,
            "wav_file":  f"BRIR_{ch}_E{el:+04d}_A{az:+04d}.wav",
            "win_channel": _WIN_CH.get(ch,ch),
            # JRiver channel index (0-based, standard order)
            "jriver_index": list(_WIN_CH.keys()).index(ch) if ch in _WIN_CH else -1,
        })

    manifest = {
        "generator":    "ASH Room Designer v3",
        "generated":    datetime.now().isoformat(),
        "layout":       cfg.layout,
        "room":         cfg.room,
        "sample_rate":  p.sample_rate,
        "bit_depth":    p.bit_depth,
        "acoustic_space": p.acoustic_space,
        "hrtf_dataset": p.dataset,
        "room_target":  p.room_target,
        "hp_comp":      p.headphone_comp,
        "channels":     channels,
        "jriver_dsp": {
            "description": "Load BRIRs via JRiver Media Center DSP Studio → Convolver",
            "steps": [
                "1. Open JRiver MC → Tools → Options → DSP & Output Format",
                "2. Enable 'Parametric Equalizer' or 'Convolver'",
                "3. Load each WAV file for the corresponding output channel",
                "4. Set output channels to match layout"
            ]
        },
        "roon_convolution": {
            "description": "Load via Roon → DSP Engine → Convolution",
            "note": "Roon supports stereo convolution. Use a mixed-down stereo BRIR or the L/R channels.",
            "stereo_file": "BRIR_L_E+000_A-030.wav"
        }
    }

    path = out/"jriver_roon_manifest.json"
    path.write_text(json.dumps(manifest,indent=2), encoding="utf-8")
    log.info(f"JRiver/Roon manifest: {path}")
    return path


# ═══════════════════════════════════════════════════════════════════
# ASH-TOOLSET METADATEN LESEN
# ═══════════════════════════════════════════════════════════════════

def _load_ash_spaces() -> List[Dict]:
    """Versucht, Acoustic Spaces direkt aus ASH-Metadaten zu laden."""
    if ASH_ROOT:
        # Versuche metadata.json
        for meta_path in [ASH_ROOT/"metadata.json", ASH_ROOT.parent/"metadata.json"]:
            if meta_path.exists():
                try:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))
                    if "acoustic_spaces" in data:
                        return data["acoustic_spaces"]
                except: pass
    return SPACES  # Fallback: eingebettete Liste

def _load_ash_listeners() -> List[Dict]:
    """Gibt verfügbare HRTF-Datensätze zurück."""
    datasets = [
        {"type":"Dummy Head","name":"KEMAR","included":True},
        {"type":"Dummy Head","name":"KU 100","included":True},
        {"type":"Dummy Head","name":"HMS II.3","included":True},
        {"type":"Dummy Head","name":"FABIAN","included":True},
        {"type":"Human Listener","name":"CIPIC","included":True},
        {"type":"Human Listener","name":"LISTEN","included":True},
        {"type":"Human Listener","name":"SADIE II","included":False,"note":"auto-download"},
        {"type":"Human Listener","name":"ITA","included":False,"note":"auto-download"},
    ]
    return datasets


# ═══════════════════════════════════════════════════════════════════
# DATEN
# ═══════════════════════════════════════════════════════════════════

SPEAKER_NAMES = {
    "L":"Left","R":"Right","C":"Center","LFE":"Subwoofer / LFE",
    "LS":"Left Surround","RS":"Right Surround",
    "LB":"Left Back","RB":"Right Back",
    "LTF":"Left Top Front","RTF":"Right Top Front",
    "LTR":"Left Top Rear","RTR":"Right Top Rear",
}

SPACES = [
    {"name":"Listening Room A","rt60":221},{"name":"Listening Room B","rt60":379},
    {"name":"Listening Room C","rt60":562},{"name":"Listening Room D","rt60":312},
    {"name":"Listening Room E","rt60":824},{"name":"Living Room","rt60":967},
    {"name":"Small Room A","rt60":500},{"name":"Small Room B","rt60":467},
    {"name":"Control Room","rt60":260},{"name":"Audio Lab A","rt60":305},
    {"name":"Audio Lab B","rt60":413},{"name":"Audio Lab C","rt60":508},
    {"name":"Recording Studio A","rt60":723},{"name":"Recording Studio B","rt60":739},
    {"name":"Studio A","rt60":398},{"name":"Studio B","rt60":351},
    {"name":"Cinema","rt60":900},
    {"name":"Concert Hall A","rt60":1599},{"name":"Concert Hall B","rt60":1386},
    {"name":"Hall A","rt60":1418},{"name":"Hall B","rt60":949},
    {"name":"Auditorium A","rt60":1455},{"name":"Auditorium B","rt60":346},
    {"name":"Small Theatre","rt60":920},{"name":"Theatre","rt60":884},
    {"name":"Seminar Room A","rt60":839},{"name":"Lecture Hall A","rt60":901},
    {"name":"Treated Room","rt60":178},{"name":"Broadcast Studio A","rt60":1183},
]

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    w = 54
    print()
    print("  " + "═"*w)
    print("  ║  ASH Room Designer Backend  v3.0" + " "*(w-35) + "║")
    print("  " + "═"*w)
    print()
    print(f"  Install type   : {ASH_TYPE}")
    print(f"  ASH Python root: {ASH_ROOT or '[NOT FOUND]'}")
    if ASH_USER_SOFA:
        sofa_files = list(ASH_USER_SOFA.glob("*.sofa"))
        print(f"  User SOFA dir  : {ASH_USER_SOFA}  ({len(sofa_files)} files)")
    print(f"  Equalizer APO  : {EQAPO_CFG or '[not found]'}")
    print(f"  Output dir     : {OUT_DIR}")
    print(f"  sofar          : {'✓' if SOFAR else '✗  pip install sofar'}")
    print(f"  scipy          : {'✓' if SCIPY else '✗  pip install scipy'}")
    print()

    if ASH_TYPE == "none":
        print("  ⚠  ASH-Toolset nicht gefunden!")
        print(f"     Erwartet: C:\\Program Files\\ASH Toolset\\_internal\\ash_toolset.py")
        print(f"     Setze Umgebungsvariable: ASH_TOOLSET_PATH=<pfad>")
        print(f"     Synthetische Preview-BRIRs werden stattdessen generiert.")
        print()
    else:
        print(f"  ✓  ASH-Toolset bereit ({ASH_TYPE})")
        print()

    if not EQAPO:
        print("  ℹ  Equalizer APO nicht gefunden.")
        print("     Download: https://sourceforge.net/projects/equalizerapo/")
        print()

    print(f"  UI:       ash_room_designer.html im Browser öffnen")
    print(f"  API Docs: http://localhost:{PORT}/docs")
    print("  " + "─"*w)
    print()

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
