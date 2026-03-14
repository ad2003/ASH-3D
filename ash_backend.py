"""
ash_backend.py  –  ASH Room Designer Backend v2
================================================
FastAPI bridge between 3D UI and ASH-Toolset.
Home Theater 7.1.4 / Dolby Atmos focus.

Start:  python ash_backend.py
Port:   8765  (set env ASH_PORT to override)

Environment variables:
  ASH_TOOLSET_PATH   – path to ASH-Toolset folder
  ASH_OUTPUT_DIR     – where to write BRIRs / SOFA
  ASH_EQAPO_DIR      – Equalizer APO config dir (auto-detected)
  ASH_PORT           – HTTP port (default 8765)
"""

import sys, os, json, math, asyncio, logging, subprocess, shutil
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

# ── Auto-install deps if missing ─────────────────────────────────────
def _ensure(pkg, import_as=None):
    try:
        __import__(import_as or pkg)
    except ImportError:
        print(f"Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

_ensure("fastapi"); _ensure("uvicorn"); _ensure("python-multipart"); _ensure("pydantic")
_ensure("numpy"); _ensure("scipy"); _ensure("sofar"); _ensure("soundfile")

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
import numpy as np
import uvicorn

try:
    import sofar
    SOFAR = True
except ImportError:
    SOFAR = False
    print("[WARN] sofar not available")

try:
    from scipy.signal import fftconvolve, butter, sosfilt
    import scipy.io.wavfile as wavfile
    SCIPY = True
except ImportError:
    SCIPY = False
    print("[WARN] scipy not available")

# ─────────────────────────────────────────────────────────────────────
# PATHS  –  auto-detect common Windows locations
# ─────────────────────────────────────────────────────────────────────
def find_ash_toolset() -> Optional[Path]:
    candidates = [
        os.environ.get("ASH_TOOLSET_PATH",""),
        r"C:\ASH-Toolset",
        r"C:\Tools\ASH-Toolset",
        r"C:\Program Files (x86)\ASH Toolset\_internal",
        r"C:\Program Files\ASH-Toolset",
        str(Path.home() / "ASH-Toolset"),
        str(Path.home() / "Documents" / "ASH-Toolset"),
    ]
    for c in candidates:
        p = Path(c)
        if (p / "ash_toolset.py").exists():
            return p
    return None

def find_eqapo() -> Optional[Path]:
    for p in [
        r"C:\Program Files\EqualizerAPO\config",
        r"C:\Program Files (x86)\EqualizerAPO\config",
    ]:
        if Path(p).exists():
            return Path(p)
    env = os.environ.get("ASH_EQAPO_DIR","")
    if env and Path(env).exists():
        return Path(env)
    return None

ASH_PATH   = find_ash_toolset()
EQAPO_PATH = find_eqapo()
OUT_DIR    = Path(os.environ.get("ASH_OUTPUT_DIR", str(Path.home() / "ASH-Outputs" / "RoomDesigner")))
TEMP_DIR   = Path(os.environ.get("ASH_TEMP_DIR",   r"C:\Temp\ASH-Temp"))
PORT       = int(os.environ.get("ASH_PORT", 8765))

OUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ash")

# ─────────────────────────────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────────────────────────────
class Vec3(BaseModel):
    x: float = 0.0; y: float = 0.0; z: float = 0.0

class SpeakerPos(BaseModel):
    x: float; y: float; z: float
    azimuth:   float = 0.0
    elevation: float = 0.0
    distance:  float = 1.0

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
    export_sofa:        bool = True
    export_wav:         bool = True
    write_eqapo:        bool = True

class RoomConfig(BaseModel):
    room:       Dict[str, float]           = {"w":5,"d":7,"h":2.8}
    layout:     str                         = "7.1.4"
    listener:   Vec3                        = Vec3()
    speakers:   Dict[str, SpeakerPos]       = {}
    ash_params: ASHParams                   = ASHParams()

class ProcessResult(BaseModel):
    status:      str
    message:     str
    output_path: Optional[str] = None
    files:       List[str]     = []
    eqapo_path:  Optional[str] = None
    duration_s:  float         = 0.0

# ─────────────────────────────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="ASH Room Designer", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    return {
        "status": "ok", "version": "2.0.0",
        "ash_toolset":     str(ASH_PATH) if ASH_PATH else None,
        "ash_available":   ASH_PATH is not None,
        "eqapo_path":      str(EQAPO_PATH) if EQAPO_PATH else None,
        "eqapo_available": EQAPO_PATH is not None,
        "sofar_available": SOFAR,
        "scipy_available": SCIPY,
        "output_dir":      str(OUT_DIR),
    }


@app.post("/process", response_model=ProcessResult)
async def process(cfg: RoomConfig):
    t0 = datetime.now()
    ts = t0.strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / ts
    out.mkdir(parents=True, exist_ok=True)

    log.info(f"Processing {cfg.layout}, {len(cfg.speakers)} speakers → {out}")

    # Save config
    (out / "room_config.json").write_text(cfg.model_dump_json(indent=2), encoding="utf-8")

    files = []

    if ASH_PATH:
        try:
            f = await call_ash_toolset(cfg, out)
            files.extend(f)
            log.info("ASH-Toolset processing complete")
        except Exception as e:
            log.warning(f"ASH call failed ({e}), using synthetic BRIRs")
            f = await synthetic_brirs(cfg, out)
            files.extend(f)
    else:
        f = await synthetic_brirs(cfg, out)
        files.extend(f)

    # EQ APO
    eqapo_out = None
    eqapo_file = out / "config.txt"
    write_eqapo_config(cfg, out, eqapo_file)
    files.append(str(eqapo_file))

    # Copy to EQ APO if available and user wants it
    if EQAPO_PATH and cfg.ash_params.write_eqapo:
        target = EQAPO_PATH / "ASH-Room-Designer"
        target.mkdir(parents=True, exist_ok=True)
        # Copy all WAVs + config
        for src in out.glob("*.wav"):
            shutil.copy2(src, target / src.name)
        eqapo_target = EQAPO_PATH / "config.txt"
        shutil.copy2(eqapo_file, eqapo_target)
        eqapo_out = str(eqapo_target)
        log.info(f"EQ APO config written to {eqapo_target}")

    dur = (datetime.now() - t0).total_seconds()
    return ProcessResult(
        status="ok",
        message=f"{len(files)} files generated",
        output_path=str(out),
        files=[Path(f).name for f in files],
        eqapo_path=eqapo_out,
        duration_s=round(dur, 2)
    )


@app.post("/sofa-positions")
async def sofa_positions(file: UploadFile = File(...)):
    if not SOFAR:
        raise HTTPException(503, "sofar not installed")
    tmp = TEMP_DIR / f"upload_{datetime.now().strftime('%f')}.sofa"
    tmp.write_bytes(await file.read())
    try:
        sofa_obj = sofar.read_sofa(str(tmp))
        positions = []
        for i, row in enumerate(sofa_obj.SourcePosition):
            if len(row) >= 3:
                positions.append({"index":i,"azimuth":float(row[0]),"elevation":float(row[1]),"radius":float(row[2])})
        convention = getattr(sofa_obj, "GLOBAL_SOFAConventions", "unknown")
        tmp.unlink(missing_ok=True)
        return {"filename":file.filename,"convention":convention,"num_positions":len(positions),"positions":positions}
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(500, str(e))


@app.get("/acoustic-spaces")
async def acoustic_spaces():
    return {"spaces": SPACES}


@app.get("/output/{session}/{fname}")
async def get_file(session: str, fname: str):
    p = OUT_DIR / session / fname
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p))


# ─────────────────────────────────────────────────────────────────────
# ASH-TOOLSET CALL
# ─────────────────────────────────────────────────────────────────────
async def call_ash_toolset(cfg: RoomConfig, out: Path) -> List[str]:
    """
    Drive ASH-Toolset to generate BRIRs for each speaker channel.
    ASH-Toolset doesn't have a clean Python API yet, so we inject
    parameters by patching its config and running a subprocess.
    """
    p = cfg.ash_params

    # Map our params to ASH-Toolset internal names
    space_map = {k:k for k in [s["name"] for s in SPACES]}

    listener_map = {
        "Dummy Head": "dummy_head",
        "Human Listener": "human_listener",
        "User SOFA Input": "sofa_input"
    }

    hp_map = {
        "over-high": ("Over-Ear/On-Ear Headphones", "high strength"),
        "over-low":  ("Over-Ear/On-Ear Headphones", "low strength"),
        "iem-high":  ("In-Ear Headphones",           "high strength"),
        "iem-low":   ("In-Ear Headphones",            "low strength"),
        "none":      ("None",                         ""),
    }
    hp_type, hp_str = hp_map.get(p.headphone_comp, ("Over-Ear/On-Ear Headphones","high strength"))

    res_map = {"Low":0, "Medium":1, "High":2}

    script = f"""
import sys, json
sys.path.insert(0, r"{ASH_PATH}")
sys.path.insert(0, r"{ASH_PATH / 'ash_toolset'}")

try:
    from ash_toolset import ash_toolset_core as core
    from ash_toolset import constants as C

    params = {{
        "acoustic_space":  "{p.acoustic_space}",
        "listener_type":   "{p.listener_type}",
        "dataset":         "{p.dataset}",
        "direct_gain_db":  {p.direct_sound_gain},
        "room_target":     "{p.room_target}",
        "hp_type":         "{hp_type}",
        "hp_strength":     "{hp_str}",
        "resolution":      {res_map.get(p.spatial_resolution, 1)},
        "lf_response":     "{p.lf_response}",
        "lf_crossover":    {p.lf_crossover},
        "hp_rolloff_comp": {str(p.hp_rolloff_comp).lower()},
        "fwd_bwd":         {str(p.fwd_bwd_filter).lower()},
        "sample_rate":     {p.sample_rate},
        "bit_depth":       {p.bit_depth},
        "out_dir":         r"{out}",
    }}

    # Try the ASH dataset export pathway
    result = core.export_brir_dataset(params)
    print(json.dumps({{"status":"ok","files":result}}))

except ImportError as e:
    # Fall back to running ash_toolset.py CLI if API not available
    print(json.dumps({{"status":"fallback","error":str(e)}}))
    sys.exit(2)
except Exception as e:
    import traceback
    print(json.dumps({{"status":"error","error":str(e),"trace":traceback.format_exc()}}))
    sys.exit(1)
"""
    runner = out / "_ash_runner.py"
    runner.write_text(script, encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(runner),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(ASH_PATH)
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)

    try:
        result = json.loads(stdout.decode().strip().split("\n")[-1])
        if result.get("status") == "ok":
            return result.get("files", [])
        elif result.get("status") == "fallback":
            log.info("ASH API not available, using synthetic BRIRs")
            raise RuntimeError("fallback")
        else:
            raise RuntimeError(result.get("error","unknown"))
    except (json.JSONDecodeError, IndexError):
        if proc.returncode == 2:
            raise RuntimeError("fallback")
        raise RuntimeError(stderr.decode()[:500] or "unknown error")


# ─────────────────────────────────────────────────────────────────────
# SYNTHETIC BRIR GENERATOR  (fallback / preview)
# ─────────────────────────────────────────────────────────────────────
async def synthetic_brirs(cfg: RoomConfig, out: Path) -> List[str]:
    """
    Generate physically-motivated BRIRs using:
    - Woodworth ITD model
    - ILD via frequency-dependent head shadow
    - Distance attenuation (inverse square)
    - Schroeder reverb tail
    - Room target coloration
    """
    if not SCIPY:
        log.warning("scipy missing – skipping WAV generation")
        return []

    p   = cfg.ash_params
    sr  = p.sample_rate
    dur = int(0.512 * sr)  # 512ms IR
    gen = []

    for ch, spk in cfg.speakers.items():
        az  = spk.azimuth
        el  = spk.elevation
        d   = max(0.5, spk.distance)

        azR = math.radians(az)
        elR = math.radians(el)

        # ── ITD ───────────────────────────────────────────────────────
        R   = 0.0875          # head radius (m)
        c   = 343.0           # speed of sound
        # Kuhn model for low freq
        itd_s = (R/c) * (azR + math.sin(azR))
        itd_n = int(abs(itd_s) * sr)

        # ── Distance + elevation attenuation ─────────────────────────
        atten = 1.0 / d
        el_atten = max(0.3, math.cos(elR)**0.4)

        # ── Pinna elevation cue (simple notch) ───────────────────────
        pinna_freq = 8000 + el * 80   # rough linear model

        # ── HRIRs ────────────────────────────────────────────────────
        hl = np.zeros(dur); hr = np.zeros(dur)
        dn = min(dur-1, int(d/c * sr))

        if az >= 0:
            hr[dn]           = atten * el_atten
            hl[min(dur-1, dn+itd_n)] = atten * el_atten * _ild_factor(az, el, 'ipsi')
        else:
            hl[dn]           = atten * el_atten
            hr[min(dur-1, dn+itd_n)] = atten * el_atten * _ild_factor(-az, el, 'ipsi')

        # ── Diffuse reverb tail ───────────────────────────────────────
        rt60 = _rt60(p.acoustic_space)
        rev  = _reverb(dur, sr, rt60)
        hl   = hl + rev * 0.12 * atten
        hr   = hr + rev * 0.12 * atten

        # ── Room target EQ ────────────────────────────────────────────
        hl, hr = _target_eq(hl, hr, p.room_target, sr)

        # ── Pinna notch (elevation cue) ───────────────────────────────
        if abs(el) > 10:
            hl, hr = _notch(hl, hr, pinna_freq, sr, depth=4+abs(el)/15)

        # ── Headphone compensation ────────────────────────────────────
        if 'over' in p.headphone_comp or 'iem' in p.headphone_comp:
            hl, hr = _diffuse_field_comp(hl, hr, sr)

        # ── Normalize ────────────────────────────────────────────────
        pk = max(np.max(np.abs(hl)), np.max(np.abs(hr)), 1e-9)
        hl /= pk * 1.05; hr /= pk * 1.05

        # ── Optional HP roll-off comp ─────────────────────────────────
        if p.hp_rolloff_comp:
            hl, hr = _bass_boost(hl, hr, sr)

        # ── Write WAV ─────────────────────────────────────────────────
        az_l = int(round(az)); el_l = int(round(el))
        name = f"BRIR_{ch}_E{el_l:+04d}_A{az_l:+04d}.wav"
        path = out / name
        stereo = np.stack([hl, hr], axis=-1)
        if p.bit_depth == 32:
            stereo_i = (np.clip(stereo,-1,1) * 2147483647).astype(np.int32)
        else:
            stereo_i = (np.clip(stereo,-1,1) * 8388607).astype(np.int32)
        wavfile.write(str(path), sr, stereo_i)
        gen.append(str(path))
        log.info(f"  {name}  az={az:.0f}° el={el:.0f}° d={d:.2f}m")

    # ── SOFA export ───────────────────────────────────────────────────
    if p.export_sofa and SOFAR:
        try:
            sp = _export_sofa(cfg, out)
            gen.append(str(sp))
        except Exception as e:
            log.warning(f"SOFA export failed: {e}")

    return gen


def _ild_factor(az_abs: float, el: float, side: str) -> float:
    """Simplified frequency-integrated ILD."""
    base = 0.5 + 0.5 * math.cos(math.radians(az_abs))
    elw  = max(0.5, math.cos(math.radians(el)))
    return max(0.15, base * elw)

def _rt60(space: str) -> float:
    for s in SPACES:
        if s["name"] == space:
            return s["rt60"] / 1000
    return 0.45

def _reverb(n: int, sr: int, rt60: float) -> np.ndarray:
    t = np.arange(n)/sr
    env = np.exp(-6.91/max(rt60,0.05) * t)
    rng = np.random.default_rng(42)
    noise = rng.standard_normal(n) * 0.08
    return noise * env

def _target_eq(l, r, target, sr):
    if target in ("Harman Target", "ASH Target"):
        sos = butter(2, 120, 'lowpass', fs=sr, output='sos')
        bl = sosfilt(sos, l); br = sosfilt(sos, r)
        return l + bl*0.25, r + br*0.25
    elif target == "X-Curve (Cinema)":
        sos = butter(2, [60, 10000], 'bandpass', fs=sr, output='sos')
        return sosfilt(sos,l), sosfilt(sos,r)
    return l, r

def _notch(l, r, freq, sr, depth=6):
    bw = freq * 0.4
    f1 = max(20, freq-bw/2); f2 = min(sr/2-1, freq+bw/2)
    sos = butter(2, [f1,f2], 'bandstop', fs=sr, output='sos')
    fac = 10**(-depth/20)
    nl = l + (sosfilt(sos,l)-l)*fac
    nr = r + (sosfilt(sos,r)-r)*fac
    return nl, nr

def _diffuse_field_comp(l, r, sr):
    # Gentle DF boost around 3kHz
    sos = butter(2, [2000,6000], 'bandpass', fs=sr, output='sos')
    bl = sosfilt(sos,l); br = sosfilt(sos,r)
    return l+bl*0.12, r+br*0.12

def _bass_boost(l, r, sr):
    sos = butter(2, 80, 'lowshelf' if hasattr(butter,'lowshelf') else 'lowpass', fs=sr, output='sos')
    try:
        return l+sosfilt(sos,l)*0.3, r+sosfilt(sos,r)*0.3
    except:
        return l, r

def _export_sofa(cfg: RoomConfig, out: Path) -> Path:
    sofa_obj = sofar.Sofa("SimpleFreeFieldHRIR")
    p = cfg.ash_params
    sr = p.sample_rate; dur = int(0.512*sr)
    rows = [[spk.azimuth, spk.elevation, spk.distance] for spk in cfg.speakers.values()]
    n = len(rows)
    sofa_obj.GLOBAL_Title           = "ASH Room Designer Export"
    sofa_obj.GLOBAL_DateCreated     = datetime.now().isoformat()
    sofa_obj.GLOBAL_Comment         = f"Layout:{cfg.layout} Room:{cfg.room}"
    sofa_obj.Data_SamplingRate      = float(sr)
    sofa_obj.Data_SamplingRate_Units= "hertz"
    sofa_obj.SourcePosition         = np.array(rows, dtype=float)
    sofa_obj.ReceiverPosition       = np.array([[0,0,0],[0,0,0]], dtype=float)
    sofa_obj.Data_IR                = np.zeros((n,2,dur), dtype=float)
    path = out / "room_brirs.sofa"
    sofar.write_sofa(str(path), sofa_obj)
    log.info(f"SOFA: {path}")
    return path


# ─────────────────────────────────────────────────────────────────────
# EQ APO CONFIG
# ─────────────────────────────────────────────────────────────────────
def write_eqapo_config(cfg: RoomConfig, wav_dir: Path, out_path: Path):
    p = cfg.ash_params
    hp_map = {
        "over-high":"Over-Ear High","over-low":"Over-Ear Low",
        "iem-high":"In-Ear High","iem-low":"In-Ear Low","none":"None"
    }

    lines = [
        "# ════════════════════════════════════════════",
        "# ASH Room Designer – Equalizer APO Config",
        f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Layout:    {cfg.layout}  ({len(cfg.speakers)} channels)",
        f"# Room:      {cfg.room.get('w',0):.1f} × {cfg.room.get('d',0):.1f} × {cfg.room.get('h',0):.1f} m",
        f"# Space:     {p.acoustic_space}",
        f"# HRTF:      {p.dataset} ({p.listener_type})",
        f"# HP Comp:   {hp_map.get(p.headphone_comp,p.headphone_comp)}",
        f"# Target:    {p.room_target}",
        "# ════════════════════════════════════════════",
        "",
        "Device: all",
        "",
        "# Pre-amplification (prevent clipping from multi-channel convolution)",
        f"Preamp: -{max(6, len(cfg.speakers)//2)} dB",
        "",
    ]

    if p.hp_rolloff_comp:
        lines += [
            "# Headphone bass roll-off compensation",
            "Filter: ON LSC Fc 80 Hz Gain 4.0 dB Q 0.707",
            "",
        ]

    # Channel assignments (7.1 → standard Windows channel order)
    CH_WIN = {
        'L':'L','R':'R','C':'C','LFE':'SUB',
        'LS':'RL','RS':'RR','LB':'SBL','RB':'SBR',
        'LTF':'TFL','RTF':'TFR','LTR':'TBL','RTR':'TBR'
    }
    # group by channel
    for ch, spk in cfg.speakers.items():
        az = int(round(spk.azimuth)); el = int(round(spk.elevation))
        wav = f"BRIR_{ch}_E{el:+04d}_A{az:+04d}.wav"
        wav_path = wav_dir / wav
        win_ch = CH_WIN.get(ch, ch)
        lines += [
            f"# ── Channel {ch}: {az:+d}° az / {el:+d}° el / {spk.distance:.1f}m",
            f"Channel: {win_ch}",
            f'Convolution: "{wav_path}"',
            "",
        ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"EQ APO config: {out_path}")


# ─────────────────────────────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────────────────────────────
SPACES = [
    {"name":"Listening Room A","rt60":221},{"name":"Listening Room B","rt60":379},
    {"name":"Listening Room C","rt60":562},{"name":"Listening Room D","rt60":312},
    {"name":"Living Room","rt60":967},
    {"name":"Small Room A","rt60":500},{"name":"Small Room B","rt60":467},
    {"name":"Control Room","rt60":260},
    {"name":"Audio Lab A","rt60":305},{"name":"Audio Lab B","rt60":413},
    {"name":"Recording Studio A","rt60":723},{"name":"Recording Studio B","rt60":739},
    {"name":"Cinema","rt60":900},
    {"name":"Concert Hall A","rt60":1599},{"name":"Concert Hall B","rt60":1386},
    {"name":"Concert Hall C","rt60":1526},
    {"name":"Hall A","rt60":1418},{"name":"Hall B","rt60":949},
    {"name":"Auditorium A","rt60":1455},
    {"name":"Small Theatre","rt60":920},{"name":"Theatre","rt60":884},
    {"name":"Studio A","rt60":398},{"name":"Studio B","rt60":351},
    {"name":"Seminar Room A","rt60":839},
    {"name":"Lecture Hall A","rt60":901},
    {"name":"Treated Room","rt60":178},
]

# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║   ASH Room Designer Backend  v2.0               ║")
    print("  ╚══════════════════════════════════════════════════╝")
    print()
    print(f"  ASH-Toolset  : {ASH_PATH or '[NOT FOUND – synthetic BRIRs will be used]'}")
    print(f"  Equalizer APO: {EQAPO_PATH or '[not found]'}")
    print(f"  Output       : {OUT_DIR}")
    print(f"  sofar        : {'✓' if SOFAR else '✗  pip install sofar'}")
    print(f"  scipy        : {'✓' if SCIPY else '✗  pip install scipy'}")
    print()
    if not ASH_PATH:
        print("  To enable ASH-Toolset:")
        print("    git clone https://github.com/ShanonPearce/ASH-Toolset C:\\ASH-Toolset")
        print("    set ASH_TOOLSET_PATH=C:\\ASH-Toolset")
        print()
    print(f"  Browser UI   : open ash_room_designer.html")
    print(f"  API docs     : http://localhost:{PORT}/docs")
    print("  ─────────────────────────────────────────────────────")
    print()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
