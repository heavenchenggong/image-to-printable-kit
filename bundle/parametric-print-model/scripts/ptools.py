"""trimesh helpers for building parametric, printable multi-colour models.

Everything is mm, Z up.  Import as:

    import sys; sys.path.insert(0, "<skill>/scripts")
    from ptools import vec, frame, place, union, chain_capsules, bezier, halfspace
"""
import numpy as np
import trimesh
from trimesh.creation import uv_sphere, cylinder, capsule, cone, box
from trimesh.geometry import align_vectors
from trimesh.boolean import union as _bunion, difference as _bdiff, \
    intersection as _bint

__all__ = ["vec", "frame", "place", "union", "difference", "intersection",
           "chain_capsules", "bezier", "halfspace", "sphere", "cyl", "caps",
           "cn", "frustum", "chamfered_base", "fillet_check"]


# ------------------------------------------------------------------ vectors / frames
def vec(*a):
    v = np.array(a, dtype=float).ravel()
    return v / np.linalg.norm(v)


def frame(d):
    """Orthonormal frame with local +Z along d -> (X, Y, Z) as world unit vectors."""
    z = np.asarray(d, float).ravel()
    z = z / np.linalg.norm(z)
    up = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(z, up))) > 0.99:
        up = np.array([0.0, 1.0, 0.0])
    x = np.cross(up, z); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return x, y, z


def place(mesh, origin, d=None, scale=None):
    """Scale, rotate (local +Z -> d) and translate a primitive into place."""
    m = mesh.copy()
    if scale is not None:
        m.apply_scale(scale)
    if d is not None:
        dv = d if isinstance(d, np.ndarray) else np.array(d, float)
        m.apply_transform(align_vectors([0, 0, 1], dv / np.linalg.norm(dv)))
    m.apply_translation(np.asarray(origin, float))
    return m


# ------------------------------------------------------------------ booleans
def union(meshes):
    out = meshes[0].copy()
    for m in meshes[1:]:
        out = _bunion([out, m])
    return out


def difference(meshes):
    return _bdiff(meshes)


def intersection(meshes):
    return _bint(meshes)


def halfspace(z_lo=None, z_hi=None, size=240.0):
    """Big box covering z >= z_lo  (or z <= z_hi).  Use with intersection()."""
    b = box(extents=[size, size, size])
    if z_lo is not None:
        b.apply_translation([0, 0, z_lo + size / 2])
    else:
        b.apply_translation([0, 0, z_hi - size / 2])
    return b


# ------------------------------------------------------------------ primitives
def sphere(r, at, subdivisions=6):
    return place(uv_sphere(radius=r, subdivisions=subdivisions), at)


def cyl(r, h, at, d=None, sections=96):
    return place(cylinder(radius=r, height=h, sections=sections), at, d)


def caps(r, length, at, d=None):
    """Capsule: `length` is the CYLINDRICAL section only; total = length + 2r.

    NOTE trimesh.creation.capsule takes count=[n1, n2] — passing `sections`
    raises "revolve() got multiple values for keyword argument 'sections'".
    """
    return place(capsule(height=length, radius=r, count=[16, 48]), at, d)


def cn(r, h, at, d=None, sections=64):
    """Cone centred on `at`, tip along +d (base at -h/2, tip at +h/2).

    GOTCHA: trimesh.creation.cone puts the BASE at z=0 and the tip at z=h,
    while cylinder/capsule are centred on the origin.  Left alone, a cone
    placed at `at` starts at `at` and grows a further h along d -- which
    silently detaches it from whatever it was supposed to join.
    """
    m = cone(radius=r, height=h, sections=sections)
    m.apply_translation([0, 0, -h / 2])
    return place(m, at, d)


def frustum(r_lo, r_hi, h, at, d=None, sections=96):
    """Truncated cone, centred on `at`: `r_lo` at -h/2, `r_hi` at +h/2.

    Covers two jobs nothing else in here does:
      * a printable shoulder.  A flat step leaves an unsupported lip; a taper
        of 45 degrees or less prints as an overhang the nozzle can hold.
      * a bottom chamfer, which is the geometric half of elephant-foot control
        and stops the sharp edge of a base from curling first (the edge of a
        flat slab is where warp starts, because it is the least supported).

    Built as the convex hull of two rings -- trimesh has no tapered-cylinder
    primitive, and stacking thin cylinders is slow and leaves internal seams.

    A bottom chamfer SHRINKS the contact patch (that is the point), so it also
    shortens the warp span; see scripts/warp.py for how much that buys you.
    """
    if r_lo <= 0.0 or r_hi <= 0.0:
        raise ValueError("frustum needs both radii > 0; use cn() for a cone")
    a = np.linspace(0.0, 2.0 * np.pi, sections, endpoint=False)
    lo = np.c_[r_lo * np.cos(a), r_lo * np.sin(a), np.full_like(a, -h / 2.0)]
    hi = np.c_[r_hi * np.cos(a), r_hi * np.sin(a), np.full_like(a, h / 2.0)]
    m = trimesh.Trimesh(vertices=np.vstack([lo, hi])).convex_hull
    return place(m, at, d)


def chamfered_base(r, h, cham=0.6, at=(0.0, 0.0, 0.0), sections=96):
    """Ø2r cylinder of height h with a 45-degree chamfer on its bottom edge.

    Why a chamfer and not just an elephant-foot slider value: the sharp edge of
    a flat base is the least supported part of the whole footprint, so it is
    where warp starts and where the nozzle catches first.  A 45-degree bevel
    also removes contact area, and contact area is what generates the
    contraction force in the first place.

    The chamfer must be CUT, not unioned.  Adding a tapered ring under a
    cylinder does nothing at all -- the cylinder's own bottom face is still
    there at full radius, and the union keeps it.  So: build the body, then
    subtract a ring knife whose inner wall IS the taper.  Two differences, not
    one: A - (B - C) is not A - B - C, and writing it the short way silently
    shaves a groove around the part.
    """
    if not 0.0 < cham < min(r, h):
        raise ValueError("chamfer must be positive and smaller than r and h")
    cx, cy, z0 = at
    body = cyl(r, h, [cx, cy, z0 + h / 2.0], sections=sections)
    outer = cyl(r + 1.0, cham + 0.3, [cx, cy, z0 + (cham - 0.3) / 2.0],
                sections=sections)
    inner = frustum(r - cham, r, cham, [cx, cy, z0 + cham / 2.0],
                    sections=sections)
    return difference([body, difference([outer, inner])])


def chain_capsules(points, radii):
    """Union capsules along a polyline -> smooth tapered tube (horns, fingers).

    This is how you get a curved, tapering form that stays one watertight solid
    instead of a pile of intersecting spheres.
    """
    pts = [np.asarray(p, float) for p in points]
    segs = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        dv = b - a
        L = float(np.linalg.norm(dv))
        r = 0.5 * (radii[i] + radii[i + 1])
        segs.append(caps(r, max(L - 2 * r, 0.01), 0.5 * (a + b), dv))
    return union(segs)


def bezier(p0, p1, p2, p3, n):
    t = np.linspace(0, 1, n)[:, None]
    p0, p1, p2, p3 = (np.asarray(p, float) for p in (p0, p1, p2, p3))
    return ((1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1
            + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3)


# ------------------------------------------------------------------ checks
def fillet_check(shells, min_feature=2.0, verbose=True):
    """Report the numbers that decide whether an FDM print survives.

    shells: dict name -> list of meshes (or a single concatenated mesh)
    """
    out = {}
    for name, parts in shells.items():
        m = parts if isinstance(parts, trimesh.Trimesh) else trimesh.util.concatenate(parts)
        b = m.bounds
        info = dict(bbox=[round(float(x), 1) for x in (b[1] - b[0])],
                    volume_cm3=round(abs(float(m.volume)) / 1000.0, 1),
                    faces=int(len(m.faces)),
                    below_bed=int((m.vertices[:, 2] < -1e-6).sum()))
        out[name] = info
        if verbose:
            print(f"{name:8s} bbox {info['bbox']}  {info['volume_cm3']:7.1f} cm3  "
                  f"faces {info['faces']:7d}  below-bed {info['below_bed']}")
    tot = sum(v["volume_cm3"] for v in out.values())
    if verbose:
        print(f"{'TOTAL':8s} solid {tot:7.1f} cm3 -> ~{tot * 1.24:.0f} g if solid, "
              f"~{tot * 1.24 * 0.28:.0f} g at 3 walls / 15% infill")
    return out
