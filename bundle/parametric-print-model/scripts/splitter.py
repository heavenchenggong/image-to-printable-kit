"""Split a model into press-fit, print-ready parts — helpers.

The AMS-single-piece route is in ptools/threemf.  This module is for the other
route: one colour per part, each part a single connected solid, each part
pre-rotated into its own print orientation, joined with pins + dowels + glue.

    import sys; sys.path.insert(0, "<skill>/scripts")
    from splitter import (slab, cyl_d, n_components, footprint, lay_flat,
                          orient, mirror_twin, sit, MIRROR_X, build_plate,
                          write_kit, write_colour_plates)

`write_kit` exports ONE mixed plate (a layout reference).  `write_colour_plates`
exports ONE plate PER COLOUR, which is what you actually hand over for a split
kit — see that function's docstring for why.

Read references/split-to-print.md first — three of the four traps there are
silent (you get a mesh that looks fine and cannot be assembled).
"""
import numpy as np
import trimesh
from trimesh.geometry import align_vectors
from trimesh.transformations import rotation_matrix

import ptools as P

__all__ = ["slab", "cyl_d", "n_components", "footprint", "footing_margin",
           "overhang_area", "lay_flat", "rest_flat", "upright",
           "sit", "orient", "mirror_twin", "MIRROR_X", "build_plate",
           "write_kit", "write_colour_plates", "plastic_g", "MIN_CONTACT",
           "CONTACT_TOL"]

MIRROR_X = np.diag([-1.0, 1.0, 1.0, 1.0])

# A footing this small is a line or a point, not a pose.  The gate (warp.py)
# already says "brim it, and watch for it being knocked loose" below this, but
# that is a bandage: the honest fix is to find the attitude the part actually
# rests in.  Same number here and there on purpose.
MIN_CONTACT = 5.0

# The SOLVER's ruler for "is this face actually on the bed" — much tighter than
# footprint()'s 0.8 mm default.  A part balanced on a corner puts 3 vertices at
# z ~ 0 and the next cluster at z ~ 0.55 mm; at 0.8 mm those invent an 80 mm2
# footprint and hide the fact that the part is standing on a point.  A face
# that is genuinely down lands its vertices at z <= 0.1 mm.
CONTACT_TOL = 0.10


# ------------------------------------------------------------------ cutting
def slab(d, limit, keep="below", centre=(0, 0, 0), size=220.0):
    """Big box covering d.(p-centre) >= limit ('above') or <= limit ('below').

    Use with intersection() to keep one side (e.g. a flat mating facet) or with
    difference() to cut it away.
    """
    c = np.asarray(centre, float)
    d = np.asarray(d, float)
    b = trimesh.creation.box(extents=[size, size, size])
    b.apply_transform(align_vectors([0.0, 0.0, 1.0], d))
    b.apply_translation(c + d * (limit + size / 2 if keep == "above"
                                 else limit - size / 2))
    return b


def cyl_d(r, lo, hi, d, centre=(0, 0, 0)):
    """Cylinder of radius r along unit d, spanning [lo, hi] measured from centre.

    Handy for pins/sockets specified as "from 20 to 32 mm out along this
    direction" instead of as a centre point.
    """
    c = np.asarray(centre, float)
    d = np.asarray(d, float)
    return P.cyl(r, hi - lo, c + d * (0.5 * (lo + hi)), d)


# ------------------------------------------------------------------ checks
def n_components(m, tol=1e-3):
    """Connected-component count of a mesh, welded by rounded COORDINATE.

    Index-based union-find (and trimesh's own graph helpers) report phantom
    islands on boolean output, because the seams carry near-duplicate vertices.
    If this returns >1 the part genuinely is several loose solids.
    """
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


def _hull2(pts):
    pts = sorted({(round(float(a), 4), round(float(b), 4)) for a, b in pts})
    if len(pts) < 3:
        return pts

    def cr(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lo = []
    for p in pts:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    up = []
    for p in reversed(pts):
        while len(up) >= 2 and cr(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def _area(h):
    if len(h) < 3:
        return 0.0
    s = 0.0
    for i in range(len(h)):
        x1, y1 = h[i]
        x2, y2 = h[(i + 1) % len(h)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _inside(p, h):
    x, y = p
    ok = False
    for i in range(len(h)):
        x1, y1 = h[i]
        x2, y2 = h[(i + 1) % len(h)]
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                ok = not ok
    return ok


def footprint(m, tol=0.8):
    """(bed footprint mm2, centre of mass over it?) — the real stability test.

    Do NOT score "area of faces near the bed": that reads 0 for anything
    curved (a capsule lying down touches along a line) and 2000 mm2 for a flat
    disc, so it flags printable parts as unstable.  What matters is whether the
    centre of mass falls inside the contact patch.
    """
    v = m.vertices[m.vertices[:, 2] < tol]
    h = _hull2(v[:, :2])
    try:
        c = m.center_mass if m.is_watertight else m.centroid
    except Exception:
        c = m.centroid
    return _area(h), (_inside((float(c[0]), float(c[1])), h) if len(h) >= 3
                      else False)


def plastic_g(m, walls=2, width=0.42, skin=5, layer=0.2, infill=0.15,
              rho=1.24):
    """Planning estimate of printed mass in grams — NOT a substitute for slicing.

    Three buckets, because scoring the whole surface as "wall" (or as "solid
    volume x a fill factor") is wrong at both ends of the size range:

        Ø16 boss    2 perimeters around a Ø16 circle are a big share of the
                    cross-section and the top skin closes the rest
                    -> ~67% of the solid volume
        2.5 mm plate walls + skin fill the whole thickness      -> ~100%
        big hollow ball  almost no wall relative to volume      -> ~20%

    So any single factor is off by ~2x somewhere.  Measured on the Winston
    coupon: a hand-picked 0.30 factor said 7 g, the slicer said 17 g.  Never
    quote these grams for a part someone is about to spend plastic on —
    slice it with `scripts/weigh_3mf.py`, which reads the toolpath.

    Buckets (defaults follow Bambu's stock profile: 2 walls, 5 skin layers at
    0.2 mm, 15% infill):

        perimeter  lateral faces only (|nz| < 0.9) x walls x line width
        skin       top/bottom faces x skin layers x layer height
        infill     whatever volume is left, at the infill density
    """
    n, a = m.face_normals, m.area_faces
    lat = float(a[np.abs(n[:, 2]) < 0.9].sum())
    horiz = float(a[np.abs(n[:, 2]) >= 0.9].sum())
    v = abs(float(m.volume))
    wall = min(lat * walls * width, v)
    skin_v = min(horiz * skin * layer, max(v - wall, 0.0))
    core = max(v - wall - skin_v, 0.0) * infill
    return (wall + skin_v + core) / 1000.0 * rho


# ------------------------------------------------------------------ orienting
def sit(m):
    out = m.copy()
    out.apply_translation([0, 0, -out.bounds[0][2]])
    return out


def footing_margin(m, tol=0.6):
    """Signed clearance (mm) from the centre of mass to the edge of the footing.

    Positive: the mass sits over the contact patch, so the part stands.  This
    is the ONLY honest stability test -- "contact area" alone is not, because a
    capsule lying down touches along a line and scores 0 mm2 while standing
    perfectly well, and a barrel can touch over 200 mm2 while being balanced on
    an edge.  Negative: the part rocks over that edge and settles somewhere
    else, taking the brim it was printed with out of the picture.
    """
    v = np.asarray(m.vertices, float)
    zmin = float(v[:, 2].min())
    pts = v[v[:, 2] < zmin + tol][:, :2]
    if len(pts) < 3:
        return float("-inf")
    try:
        from scipy.spatial import ConvexHull
        h = ConvexHull(pts)
    except Exception:
        return float("-inf")
    try:
        c = np.asarray(m.center_mass if m.is_watertight else m.centroid, float)
    except Exception:
        c = np.asarray(m.centroid, float)
    eq = np.asarray(h.equations, float)
    d = (eq[:, :2] @ c[:2] + eq[:, 2]) / np.linalg.norm(eq[:, :2], axis=1)
    return float(-d.max())


def overhang_area(m, cut=-0.70):
    """Area (mm2) facing down steeper than `cut`*90 deg -- how much needs support.

    cut = -0.70 is ~45 deg below horizontal, the usual threshold.  Faces inside
    a part's own shadow are excluded by the caller's choice of cut, not here:
    this is a cheap proxy, use it to compare two candidate orientations.
    """
    n = np.asarray(m.face_normals, float)
    a = np.asarray(m.area_faces, float)
    return float(a[n[:, 2] < cut].sum())


def rest_flat(m, topn=48, min_margin=1.0):
    """-> (mesh, 4x4) in the attitude the part actually RESTS in.  See below.

    Why not just `lay_flat`
    -----------------------
    `lay_flat` puts the thinnest principal axis vertical and then searches
    single-axis tilts of up to 25 deg.  Most parts are fine.  Two parts of the
    Winston kit were not: both came out balanced on a 1 mm2 tangent line with
    the centre of mass 2-2.4 mm OUTSIDE the footprint, i.e. on the wrong side
    of the tipping line.  In a slicer that looks like a normal part with a
    normal brim.  On the plate it rocks over and prints loose.

    The pose a rigid body rests in is one of the faces of its convex hull
    against the plate, so search THOSE directions instead of a hand-rolled tilt
    grid.  Keep the poses whose footprint contains the centre of mass, and
    score what is left by footprint size, clearance, height and support need.

    This never calls lay_flat (lay_flat calls this), so there is no cycle.
    """
    hull = m.convex_hull
    nrm = np.asarray(hull.face_normals, float)
    areas = np.asarray(hull.area_faces, float)
    dirs = []
    for i in np.argsort(-areas):                 # biggest faces first
        d = nrm[i]
        if all(float(d @ nrm[j]) < 0.985 for j in dirs):
            dirs.append(i)
        if len(dirs) >= topn:
            break

    settled, settled_T, settled_s = None, None, -1e18
    rocking, rocking_m, rocking_T = None, -1e18, None
    for i in dirs:
        R = align_vectors(nrm[i], np.array([0.0, 0.0, -1.0]))
        if R.shape != (4, 4):
            raise ValueError("trimesh.geometry.align_vectors must return 4x4")
        m2 = m.copy()
        m2.apply_transform(R)
        # Drop it onto the plate AFTER rotating: taking the offset from the
        # pre-rotation bounds is a classic way to sink a part halfway in.
        tz = np.eye(4)
        tz[2, 3] = -float(m2.bounds[0][2])
        T = tz @ R
        m3 = m.copy()
        m3.apply_transform(T)
        marg = footing_margin(m3)
        area, inside = footprint(m3)
        h = float(m3.bounds[1][2] - m3.bounds[0][2])
        if inside and marg >= min_margin:
            s = (area + 500.0 * min(marg, 4.0) / 4.0 - 1.0 * h
                 - 0.35 * overhang_area(m3))
            if s > settled_s:
                settled, settled_T, settled_s = m3, T, s
        elif marg > rocking_m:
            rocking, rocking_m, rocking_T = m3, marg, T
    if settled is not None:
        return settled, settled_T
    # Nothing rests cleanly.  Return the least-bad pose rather than recursing
    # into lay_flat -- a part that rocks in every attitude still has to be
    # printed, and the caller needs to know which attitude rocks least.
    return (rocking, rocking_T) if rocking is not None else lay_flat(m)


def lay_flat(m):
    """Thinnest principal axis vertical, then a small tilt search for contact.

    Returns (mesh, 4x4 transform).  Keep the transform if a mirrored twin
    should inherit the SAME orientation (see mirror_twin) — re-solving it
    separately lands a visibly different angle on the two sides.
    """
    v = m.vertices - m.vertices.mean(axis=0)
    _, _, vt = np.linalg.svd(v, full_matrices=False)
    tb = align_vectors(vt[2], [0.0, 0.0, 1.0])

    def cand(T):
        m2 = m.copy()
        m2.apply_transform(T)
        tz = np.eye(4)
        tz[2, 3] = -m2.bounds[0][2]
        Tf = tz @ T
        m3 = m.copy()
        m3.apply_transform(Tf)
        return m3, Tf

    best, best_T = cand(tb)
    a, ok = footprint(best)
    best_score = a + (600.0 if ok else 0.0) - 1.5 * (
        best.bounds[1][2] - best.bounds[0][2])
    h0 = best.bounds[1][2] - best.bounds[0][2]
    for axis in ([1.0, 0, 0], [0, 1.0, 0]):
        for ang in range(-25, 26, 5):
            m2, T = cand(rotation_matrix(np.radians(ang), axis) @ tb)
            h = m2.bounds[1][2] - m2.bounds[0][2]
            if h > 1.7 * h0:
                continue
            a, ok = footprint(m2)
            s = a + (600.0 if ok else 0.0) - 1.5 * h
            if s > best_score:
                best, best_T, best_score = m2, T, s

    a, ok = footprint(best)
    # Marginal is not good enough, and it must be judged on the SAME ruler the
    # gate uses: footprint()'s default 0.8 mm tolerance counts vertices that
    # are 0.8 mm off the plate as contact, which for a 10 mm part invents a
    # support polygon it does not have and hides a tipping hazard.
    a_real = footprint(best, tol=CONTACT_TOL)[0]
    if not ok or footing_margin(best) < 1.0 or a_real < MIN_CONTACT:
        # The tilt grid found no attitude this part can stand in.  Fall back to
        # the hull-face search, which is where rigid bodies really rest.  Two
        # parts of the Winston kit were silently balanced on a 1 mm2 tangent
        # line with the centre of mass 2 mm outside it.
        m2, T2 = rest_flat(m)
        a2, b2 = footprint(m2)
        h2 = m2.bounds[1][2] - m2.bounds[0][2]
        s2 = a2 + (600.0 if b2 else 0.0) - 1.5 * h2
        # When the incumbent is a line contact, ANY attitude that genuinely
        # stands beats it — do not let the old score veto it.  (The scoring
        # function has no term for "0.3 mm2 is not a footprint", so a tall
        # balanced-on-a-knife-edge pose can out-score a short, honest one.)
        if b2 and (s2 > best_score or a_real < MIN_CONTACT):
            best, best_T = m2, T2
    return best, best_T


def upright(m, d):
    """Rotate so direction d becomes +Z, then sit on the bed.

    The point of splitting: an appendage whose assembly axis is 60 deg off
    vertical still prints as a stable upright cone on its own.
    """
    out = m.copy()
    out.apply_transform(align_vectors(np.asarray(d, float), [0.0, 0.0, 1.0]))
    return sit(out)


def orient(mesh, spec, dirvec=None):
    """-> (mesh in print orientation, 4x4).  spec: identity|flip|layflat|upright|face.

    `face` takes the direction that must end up facing the BED, expressed in
    the pose `lay_flat` returns -- an increment ON TOP of the stock solver, not
    an absolute machine-frame direction.  That distinction is not cosmetic: a
    pose chosen by cutting candidates is chosen against the exported print-pose
    STL, which already sits in lay_flat's frame, so feeding those numbers in as
    machine-frame axes rotates the part twice and silently lands somewhere else.

    Reach for `face` when the pose was settled by cutting candidates instead of
    by scoring them -- the stocked solvers rank with a footprint proxy, and on
    a concave base that proxy is wrong by 20x, so their "best" can still be a
    knife-edge.  See references/split-to-print.md.
    """
    if spec == "layflat":
        return lay_flat(mesh)
    if spec == "face":
        base, T_base = lay_flat(mesh)
        return upright(base, -np.asarray(dirvec, float)), T_base
    if spec == "identity":
        out = sit(mesh)
    elif spec == "flip":                      # 180 deg about X, no mirroring
        out = mesh.copy()
        out.apply_scale([1, -1, -1])
        out = sit(out)
    elif spec == "upright":
        out = upright(mesh, dirvec)
    else:
        raise ValueError(spec)
    return out, np.eye(4)


def mirror_twin(mesh_asm, T_of_twin):
    """Print orientation of an X-mirrored part: M @ T @ M."""
    out = mesh_asm.copy()
    out.apply_transform(MIRROR_X @ T_of_twin @ MIRROR_X)
    return out


# ------------------------------------------------------------------ plate
def build_plate(parts, bed=256.0, gap=6.0):
    """Shelf-pack print-oriented parts.  parts: list of dicts with 'mesh'.

    Mutates each dict, adding 'plate' (laid-out copy) plus w/d/h and stability.
    Conservative: anything that does not fit a 256 bed is reported.
    """
    cursor = [0.0, 0.0, 0.0]
    for spec in parts:
        m = spec["mesh"]
        b = m.bounds
        w, d = b[1][0] - b[0][0], b[1][1] - b[0][1]
        if cursor[0] + w > bed - gap:
            cursor[1] += cursor[2] + gap
            cursor[0] = cursor[2] = 0.0
        laid = m.copy()
        laid.apply_translation([cursor[0] - b[0][0], cursor[1] - b[0][1], 0.0])
        cursor[0] += w + gap
        cursor[2] = max(cursor[2], d)
        spec["plate"] = laid
        spec["size"] = (w, d, b[1][2] - b[0][2])
        spec["footprint"] = footprint(m)
        if cursor[1] + cursor[2] > bed - gap:
            print(f"  !! {spec.get('id', '?')} does not fit the plate")
    return cursor[0], cursor[1] + cursor[2]


def write_kit(parts, stl_dir, threemf_path, title="", bed=256.0, gap=6.0):
    """Lay out and export: one STL per part + one multi-colour 3MF for the plate.

    Each part must already be in its print orientation (use orient()).
    """
    import os
    import threemf
    os.makedirs(stl_dir, exist_ok=True)
    used = build_plate(parts, bed=bed, gap=gap)
    plate = []
    for spec in parts:
        pid = spec.get("id", spec.get("name", "part"))
        spec["plate"].export(os.path.join(stl_dir, f"{pid}.stl"))
        plate.append((f"{pid} {spec.get('name', '')}", spec["colour"],
                      spec["plate"]))
    if threemf_path:
        threemf.write_3mf(plate, threemf_path, title=title)
    return used


def write_colour_plates(plate, out_dir, title="", bed=256.0, gap=6.0,
                        stem="plate", order=None, names=None, merge=None):
    """One SINGLE-FILAMENT 3MF per colour — the plate unit a split kit wants.

    A split kit's whole payoff is that every part is one colour, so the parts
    should be printed colour by colour.  Ship them as one multi-colour plate
    instead and you drag the AMS problem straight back in: purge at every
    colour change, a prime tower, and — on a dual-nozzle machine — a hard
    refusal when the two loaded spools are not the same temperature class
    ("high- and low-temperature filament at the same time may clog the nozzle").
    Bambu ignores 3MF base-material colours anyway, so a multi-colour file buys
    you nothing but slot numbers, which is exactly what you do not want here.

    Every file written by this function carries ONE filament slot, so it slices
    against slot 1 alone: no purge, no prime tower, no mixed-material check —
    and the user loads one spool, prints, swaps, repeats.

    plate  [(label, "#RRGGBB", mesh), ...] — print-oriented; layout is redone
           per colour, so pass the parts as they come.
    stem   filename prefix; the group index is appended -> f"{stem}1_main.3mf"
    order  optional hex list fixing the plate order (default: first appearance)
    names  optional {hex: slug} for readable filenames (default: the bare hex)
    merge  optional {src_hex: dst_hex} folding a colour into another plate —
           use it when only two spools are loaded (e.g. a lone eye disc has to
           ride with the body plate; nobody wants a whole plate for one disc).

    Returns [(path, hex, n_parts, (w, d, h), slug), ...] in plate order.
    """
    import os
    import threemf

    merge = {k.upper(): v.upper() for k, v in (merge or {}).items()}
    buckets = {}
    for label, hexcol, mesh in plate:
        h = merge.get(hexcol.upper(), hexcol.upper())
        buckets.setdefault(h, []).append((label, mesh))

    nm = {str(k).upper(): v for k, v in (names or {}).items()}
    keys = [h.upper() for h in (order or []) if h.upper() in buckets]
    keys += [h for h in buckets if h not in keys]

    os.makedirs(out_dir, exist_ok=True)
    out = []
    for i, h in enumerate(keys, 1):
        slug = nm.get(h) or h.lstrip("#").lower()
        specs = [dict(id=lab, name=lab, colour=h, mesh=m)
                 for lab, m in buckets[h]]
        build_plate(specs, bed=bed, gap=gap)
        lo = np.min([s["plate"].bounds[0] for s in specs], axis=0)
        hi = np.max([s["plate"].bounds[1] for s in specs], axis=0)
        path = os.path.join(out_dir, f"{stem}{i}_{slug}.3mf")
        threemf.write_3mf([(s["name"], h, s["plate"]) for s in specs], path,
                          title=f"{title} / {slug}".strip(" /"),
                          filaments=[(slug, h)])
        print(f"  plate {i}: {len(specs):2d} part(s) -> {os.path.basename(path)}"
              f"   {hi[0] - lo[0]:5.1f} x {hi[1] - lo[1]:5.1f} x "
              f"{hi[2]:5.1f} mm   ~{sum(plastic_g(s['mesh']) for s in specs):5.1f} g"
              f"   {', '.join(s['name'] for s in specs)}")
        out.append((path, h, len(specs),
                    (hi[0] - lo[0], hi[1] - lo[1], hi[2]), slug))
    return out
