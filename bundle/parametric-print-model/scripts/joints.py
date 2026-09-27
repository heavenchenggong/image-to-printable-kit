"""Glue-free, re-openable joints for FDM print kits.

Three mechanisms, in order of preference:

1. ``ball_pin`` / ``ball_socket`` — a ball pressed past an undersized mouth into
   a spherical cavity.  The lip is split by N relief slots so it can flex open,
   then springs back.  One joint gives BOTH retention and angular adjustment,
   and it re-opens indefinitely (the ball never deforms).  Use this by default.
2. ``snap_skirt`` / ``boss_groove`` — an annular snap: a thin skirt with an
   inward bead drops over a boss and the bead clicks into a groove.  No clocking
   needed (it is rotationally symmetric).  Use it for thin parts that cannot
   host a socket.
3. ``taper_pin`` / ``taper_hole`` — Morse-style friction fit.  Cheap to model,
   but it wears, so it is the fallback for parts too small for a ball.

Why NO threads and NO bayonet:
  a blind bore whose mouth faces DOWN on the bed needs a bridge across the full
  bore diameter.  Ø40+ does not survive; Ø11 does (sags ~0.4 mm, and a sagged
  socket roof only costs insertion depth).  So every female feature on this kit
  is either Ø11 or smaller, or is a vertical bore that prints open-end-up.

Printing notes that drove the constants:
  * SLOT_W 0.9 mm — a 0.4 nozzle leaves ~0.75 mm.  Do not go below 0.8.
  * the lip should be >= 5 mm long along the axis (MOUTH->cavity centre) or it
    is too stiff to open.
  * male pins want to print with their axis along +Z: that puts the press-in
    load across layer lines instead of along them.

mm, Z up.  Every function returns watertight solids; female features are
returned as NEGATIVE solids to be fed to ``difference``.
"""
import numpy as np
import trimesh
from trimesh.creation import box, cylinder, uv_sphere
from trimesh.geometry import align_vectors
from trimesh.transformations import rotation_matrix

__all__ = ["ball_pin", "ball_socket", "snap_skirt", "boss_groove",
           "taper_pin", "taper_hole", "press_peg", "press_hole",
           "bead_peg", "bead_hole", "bead_strain",
           "annulus", "check", "JOINT_S", "JOINT_XS", "JOINT_XXS"]

# --- stock sizes -------------------------------------------------------------
# The interference that matters is (r_ball - r_mouth), because the moment of
# worst squeeze is when the ball's EQUATOR crosses the mouth plane.  Feed that
# into `check()` before you print anything: a rigid ball through a rigid mouth
# needs <1 % of the ball diameter, which is almost nothing — so the pin is a
# COLLET (see ball_pin) and the compliance lives on the male side.
#
# S:   Ø10.6 collet through a Ø10.0 mouth (0.6 mm diametral, 5.7 % of Ø).
# XS:  Ø7.4 collet through a Ø6.8 mouth — for parts narrower than ~Ø14.
# XXS: Ø5.2 collet through a Ø4.8 mouth — the floor of what an 0.4 nozzle can do.
JOINT_S = dict(r_ball=5.3, r_socket=5.5, r_mouth=5.0, r_neck=3.9,
               standoff=9.0, slit_w=0.7, bore_r=1.7)
JOINT_XS = dict(r_ball=3.7, r_socket=3.9, r_mouth=3.4, r_neck=2.7,
                standoff=8.0, slit_w=0.65, bore_r=1.2)
JOINT_XXS = dict(r_ball=2.6, r_socket=2.8, r_mouth=2.4, r_neck=1.8,
                 standoff=6.0, slit_w=0.6, bore_r=0.9)


def _u(d):
    d = np.asarray(d, float).ravel()
    return d / np.linalg.norm(d)


def _at(mesh, seat, d, z0=0.0):
    """Move a +Z-built primitive so its z=z0 lands on `seat` and +Z follows d."""
    m = mesh.copy()
    m.apply_translation([0, 0, -z0])
    m.apply_transform(align_vectors([0.0, 0.0, 1.0], _u(d)))
    m.apply_translation(np.asarray(seat, float))
    return m


def _cyl_span(r, seat, d, a, b, sections=64):
    """Cylinder spanning `a` (behind the seat, usually negative) to `b` along d.

    Use this instead of hand-picking a z0.  `_at` maps primitive z=z0 onto the
    seat, so a centred cylinder passed as `_at(cyl(h), seat, d, z0=-bury)` does
    NOT start at the seat — it starts at (bury-height)/2, i.e. half of `bury`
    ends up on the wrong side.  That silently shifted a relief bore 9 mm behind
    the ball (where it cut nothing) and detached a press peg's chamfer.
    """
    h = float(b) - float(a)
    if h <= 0:
        raise ValueError("empty span")
    return _at(cylinder(radius=r, height=h, sections=sections), seat, d,
               z0=-(float(a) + float(b)) / 2.0)


def annulus(r_out, r_in, h, sections=96):
    """Flat ring (a tube of length h).  r_in=0 -> solid cylinder."""
    if r_in <= 1e-6:
        return cylinder(radius=r_out, height=h, sections=sections)
    return trimesh.boolean.difference([
        cylinder(radius=r_out, height=h, sections=sections),
        cylinder(radius=r_in, height=h + 2.0, sections=sections)])


def _ring_span(r_out, r_in, seat, d, a, b):
    """Annulus spanning `a` to `b` along d (same convention as _cyl_span)."""
    h = float(b) - float(a)
    if h <= 0:
        raise ValueError("empty span")
    return _at(annulus(r_out, r_in, h), seat, d, z0=-(float(a) + float(b)) / 2.0)


# ------------------------------------------------------------------ ball joint
def ball_pin(seat, d, standoff=None, r_ball=None, r_neck=None,
             n_slit=4, slit_w=None, bore_r=None, bury=2.5, **kw):
    """Male half: a slotted COLLET ball on a neck, centred `standoff` along d.

    `seat` is on the mating surface, `d` points INTO the receiving part.

    The slits are the whole point.  A solid ball cannot pass an undersized
    mouth without the socket yielding, and yielding is permanent — the joint
    works once and then rattles.  Cutting the ball + neck into N leaves turns
    the pin into a cantilever set, so the squeeze is elastic and the joint
    re-opens forever.  The relief bore down the axis is what lets the leaves
    pivot inward at all (without it, four leaves meeting at the pole can only
    compress, not bend).
    """
    p = dict(JOINT_S, **{k: v for k, v in kw.items() if k in JOINT_S})
    r_ball = p["r_ball"] if r_ball is None else r_ball
    r_neck = p["r_neck"] if r_neck is None else r_neck
    standoff = p["standoff"] if standoff is None else standoff
    slit_w = p["slit_w"] if slit_w is None else slit_w
    bore_r = p["bore_r"] if bore_r is None else bore_r
    d = _u(d)
    seat = np.asarray(seat, float)

    neck = _cyl_span(r_neck, seat, d, -bury, standoff)
    ball = uv_sphere(radius=r_ball, subdivisions=4)
    ball.apply_translation(seat + d * standoff)
    out = trimesh.boolean.union([neck, ball])

    # axial relief bore: from just outside the outer pole back past the equator.
    # Must be ON the ball, not behind the seat — without it the four leaves meet
    # at the pole and can only compress, not bend, so the collet does nothing.
    out = trimesh.boolean.difference([out, _cyl_span(
        bore_r, seat, d, standoff - r_ball - 1.0, standoff + r_ball + 0.5,
        sections=48)])

    # collet slits: thin radial slabs from the outer pole back to the seat
    L = standoff + r_ball + 1.0
    for i in range(n_slit):
        s = box(extents=[slit_w, 2 * r_ball + 3.0, L])
        s.apply_translation([0.0, 0.0, standoff + r_ball + 0.5 - L / 2])
        s.apply_transform(rotation_matrix(2 * np.pi * i / n_slit, [0.0, 0.0, 1.0]))
        s.apply_transform(align_vectors([0.0, 0.0, 1.0], d))
        s.apply_translation(seat)
        out = trimesh.boolean.difference([out, s])
    return out


def ball_socket(seat, d, standoff=None, r_socket=None, r_mouth=None,
                n_slot=None, slot_w=None, **kw):
    """Female half as a NEGATIVE solid: mouth + spherical cavity.

    Subtract this from the receiving part.  The mouth must open AT the surface,
    otherwise the ball can never get in (a bore wholly inside solid reads as a
    sealed void, not a hole).

    `n_slot` defaults to 0 — a rigid socket.  Since the collet pin supplies all
    the compliance, slots here only give away retention.  Set n_slot only when
    the MALE side genuinely cannot be slit (e.g. it is part of a big casting).

    The cavity roof that closes over while printing is a dome, not a flat
    bridge: the worst unsupported ring is Ø(2*r_socket*sin45) at the top and a
    little sag there costs insertion depth, nothing else.
    """
    p = dict(JOINT_S, **{k: v for k, v in kw.items() if k in JOINT_S})
    r_socket = p["r_socket"] if r_socket is None else r_socket
    r_mouth = p["r_mouth"] if r_mouth is None else r_mouth
    standoff = p["standoff"] if standoff is None else standoff
    n_slot = 0 if n_slot is None else n_slot
    slot_w = p.get("slot_w", 0.8) if slot_w is None else slot_w
    d = _u(d)
    seat = np.asarray(seat, float)

    # Mouth cylinder runs from the seat all the way to the cavity centre.  It
    # MUST reach past the point where the cavity sphere has grown back to
    # r_mouth, otherwise the sphere's own flank becomes the throat and the
    # real squeeze is (r_ball - sphere_flank) — a silent, much tighter joint.
    h = standoff + 0.5
    mouth = _at(cylinder(radius=r_mouth, height=h, sections=64), seat, d,
                z0=-h / 2.0 + 1.0)
    cav = uv_sphere(radius=r_socket, subdivisions=4)
    cav.apply_translation(seat + d * standoff)
    neg = trimesh.boolean.union([mouth, cav])

    # relief slots: cut the lip into leaves.  Radial span must reach from just
    # inside the mouth to just outside the cavity -- stopping short leaves the
    # lip as a closed ring that cannot open.
    L = standoff + r_socket + 1.0
    y0, y1 = r_mouth - 0.6, r_socket + 1.4
    for i in range(n_slot):
        s = box(extents=[slot_w, y1 - y0, L])
        s.apply_translation([0.0, 0.5 * (y0 + y1), L / 2 - 1.0])
        s.apply_transform(rotation_matrix(2 * np.pi * i / n_slot + np.pi / n_slot,
                                          [0.0, 0.0, 1.0]))
        s.apply_transform(align_vectors([0.0, 0.0, 1.0], d))
        s.apply_translation(seat)
        neg = trimesh.boolean.union([neg, s])
    return neg


# ------------------------------------------------------------------ annular snap
def snap_skirt(seat, d, r_in, wall=1.2, height=3.0, bead=0.6, bead_h=1.2,
               n_slot=3, slot_w=0.8):
    """Male half: a slotted tube with an inward bead near its open end.

    Thin parts (2-3 mm) cannot host a socket, but they CAN carry a skirt that
    grips a boss on the mating part.  Slots make the skirt leaves springy.

    The skirt runs from the seat outward to `height`; the bead sits at the open
    end (`height - bead_h` .. `height`) and points inward.
    """
    d = _u(d)
    seat = np.asarray(seat, float)
    out = trimesh.boolean.union([
        _ring_span(r_in + wall, r_in, seat, d, -0.4, height),
        _ring_span(r_in + wall + 1.0, r_in - bead, seat, d,
                   height - bead_h, height)])
    if n_slot:
        L = height + 0.8
        y0, y1 = r_in - bead - 0.6, r_in + wall + 1.4
        for i in range(n_slot):
            s = box(extents=[slot_w, y1 - y0, L])
            s.apply_translation([0.0, 0.5 * (y0 + y1), L / 2 - 0.4])
            s.apply_transform(rotation_matrix(2 * np.pi * i / n_slot,
                                              [0.0, 0.0, 1.0]))
            s.apply_transform(align_vectors([0.0, 0.0, 1.0], d))
            s.apply_translation(seat)
            out = trimesh.boolean.difference([out, s])
    return out


def boss_groove(seat, d, r_boss, groove=0.7, groove_h=1.6, at_depth=0.0):
    """Female half as a NEGATIVE solid: turn a groove around a boss's side wall.

    `seat`/`d` must be the same pair you used for the boss, and `at_depth` is
    where the groove's centre sits along d (default: straddling the seat).
    The groove's FAR wall is what blocks the bead, so it must be solid material.
    """
    d = _u(d)
    return _ring_span(r_boss + 1.2, r_boss - groove, seat, d,
                      at_depth - groove_h / 2.0, at_depth + groove_h / 2.0)


# ------------------------------------------------------------------ taper fit
def taper_pin(seat, d, r_big, length, taper=1 / 12.0):
    """Male half: a cone that wedges in.  `r_big` is at the seat end."""
    d = _u(d)
    r_small = r_big - taper * length
    n = 64
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    v, f = [], []
    for r, z in ((r_big, 0.0), (r_small, length)):
        v += [[r * np.cos(t), r * np.sin(t), z] for t in th]
    for i in range(n):
        j = (i + 1) % n
        f += [[i, j, j + n], [i, j + n, i + n]]
    v += [[0, 0, 0], [0, 0, length]]
    for i in range(n):
        j = (i + 1) % n
        f += [[2 * n, j, i], [2 * n + 1, i + n, j + n]]
    m = trimesh.Trimesh(np.array(v), np.array(f))
    trimesh.repair.fix_normals(m)
    m.apply_transform(align_vectors([0.0, 0.0, 1.0], d))
    m.apply_translation(np.asarray(seat, float))
    return m


def taper_hole(seat, d, r_big, length, taper=1 / 12.0):
    """Female half as a NEGATIVE solid (0.25 mm on the radius)."""
    return taper_pin(seat, d, r_big + 0.25, length, taper)


# ------------------------------------------------------------------ press fit
def press_peg(seat, d, r, length, bury=1.5, chamfer=0.6):
    """Male half: a plain peg with a lead-in chamfer.  The cheap option.

    Use it where there is no room for a collet AND no room for a taper: a peg
    under 4 mm long cannot wedge, so it just needs a chamfer to start, plus
    `bury` of shank behind the seat so the union really welds it to its carrier
    (a peg whose base sits exactly on the mating plane is two solids sharing a
    face, and booleans drop those).
    """
    d = _u(d)
    p = _cyl_span(r, seat, d, -bury, length)
    # chamfer cone: trimesh's cone has its BASE at z=0, so z0 must be -length
    # for the base to land at the tip rather than back at the seat.
    tip = _at(trimesh.creation.cone(radius=r, height=chamfer, sections=64),
              seat, d, z0=-length)
    return trimesh.boolean.union([p, tip])


def press_hole(seat, d, r, length, clearance=0.15):
    """Female half as a NEGATIVE solid.  The bore is over-cut 1 mm OUTSIDE the
    seat so it always opens on the surface instead of leaving a skin.

    MEASURED, NOT THEORISED -- `clearance` may be negative, but a negative one
    is not a "press fit", it is a jam.  A delivered kit used clearance=-0.10
    (bore 0.20 smaller than the pin on the diameter) on three joints; the user's
    report on the physical parts was:

        arm -> palm        Ø4.60 pin into Ø4.40 bore   "won't go in at all"
                           (had to cut it off to assemble)
        pupil -> eye plate Ø4.00 pin into Ø3.80 bore   "so close -- just
                           barely would not go in"
        ear fin -> body    Ø6.00 pin into Ø5.80 bore   (not reached yet; same defect)

    Two independent reasons, both arithmetic and both visible before printing:

      1. Effective interference is not the CAD number.  The bore prints small
         (nozzle squish, and elephant foot where it opens onto the bed) and the
         pin prints fat, so -0.10 on the radius lands near 0.45 mm on the
         diameter -- ~10 % of a Ø4.6 pin, times three pins that must all start
         at once.
      2. THE PIN WAS LONGER THAN THE BORE.  This helper now makes
         `length + 2.4`, of which 1.0 sits outside the seat, so usable depth is
         `length + 1.4` -- 0.8 mm more than press_peg's `length + 0.6`
         protrusion.  (It used to make `length + 1.8` = `length + 0.8` usable,
         only 0.2 mm of margin; the joint that bottoms out can never close,
         however loose the fit.)

    So: keep the pin CLEARANCE in the bore (0.10-0.15 per side), keep 0.8 mm of
    axial margin, and if the joint has to hold, get the retention from an
    elastic bead on the MALE half -- see bead_peg().
    """
    d = _u(d)
    L = length + 2.4
    return _at(cylinder(radius=r + clearance, height=L, sections=64), seat, d,
               z0=-L / 2.0 + 1.0)


# ------------------------------------------------------------------ snap bead
def bead_peg(seat, d, r_body, length, r_bead=None, cone=1.5, slit_w=0.7,
             slit_depth=3.4, relief_r=0.9, bead_sink=0.9, bury=2.0):
    """Male half: clearance body, elastic BEAD near the tip.  The snap.

    Returns a LIST of solids plus a LIST to subtract after the union -- the
    slits have to cut the bead too, so they cannot go in with the bodies.

    The bead is what holds, and the slits are what make the bead elastic.  A
    solid bead in a rigid socket yields the socket instead: the joint works once
    and rattles forever, which is exactly the failure this kit already paid for
    on the collet-free press pegs.

    Sizing that has held up:
        r_body  = r_bore - 0.10 .. -0.15      body slides, it never grips
        r_bead  = r_bore + 0.09 .. +0.13      squeeze at the bead only
        length  = 4.0 - 5.0                   engagement, above the bead
        breast  = bead_sink below the cone base
        slice   = leaf thickness t = (2*r_body - 2*relief_r)/2, and the free
                  length L = slit_depth - the bead's own half-height
        strain  = 1.5 * delta * t / L^2  with delta = r_bead - r_bore
                  keep it under 2 % or the leaves take a set and stop snapping.
    """
    d = _u(d)
    r_bead = float(r_body + 0.22) if r_bead is None else float(r_bead)
    body = _cyl_span(r_body, seat, d, -bury, length)
    tip = _at(trimesh.creation.cone(radius=r_body, height=cone, sections=64),
              seat, d, z0=-length)
    bead = trimesh.creation.uv_sphere(radius=r_bead, count=[48, 32])
    bead.apply_transform(align_vectors([0.0, 0.0, 1.0], d))
    bead.apply_translation(np.asarray(seat, float) + d * (length - bead_sink))

    add = trimesh.boolean.union([body, tip, bead])

    z_lo, z_hi = length - slit_depth, length + cone + 1.0
    # Wide enough to pass right through the bead, narrow enough not to reach a
    # NEIGHBOUR peg: on a 3-pin ring of 6.3 mm the next pin is 10.9 mm away, and
    # a 16 mm-wide box centred here would shave it.  2.8 * r_bead leaves 0.4 mm.
    big = 2.8 * max(r_bead, 2.5)
    cut = []
    # Two crossing slits, built +Z and placed with _at so they follow d.  The
    # box is symmetric, so swapping x/y is the whole of the 90 deg rotation.
    for ex in ([slit_w, big, z_hi - z_lo], [big, slit_w, z_hi - z_lo]):
        box = trimesh.creation.box(extents=ex)
        cut.append(_at(box, seat, d, z0=-0.5 * (z_lo + z_hi)))
    cut.append(_cyl_span(relief_r, seat, d, z_lo - 0.4, z_hi))
    return [add], cut


def bead_hole(seat, d, r_body, length, clearance=0.12, mouth=0.8, cone=1.5,
              groove=None, groove_sink=None):
    """Female half as NEGATIVE solids: clearance bore + 45 deg mouth.

    `clearance` is positive and stays positive -- the bore matches the peg's
    BODY, not its bead.  `groove` adds a relief ring (radius) `groove_sink` mm
    below the seat so the bead can pop into it and lock axially; leave it None
    for a friction snap that re-opens with a pull.

    Depth is `length + cone + 0.8` on purpose: the 0.8 is the clearance that
    stops the pin from bottoming out before the faces meet.
    """
    d = _u(d)
    depth = length + cone + 0.8
    neg = [_cyl_span(r_body + clearance, seat, d, -1.0, depth),
           _at(trimesh.creation.cone(radius=r_body + clearance + mouth,
                                     height=mouth, sections=64),
               seat, d, z0=-mouth + 0.02)]
    if groove is not None:
        c = 0.0 if groove_sink is None else groove_sink
        neg.append(_cyl_span(groove, seat, d, c - 0.7, c + 0.7))
    return neg


def bead_strain(r_body, r_bead, r_bore, relief_r, slit_depth, bead_r=None):
    """(ok, eps) for the elastic leaves.  Under 2 % or they take a set."""
    t = r_body - relief_r
    L = max(slit_depth - (r_bead - r_bore), 0.5)
    eps = 1.5 * (r_bead - r_bore) * t / (L * L)
    return eps <= 0.02, eps


# ------------------------------------------------------------------ checking
def check(joint, standoff=None, n_slit=4, label=""):
    """Print the numbers that decide whether a ball joint will actually work.

    Returns (ok, strain).  Strain uses the cantilever result
    eps = 1.5 * delta * t / L^2, with
      delta = r_ball - r_mouth          (squeeze at the equator)
      t     = r_ball - bore_r           (leaf thickness)
      L     = standoff                  (anchor to equator = free length)
    """
    p = dict(JOINT_S, **{k: v for k, v in joint.items() if k in JOINT_S})
    s = p["standoff"] if standoff is None else standoff
    delta = p["r_ball"] - p["r_mouth"]
    t = p["r_ball"] - p["bore_r"]
    strain = 1.5 * delta * t / (s * s)
    # the retaining shoulder: how far the mouth undercuts the ball equator
    flank = s - np.sqrt(max(p["r_socket"] ** 2 - p["r_mouth"] ** 2, 0.0))
    shoulder = p["r_ball"] - p["r_mouth"]
    ok = strain < 0.02 and flank > 0 and shoulder > 0 and p["r_socket"] > p["r_ball"]
    print(f"{label or 'joint'}: ball Ø{2*p['r_ball']:.1f} mouth Ø{2*p['r_mouth']:.1f} "
          f"cavity Ø{2*p['r_socket']:.1f}")
    print(f"   squeeze {delta:.2f} mm radial / free length {s:.1f} mm / leaf "
          f"{t:.2f} mm  ->  strain {100*strain:.2f} %   "
          f"{'OK' if ok else 'OVER PLA LIMIT (~3.5 %, keep <2 %)'}")
    print(f"   throat at {flank:.2f} mm deep, shoulder {shoulder:.2f} mm radial, "
          f"cavity clearance {p['r_socket']-p['r_ball']:.2f} mm")
    return ok, strain
