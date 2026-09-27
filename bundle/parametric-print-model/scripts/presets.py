#!/usr/bin/env python3
"""Bambu preset resolution — the ONE copy of this logic.

Shared by `bake_project.py` (what the slice is made with) and `weigh_3mf.py`
(what the numbers are read from).  They must agree: if the baker flattens a
preset differently from the weigher, the delivered file and the delivered
gram figure disagree, and the user is holding two different truths.

## Why this module exists at all

`BambuStudio --slice` resolves **almost nothing** in a preset's `inherits`
chain.  Feed it `Bambu PETG Basic @BBL X2D 0.4 nozzle.json` directly and:

    ; filament_type = PLA                  (should be PETG)
    ; filament_density = 0                 -> "total filament weight [g]: 0.00"
    ; textured_plate_temp = 45             -> M190 S45 (should be 70)
    filament_flow_ratio -> default 1.0     (should be 0.95, measured +5% plastic)

Three silent lies, and two of them ruin the print rather than the report.  The
fields that DO come through (nozzle temperature, fan) are the ones you would
happen to eyeball, which is why it hides so well.

So we flatten the chain ourselves — ancestors first, nearest definition wins,
exactly the rule the slicer uses.  Never hand-type a value that lives in the
chain: the nearest definition is usually NOT the category one.

    Bambu PETG Basic @BBL X2D 0.4 nozzle   --
      Bambu PETG Basic @base                1.25  density   70 C bed
        fdm_filament_pet                    1.27           80 C
          fdm_filament_common               0 (placeholder)

## Gotchas this module also encodes

* A chain value of `0` / `[]` / `null` means "not defined here" for density,
  but `0` is a REAL value for `cool_plate_temp` — only skip what must be
  non-zero (density), and let everything else pass through.
* A subtring search for a preset must be biased to the right machine and
  nozzle, or `PETG` lands on "...@BBL A1 0.2 nozzle.json".
* The same `ps` probe: `ps` is denied in some sandboxes, so callers use `lsof`.
"""
import json
import os
import re

BBL = "/Applications/BambuStudio.app/Contents/Resources/profiles/BBL"

# Last-resort only; the truth is in the chain (see resolve_density).
RHO = {"PLA": 1.26, "PLA-CF": 1.21, "PETG": 1.25, "ABS": 1.04, "ASA": 1.07,
       "TPU": 1.21, "PC": 1.20, "PA": 1.14, "PA-CF": 1.06, "PVA": 1.23,
       "Support": 1.24, "HIPS": 1.04}

D_PRINTER = "Bambu Lab X2D 0.4 nozzle"
D_PROCESS = "0.20mm Standard @BBL X2D"


def printer_tokens(printer):
    """['X2D', '0.4 nozzle'] from 'Bambu Lab X2D 0.4 nozzle'."""
    m = re.search(r"([A-Za-z0-9]+)\s+([\d.]+)\s*nozzle", printer)
    if m:
        return [m.group(1), f"{m.group(2)} nozzle"]
    m = re.search(r"Bambu Lab\s+([A-Za-z0-9]+)", printer)
    return [m.group(1)] if m else []


def find_preset(kind, stem, root=BBL, prefer=()):
    """Exact `<stem>.json` in <root>/<kind>/, else the best substring hit.

    `prefer` tokens are scored first: without them a loose stem picks the
    alphabetically-first match, i.e. the wrong printer AND the wrong nozzle.
    """
    d = os.path.join(root, kind)
    exact = os.path.join(d, stem + ".json")
    if os.path.exists(exact):
        return exact
    try:
        hits = [f for f in os.listdir(d)
                if f.endswith(".json") and stem.lower() in f.lower()]
    except OSError:
        hits = []
    if not hits:
        raise FileNotFoundError(
            f"{kind} preset {stem!r} not found in {d}\n"
            f"  list them with: ls '{d}' | grep -i <model>")
    hits.sort(key=lambda f: (-sum(t.lower() in f.lower() for t in prefer),
                             len(f), f))
    return os.path.join(d, hits[0])


def _chain(kind, stem, root=BBL):
    """[nearest, ..., root] preset dicts following `inherits`."""
    out, cur, seen = [], stem, set()
    while cur and cur not in seen:
        seen.add(cur)
        path = os.path.join(root, kind, cur + ".json")
        if not os.path.exists(path):
            break
        d = json.load(open(path))
        out.append(d)
        cur = d.get("inherits")
    return out


def flatten_preset(kind, stem, root=BBL):
    """Merge a preset's whole chain into one self-contained dict.

    Ancestors first, child overrides -> nearest definition wins.  `inherits` is
    dropped: nothing is left to resolve, which is the entire point.
    """
    merged = {}
    for d in reversed(_chain(kind, stem, root)):
        for k, v in d.items():
            if v is not None:
                merged[k] = v
    merged.pop("inherits", None)
    merged["name"] = stem
    return merged


def resolve_filament(stem, root=BBL):
    """Walk a filament preset's chain -> (filament_type, density).

    Density uses the NEAREST non-zero definition: `Bambu PETG Basic @base` says
    1.25 and that beats `fdm_filament_pet`'s 1.27.
    """
    ftype = dens = None
    for d in _chain("filament", stem, root):
        v = d.get("filament_type")
        if ftype is None and v is not None:
            ftype = v[0] if isinstance(v, list) else v
        v = d.get("filament_density")
        if dens is None and v is not None:
            v = v[0] if isinstance(v, list) else v
            if float(v) > 0:
                dens = v
    return ftype, dens


def resolve_density(preset_path):
    """Density from an already-resolved path, or None."""
    stem = os.path.basename(preset_path)[:-5]
    return resolve_filament(stem, root=os.path.dirname(os.path.dirname(preset_path)))[1]


def write_presets(out_dir, printer=D_PRINTER, process=D_PROCESS, filament=None,
                  material="PLA", bed=None, fan_layers=None,
                  filament_type=None, density=None, root=BBL):
    """Flatten the three presets into `out_dir` and report what they resolve to.

    Returns a dict with `settings` / `filament` (the CLI arguments), the three
    paths, and the effective `type` / `density` so callers can print the
    resolved values.
    `filament_type` / `density` default to what the chain says -- pass them only
    to override deliberately.
    """
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.basename(find_preset(
        "filament", filament or f"Bambu {material}", root,
        prefer=printer_tokens(printer) + ["Basic"]))[:-5]

    m = flatten_preset("machine", printer, root)
    if bed:
        m["curr_bed_type"] = bed
    p = flatten_preset("process", process, root)
    if fan_layers not in (None, ""):
        p["close_fan_the_first_x_layers"] = str(fan_layers)

    rtype, rdensity = resolve_filament(stem, root)
    f = flatten_preset("filament", stem, root)
    f["filament_type"] = [filament_type or rtype]
    f["filament_density"] = [str(density or rdensity)]

    paths = {}
    for key, d in (("machine", m), ("process", p), ("filament", f)):
        paths[key] = os.path.join(out_dir, key + ".json")
        json.dump(d, open(paths[key], "w"), indent=4)
    return {
        "paths": paths,
        "settings": f"{paths['machine']};{paths['process']}",
        "filament": paths["filament"],
        "filament_stem": stem,
        "type": f["filament_type"][0],
        "density": float(f["filament_density"][0]),
        "default_type": rtype,
        "default_density": rdensity,
    }


def preset_args(out_dir, **kw):
    """-> (settings_arg, filaments_arg, info) ready for the CLI."""
    info = write_presets(out_dir, **kw)
    return info["settings"], info["filament"], info


def read_result_error(workdir):
    """The slicer's OWN refusal reason for `workdir`, or None if it is clean.

    When `--slice` rejects a job it prints the reason, writes it to
    `result.json`, and then just SITS THERE -- so a rejection is
    indistinguishable from a hang unless you read this file:

        got error when validate: Plate 1: Cool Plate does not support
        filament 1
        run found error, exit
        {"error_string": "Filaments are not compatible with the plate type.",
         "return_code": -61}

    The process does not exit, so the caller must poll for it (or wait out the
    timeout and never learn why).  None means "absent / still being written /
    clean" -- keep going.

    Judge it by `return_code`, NOT by `error_string`: a SUCCESSFUL run also
    fills that field in, with the literal string `"Success."`.
    """
    try:
        with open(os.path.join(workdir, "result.json")) as fh:
            d = json.load(fh)
    except Exception:
        return None
    rc = d.get("return_code")
    if isinstance(rc, int) and rc != 0:
        return d.get("error_string") or f"return_code {rc}"
    return None
