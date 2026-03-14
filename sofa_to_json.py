"""
sofa_to_json.py
===============
Standalone helper: converts a SOFA file to a JSON positions file
that can be loaded directly into the 3D Room Designer.

Usage:
    python sofa_to_json.py path\to\file.sofa

Output:
    path\to\file_positions.json
    → Load via the SOFA Import area in the 3D UI
"""

import sys
import json
from pathlib import Path

def sofa_to_json(sofa_path: str) -> str:
    try:
        import sofar
    except ImportError:
        print("ERROR: sofar not installed. Run: pip install sofar")
        sys.exit(1)

    p = Path(sofa_path)
    if not p.exists():
        print(f"ERROR: File not found: {p}")
        sys.exit(1)

    print(f"Loading {p.name}...")
    sofa_obj = sofar.read_sofa(str(p))

    print(f"Convention : {getattr(sofa_obj, 'GLOBAL_SOFAConventions', 'unknown')}")

    src_pos = sofa_obj.SourcePosition
    print(f"Positions  : {len(src_pos)}")

    positions = []
    for i, row in enumerate(src_pos):
        if len(row) >= 3:
            positions.append({
                "index":     i,
                "azimuth":   float(row[0]),
                "elevation": float(row[1]),
                "radius":    float(row[2]),
            })
        elif len(row) == 2:
            positions.append({
                "index":     i,
                "azimuth":   float(row[0]),
                "elevation": float(row[1]),
                "radius":    2.0,
            })

    out_data = {
        "source_file": p.name,
        "convention":  getattr(sofa_obj, 'GLOBAL_SOFAConventions', 'unknown'),
        "num_positions": len(positions),
        "positions":   [[p["azimuth"], p["elevation"], p["radius"]] for p in positions],
        "positions_detailed": positions
    }

    out_path = p.with_name(p.stem + "_positions.json")
    out_path.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
    print(f"Saved to   : {out_path}")
    print()
    print("Next step: In the 3D Room Designer, click 'Drop SOFA file'")
    print("           and load the generated _positions.json file.")
    return str(out_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sofa_to_json.py path\\to\\file.sofa")
        sys.exit(1)
    sofa_to_json(sys.argv[1])
