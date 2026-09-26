"""Multi-view orthographic preview of a multi-colour model.

Two traps, both learned the hard way:

 1) Do NOT put each colour in its own Poly3DCollection.  matplotlib only
    depth-sorts WITHIN a collection, so the last one added paints over
    everything — you get a model that is 100% the last colour.  Build one
    collection with per-face colours and zsort="average".

 2) Do NOT pre-project the vertices and then also call view_init().  matplotlib
    projects again and you get a 90-degree mis-mapped view that looks like a
    top-down render.  Feed real world coordinates and let view_init do the work.

matplotlib azimuth convention: the camera sits at
    (cos(az)*cos(elev), sin(az)*cos(elev), sin(elev))
so az=+90 is the +Y side.  Put the character's face on +Y and use az=90 for
the front view; note either side is mirrored relative to the reference image,
so flip the model in X if the asymmetry has to match the artwork exactly.
"""
import os

import numpy as np
import trimesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

# CJK titles/annotations render as tofu boxes under matplotlib's default
# DejaVu Sans — it has no CJK glyphs.  Name a few likely families; matplotlib
# silently ignores the ones that are not installed.
matplotlib.rcParams["font.family"] = [
    "PingFang SC", "Hiragino Sans GB", "Heiti SC", "Noto Sans CJK SC",
    "Source Han Sans SC", "Microsoft YaHei", "Arial Unicode MS",
    "DejaVu Sans",
]
matplotlib.rcParams["axes.unicode_minus"] = False

VIEWS = [("front", 90), ("3/4", 130), ("side", 0), ("back", -90)]


def render(shells, out_path, views=None, light=(-0.38, 0.72, 0.58),
           dpi=140, title=""):
    """shells: list of (hex_color, mesh_or_path).  Writes a 4-up PNG.

    views: [(name, azimuth), ...] or [(name, azimuth, elevation), ...].  The
    elevation defaults to 8 deg; pass ~75 for a near-top view, which is the only
    way to read a flat test coupon (bores and count-marks are invisible from the
    side).
    """
    views = views or VIEWS
    L = np.array(light, float); L /= np.linalg.norm(L)

    tris, cols = [], []
    for hexcol, src in shells:
        m = src if isinstance(src, trimesh.Trimesh) else trimesh.load(src, force="mesh")
        shade = 0.40 + 0.60 * np.clip(m.face_normals @ L, 0, 1)
        base = np.array(to_rgb(hexcol))
        tris.append(m.triangles)
        cols.append(np.clip(base[None, :] * shade[:, None], 0, 1))
    tris = np.concatenate(tris)
    cols = np.concatenate(cols)

    v = tris.reshape(-1, 3)
    ctr = 0.5 * (v.min(0) + v.max(0))
    ext = v.max(0) - v.min(0)
    span = ext.max() * 0.56
    # Frame on the real proportions, floored at 35 % so a flat part (a coupon,
    # a disc) does not degenerate into a sliver.  A fixed cube wastes most of
    # the canvas on anything that is not roughly cubic.
    aspect = np.maximum(ext / ext.max(), 0.35)

    fig = plt.figure(figsize=(4 * len(views), 4.4), dpi=dpi, facecolor="#F4F4F2")
    for i, spec in enumerate(views):
        name, az = spec[0], spec[1]
        elev = spec[2] if len(spec) > 2 else 8
        ax = fig.add_subplot(1, len(views), i + 1, projection="3d")
        ax.add_collection3d(Poly3DCollection(tris - ctr, facecolors=cols,
                                             edgecolors="none", zsort="average"))
        ax.set_xlim(-span, span); ax.set_ylim(-span, span); ax.set_zlim(-span, span)
        ax.set_box_aspect(tuple(aspect))
        ax.view_init(elev=elev, azim=az)
        ax.set_axis_off()
        ax.set_title(name, fontsize=11, color="#333", pad=-8)
    if title:
        fig.suptitle(title, fontsize=11, color="#555", y=0.99)
    fig.savefig(out_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print("wrote", out_path)
    return out_path
