#!/usr/bin/env python3
"""End-to-end smoke test of scripts/joints.py — every mechanism, both halves.

Run this after touching joints.py.  It checks the things the strain maths cannot:
  * every returned solid is watertight
  * the male half is enough pieces that the leaves can actually move
    (a collet pin must NOT be a single closed blob around the bore)
  * male and female halves align: seated, the male is clear of the female's
    material, and only the female's material was removed
"""
import os
import sys
import pathlib
import tempfile

import numpy as np
import trimesh

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import joints as J  # noqa: E402
import ptools as P  # noqa: E402

SEAT = np.array([0.0, 0.0, 20.0])
D = np.array([0.0, 0.0, 1.0])
fails = []


def comps(m, tol=1e-3):
    key, parent = {}, {}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for f in m.faces:
        ids = []
        for v in m.vertices[f]:
            k = (round(float(v[0]) / tol), round(float(v[1]) / tol),
                 round(float(v[2]) / tol))
            i = key.setdefault(k, len(key))
            parent.setdefault(i, i)
            ids.append(i)
        for a, b in ((0, 1), (1, 2), (0, 2)):
            ra, rb = find(ids[a]), find(ids[b])
            if ra != rb:
                parent[ra] = rb
    return len({find(k) for k in parent})


def vol(a, b):
    try:
        return abs(trimesh.boolean.intersection([a, b]).volume)
    except Exception:
        return float("nan")


print("== strain / clearance ==")
ok_s, _ = J.check(J.JOINT_S, label="S  ")
ok_x, _ = J.check(J.JOINT_XS, label="XS ")
ok_x2, _ = J.check(J.JOINT_XXS, label="XXS")
for nm, ok in (("S", ok_s), ("XS", ok_x), ("XXS", ok_x2)):
    if not ok:
        fails.append(f"{nm} joint fails its own strain/shoulder check")

print("\n== ball joint: assemble / seat / collet ==")
for nm, p in (("S", J.JOINT_S), ("XS", J.JOINT_XS), ("XXS", J.JOINT_XXS)):
    pin = J.ball_pin(SEAT, D, **p)
    block = P.difference([P.cyl(30, 40, [0, 0, 40.0]),
                          J.ball_socket(SEAT, D, **p)])
    seated = vol(pin, block)
    # the leaves: slice through the ball's equator and count the pieces.  A
    # working collet gives n_slit separate leaves; a sealed one gives 1 ring.
    slab = P.cyl(p["r_ball"] + 2.0, 0.3, SEAT + D * p["standoff"])
    leaves = comps(trimesh.boolean.intersection([pin, slab]))
    plain = trimesh.boolean.union([
        P.cyl(p["r_neck"], p["standoff"], SEAT + D * (p["standoff"] / 2), D),
        P.sphere(p["r_ball"], SEAT + D * p["standoff"], subdivisions=4)])
    removed = 100 * (1 - abs(pin.volume) / abs(plain.volume))
    print(f"  {nm:4s} watertight={pin.is_watertight} seated clash={seated:.2f} mm3 "
          f"leaves@equator={leaves} (want 4)  relief material removed {removed:.0f}%")
    if not (pin.is_watertight and block.is_watertight):
        fails.append(f"{nm}: not watertight")
    if leaves != 4:
        fails.append(f"{nm}: equator slice has {leaves} pieces, not 4 "
                     f"-> the slits or the relief bore are not where they should be")
    if removed < 8:
        fails.append(f"{nm}: collet removed only {removed:.0f}% of a plain pin "
                     f"-> relief bore missing")
    if not (seated == seated and seated < 1.0):
        fails.append(f"{nm}: pin collides with the socket when seated")

print("\n== press fit ==\n")
print("   (a press fit MUST interfere — that volume is the fit, not a clash)")
for nm, r, lg in (("Ø6.0", 3.0, 7.0), ("Ø4.0", 2.0, 4.5), ("Ø4.6", 2.3, 5.0)):
    peg = J.press_peg(SEAT, D, r, lg)
    blk = P.difference([P.cyl(20, 30, [0, 0, 35.0]),
                        J.press_hole(SEAT, D, r, lg - 0.5,
                                     clearance=-0.10)])
    h = peg.bounds[1][2] - peg.bounds[0][2]
    base = SEAT[2] - peg.bounds[0][2]
    clash = vol(peg, blk)
    want = np.pi * (r ** 2 - (r - 0.10) ** 2) * (lg - 0.5)
    print(f"  {nm} watertight={peg.is_watertight} height={h:.1f} mm "
          f"(want {lg + 0.6 + 1.5:.1f}) base-below-seat={base:.2f} mm  "
          f"interference={clash:.1f} mm3 (want ~{want:.1f})")
    if not peg.is_watertight:
        fails.append(f"press peg {nm} not watertight")
    if abs(base - 1.5) > 0.05:
        fails.append(f"press peg {nm}: shank starts {base:.2f} mm below the "
                     f"seat, want 1.50")
    if comps(peg) != 1:
        fails.append(f"press peg {nm}: chamfer is detached from the shank")
    if not (0.6 * want < clash < 1.4 * want):
        fails.append(f"press peg {nm}: interference {clash:.1f} mm3, expected "
                     f"~{want:.1f}")

print("\n== annular snap / taper (must still build) ==")
sk = J.snap_skirt([0, 0, 20], [0, 0, 1], r_in=6.0)
bg = J.boss_groove([0, 0, 20], [0, 0, 1], r_boss=6.0)
tp = J.taper_pin([0, 0, 20], [0, 0, 1], r_big=4.0, length=10.0)
th = J.taper_hole([0, 0, 20], [0, 0, 1], r_big=4.0, length=10.0)
for nm, m in (("snap_skirt", sk), ("boss_groove", bg), ("taper_pin", tp),
              ("taper_hole", th)):
    print(f"  {nm:12s} watertight={m.is_watertight} vol={abs(m.volume):.1f} mm3")
    if not m.is_watertight:
        fails.append(f"{nm} not watertight")

print("\n" + ("FAILURES:\n  " + "\n  ".join(fails) if fails else "ALL PASS"))
sys.exit(1 if fails else 0)
