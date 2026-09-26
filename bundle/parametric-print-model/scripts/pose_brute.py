#!/usr/bin/env python3
"""Pick a print orientation by asking the slicer, instead of trusting a proxy.

WHY THIS EXISTS
    `splitter.footprint()` measures contact as the convex hull of the vertices
    within 0.8 mm of the plate.  On a concave base that is wrong in BOTH
    directions -- measured on one kit:

        part             hull proxy   real first layer (z = 0.2)
        犄角 (bent tube)   153 mm2  ->  18.4 mm2    8x OVER
        耳鳍 (cone+pin)      1 mm2  ->   4.35 mm2   4x UNDER

    Over-reporting is the dangerous direction: it passes poses that are really
    balanced on a knife edge, which is exactly what `MIN_CONTACT = 5.0` is
    supposed to stop.  So rank candidates offline (cheap) and let the slicer
    decide (authoritative):

        result.json -> sliced_plates[0].warning_message
        clean is an EMPTY STRING, not a missing field.

    One 45 mm part slices in ~1.4 s, so a few hundred candidates are affordable
    at one process each -- the sweep is embarrassingly parallel.

TWO OUTCOMES, BOTH USEFUL
    * a pose exists  -> feed the winner to `splitter.orient(mesh, "face", dir)`
    * no pose exists -> the part needs per-object support instead.  Write
      `enable_support=1` + `support_type=tree(auto)` into that object's block in
      the delivered 3MF's Metadata/model_settings.config.  Only the OBJECT
      level: flipping the process-wide switch wraps the whole plate.

THE FRAME TRAP (cost a full rebuild)
    Candidates are cut from the print-pose STL, which already sits in
    `lay_flat()`'s frame.  So the direction below is an INCREMENT ON TOP of
    `lay_flat`, not a machine-frame axis:

        base, T = lay_flat(mesh);  out = upright(base, -dir)   # correct
        out = upright(mesh, -dir)                              # rotates twice

    The second form lands the part somewhere else entirely (it came out 16.1 mm
    tall instead of 10.7, and was still flagged).

USAGE
    python3 pose_brute.py part.stl [--n 240] [--jobs 8] [--out poses.json]
    python3 pose_brute.py part.stl --offline          # rank only, no slicing
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import trimesh
from trimesh.geometry import align_vectors
from trimesh.path.entities import Line
from trimesh.path.path import Path2D
from shapely.geometry import LineString
from shapely.ops import unary_union

CLI = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
FILAMENT = "Bambu PETG Basic @BBL X2D 0.4 nozzle"

LH = 0.20        # probe layer height -- keep at 0.2 to match the slicer
MARGIN = 0.70    # the `gap` in detect_floating_line / detect_floating_vertical_shell
FIRST_Z = 0.20   # height at which the contact patch is measured
MIN_FIRST = 5.0  # same number as splitter.MIN_CONTACT, on the honest ruler

_M = None
_PRESETS = None
_WORK = None


# ------------------------------------------------------------------ geometry
def load(path):
    path = os.path.abspath(path)
    if path.lower().endswith(".stl"):
        return trimesh.load(path, process=False)
    # trimesh refuses the .model extension outright
    raw = zipfile.ZipFile(path).read("3D/3dmodel.model").decode("utf-8", "replace")
    v = np.array(re.findall(r'<vertex x="([-\d.eE]+)" y="([-\d.eE]+)" z="([-\d.eE]+)"',
                            raw), dtype=float)
    t = np.array(re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', raw), dtype=int)
    return trimesh.Trimesh(vertices=v, faces=t, process=False)


def polygonise(segments):
    """Stitch raw 2-point segments into filled shapely polygons.

    `paths_to_polygons()` would be the obvious call, but it only accepts loops
    that are already closed -- feed it 2-point segments and every one is
    silently skipped (len(path) < 4), giving all-None with no error.
    """
    if len(segments) == 0:
        return None
    ent = [Line(points=[2 * k, 2 * k + 1]) for k in range(len(segments))]
    p = Path2D(entities=ent, vertices=np.asarray(segments, float).reshape(-1, 2))
    p.merge_vertices()
    p.remove_duplicate_entities()
    try:
        p.process()
    except Exception:
        pass
    polys = [q for q in p.polygons_full if q is not None and not q.is_empty]
    return unary_union(polys) if polys else None


def outermost(geom):
    """Exterior ring of the OUTERMOST polygons only, or None.

    `polygons_full` returns a cavity as its own polygon, so taking every
    polygon's `.exterior` counts the inside of a hollow part as exposed wall --
    one pedestal scored 113 mm of "floating" wall that way, all of it its own
    cavity roof, wrapped in solid material.  A polygon sitting inside a sibling
    is a cavity and has to go too.
    """
    if geom is None or geom.is_empty:
        return None
    parts = [p for p in (geom.geoms if hasattr(geom, "geoms") else [geom])
             if p is not None and not p.is_empty and hasattr(p, "exterior")]
    outer = [p for p in parts
             if not any(q is not p and q.covers(p.representative_point()) for q in parts)]
    if not outer:
        return None
    return unary_union([LineString(p.exterior.coords) for p in outer])


def sit(mesh, d):
    """Rotate so direction d faces the bed, then drop onto z = 0."""
    d = np.asarray(d, float)
    d = d / np.linalg.norm(d)
    if abs(d[2] + 1.0) < 1e-9:          # align_vectors degenerates at -Z
        d = d + np.array([1e-6, 0.0, 0.0])
        d = d / np.linalg.norm(d)
    r = align_vectors(d, np.array([0.0, 0.0, -1.0]))
    m2 = mesh.copy()
    m2.apply_transform(r)
    tz = np.eye(4)
    tz[2, 3] = -float(m2.bounds[0][2])
    m3 = mesh.copy()
    m3.apply_transform(tz @ r)
    return m3, tz @ r


def measure(m3, lh=LH, margin=MARGIN):
    """-> dict(first, float_vol, longest_run, height) or None.

    `float_vol` counts only floats that SURVIVE two or more layers, because a
    single floating layer is a bridge and prints fine.  That distinction is what
    makes the number line up with the slicer: a part with one 342 mm2 floating
    layer (68.6 mm3 naive) passes cleanly, while a part with 7.5 mm3 spread over
    four consecutive layers is flagged.
    """
    zmin, zmax = float(m3.bounds[0][2]), float(m3.bounds[1][2])
    hs = np.arange(zmin + lh, zmax, lh)
    if len(hs) == 0:
        return None
    s0, _, _ = trimesh.intersections.mesh_multiplane(
        m3, [0.0, 0.0, zmin], [0.0, 0.0, 1.0], [FIRST_Z])
    g0 = polygonise(s0[0])
    first = 0.0 if g0 is None else float(g0.area)

    segs, _, _ = trimesh.intersections.mesh_multiplane(
        m3, [0.0, 0.0, zmin], [0.0, 0.0, 1.0], hs - zmin)
    prev, flen, bare = None, [], []
    for seg in segs:
        cur = polygonise(seg)
        if cur is None or prev is None:
            prev = cur
            continue
        allowed = prev.buffer(margin)
        walls = outermost(cur)
        flen.append(0.0 if walls is None
                    else (0.0 if walls.difference(allowed).is_empty
                          else float(walls.difference(allowed).length)))
        b = cur.difference(allowed)
        bare.append(0.0 if b.is_empty else float(b.area))
        prev = cur
    if not flen:
        return None

    runs, s, p = [], None, None
    for i, x in enumerate(flen):
        if x <= 0.5:
            continue
        if s is None:
            s = p = i
        elif i == p + 1:
            p = i
        else:
            runs.append((s, p))
            s = p = i
    if s is not None:
        runs.append((s, p))
    multi = [i for a, b in runs if b - a + 1 >= 2 for i in range(a, b + 1)]
    return dict(first=first, float_vol=float(sum(bare[i] for i in multi) * lh),
                flen_max=float(max(flen)) if flen else 0.0,
                longest_run=max([b - a + 1 for a, b in runs], default=0),
                layers=len(flen),
                height=float(m3.bounds[1][2] - m3.bounds[0][2]))


# ---------------------------------------------------------------- candidates
def _dedupe(dirs, ang_tol=7.0):
    out, cos_tol = [], np.cos(np.radians(ang_tol))
    for d in dirs:
        d = np.asarray(d, float)
        n = np.linalg.norm(d)
        if n < 1e-9:
            continue
        d = d / n
        if all(float(d @ p) < cos_tol for p in out):
            out.append(d)
    return out


def candidate_dirs(mesh, min_area=0.4, ang_tol=9.0, limit=90, sphere=200):
    """Faces big enough to rest on, plus hull faces, plus a sphere sweep.

    The sphere matters: a tube lying along its own bottom generatrix is a face
    contact, but no mesh face points straight down there, so a face-only set
    misses that whole family of poses.
    """
    n = np.asarray(mesh.face_normals, float)
    a = np.asarray(mesh.area_faces, float)
    picked = []
    for i in np.argsort(-a):
        if a[i] < min_area or len(picked) >= limit:
            break
        d = n[i]
        if np.isfinite(d).all():
            picked.append(d / np.linalg.norm(d))
    hull = mesh.convex_hull
    hn, ha = np.asarray(hull.face_normals, float), np.asarray(hull.area_faces, float)
    picked += [hn[i] for i in np.argsort(-ha)[:40]]
    i = np.arange(sphere) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / sphere)
    theta = np.pi * (1.0 + 5.0 ** 0.5) * i
    picked += list(np.c_[np.cos(theta) * np.sin(phi),
                         np.sin(theta) * np.sin(phi), np.cos(phi)])
    return _dedupe(picked, ang_tol)


# ------------------------------------------------------------------ slicer
def _init(path, presets, work):
    global _M, _PRESETS, _WORK
    _M, _PRESETS, _WORK = load(path), presets, work


def make_presets(out_dir):
    """Flatten the three preset chains -- the CLI resolves almost no `inherits`."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import presets as core
    info = core.write_presets(out_dir, printer=core.D_PRINTER, process=core.D_PROCESS,
                              filament=FILAMENT, material="PETG",
                              bed="Textured PEI Plate", fan_layers="3", root=core.BBL)
    p = info["paths"]
    return p["machine"], p["process"], p["filament"]


def slice_one(src, wd, timeout=180):
    """Slice, then kill the process group -- the CLI is a GUI binary that never exits."""
    mp, pp, fp = _PRESETS
    os.makedirs(wd, exist_ok=True)
    log = open(f"{wd}/slice.log", "wb")
    p = subprocess.Popen(
        [CLI, "--load-settings", f"{mp};{pp}", "--load-filaments", fp,
         "--slice", "1", "--export-3mf", "baked.3mf", "--outputdir", wd,
         os.path.abspath(src)],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=wd)
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        if os.path.exists(f"{wd}/baked.3mf") or p.poll() is not None:
            break
    log.close()
    try:
        os.killpg(os.getpgid(p.pid), 9)
    except Exception:
        pass
    return os.path.exists(f"{wd}/baked.3mf")


def judge(wd, tries=24):
    """-> (warning_message or None, bbox of the first object).

    result.json is written AFTER baked.3mf appears, so catching it mid-write
    raises on an empty string.  Retry -- and keep None distinct from "", or a
    slice that never finished will look like a clean pass.
    """
    p = f"{wd}/result.json"
    for _ in range(tries):
        if os.path.exists(p):
            try:
                d = json.load(open(p))
            except (json.JSONDecodeError, OSError):
                time.sleep(0.25)
                continue
            pl = (d.get("sliced_plates") or [{}])[0]
            objs = pl.get("objects") or [{}]
            return pl.get("warning_message", "") or "", objs[0].get("bbox")
        time.sleep(0.25)
    return None, None


def _one(job):
    idx, d = job
    wd = os.path.join(_WORK, f"c{idx:04d}")
    shutil.rmtree(wd, ignore_errors=True)
    os.makedirs(wd, exist_ok=True)
    stl = f"{wd}/in.stl"
    m3, _ = sit(_M, d)
    m3.export(stl)
    sliced = slice_one(stl, wd)
    warn, bbox = judge(wd)
    try:
        os.remove(stl)
    except OSError:
        pass
    return dict(idx=idx, dir=[float(x) for x in d], sliced=sliced,
                judged=warn is not None, warn=warn or "", bbox=bbox)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("part")
    ap.add_argument("--n", type=int, default=240, help="sphere samples (default 240)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--out", default=None)
    ap.add_argument("--offline", action="store_true",
                    help="rank with the offline proxy only, do not slice")
    ap.add_argument("--keep", action="store_true", help="keep scratch dirs")
    a = ap.parse_args()

    out = a.out or os.path.splitext(os.path.basename(a.part))[0] + "_poses.json"
    m = load(a.part)
    dirs = candidate_dirs(m, sphere=a.n)
    print(f"# {os.path.basename(a.part)}: {len(dirs)} candidate orientations")

    if a.offline:
        rows = []
        for d in dirs:
            m3, _ = sit(m, d)
            r = measure(m3)
            if r:
                r["dir"] = [float(x) for x in d]
                rows.append(r)
        rows.sort(key=lambda r: (r["first"] < MIN_FIRST, r["float_vol"], -r["first"]))
        _report(rows)
        return

    work = tempfile.mkdtemp(prefix="pose_brute_")
    global _WORK
    _WORK = work
    presets = make_presets(os.path.join(work, "presets"))
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=a.jobs, initializer=_init,
                             initargs=(a.part, presets, work)) as ex:
        rows = list(ex.map(_one, list(enumerate(dirs)), chunksize=1))
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)

    good = [r for r in rows if r["sliced"] and r["judged"] and not r["warn"]]
    bad = [r for r in rows if r["sliced"] and r["judged"] and r["warn"]]
    unk = [r for r in rows if not (r["sliced"] and r["judged"])]
    print(f"# accepted {len(good)}  flagged {len(bad)}  undecided {len(unk)}  "
          f"({time.time() - t0:.0f}s)")

    # rank survivors on the honest ruler -- the slicer only says yes/no, it does
    # not say which yes is best
    for r in good:
        m3, _ = sit(m, np.array(r["dir"]))
        q = measure(m3)
        r.update(q or {})
    good.sort(key=lambda r: -r.get("first", 0.0))
    with open(out, "w") as f:
        json.dump(rows, f)
    print(f"# -> {out}")
    _report(good, sliced=True)
    if not good:
        print("\n# NO accepted pose.  Do not keep hunting: give the part per-object\n"
              "# tree support instead (enable_support=1 / support_type=tree(auto)\n"
              "# in that object's block of Metadata/model_settings.config).")


def _report(rows, sliced=False):
    print(f"  {'dir (bed-facing)':34s} {'first mm2':>10s} {'float mm3':>10s} "
          f"{'longest':>8s} {'h mm':>6s}")
    for r in rows[:16]:
        dv = "[" + " ".join(f"{x:+.3f}" for x in r["dir"]) + "]"
        print(f"  {dv:34s} {r.get('first', 0):10.3f} {r.get('float_vol', 0):10.2f} "
              f"{r.get('longest_run', 0):8d} {r.get('height', 0):6.1f}")
    if not sliced:
        print("\n# PROXY NUMBERS, NOT A VERDICT.  These two columns rank candidates;\n"
              "# they do not predict the slicer.  A candidate with a better `first`\n"
              "# and a smaller `float` than every passing part in the kit was still\n"
              "# flagged when cut.  Drop --offline and let the slicer decide.")
    elif rows:
        print("\n# Feed the winner to splitter.orient(mesh, \"face\", dir) -- dir is an\n"
              "# increment ON TOP of lay_flat(), because the candidates were cut from\n"
              "# the print-pose STL, which already lives in lay_flat's frame.")


if __name__ == "__main__":
    main()
