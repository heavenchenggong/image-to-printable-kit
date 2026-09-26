#!/usr/bin/env python3
"""Bake geometry-only 3MFs into self-contained Bambu project 3MFs.

    python bake_project.py --brim "kit_P2.3mf=瞳孔" kit_P1.3mf kit_P2.3mf ...

WHY
    A 3MF that carries only geometry (3D/3dmodel.model + Metadata/
    model_settings.config) makes Bambu Studio greet you with
    "3mf 文件配置无效，仅加载几何数据" -- there is simply no
    Metadata/project_settings.config inside.  Hand-writing that file is
    fragile (version string, preset ids, 569 keys); the trick is to let the
    slicer write it.  One pass of

        BambuStudio --load-settings <machine>;<process> --load-filaments <fil>
                    --slice 1 --export-3mf out.3mf --outputdir <dir> in.3mf

    yields a full project: project_settings.config, model_settings.config,
    the plate gcode, thumbnails and slice_info.  It opens silently and is
    already sliced.

WHAT YOU CAN FORCE IN
    --bed           e.g. "Textured PEI Plate"   (PETG wants 45 C, not the
                                                35 C of the preset default)
    --fan-layers    e.g. 3   close_fan_the_first_x_layers -- the usual cure
                             for a torn first layer
    --filament      a Bambu filament preset name
    --brim          per-object outer_only brim, as "FILE.3mf=NAME_SUBSTRING"
    --support       per-object tree support, same syntax -- for a part whose
                    pose cannot be rescued (see inject_support)

IT REFUSES TO DELIVER A PLATE THE SLICER COMPLAINS ABOUT
    Every plate is re-cut and its `result.json -> sliced_plates[0]
    .warning_message` is read back.  That string is the exact text the GUI
    puts in its orange bar ("It seems object 07 手臂L has floating regions.
    Please re-orient the object or enable support generation.").  A
    compliant file is replaced; a flagged one is left alone and the run
    exits 1.  Re-baking an unchanged plate is the normal way to ask "is this
    still clean?".

GOTCHAS THAT COST A RUN EACH
    * `--slice` WITHOUT --load-settings hangs forever on
      "Initializing StaticPrintConfigs": no error, no gcode, exit code 0,
      process never exits.  It looks exactly like a single-instance lock
      held by the GUI; it is not.
    * `--export-3mf` takes a NAME, not a path -- the CLI joins it onto
      --outputdir, so an absolute path becomes "<outputdir>/<abs path>" and
      the export dies with "Unable to open the file".
    * Never rmtree the directory this script runs in: the child's cwd then
      points at a deleted inode and the slicer exits with "Could not
      determine canonical path to application directory".
    * A filament preset's `inherits` chain is NOT fully resolved by the CLI:
      loading "Bambu PETG Basic @BBL X2D" alone leaves filament_type = PLA
      and filament_density = 0, so the project reads 0 g of filament -- the
      gcode footer literally says "total filament weight [g]: 0.00".  The
      values that drive the toolpaths (fan, temperature, flow) do come
      through, which is what makes it easy to miss.  resolve_filament() walks
      the chain for the two fields that are missing.
    * Do not hand-type the density either.  The nearest definition in the chain
      wins and it is not the category value: Bambu PETG Basic @base says 1.25
      (over fdm_filament_pet's 1.27), Bambu PLA Basic @base says 1.26 (over
      fdm_filament_pla's 1.24).  A hand-typed 1.27 is a 1.6% lie in the file
      the user will read filament off.
    * Splice per-object metadata AFTER the whole tag.  Replacing only the
      matched substring leaves `<metadata key="name" value="x" <metadata
      .../>`, which is not well-formed XML; the slicer then silently drops
      every object name and you get Object_3, Object_4 ... in the output.
    * result.json IS WRITTEN LAST -- after baked.3mf and the gcode.  Killing
      the CLI the moment baked.3mf appears (which you must do, it never exits)
      can beat the verdict to disk.  A missing result.json then reads as
      "no complaint" because `not None` is True, and a plate that was never
      judged sails straight into the delivery directory.  slice_one() now
      keeps polling for it, and read_warning() returns None -- a third state
      -- so the gate can tell "passed" from "never spoke".
    * Only the FIRST offender is reported.  A clean plate is trustworthy; a
      dirty one under-reports, so fixing the named part may just reveal the
      next one (that is exactly how 06 耳鳍 surfaced after 05 犄角 was
      supported).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import presets as core  # noqa: E402  (single source for preset resolution)

BBL = core.BBL
CLI = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
OBJ_BLOCK = re.compile(r'<object id="(\d+)">(.*?)</object>', re.S)


def resolve_filament(stem):
    """Deprecated shim — the real one lives in presets.py (single source)."""
    return core.resolve_filament(stem, root=BBL)


def make_presets(machine, process, filament, bed, fan_layers, ftype, density, out_dir):
    """Flattened preset copies carrying our deviations.

    Delegates to presets.write_presets so the baker and the weigher cannot
    drift apart: if they resolve a preset differently, the delivered file and
    the delivered gram figure disagree.
    """
    info = core.write_presets(out_dir, printer=machine, process=process,
                              filament=filament, bed=bed,
                              fan_layers=fan_layers,
                              filament_type=ftype, density=density, root=BBL)
    return info["paths"]["machine"], info["paths"]["process"], info["paths"]["filament"]


def inject_brim(ms, needle, width):
    """Per-object outer_only brim.  Idempotent, and keeps the XML well formed."""
    hits = []

    def repl(m):
        oid, body = m.group(1), m.group(2)
        tag = re.search(r'<metadata key="name" value="[^"]*"\s*/>', body)
        if not tag:
            return m.group(0)
        whole = tag.group(0)
        nm = re.search(r'value="([^"]*)"', whole).group(1)
        if needle not in nm:
            return m.group(0)
        if 'key="brim_type"' in body:
            hits.append(f"{nm} (already)")
            return m.group(0)
        hits.append(nm)
        extra = (whole +
                 f'\n    <metadata key="brim_type" value="outer_only"/>'
                 f'\n    <metadata key="brim_width" value="{width}"/>')
        return f'<object id="{oid}">' + body.replace(whole, extra, 1) + '</object>'

    return OBJ_BLOCK.sub(repl, ms), hits


def inject_support(ms, needle, kind="tree(auto)", angle="30"):
    """Per-object tree support, for a part whose pose cannot be rescued.

    A bent tube (05 犄角) is the canonical case: cutting 240 candidate
    orientations and letting the slicer judge each one produced exactly ONE
    accepted pose -- standing the part 45.6 mm tall, which is neither stable
    nor quick.  With support on, the warning clears at the short, stable pose
    you actually want to print.

    Keep the process-wide enable_support at 0: this writes the flag onto the
    one object that needs it, so the other parts on the plate stay
    support-free.

    The splice has the same trap as inject_brim -- replacing only the matched
    substring breaks the XML and the slicer drops EVERY object name.
    """
    hits = []

    def repl(m):
        oid, body = m.group(1), m.group(2)
        tag = re.search(r'<metadata key="name" value="[^"]*"\s*/>', body)
        if not tag:
            return m.group(0)
        whole = tag.group(0)
        nm = re.search(r'value="([^"]*)"', whole).group(1)
        if needle not in nm:
            return m.group(0)
        if 'key="enable_support"' in body:
            hits.append(f"{nm} (already)")
            return m.group(0)
        hits.append(nm)
        extra = (whole +
                 '\n    <metadata key="enable_support" value="1"/>'
                 f'\n    <metadata key="support_type" value="{kind}"/>'
                 f'\n    <metadata key="support_threshold_angle" value="{angle}"/>')
        return f'<object id="{oid}">' + body.replace(whole, extra, 1) + '</object>'

    return OBJ_BLOCK.sub(repl, ms), hits


def read_warning(wd):
    """The slicer's verdict on floating regions.  THREE states, not two.

        ""     -> the slicer spoke and had no complaint   (deliverable)
        "..."  -> the slicer spoke and named an offender  (do NOT deliver)
        None   -> the slicer NEVER SPOKE (no result.json) -- not a pass

    The None state is the dangerous one: result.json is written after
    baked.3mf, so a poll loop that kills the CLI as soon as the geometry lands
    can lose the verdict, and callers that test truthiness let it through.
    Always test `is not None`.
    """
    p = f"{wd}/result.json"
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p))
    except Exception:
        return None
    if not d.get("sliced_plates"):
        return None
    return d["sliced_plates"][0].get("warning_message", "") or ""


def slice_one(src, wd, presets, timeout=400, settle=45):
    """Slice one plate -> (ok, seconds).  Fails fast on a refused job.

    Stops polling at result.json, NOT at baked.3mf: the verdict is written
    last, and killing the process group (which you have to do -- the CLI is
    the GUI binary and never exits) can otherwise win the race and leave no
    verdict on disk at all.  `settle` bounds the extra wait.
    """
    os.makedirs(wd, exist_ok=True)
    mp, pp, fp = presets
    log = open(f"{wd}/slice.log", "wb")
    p = subprocess.Popen(
        [CLI, "--load-settings", f"{mp};{pp}", "--load-filaments", fp,
         "--slice", "1", "--export-3mf", "baked.3mf", "--outputdir", wd, src],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=wd)
    t0 = time.time()
    t_baked = None
    while time.time() - t0 < timeout:
        time.sleep(1)
        if t_baked is None and os.path.exists(f"{wd}/baked.3mf"):
            t_baked = time.time()
        # The workdir was rmtree'd at startup, so a result.json here is ours.
        if t_baked is not None and os.path.exists(f"{wd}/result.json"):
            break
        if t_baked is not None and time.time() - t_baked > settle:
            break
        # A refused job (e.g. plate/filament mismatch) is written to result.json
        # and the process then HANGS rather than exiting -- check for it or you
        # wait out the whole timeout and never learn the reason.
        err = core.read_result_error(wd)
        if err:
            print(f"   !! the slicer refused it: {err}")
            break
        if p.poll() is not None:
            break
    log.close()
    try:
        os.killpg(os.getpgid(p.pid), 9)
    except Exception:
        pass
    return os.path.exists(f"{wd}/baked.3mf"), time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="geometry-only 3MFs, baked in place")
    ap.add_argument("--machine", default="Bambu Lab X2D 0.4 nozzle")
    ap.add_argument("--process", default="0.20mm Standard @BBL X2D")
    ap.add_argument("--filament", default="Bambu PETG Basic @BBL X2D 0.4 nozzle")
    ap.add_argument("--filament-type", default=None,
                    help="default: resolved from the preset's inherits chain")
    ap.add_argument("--density", default=None,
                    help="g/cm3; default: resolved from the preset's inherits "
                         "chain -- do NOT hand-type 1.27 for PETG Basic, the "
                         "preset says 1.25")
    ap.add_argument("--bed", default="", help='e.g. "Textured PEI Plate"')
    ap.add_argument("--fan-layers", default="", help="close_fan_the_first_x_layers")
    ap.add_argument("--brim", action="append", default=[],
                    metavar="FILE.3mf=NAME_SUBSTRING",
                    help="per-object 5 mm outer_only brim; repeatable")
    ap.add_argument("--brim-width", default="5")
    ap.add_argument("--support", action="append", default=[],
                    metavar="FILE.3mf=NAME_SUBSTRING",
                    help="per-object tree support for a part whose pose cannot "
                         "be rescued; repeatable. Process-wide enable_support "
                         "stays 0 -- only the named objects get it")
    ap.add_argument("--support-type", default="tree(auto)")
    ap.add_argument("--support-angle", default="30",
                    help="support_threshold_angle for --support objects")
    ap.add_argument("--work", default="/tmp/bake_project_out")
    a = ap.parse_args()

    rtype, rdensity = core.resolve_filament(a.filament, root=BBL)
    if a.filament_type is None:
        if rtype is None:
            raise SystemExit("could not resolve filament_type; pass it "
                             "explicitly with --filament-type")
        a.filament_type = rtype
    if a.density is None:
        if rdensity is None:
            raise SystemExit("could not resolve filament_density; pass it "
                             "explicitly with --density")
        a.density = rdensity

    brim_map = {}
    for spec in a.brim:
        if "=" not in spec:
            raise SystemExit(f"--brim wants FILE=SUBSTRING, got {spec!r}")
        fn, needle = spec.split("=", 1)
        brim_map.setdefault(os.path.basename(fn), []).append(needle)

    sup_map = {}
    for spec in a.support:
        if "=" not in spec:
            raise SystemExit(f"--support wants FILE=SUBSTRING, got {spec!r}")
        fn, needle = spec.split("=", 1)
        sup_map.setdefault(os.path.basename(fn), []).append(needle)

    preset_dir = "/tmp/bake_project_presets"
    presets = make_presets(a.machine, a.process, a.filament, a.bed,
                           a.fan_layers, a.filament_type, a.density, preset_dir)
    shutil.rmtree(a.work, ignore_errors=True)
    os.makedirs(a.work, exist_ok=True)

    # The delivered file IS a project 3MF, so the geometry-only original must
    # live elsewhere or the second bake would feed the slicer its own output.
    geo_src = os.path.join(os.path.dirname(os.path.abspath(a.files[0])), "geo_src")
    os.makedirs(geo_src, exist_ok=True)

    print(f"machine {a.machine} | bed {a.bed or '(preset default)'}")
    print(f"process {a.process} | close_fan_the_first_x_layers {a.fan_layers or '(default)'}")
    print(f"filament {a.filament}")
    print(f"         type {a.filament_type}, density {a.density} g/cm3"
          f"{'  (resolved from the preset chain)' if rdensity == a.density else ''}\n")

    ok_all = True
    for src in a.files:
        name = os.path.basename(src)
        pure = os.path.join(geo_src, name)
        wd = os.path.join(a.work, name[:-4])
        print(f"===== {name}")
        if not os.path.exists(pure):
            if not os.path.exists(src):
                print("   missing, skip")
                ok_all = False
                continue
            shutil.copy(src, pure)

        zin = zipfile.ZipFile(pure)
        items = [(i.filename, zin.read(i.filename)) for i in zin.infolist()]
        zin.close()
        ms = dict(items)["Metadata/model_settings.config"].decode("utf-8")
        for needle in brim_map.get(name, []):
            ms, hits = inject_brim(ms, needle, a.brim_width)
            print(f"   brim {a.brim_width}mm -> {hits or 'NOT FOUND: ' + needle}")
        for needle in sup_map.get(name, []):
            ms, hits = inject_support(ms, needle, a.support_type, a.support_angle)
            print(f"   {a.support_type} support -> {hits or 'NOT FOUND: ' + needle}")
        try:
            ET.fromstring(ms)
        except Exception as e:
            print(f"   !! model_settings.config is not well-formed, aborting: {e}")
            ok_all = False
            continue

        os.makedirs(wd, exist_ok=True)
        tmp = f"{wd}/_in.3mf"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
            for n, d in items:
                zo.writestr(n, ms.encode("utf-8")
                            if n == "Metadata/model_settings.config" else d)

        ok, secs = slice_one(tmp, wd, presets)
        if not ok:
            tail = open(f"{wd}/slice.log", errors="ignore").read()[-400:]
            print(f"   FAILED in {secs:.0f}s: {tail}")
            ok_all = False
            continue

        z = zipfile.ZipFile(f"{wd}/baked.3mf")
        names = set(z.namelist())
        ps = json.loads(z.read("Metadata/project_settings.config").decode("utf-8"))
        msl = z.read("Metadata/model_settings.config").decode("utf-8")
        objs = re.findall(r'<metadata key="name" value="([^"]*)"\s*/>', msl)
        warning = read_warning(wd)
        # `is not None`, NOT truthiness: a missing verdict must fail the gate
        # exactly like a complaint, or an un-judged plate gets delivered.
        judged = warning is not None
        good = ("Metadata/project_settings.config" in names
                and any(n.endswith(".gcode") for n in names)
                and ps.get("filament_density") == [str(a.density)]
                and ps.get("filament_type") == [a.filament_type]
                and judged and not warning)
        print(f"   {'OK ' if good else 'CHECK'} {secs:4.0f}s  "
              f"fan={ps.get('close_fan_the_first_x_layers')}  "
              f"bed={ps.get('curr_bed_type')}  "
              f"fil={ps.get('filament_type')}/{ps.get('filament_density')}  "
              f"brim={'yes' if 'brim_type' in msl else 'no'}")
        print(f"        {' | '.join(sorted(set(objs)))}")
        if not judged:
            print("        !! no verdict from the slicer (result.json missing) "
                  "-- treating as NOT passed")
        elif warning:
            print(f"        !! the slicer complains: {warning}")
        if good:
            shutil.copy(f"{wd}/baked.3mf", src)
        else:
            ok_all = False

    print(f"\ngeometry-only snapshots (next bake's input): {geo_src}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
