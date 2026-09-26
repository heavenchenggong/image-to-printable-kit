#!/usr/bin/env python3
"""Warp-risk report for a print-oriented part.  Run it BEFORE slicing.

Why this file exists
--------------------
The first Winston fit coupon failed on the bed, and the root cause was not the
slicer profile.  It was the geometry: a 102 x 28 x 2.5 mm slab with a 102 mm
unbroken bottom edge.  FDM warps that shape almost by definition -- contraction
stress accumulates along the long run and a 2.5 mm section has nothing like the
bending stiffness to resist it.  The edge lifts, the nozzle then drags through
the lifted lip on every pass, and what you get is a row of smeared, torn
plastic along the edge plus a part that no longer sits flat.

A slicer cannot warn you about this.  The model can.

The three numbers, all measured on the bed-facing face
------------------------------------------------------
  contact area   grip on the plate, and heat fed back into the base.
  span           longest straight run of the bottom face in X or Y, mm.  This
                 is the lever arm: contraction stress accumulates along it.
  depth          mean depth of material standing on the contact patch,
                 = part volume / contact area, mm.  This is what resists the
                 bend, and it is deliberately NOT taken from vertex z-extents
                 or from the bounding box:

                   Ø20 x 2.5 disc        depth 3.8   (a wide thin plate)
                   Ø16 x 18.5 pillar     depth 21    (a solid column)
                   100 x 100 x 3 plate   depth 3     (a wide thin plate)
                   100 x 100 x 50 block  depth 50    (a solid block)

                 A vertex-based number is fooled by the thin cases, because a
                 trimesh cylinder carries vertices only on its end caps; a
                 volume/area number is not.

lift = span**2 / depth decides everything
-----------------------------------------
Residual lift goes as the shrinkage strain times the square of the free run,
over the thickness, h ~ eps * span**2 / depth.  So the discriminating quantity
is span**2/depth -- NOT span/depth.  Both powers of span matter: a 35 mm disc
is not a small version of a 102 mm plate, it is one eleventh of it
((35/102)**2).  A ratio-only rule calls those two the same kind of part, which
is how this file's first version told me to redesign a 35 mm pupil disc that in
practice prints flat with a brim.  Do not "fix" geometry because of a false
positive.

Bands (PLA / PETG, textured PEI, X-class Bambu machine, no enclosure, room
20-25 C, part not hanging over the plate edge, 0.2 mm layers):

  span <= 15 mm          fine -- nothing for contraction to pull against
  lift <= 400            fine.  Print it.
  depth <= 4.5 mm        brim 5-8 mm is forced, whatever the lift: a section
                         this thin is held down by its brim, not by its own
                         bending stiffness.
  400 < lift <= 1200     brim 5-8 mm, fan off the first 3 layers, part centred
                         on the plate, first layer 20-25 mm/s
  lift > 1200            redesign, do not tune.  Split the contact face, chamfer
                         the corners, thicken the section, or drill holes to
                         break the long run into islands.  Anchoring tricks
                         only postpone the failure.

Calibration -- measured on this project's own parts, not invented:

                           contact   span   depth   lift   band
  v1 coupon raft           2856    102.0    6.0   1734   redesign
  100 x 100 x 3 plate         -    100.0    3.0   3333   redesign
  Ø35 x 2.8 pupil disc        -     35.0    2.8    437   brim
  Ø47 x 8 eye plate           -     47.0    8.0    276   ok
  100 x 100 x 50 block        -    100.0   50.0    200   ok
  v1 Ø20 x 3.8 pin disc     314     20.0    3.8    105   brim (thin floor)
  Ø16 x 21 coupon boss      172     14.8   21.0     10   ok
  v2 coupon pin             172     14.8    7.6      6   ok

  The v1 raft is the part that actually failed on the bed: 102 mm of unbroken
  bottom edge, edge lifted, nozzle dragged through the lip and tore a row of
  plastic along it.  The two Ø20 discs beside it lifted at the rim -- brim, not
  redesign.  One tool, two verdicts, and both matched the photo of the wreck.

CLI
---
    python warp.py part.3mf [part2.stl ...]
    python warp.py --json part.3mf

Exits non-zero if any input lands in the "redesign" band, so it can be a gate
in a build script.
"""
import json
import os
import sys

import numpy as np

__all__ = ["ground_pts", "report", "verdict", "MIN_SPAN", "THIN_DEPTH",
           "LIFT_OK", "LIFT_BRIM"]

MIN_SPAN = 15.0          # mm -- below this, contraction has nothing to pull on
THIN_DEPTH = 4.5         # mm -- thinner than this, hold it with a brim
LIFT_OK = 400.0          # span**2/depth, mm
LIFT_BRIM = 1200.0       # span**2/depth, mm


def ground_pts(m, tol=0.4):
    """Vertices sitting on the bed-facing face (the lowest z, within `tol`)."""
    zmin = float(m.bounds[0][2])
    pts = np.asarray(m.vertices, float)
    return pts[pts[:, 2] <= zmin + tol][:, :2]


def verdict(span, depth):
    """(band, lift, ratio, note) for a contact span and the mean depth."""
    if span <= MIN_SPAN:
        return "ok", 0.0, 0.0, f"span {span:.1f} mm is under the {MIN_SPAN:.0f} mm floor"
    if depth <= 0.0:
        return "ok", 0.0, 0.0, ""
    lift = span * span / depth
    ratio = span / depth
    band = ("redesign" if lift > LIFT_BRIM else
            "brim" if lift > LIFT_OK else "ok")
    note = ""
    if band == "ok" and depth <= THIN_DEPTH:
        # A plate this thin prints flat or it does not, and it is the brim that
        # decides -- its own stiffness is far too low to matter either way.
        band = "brim"
        note = (f"lift {lift:.0f} is low but depth {depth:.1f} mm < "
                f"{THIN_DEPTH:.1f} mm -- thin plate, brim it anyway")
    elif band == "brim":
        note = (f"lift {lift:.0f} (span {span:.0f} mm over depth {depth:.1f} mm)"
                f" -- brim 5-8 mm, fan off the first 3 layers")
    elif band == "redesign":
        note = (f"lift {lift:.0f} -- split, chamfer or thicken the contact "
                f"face; do not tune the profile")
    return band, lift, ratio, note


def _margin(m, tol=0.6):
    """Centre-of-mass clearance to the footing edge, via splitter if present."""
    try:
        import splitter as SP
        return SP.footing_margin(m, tol)
    except Exception:
        return float("nan")


def report(m, tol=0.4, name=""):
    """Dict of the warp-relevant numbers for one print-oriented mesh.

    Also reports the FOOTING, because "will this part cause a failed print" is
    one question and it has two answers: will it warp, and will it stand up.
    The two parts of this kit that failed the footing test were balanced on a
    1 mm2 tangent line with the centre of mass 2 mm outside it -- the slicer
    showed nothing wrong, and on the plate they would have rocked over and
    printed loose.  A warp number alone would have passed them.
    """
    pts = ground_pts(m, tol)
    if len(pts) < 3:
        return dict(name=name, contact_area=0.0, span_x=0.0, span_y=0.0,
                    span=0.0, depth=0.0, lift=0.0, ratio=0.0, margin=float("nan"),
                    band="no-contact", note="")
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    span_x, span_y = float(hi[0] - lo[0]), float(hi[1] - lo[1])
    span = max(span_x, span_y)

    # Convex hull of the contact patch -> real area, not the bounding box.
    try:
        from scipy.spatial import ConvexHull
        area = float(ConvexHull(pts).volume)          # 2-D hull "volume" = area
    except Exception:
        area = float(np.prod(hi - lo))

    if area < 1.0:
        # Degenerate patch: the bottom face is a line or a couple of points
        # (a knife edge, a ball resting on the bed, a sliver).  Dividing by it
        # produced depth = 8000 mm before this guard existed.  There is nothing
        # to warp -- but the footing test below still applies.
        depth, band, lift, ratio = 0.0, "ok", 0.0, 0.0
        note = "bottom face is a line or a point: no plate to warp"
    else:
        depth = abs(float(m.volume)) / area if area > 0 else 0.0
        band, lift, ratio, note = verdict(span, depth)

    margin = _margin(m)
    if margin == margin and margin < 0.0:
        band = "redesign"
        note = (f"centre of mass is {abs(margin):.1f} mm OUTSIDE the footing: "
                f"it rocks over and settles elsewhere, taking its brim with it. "
                f"Reorient -- splitter.rest_flat -- do not slice this as is")
    elif margin == margin and margin < 1.0:
        band = "redesign" if band == "redesign" else "brim"
        note = (f"only {margin:.1f} mm from the centre of mass to the footing "
                f"edge -- it will wobble; brim it")
    elif area < 5.0 and band == "ok":
        band = "brim"
        note = (f"footing is a line or a point ({area:.1f} mm2) -- brim it and "
                f"watch for it being knocked loose")

    return dict(name=name, contact_area=area, span_x=span_x, span_y=span_y,
                span=span, depth=depth, lift=lift, ratio=ratio, margin=margin,
                band=band, note=note)


def _names(path):
    """Object names out of a 3MF, in the SAME ORDER _load() yields meshes.

    Without this the report says "winston_kit_P2_dark.3mf #3" and you cannot
    tell which part is the risky one -- which is the only thing you wanted to
    know.  Bambu stores the names in Metadata/model_settings.config, but the
    order there is NOT the order of the objects in 3D/3dmodel.model, so the
    names have to be re-sorted along the model's own <object id=...> sequence.
    Pairing the two lists positionally without this step quietly swaps names
    between parts (measured: ear-fin and left-hand reports traded places).
    """
    if not str(path).lower().endswith(".3mf"):
        return []
    import re
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            msc = z.read("Metadata/model_settings.config").decode("utf-8")
            id2name = dict(re.findall(
                r'<object id="(\d+)">\s*\n\s*<metadata key="name" value="([^"]*)"', msc))
            try:
                model = z.read("3D/3dmodel.model").decode("utf-8")
                order = re.findall(r'<object id="(\d+)"', model)
            except Exception:
                order = list(id2name)
    except Exception:
        return []
    if not id2name:
        return []
    return [id2name.get(o, f"object {o}") for o in order]


def _load(path):
    import trimesh
    ext = os.path.splitext(path)[1].lower()
    if ext == ".3mf":
        import zipfile
        import re
        import io
        import xml.etree.ElementTree as ET
        NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
        PROD = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
        out = []
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("3D/3dmodel.model"))
            # <build><item transform="..."> carries the placement.  In the
            # project layout the part files hold local coordinates, so without
            # this the "which face is on the plate" answer is meaningless.
            xf = {}
            for it in root.iter(f"{NS}item"):
                oid, tr = it.get("objectid"), it.get("transform")
                if oid and tr:
                    xf[oid] = np.array([float(x) for x in tr.split()]).reshape(4, 3)

            def mesh_of(el):
                vs = [[float(c.get("x")), float(c.get("y")), float(c.get("z"))]
                      for c in el.find(f"{NS}vertices")]
                fs = [[int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))]
                      for t in el.find(f"{NS}triangles")]
                return vs, fs

            for obj in root.iter(f"{NS}object"):
                oid = obj.get("id")
                mesh_el = obj.find(f"{NS}mesh")
                if mesh_el is not None:
                    vs, fs = mesh_of(mesh_el)
                else:
                    # Bambu *project* 3MF: the main model carries only
                    # <component p:path="/3D/Objects/object_N.model"/> stubs and
                    # the meshes live in those part files.  Resolve them --
                    # skipping these makes the gate print "all clear" on a file
                    # it never actually read (a silent false negative).
                    vs, fs = [], []
                    for comp in obj.iter(f"{NS}component"):
                        p = comp.get(f"{PROD}path") or comp.get("path") or ""
                        if not p:
                            continue
                        try:
                            sub = ET.fromstring(z.read(p.lstrip("/")))
                        except KeyError:
                            continue
                        for so in sub.iter(f"{NS}object"):
                            sm = so.find(f"{NS}mesh")
                            if sm is None:
                                continue
                            sv, sf = mesh_of(sm)
                            off = len(vs)
                            vs += sv
                            fs += [[i + off for i in t] for t in sf]
                if len(vs) == 0 or len(fs) == 0:
                    continue
                v = np.array(vs, dtype=float)
                if oid in xf:
                    M = xf[oid]
                    v = v @ M[:3, :] + M[3, :]
                out.append(trimesh.Trimesh(vertices=v, faces=np.array(fs),
                                           process=False))
        return out
    return [trimesh.load(path, process=False)]


def _main(argv):
    as_json = "--json" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    reports, worst = [], 0
    for p in paths:
        names = _names(p)
        for i, m in enumerate(_load(p)):
            if i < len(names):
                nm = names[i]
            elif i == 0:
                nm = os.path.basename(p)
            else:
                nm = f"{os.path.basename(p)} #{i + 1}"
            r = report(m, name=nm)
            reports.append(r)
            worst = max(worst, {"ok": 0, "brim": 1, "redesign": 2}.get(
                r["band"], 0))
    if as_json:
        print(json.dumps(reports, indent=2))
        return worst
    print(f"\n  {'part':38s} {'contact':>8s} {'span x':>7s} {'span y':>7s} "
          f"{'depth':>6s} {'lift':>6s} {'COM':>6s}  band")
    for r in reports:
        print(f"  {r['name'][:38]:38s} {r['contact_area']:7.0f} "
              f"{r['span_x']:7.1f} {r['span_y']:7.1f} {r['depth']:6.1f} "
              f"{r['lift']:6.0f} {r['margin']:6.1f}  {r['band']}")
        if r["note"]:
            print(f"    -> {r['note']}")
    print(f"\n  mm.  lift = span**2 / depth.  ok (<= {LIFT_OK:.0f}) | "
          f"brim (<= {LIFT_BRIM:.0f}) | redesign (> {LIFT_BRIM:.0f});  "
          f"depth <= {THIN_DEPTH:.1f} mm forces brim")
    print("  COM = centre-of-mass clearance to the footing edge; negative means "
          "the part cannot stand")
    return worst


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
