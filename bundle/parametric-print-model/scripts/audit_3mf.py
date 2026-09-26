#!/usr/bin/env python3
"""Audit a delivered 3MF: is every object ONE watertight printable solid?

    python audit_3mf.py winston_snapkit_printready.3mf [more.3mf ...]

Why this exists — and why you cannot use `trimesh.load()` for it:

  trimesh's STL and 3MF **loaders repair on the way in** (they merge vertices
  and drop degenerate faces).  On a perfectly good boolean-built solid that
  repair breaks the edge pairing, so `is_watertight` comes back False and
  `split()` invents dozens of zero-volume "fragments".  Measured on one real
  kit: the loader said 8 of 10 parts were broken; parsing the file's own vertex
  indices says all 10 are watertight single solids — and the slicer's kernel
  agrees with the latter.

  So: read the model file yourself, build `Trimesh(..., process=False)`, and
  judge that.  Then confirm with the slicer's own kernel:

      BambuStudio --info model.3mf        # manifold / number_of_parts per object

  `number_of_parts` is the connectivity count: 1 is what you want.  It is the
  only check that caught a pin whose end face was exactly coplanar with the
  collar it was supposed to join (the union left slivers; every in-process
  check had passed).

TWO LAYOUTS, AND WHY MISSING ONE IS WORSE THAN A FALSE ALARM:

  geometry-only  our own exporter: meshes inline in `3D/3dmodel.model`
  PROJECT         the slicer's `--export-3mf`: the main model holds only
                  `<component p:path="/3D/Objects/object_N.model"/>` and the
                  placement lives in `<build><item transform="...">` (the part
                  files carry LOCAL coordinates, z can be negative)

  Reading only `3D/3dmodel.model` on a project file finds ZERO objects and
  then reports success — **it verified nothing**.  Delivered files get baked
  into project 3MFs on purpose (see `bake_project.py`), so this is the normal
  case, not an edge case.  Both layouts are handled below, and the build
  transform is applied.

Exit code is 1 if anything fails, so it can gate a build.
"""
import re
import sys
import zipfile

import numpy as np
import trimesh

OBJ = re.compile(r'<object id="(\d+)"[^>]*>(.*?)</object>', re.S)
# Coordinates must accept scientific notation.  Our own exporter writes plain
# decimals, but the slicer's exporter writes things like 1.5e-06; missing those
# vertices makes the triangle indices point past the end of the vertex list and
# trimesh dies with "index 5798 is out of bounds for axis 0 with size 5798".
NUM = r'[-+0-9.]+(?:[eE][-+]?[0-9]+)?'
VERT = re.compile(rf'<vertex x="({NUM})" y="({NUM})" z="({NUM})"')
TRI = re.compile(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"')
NAME = re.compile(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]*)"')
COMP = re.compile(r'p:path="([^"]+)"')


def objects(path):
    """Yield (name, Trimesh) straight from the file's own indices.

    Two layouts have to be handled, and skipping the second one makes this
    script LIE:

      * geometry-only 3MF -- meshes inline in 3D/3dmodel.model
      * project 3MF       -- 3D/3dmodel.model contains only
                             <component p:path="/3D/Objects/object_N.model"/>
                             stubs and the meshes live in those part files.
                             This is what BambuStudio --export-3mf writes, ie
                             every file that has been "baked" for delivery.
    """
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        model = z.read("3D/3dmodel.model").decode()
        label = {}
        if "Metadata/model_settings.config" in names:
            msc = z.read("Metadata/model_settings.config").decode()
            label = {i: n for i, n in NAME.findall(msc)}

        # Placement lives in <build><item transform="...">: in the project
        # layout the part files hold LOCAL coordinates (z can be negative) and
        # this 4x3 matrix puts them on the plate.  Ignore it and the bed check
        # reports every part as sunk below the bed.
        xf = {}
        for tag in re.findall(r'<item\b[^>]*>', model):
            oid = re.search(r'objectid="(\d+)"', tag)
            tr = re.search(r'transform="([^"]*)"', tag)
            if oid and tr:
                xf[oid.group(1)] = np.array(
                    [float(x) for x in tr.group(1).split()]).reshape(4, 3)

        def mesh_of(body):
            return ([[float(a), float(b), float(c)] for a, b, c in VERT.findall(body)],
                    [[int(a), int(b), int(c)] for a, b, c in TRI.findall(body)])

        for oid, body in OBJ.findall(model):
            if VERT.search(body):
                v, f = mesh_of(body)
            else:
                v, f = [], []
                for part in COMP.findall(body):
                    sub = z.read(part.lstrip("/")).decode()
                    for _, sbody in OBJ.findall(sub):
                        sv, sf = mesh_of(sbody)
                        off = len(v)
                        v = v + sv
                        f = f + [[i + off for i in t] for t in sf]
            v = np.asarray(v, dtype=float)
            if len(v) < 3 or len(f) < 1:
                continue
            if oid in xf:
                M = xf[oid]
                v = v @ M[:3, :] + M[3, :]
            yield label.get(oid, f"object {oid}"), trimesh.Trimesh(
                vertices=v, faces=np.array(f), process=False)


def audit(path, verbose=True):
    ok_all = True
    if verbose:
        print(f"===== {path}")
    for name, m in objects(path):
        shells = m.split(only_watertight=False)
        real = [p for p in shells if abs(p.volume) > 1.0]
        junk = len(shells) - len(real)
        degen = int((m.area_faces < 1e-9).sum())
        below = int((m.vertices[:, 2] < -1e-6).sum())
        bad, warn = [], []
        if not m.is_watertight:
            bad.append("not watertight")
        if junk:
            bad.append(f"{junk} junk shells")
        if below:
            bad.append(f"{below} verts below the bed")
        if len(real) != 1:
            # Legitimately multi-solid objects exist (spare test pins on a
            # coupon).  Flag it, do not fail it — a genuinely split part is
            # caught by the slicer's own number_of_parts instead.
            warn.append(f"{len(real)} solids")
        ok_all &= not bad
        if verbose:
            b = m.bounds
            verdict = "PASS" if not bad else "FAIL " + "; ".join(bad)
            if warn:
                verdict += "  (WARN " + "; ".join(warn) + ")"
            print(f"  {name:24s} {verdict}"
                  f"   {b[1][0]-b[0][0]:5.1f}x{b[1][1]-b[0][1]:5.1f}x{b[1][2]-b[0][2]:5.1f} mm"
                  f"  {abs(m.volume)/1000:6.2f} cm3  degen={degen}")
    if verbose:
        print(f"  -> {'all good' if ok_all else 'NEEDS FIXING'}\n")
    return ok_all


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    results = [audit(p) for p in args]      # no short-circuit: audit them all
    sys.exit(0 if all(results) else 1)
