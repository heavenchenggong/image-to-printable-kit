#!/usr/bin/env python3
"""Layer-by-layer scan for MID-PART overhangs, by print orientation --
the class of failure the existing gates miss.

Why this script exists
----------------------
`warp.py` only looks at FIRST-LAYER grounding (warping); the slicer only looks
at **floating regions** (whole detached islands).  Neither catches the most
common way a print is scrapped: **a ring of horizontal step around the middle
of a part**.  It is connected to material above and below, so the slicer does
not complain; it is not in the first layer, so warp.py does not complain;
but underneath that ring is air, so the first few layers extrude into open air
-> drooping/stringing/no adhesion -> snaps off at a touch.

Measured case (winston glue-free P2 plate object `01 底座+下身罩`
(base + lower-body cover), scrapped as a physical print on 2026-09-26):
the cross-section radius sits at 13.995 mm (= neck Ø28) all the way to z=20.8,
then **one step at z=21.0 jumps to 18.030 mm** -- a single layer suddenly
gains **406 mm²** of unsupported ring (615.3 -> 1021.3 mm²).  The slicer says
"no warning"; the real print turned into a blob of spaghetti at the
neck->skirt junction.

Algorithm
---------
Two measurements; picked automatically by whether the mesh is sealed:

1. **watertight -> real section outline** (primary criterion)
   `mesh.section()` + `Polygon2D.polygons_full`, measuring two numbers:
   - `area` the overhang area newly appearing on this layer (mm²)
   - `span` how far the new material is from the nearest material below (mm)
     = **the real cantilever span**
   Both are required: `area` alone is fooled by "a big part with a thin ring",
   `span` alone by "a small part with one sharp corner".

2. **not sealed -> area only** (fallback)
   Section area `A(z)` -> equivalent radius `r = sqrt(A/π)`, layer-to-layer
   difference `dr = r(z) − r(z−h)`.  `dr > layer height` means a wall steeper
   than 45°.  Area is coordinate-independent, so this path always works.

⚠️ Three traps already stepped in, recorded here so nobody walks them again
--------------------------------------------------------------------------
1. **`Path3D.to_2D()` re-locates the plane by "this layer's own bounding box"
   when `to_2D` is not passed** -- every layer gets a different frame ->
   layer-to-layer `difference()` results are all garbage.
   Always pass `plane_transform(origin, normal)` explicitly.
2. **A convex hull bridges the two limbs of a bent part**: measured on an arm,
   the hull reported 101.8 mm² / 7.04 mm while the real section grows only
   0.015 mm/layer (= perfectly fine).  On bent parts the hull is **all false
   positives**.  -- An earlier version of this script used the hull and
   false-flagged a whole plate.
3. **3MF object naming has three numbering schemes**:
   - a plate we just wrote (`splitter.write_colour_plates`): trimesh's node
     name == object id;
   - a plate rewritten by the slicer: node names are renumbered (observed
     offset −1), but the `<object>` gains a `face_count` attribute;
   - `face_count` is **not a 3MF spec field** and is absent on fresh plates.
   So: look the name up by id first, fall back to `face_count`, then to the
   node name.

Usage
-----
    python overhang.py plate.3mf                  # scan a whole plate
    python overhang.py plate.3mf --json out.json  # save JSON, usable as a CI gate
    python overhang.py part.stl                   # single part (no transform, own z axis)

Exit code: 0 = everything passed; 2 = at least one part flagged.

Scope note: this script judges only **geometric** overhang.  Actual support is
injected per object on the slicer side by `SUPPORT` in `bake_project.py`
(`enable_support=1` + `tree(auto)`).  This script tells you **which part,
which layer** needs it, and verifies after a design change that the ring
really went away.
"""

import argparse
import json
import os
import re
import sys
import zipfile

import numpy as np
import trimesh
from shapely.geometry import Point
from shapely.ops import unary_union
from trimesh.geometry import plane_transform

# One fixed 2-D frame for EVERY layer.  See trap 1 in the module docstring.
PLANE = plane_transform(origin=[0.0, 0.0, 0.0], normal=[0.0, 0.0, 1.0])


def weld(mesh):
    """Merge duplicate vertices.  STL never shares vertices; without welding
    nothing measures repeatably."""
    m = mesh.copy()
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    return m


def section(mesh, z):
    """-> (section area mm², outline as shapely geometry or None).

    The area holds for **any** mesh (summed over closed loops,
    coordinate-independent); the outline is only trustworthy when watertight,
    so on a non-sealed mesh None is returned and the fallback criterion kicks in.
    """
    s = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if s is None:
        return None, None
    if isinstance(s, tuple):
        s = s[0]
    p2, _ = s.to_2D(to_2D=PLANE)
    polys = [p for p in p2.polygons_full if p.area > 1e-9]
    if not polys:
        return 0.0, None
    union = unary_union(polys)
    return float(union.area), (union if mesh.is_watertight else None)


def scan(mesh, label, layer=0.2, tol=0.25, skip_bottom=1.5,
         area_warn=150.0, span_warn=3.0, area_min=30.0,
         dr_warn=0.35, darea_warn=25.0):
    """tol = outward padding of the layer below; at 0.2 layer height,
    tol=0.25 roughly admits walls up to 51° from vertical."""
    z0, z1 = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    prev, prev_a = None, None
    rows, z = [], z0
    while z <= z1 + 1e-9:
        a, cur = section(mesh, z)
        if a is not None and a > 0 and z - z0 > skip_bottom:
            rec = {"h": round(z - z0, 2), "section_area": round(a, 1),
                   "r_eff": round(float(np.sqrt(a / np.pi)), 2),
                   "area": 0.0, "span": 0.0, "dr": None, "darea": None}
            if cur is not None and prev is not None:
                new = cur.difference(prev.buffer(tol, join_style=2))
                if not new.is_empty and new.area > 1e-6:
                    span = 0.0
                    for g in (list(new.geoms) if hasattr(new, "geoms") else [new]):
                        for xy in g.exterior.coords:
                            span = max(span, prev.distance(Point(xy)))
                    rec["area"] = round(new.area, 1)
                    rec["span"] = round(span, 2)
            if prev_a is not None:
                rec["dr"] = round(rec["r_eff"] - float(np.sqrt(prev_a / np.pi)), 3)
                rec["darea"] = round(a - prev_a, 1)
            rows.append(rec)
        if a is not None:
            prev_a = a
            prev = cur if cur is not None else None   # lost outline -> a diff
        z += layer                                  # against it would be fake

    # There is exactly one criterion, chosen by whether the mesh is sealed --
    # mixing the two contaminates both:
    #   watertight -> span/area: real geometry, can localise "how far above
    #                the layer below"
    #   not sealed -> dr/darea : consumes area only, holds for any mesh
    # The cost of mixing them was measured: `08 手掌` (palm) has a slender
    # cross-section, and lateral shift alone pushes r_eff up by 0.77 while the
    # real cantilever is only 0.25 mm -- the dr criterion false-positives on
    # slender sections.
    sealed = mesh.is_watertight

    def bad(r):
        if sealed:
            return ((r["span"] >= span_warn and r["area"] >= area_min)
                    or r["area"] >= area_warn)
        return (r["dr"] is not None and r["dr"] >= dr_warn
                and (r["darea"] or 0.0) >= darea_warn)

    def severity(r):
        """Even without a flag, report the layer closest to flagging --
        otherwise a passing part looks like it was never measured."""
        if sealed:
            return max(r["span"] / span_warn if span_warn else 0.0,
                       r["area"] / area_warn if area_warn else 0.0)
        return (r["dr"] / dr_warn if (dr_warn and r["dr"] is not None) else 0.0,
                (r["darea"] or 0.0) / darea_warn if darea_warn else 0.0)

    worst = max(rows, key=lambda r: (bad(r), severity(r)), default=None)
    verdict = "warn" if (worst and bad(worst)) else "ok"
    return {"part": label, "height": round(z1 - z0, 1), "verdict": verdict,
            "worst": worst,
            "top": sorted(rows, key=lambda r: (bad(r), severity(r)),
                          reverse=True)[:3],
            "method": "poly+area" if sealed else "area-only"}


def object_names(path):
    """3mf: object id -> display name on the plate.

    For the three-numbering-schemes trap see item 3 in the module docstring.
    Returns two dicts; the caller applies them in priority order.
    """
    try:
        with zipfile.ZipFile(path) as z:
            ms = z.read("Metadata/model_settings.config").decode("utf-8", "ignore")
    except Exception:
        return {}, {}
    by_id, by_faces = {}, {}
    for oid, block in re.findall(r'<object id="([^"]+)"[^>]*>(.*?)</object>',
                                 ms, re.S):
        n = re.search(r'key="name"\s+value="([^"]+)"', block)
        f = re.search(r'face_count="(\d+)"', block)
        if n:
            by_id[oid] = n.group(1)
            if f:
                by_faces[int(f.group(1))] = n.group(1)
    return by_id, by_faces


def load_plate(path):
    """-> [(name, mesh in world coordinates)].  3mf goes through the scene
    graph; stl is read directly."""
    obj = trimesh.load(path)
    if hasattr(obj, "graph") and hasattr(obj, "geometry"):
        by_id, by_faces = object_names(path)
        out = []
        for node in obj.graph.nodes_geometry:
            T, gname = obj.graph[node]
            raw = obj.geometry[gname]
            # Name by the RAW face count before welding -- welding deletes
            # degenerate faces, the count changes and `face_count` no longer
            # matches (on slicer-rewritten plates this is the only path that
            # works).
            lab = (by_id.get(str(node)) or by_faces.get(len(raw.faces))
                   or str(node))
            m = raw.copy()
            m.apply_transform(T)
            out.append((lab, weld(m)))
        return out
    return [(os.path.basename(path), weld(obj))]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plates", nargs="+")
    ap.add_argument("--layer", type=float, default=0.2)
    ap.add_argument("--tol", type=float, default=0.25,
                    help="outward padding of the layer below, absorbs slopes "
                         "(at 0.2 layer height, 0.25 ≈ admits walls up to 51°)")
    ap.add_argument("--skip-bottom", type=float, default=1.5,
                    help="ignore layers this close to the bed (sloped bottoms "
                         "are warp.py's job)")
    ap.add_argument("--area-warn", type=float, default=150.0,
                    help="alarm threshold for newly unsupported area per layer")
    ap.add_argument("--span-warn", type=float, default=3.0,
                    help="alarm threshold for cantilever span (must also meet "
                         "--area-min)")
    ap.add_argument("--area-min", type=float, default=30.0,
                    help="minimum area for the span criterion; filters needle-tip noise")
    ap.add_argument("--dr-warn", type=float, default=0.35,
                    help="alarm threshold for per-layer equivalent-radius growth "
                         "(0.35 mm / 0.2 mm ≈ 60°)")
    ap.add_argument("--darea-warn", type=float, default=25.0,
                    help="minimum area increment for the dr criterion")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    report, bad = [], 0
    for plate in a.plates:
        print(f"===== {plate}")
        for label, mesh in load_plate(plate):
            s = scan(mesh, label, layer=a.layer, tol=a.tol,
                     skip_bottom=a.skip_bottom, area_warn=a.area_warn,
                     span_warn=a.span_warn, area_min=a.area_min,
                     dr_warn=a.dr_warn, darea_warn=a.darea_warn)
            report.append({"plate": plate, **s})
            w = s["worst"]
            if w is None:
                print(f"   {label:16s} ok    no mid-part overhang "
                      f"(part height {s['height']} mm)")
                continue
            tag = "!! WARN" if s["verdict"] == "warn" else "ok    "
            if s["method"] == "area-only":
                detail = (f"Δr {w['dr']:+6.3f} mm/layer  "
                          f"ΔA {w['darea']:+8.1f} mm²")
            else:
                detail = (f"unsupported {w['area']:7.1f} mm²  "
                          f"span {w['span']:5.2f} mm")
            print(f"   {label:16s} {tag} [{s['method']:9s}] worst layer at "
                  f"{w['h']:5.1f} mm above the bed  {detail}")
            if s["verdict"] == "warn":
                bad += 1
                print("       → this part needs per-object support, or turn "
                      "that ring into a 45° chamfer")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\nJSON → {a.json}")
    print(f"\ntotal flagged: {bad} part(s)")
    sys.exit(2 if bad else 0)


if __name__ == "__main__":
    main()
