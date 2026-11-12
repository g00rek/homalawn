#!/usr/bin/env python3
"""Usage: gen-prefs-catalog.py lawnchair/  -> writes src/homalawn/prefs_catalog.json"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from homalawn.prefs import catalog_from_sources  # noqa: E402

root = Path(sys.argv[1]) / "lawnchair/src/app/lawnchair"
ds = [p.read_text() for p in sorted((root / "preferences2").glob("*.kt"))]
ds += [p.read_text() for p in sorted((root / "omalawn").rglob("*.kt"))]  # our own settings (patches 0014+)
sh = (root / "preferences/PreferenceManager.kt").read_text()
sh += "".join(p.read_text() for p in sorted((root / "omalawn").rglob("*.kt")))  # our shared settings (0023+)
cat = catalog_from_sources(ds, sh)
out = Path(__file__).resolve().parent.parent / "src/homalawn/prefs_catalog.json"
out.write_text(json.dumps(cat, indent=1, sort_keys=True) + "\n")
print(f"{len(cat)} keys -> {out}")
