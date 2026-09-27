"""Axial fit check for elastic-bead snap joints (bead_peg / bead_hole).

The two questions that decide whether a press joint assembles at all are radial
AND axial, and only one of them is visible in CAD:

  radial -- does the BODY slide and does the BEAD grip?
  axial  -- does the peg bottom out before the faces meet?

A peg that bottoms out can never close, however loose it is.  That is exactly
how the first Winston kit failed: pin 5.60 long in a 5.30 bore, pin 5.10 in a
5.00 bore, plus a NEGATIVE clearance.  The user had to cut the pegs off.

Usage:

    from scripts import joints as J, mate_profile as MP
    MP.profile({"label": "wrist", "r_body": 2.275, "r_bead": 2.50,
                "bore": 2.40, "length": 4.5, "cone": 1.5,
                "slit_w": 0.7, "slit_depth": 4.0, "relief_r": 0.9, "bury": 2.5})

or from the shell, editing CASES at the bottom of this file.

Every number printed is measured off the built solids, not echoed from the
arguments, so it also catches boolean errors (a mis-placed slit, a bead that was
swallowed by the union, a bore that stopped short).
"""
import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

try:
    from scripts import joints as J
except ImportError:                     # imported with scripts/ itself on sys.path
    import joints as J

BLOCK = 16.0        # side of the throwaway block that carries the joint


def _rings(mesh, z):
    """Closed polygons of the horizontal cross-section at height z."""
    seg, _, _ = trimesh.intersections.mesh_multiplane(
        mesh, [0, 0, z], [0, 0, 1], [0.0])
    arr = np.asarray(seg[0])
    if arr.size == 0:
        return []
    lines = [LineString(np.round([p[0], p[1]], 4)) for p in arr
             if np.linalg.norm(p[0] - p[1]) > 1e-9]
    return [q for q in polygonize(unary_union(lines)) if q.is_valid and q.area > 0.15]


def _outer_r(mesh, z):
    """Largest radius reached by any material at height z."""
    out = []
    for q in _rings(mesh, z):
        c = np.asarray(q.exterior.coords)
        out.append(np.hypot(c[:, 0], c[:, 1]).max())
    return max(out, default=0.0)


def _bore_d(mesh, z):
    """Equivalent diameter of the largest internal cavity at height z."""
    out = []
    for q in _rings(mesh, z):
        for h in q.interiors:
            a = Polygon(h).area
            if a > 3.0:
                out.append(2.0 * np.sqrt(a / np.pi))
    return max(out, default=0.0)


def bore_bottom(mesh, z_start, step=0.25):
    """Highest z that still cuts an internal cavity — the bore's bottom.

    Measured, not computed from the arguments, so it also catches a bore that
    came out shorter than intended (a mis-placed negative, a swallowed cone).
    Coarse scan first, then a fine pass around the last hit: a 0.25 mm step
    alone reads a 0.45 mm gap as 0.28, which fails a 0.3 mm gate on a good
    joint (the ear-fin joint did exactly that).
    """
    z, last, top = z_start, None, mesh.bounds[1][2]
    while z <= top:
        if _bore_d(mesh, z) > 0.0:
            last = z
        z += step
    if last is None:
        return z_start
    z = max(last - step, z_start)
    while z <= min(last + step, top):
        if _bore_d(mesh, z) > 0.0:
            last = z
        z += 0.02
    return last


def build(spec):
    """Return (male, female) solids for one joint spec, axis along +Z."""
    seat, d = (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)
    rb, rbe, bore = spec["r_body"], spec["r_bead"], spec["bore"]
    L, cone = spec["length"], spec["cone"]
    add, cut = J.bead_peg(seat, d, rb, L, r_bead=rbe, cone=cone,
                          slit_w=spec["slit_w"], slit_depth=spec["slit_depth"],
                          relief_r=spec["relief_r"], bury=spec.get("bury", 2.0))
    carrier = trimesh.creation.box((BLOCK, BLOCK, 4.0))
    carrier.apply_translation([0, 0, -4.0])
    male = trimesh.boolean.difference(
        [trimesh.boolean.union([carrier] + add)] + cut)

    neg = J.bead_hole(seat, d, rb, L, clearance=bore - rb, cone=cone)
    H = L + cone + 10.0                       # female material sits along +d
    block = trimesh.creation.box((BLOCK, BLOCK, H))
    block.apply_translation([0, 0, H / 2.0 - 1.0])
    female = trimesh.boolean.difference([block] + list(neg))
    return male, female


def profile(spec, verbose=True):
    """Measure one joint.  Returns a dict of the decisive numbers."""
    rb, rbe, bore = spec["r_body"], spec["r_bead"], spec["bore"]
    L, cone = spec["length"], spec["cone"]
    label = spec.get("label", "joint")
    male, female = build(spec)
    ok_strain, eps = J.bead_strain(rb, rbe, bore, spec["relief_r"],
                                   spec["slit_depth"])

    # Body clearance is measured over the SHANK, i.e. below the bead's leading
    # flank.  Taking the minimum over the whole peg instead would pick up the
    # bead's own ramp and read ~0.02 instead of the real 0.12.
    shank_top = 0.6 * L
    clear, squeeze, z_tip = [], [], 0.0
    for z in np.arange(0.6, male.bounds[1][2], 0.25):
        r_out = _outer_r(male, z)
        if r_out <= 0:
            continue
        z_tip = z
        bd = _bore_d(female, z)
        if bd <= 0:
            continue
        gap = bd / 2.0 - r_out
        (squeeze if gap < 0 else (clear if z <= shank_top else [])).append((z, gap))

    min_clear = min((g for _, g in clear), default=float("nan"))
    max_grip = max((-g for _, g in squeeze), default=0.0)
    z_grip = max(squeeze, key=lambda t: -t[1])[0] if squeeze else None

    res = dict(
        label=label, body=2 * rb, bead=2 * rbe, bore=2 * bore,
        peg_tip=z_tip, bore_bottom=bore_bottom(female, 0.3),
        axial_gap=bore_bottom(female, 0.3) - z_tip,
        min_clear=min_clear, grip=max_grip, z_grip=z_grip,
        strain=eps, strain_ok=ok_strain,
        watertight=bool(male.is_watertight and female.is_watertight),
    )
    # Acceptance: the body must slide, the bead must grip, the peg must never
    # reach the bottom of the bore, and the leaves must stay under 2 %.
    res["ok"] = (min_clear > 0.02) and (max_grip >= 0.05) and \
                (res["axial_gap"] > 0.3) and ok_strain and res["watertight"]

    if verbose:
        print(f"\n=== {label} ===")
        print(f"  body Ø{res['body']:.2f}  bead Ø{res['bead']:.2f}  bore Ø{res['bore']:.2f}"
              f"   length {L} + {cone} cone")
        print(f"  body clearance {res['min_clear']:+.3f} /side   bead grip {res['grip']:.3f} /side"
              f" @ z={res['z_grip'] if res['z_grip'] is None else round(res['z_grip'],1)}")
        print(f"  peg tip z={res['peg_tip']:.2f}  bore bottom z={res['bore_bottom']:.2f}"
              f"   axial margin {res['axial_gap']:.2f} mm")
        print(f"  leaf strain {100*eps:.2f} %  {'OK' if ok_strain else '>2% takes a set and fails'}"
              f"   watertight {res['watertight']}")
        print(f"  -> {'PASS' if res['ok'] else 'FAIL'}")
    return res


CASES = [
    dict(label="wrist hand<->arm", r_body=2.275, r_bead=2.50, bore=2.40,
         length=4.5, cone=1.5, slit_w=0.7, slit_depth=4.0, relief_r=0.9, bury=2.5),
    dict(label="pupil pupil<->eye-plate", r_body=1.975, r_bead=2.175, bore=2.075,
         length=4.0, cone=1.2, slit_w=0.6, slit_depth=3.4, relief_r=0.75, bury=2.0),
    dict(label="ear-fin ear<->body", r_body=2.95, r_bead=3.225, bore=3.10,
         length=6.0, cone=1.8, slit_w=0.8, slit_depth=5.0, relief_r=0.8, bury=2.5),
]

if __name__ == "__main__":
    bad = 0
    for s in CASES:
        r = profile(s)
        bad += 0 if r["ok"] else 1
    print(f"\n{len(CASES) - bad}/{len(CASES)} PASS")
    raise SystemExit(1 if bad else 0)
