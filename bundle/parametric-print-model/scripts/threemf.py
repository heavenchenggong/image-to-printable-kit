"""Write a multi-colour 3MF that Bambu Studio opens with names and AMS slots.

trimesh's own 3MF exporter carries no materials, so the XML is built by hand.
A 3MF is just a zip:

    [Content_Types].xml
    _rels/.rels
    3D/3dmodel.model                  <- one <object> per part, one colour each
    Metadata/model_settings.config    <- Bambu: object names + filament slots

## Two things that only showed up when tested against Bambu's own kernel

1. **Bambu Studio IGNORES `<basematerials>`.**  A 3MF carrying three base
   colours still imports as ONE filament — every object comes in the same
   colour, and the object names ("03_body") are gone too, replaced by
   Object_1..Object_N.  The only thing Bambu reads for per-object colour and
   naming is `Metadata/model_settings.config`, and its per-object `extruder`
   value is the **filament slot number, 1-based**.  So that is what we write.
   (`<basematerials>` is still emitted — OrcaSlicer and other tools read it.)

2. **Embedding `Metadata/project_settings.config` achieves nothing.**  Bambu
   keeps its own profile and discards the one in the file: after a round trip
   `filament_colour` came back as Bambu's default `#00AE42`, not ours.  So do
   not pretend to ship print settings.  Ship geometry + names + slot numbers,
   and let the user load the AMS in the documented order.

Consequence for the caller: `filaments` is not decoration.  It fixes the slot
ORDER, and slot 1 is whatever is in AMS slot 1 on the user's machine.  List the
colours in the order you want them loaded.

## Single-colour plates are a feature, not a compromise

For a **split kit**, give the user ONE filament slot per file — see
`splitter.write_colour_plates`.  A multi-colour plate costs a purge at every
colour change plus a prime tower, and a dual-nozzle machine refuses to slice at
all when the two spools are not the same temperature class:

    同时打印高温和低温材料可能导致喷嘴堵塞或打印机损坏
    (printing high- and low-temperature filament together may clog the nozzle)

One slot per file means no purge, no tower, no refusal — and it prints from a
spool holder with no AMS at all.  Pass `filaments=[(slug, hex)]` with a single
entry; every object then lands on slot 1.

## The "not from Bambu Lab" popup

A hand-written 3MF always opens with *"The 3mf is not from Bambu Lab, load
geometry data only"* (中文: 「3mf 文件配置无效，仅加载几何数据」).  It is an
identity notice, not an error — Bambu's own forum says to "consider it as
'information'".  **Adding members to our archive does not remove it**; the only
thing that works is letting Bambu rewrite the package:

    BambuStudio --export-3mf rt.3mf --outputdir /tmp/rt model.3mf

That takes the archive from 4 members to ~17 (project_settings.config, plate
thumbnails, plate layout) and the result opens quietly; object names and
extruder slots survive (verified).  Offer it as a second copy, not the default:
the wrapped file also carries Bambu's stock profile (0.2 mm / 2 walls / 20% /
auto brim), which the user then has to override.

## This file cannot tell you the mass

Filament use is not derivable from the mesh — see `weigh_3mf.py`, which slices
the plate and adds up the toolpath.  Measured on the Winston coupon: a mesh
estimate said 7 g, the slicer said 17 g.

## Acceptance test — run this after touching anything in here

    BambuStudio --info model.3mf                     # manifold / parts per object
    BambuStudio --export-3mf rt.3mf --outputdir /tmp/out model.3mf
    # then read Metadata/model_settings.config out of rt.3mf: the names and the
    # extruder numbers must survive the round trip.

The CLI is a GUI binary that never exits, and macOS has no `timeout`; launch it
in the background, sleep ~20 s, then kill it.

Those two subcommands need no presets.  `--slice` DOES — without
`--load-settings "<machine>.json;<process>.json"` and
`--load-filaments "<filament>.json"` it parks on
`Initializing StaticPrintConfigs` and never returns (no gcode, no error, exit
code 0).  It reads exactly like "the GUI is holding the single-instance lock";
it is not.  `weigh_3mf.py` passes the presets and finishes in ~2 s.
"""
import os
import re
import zipfile
import xml.etree.ElementTree as ET

import trimesh

NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""

MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
{objects}
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <metadata key="filament_map_mode" value="Auto For Flush"/>
{instances}
  </plate>
  <assemble>
  </assemble>
</config>
"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _mesh_xml(m):
    verts = "".join(f'<vertex x="{p[0]:.5f}" y="{p[1]:.5f}" z="{p[2]:.5f}"/>'
                    for p in m.vertices)
    tris = "".join(f'<triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>' for t in m.faces)
    return f"<mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh>"


def _norm(h):
    """"#rrggbb" -> "#RRGGBBFF" so a 6-digit and a 9-digit spelling match."""
    h = str(h).upper()
    return h if len(h) == 9 else h + "FF"


def _slots(shells, filaments):
    """Ordered unique filament list -> (list of (label, hex), slot per shell)."""
    order = [(str(l), _norm(c)) for l, c in (filaments or [])]
    known = [c for _, c in order]
    for _, hexcol, _ in shells:
        c = _norm(hexcol)
        if c not in known:
            known.append(c)
            order.append((f"Filament {len(order) + 1}", c))
    slot = {c: i + 1 for i, (_, c) in enumerate(order)}
    return order, [slot[_norm(hexcol)] for _, hexcol, _ in shells]


def _palette(shells):
    """Unique colours in first-appearance order -> ([(hex, first_label)], index)."""
    pal, idx = [], {}
    for name, hexcol, _ in shells:
        c = _norm(hexcol)
        if c not in idx:
            idx[c] = len(pal)
            pal.append((c, name))
    return pal, idx


def write_3mf(shells, out_path, title="parametric print model", validate=True,
              bambu=True, filaments=None):
    """shells: list of (name, hex_color, mesh) — one object per entry.

    name      label shown in the slicer's object list (Chinese is fine)
    hex_color "#RRGGBB" (alpha is appended automatically)
    filaments optional ordered [(label, hex), ...] fixing the AMS slot order;
              default is order of first appearance in `shells`.
    bambu     also write Metadata/model_settings.config (see module docstring).

    Returns the written path.  Raises if the archive fails to parse.
    """
    order, slots = _slots(shells, filaments)
    pal, pidx = _palette(shells)
    mats, objs, items = [], [], []
    for c, nm in pal:
        mats.append(f'<base name="PLA {_esc(nm)}" displaycolor="{c}"/>')
    for i, (name, hexcol, mesh) in enumerate(shells):
        m = mesh if isinstance(mesh, trimesh.Trimesh) else trimesh.util.concatenate(mesh)
        oid = 2 + i
        objs.append(f'<object id="{oid}" type="model" pid="1" '
                    f'pindex="{pidx[_norm(hexcol)]}">{_mesh_xml(m)}</object>')
        items.append(f'<item objectid="{oid}"/>')

    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             f'<model unit="millimeter" xml:lang="en-US" xmlns="{NS}">\n'
             '<metadata name="Application">ptools-exporter</metadata>\n'
             f'<metadata name="Title">{_esc(title)}</metadata>\n'
             f'<resources><basematerials id="1">{"".join(mats)}</basematerials>'
             f'{"".join(objs)}</resources>\n'
             f'<build>{"".join(items)}</build>\n</model>\n')

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model)
        if bambu:
            z.writestr("Metadata/model_settings.config",
                       _model_settings(shells, slots))

    if validate:
        _validate(out_path, len(shells), len(pal), bambu)
    return out_path


def _model_settings(shells, slots):
    objs, insts = [], []
    for i, (name, _hexcol, _mesh) in enumerate(shells):
        oid, pid = 2 + i, 2 * i + 1
        nm = _esc(name)
        objs.append(f'  <object id="{oid}">\n'
                    f'    <metadata key="name" value="{nm}"/>\n'
                    f'    <metadata key="extruder" value="{slots[i]}"/>\n'
                    f'    <part id="{pid}" subtype="normal_part">\n'
                    f'      <metadata key="name" value="{nm}"/>\n'
                    f'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                    f'    </part>\n  </object>')
        insts.append(f'    <model_instance>\n'
                     f'      <metadata key="object_id" value="{oid}"/>\n'
                     f'      <metadata key="instance_id" value="0"/>\n'
                     f'      <metadata key="identify_id" value="{oid * 4}"/>\n'
                     f'    </model_instance>')
    return MODEL_SETTINGS.format(objects="\n".join(objs),
                                 instances="\n".join(insts)).encode()


def _validate(path, expect, n_colours, bambu):
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        assert bad is None, f"corrupt 3MF member: {bad}"
        need = {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
        assert need <= set(z.namelist())
        root = ET.fromstring(z.read("3D/3dmodel.model"))
        msc = z.read("Metadata/model_settings.config").decode() if bambu else ""
    r = f"{{{NS}}}"
    n_obj = len(root.findall(f"{r}resources/{r}object"))
    n_item = len(root.findall(f"{r}build/{r}item"))
    n_base = len(root.findall(f"{r}resources/{r}basematerials/{r}base"))
    print(f"3MF ok: {os.path.getsize(path) / 1e6:.1f} MB, objects={n_obj} "
          f"items={n_item} colours={n_base}"
          + ("   (single-colour plate)" if n_base == 1 and expect > 1 else ""))
    assert (n_obj, n_item) == (expect, expect), \
        "3MF structure does not match the shell list"
    assert n_base == n_colours, "3MF palette does not match the colour list"
    if bambu:
        assert msc.count("<object id=") == expect, "model_settings: object count"
        assert msc.count('key="extruder"') == expect, "model_settings: extruder count"
        slots = set(re.findall(r'key="extruder" value="(\d+)"', msc))
        assert slots == {str(s) for s in range(1, len(slots) + 1)}, \
            f"model_settings: non-contiguous filament slots {sorted(slots)}"
