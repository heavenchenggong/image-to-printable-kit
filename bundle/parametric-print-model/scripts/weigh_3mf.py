#!/usr/bin/env python3
"""Weigh a 3MF with the slicer's own kernel — the only number that counts.

    python weigh_3mf.py a.3mf b.3mf ...        [--material PETG]
                                               [--density 1.25]
                                               [--printer ...] [--process ...]
                                               [--filament ...]
                                               [--bed "Textured PEI Plate"]

## Why you cannot compute this from the mesh

Filament use is NOT `volume x some fill factor`.  The fill factor depends on
how much of the cross-section the perimeters and the top/bottom skin already
eat, which explodes on small parts:

    Ø16 x 16 solid boss   2 perimeters around a Ø16 circle are 1.7 mm of wall
                          out of an 8 mm radius -> the skin closes the rest.
                          Measured: ~67% of the solid volume.
    thin 2.5 mm plate     walls + skin fill the whole thickness -> ~100%.
    big hollow ball       mostly infill -> ~20%.

So one factor is always wrong by 2x somewhere.  Measured on the Winston coupon:
a 0.30 factor predicted 7 g; the slicer said 17 g.  **That is a 2.4x miss on a
deliverable the user is about to spend plastic on.**

Add up the E values in the gcode and you get the extruded volume exactly — no
guessing.  With the presets below a plate slices in a couple of seconds.

## THE TRAP: `--slice` without presets hangs forever

`--slice` on its own stalls at `Initializing StaticPrintConfigs` and NEVER
comes back: no gcode, no error, exit code 0, the process just sits there until
you kill it.  It is easy to misread that as "the GUI is holding the
single-instance lock" — that was our first diagnosis and it was WRONG; the same
stall happens with the GUI long gone.  The real cause is the missing printer
preset.  Pass machine + process and a filament and the identical call returns
`plate_1.gcode` in ~2 s:

    --load-settings "<machine>.json;<process>.json"
    --load-filaments "<filament>.json"

⚠️ That line is **not** a diagnostic.  The CLI writes exactly one line to
stdout/stderr and we SIGKILL it before it can flush anything more, so a
**successful** run's log ends on the very same `Initializing StaticPrintConfigs`
line (measured: 92 bytes either way).  Reading the log to decide what went
wrong is a dead end; check the inputs instead.

## AND THE ONE THAT LOOKS IDENTICAL: a refusal also hangs

If the plate and the filament disagree (PETG on a Cool Plate) the slicer emits
its reason, writes `result.json` next to the workdir --

    {"error_string": "Filaments are not compatible with the plate type.",
     "return_code": -61}

-- and then DOES NOT EXIT.  Externally a refusal and a stall look exactly the
same, so fixing the trap above does not cover this one.  Poll `result.json`:

    err = read_result_error(workdir)     # judges return_code, not error_string

⚠️ Judge it by `return_code`.  A **successful** run also fills `error_string`
in, with the literal string `"Success."` -- testing its truthiness makes every
good slice report `refused: Success.`.  (And `slice.log` is useless here: it is
one line, 92 bytes, byte-identical on success and on failure.)  With this poll
an unsupported plate fails in **1.2 s** instead of hitting the 180 s timeout.

`--bed` therefore MUST name the plate actually on the machine: the preset
default is Cool Plate, which the slicer refuses outright for PETG.

Defaults target the Bambu Lab X2D / 0.4 nozzle / 0.20 mm Standard, resolved
from the app bundle's own profiles; the filament follows `--material`.

Note also that `ps` may be denied by a sandbox; the GUI probe therefore uses
`lsof`, and it is only a warning — it no longer blocks anything.

## THE OTHER TRAP: the density is in the `inherits` chain, and the CLI ignores it

Hand a filament preset straight to the CLI and it does NOT resolve `inherits`:

    --load-filaments "Bambu PETG Basic @BBL X2D 0.4 nozzle.json"
    -> ; filament_density: 0        ; total filament weight [g]: 0.00

so the project reports **0 g**.  The fields that do come through (nozzle temp,
fan, flow ratio) are the ones you would eyeball, so this hides well.

And never hand-type the number either, because the nearest definition wins and
it is not the one you would guess:

    Bambu PETG Basic @base      1.25   <- the one the GUI uses
      fdm_filament_pet          1.27   <- the category value, NOT used
    Bambu PLA Basic @base       1.26
      fdm_filament_pla          1.24

`RHO` below is only a last-resort fallback; `resolve_density()` walks the chain
and that is what gets used and reported.

## What is and is not trustworthy

Whether we ship print settings or not, the CLI slices with the preset you hand
it — by default the X2D 0.20 mm Standard / 2 walls / 20% infill one.  Therefore:

* **extruded volume / filament length is trustworthy** (within ~1%: measured
  5.66 m by this script vs 5.71 m in the user's X2D PETG slice of the same file)
* **the gram figure is only as good as the density** — now resolved from the
  preset chain, but the user's own re-spooled profile will still move it a bit

Report it as "≈N g at <profile>" or, better, tell the user to read the slicer.
Never present it as exact.
"""
import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

CLI = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
DIA = 1.75

# Preset resolution lives in ONE place, shared with bake_project.py -- if the
# baker flattens a preset differently from the weigher, the delivered file and
# the delivered gram figure disagree and the user holds two truths.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from presets import (BBL as PROFILES, D_PRINTER, D_PROCESS, RHO,  # noqa: E402
                     find_preset, preset_args, read_result_error,
                     resolve_density)


def gui_running():
    """True if a Bambu Studio process is up.  A warning, not a blocker.

    `ps` is refused in some sandboxes ("operation not permitted"), which used
    to make this probe silently useless; `lsof` is allowed.  It no longer gates
    slicing — the old "the GUI blocks the CLI" story was really the missing
    presets — so callers only print a note.
    """
    try:
        out = subprocess.run(["lsof", "-c", "Bambu"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return False
    return bool(out.strip())


def slice_plate(path, workdir, cli=CLI, timeout=180.0, presets=None):
    """Slice with Bambu's kernel -> gcode path.

    Presets are mandatory: see the module docstring.  The CLI is a GUI binary
    that never exits, so: start it in its own session, poll for the gcode to
    appear and stop growing, then kill the whole group.  macOS has no
    `timeout`, so the deadline is enforced here.
    """
    if presets is None:
        presets = preset_args(tempfile.mkdtemp(prefix="weigh_presets_"))[:2]
    settings, filaments = presets
    os.makedirs(workdir, exist_ok=True)
    log = open(os.path.join(workdir, "slice.log"), "wb")
    proc = subprocess.Popen([cli,
                             "--load-settings", settings,
                             "--load-filaments", filaments,
                             "--slice", "1",
                             "--export-3mf", "sl.3mf",
                             "--outputdir", workdir, path],
                            stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    gcode, last, stable, t0 = None, -1, 0, time.time()
    failed = None
    try:
        while time.time() - t0 < timeout:
            time.sleep(1.0)
            # THE validation failure is written to result.json and the process
            # then just SITS THERE.  Read it and fail in ~2 s instead of
            # burning the whole timeout.
            failed = read_result_error(workdir)
            if failed:
                break
            cand = sorted(glob.glob(os.path.join(workdir, "plate_*.gcode")))
            if not cand:
                continue
            gcode = cand[0]
            size = os.path.getsize(gcode)
            if size > 0 and size == last:
                stable += 1
                if stable >= 2:            # unchanged across two polls = done
                    break
            else:
                stable = 0
            last = size
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except Exception:
            pass
        log.close()
    if failed:
        raise RuntimeError(f"the slicer refused this job: {failed}\n"
                           f"  (it printed that reason and then HUNG instead of\n"
                           f"  exiting -- that is why a rejection looks like a\n"
                           f"  timeout.  result.json in {workdir} is the real log.)")
    if gcode is None or os.path.getsize(gcode) == 0:
        raise RuntimeError(
            f"no gcode after {timeout:.0f}s — see {workdir}/slice.log\n"
            "  NB: that log is ONE line and is IDENTICAL on success and failure\n"
            "  (the process is SIGKILLed before it flushes anything else), so it\n"
            "  cannot tell you why.  A successful run's log looks exactly like this.\n"
            "  Things actually worth checking, in order:\n"
            "   1. did --load-settings/--load-filaments get through?  Without them\n"
            "      `--slice` stalls forever on that same line.\n"
            "   2. does the file have printable objects on plate 1?\n"
            "   3. is it a project 3MF (Metadata/project_settings.config present)?\n"
            "      We have seen one archived project file run to full timeout for\n"
            "      reasons never explained -- that is NOT a reason to distrust the\n"
            "      fresh ones.  Re-bake it (bake_project.py) and try again.")
    return gcode


def extruded_mm(gcode):
    """Total filament pushed out, in mm, straight from the toolpath.

    Relative extrusion (M83): retractions are negative and are repaid by the
    unretract, so the plain sum is the real figure.
    """
    tot = 0.0
    with open(gcode, errors="ignore") as fh:
        for line in fh:
            if not line.startswith(("G1", "G2", "G3")):
                continue
            for m in re.finditer(r"[Ee](-?\d*\.?\d+)", line):
                tot += float(m.group(1))
    return tot


def weigh(path, density=1.24, cli=CLI, workdir=None, keep=False, quiet=False,
          presets=None):
    """-> (filament_mm, volume_mm3, grams).  Slices the plate to find out."""
    tmp = workdir or tempfile.mkdtemp(prefix="weigh_")
    try:
        gcode = slice_plate(path, tmp, cli=cli, presets=presets)
        mm = extruded_mm(gcode)
    finally:
        if not keep and workdir is None:
            shutil.rmtree(tmp, ignore_errors=True)
    mm3 = mm * math.pi * (DIA / 2.0) ** 2
    if not quiet:
        print(f"  {os.path.basename(path):38s} {mm / 1000:6.2f} m  "
              f"{mm3 / 1000:6.2f} cm3  {mm3 / 1000 * density:6.1f} g")
    return mm, mm3, mm3 / 1000.0 * density


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--material", default="PLA",
                    help="picks both the fallback density and the filament "
                         "preset; overridden by --density / --filament")
    ap.add_argument("--density", type=float, default=None,
                    help="overrides both --material and the preset chain")
    ap.add_argument("--cli", default=CLI)
    ap.add_argument("--printer", default=D_PRINTER,
                    help="machine preset name, no .json")
    ap.add_argument("--process", default=D_PROCESS,
                    help="process preset name, no .json")
    ap.add_argument("--filament", default=None,
                    help="filament preset name, no .json; default follows "
                         "--material on --printer's nozzle")
    ap.add_argument("--bed", default="Textured PEI Plate",
                    help="curr_bed_type.  MUST be set: the preset default is "
                         "Cool Plate, which the slicer refuses for PETG "
                         "('Cool Plate does not support filament 1')")
    ap.add_argument("--keep", action="store_true",
                    help="keep the gcode next to the model")
    a = ap.parse_args(argv)

    # Flattened presets, written once and reused for every file: the CLI only
    # resolves `inherits` for some fields (see presets.py), so handing it the
    # raw files silently gives a 45 C bed and a 1.0 flow ratio.
    preset_dir = tempfile.mkdtemp(prefix="weigh_presets_")
    settings, filaments, info = preset_args(
        preset_dir, printer=a.printer, process=a.process, bed=a.bed,
        filament=a.filament, material=a.material)
    presets = (settings, filaments)
    rho = a.density or info["density"]
    src = "the preset chain"
    if a.density:
        src = "--density"
    if a.material.upper() not in info["filament_stem"].upper():
        print(f"  note: slicing with '{info['filament_stem']}', not a "
              f"{a.material} profile.", file=sys.stderr)
    if gui_running():
        print("  note: a Bambu Studio window is open; slicing continues "
              "anyway (presets, not the GUI, are what make --slice work).",
              file=sys.stderr)
    print(f"  {'file':38s} {'filament':>10s} {'volume':>10s} "
          f"{'mass':>8s}   (rho {rho} from {src})")
    total = 0.0
    for f in a.files:
        total += weigh(f, rho, cli=a.cli, keep=a.keep, presets=presets)[2]
    if len(a.files) > 1:
        print(f"  {'TOTAL':38s} {'':10s} {'':10s} {total:6.1f} g")
    print(f"\n  Sliced with Bambu's own presets, flattened: "
          f"{a.printer} + {a.process}\n"
          f"  + {info['filament_stem']}  ({info['type']}, rho {rho} g/cm3)\n"
          "  Volume is trustworthy to ~1%; grams follow the density. The "
          "user's own profile\n  will differ — tell them to read the slicer "
          "for the number that counts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
