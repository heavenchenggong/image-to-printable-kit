---
name: image-to-printable-kit
description: End-to-end pipeline: from a raw image/reference picture through parametric modeling, color-split parts (three routes: single AMS plate / glue / glue-free snap-fit), per-part print orientation, slicing bake, producing a set of .3mf print files — one color per plate, ready to slice on open, with gcode and thumbnails, passing six quality gates — plus README / parts list / verification images. Trigger when the user asks to cover the full workflow: "turn this image into something printable", "make a complete printable set", "image → model → color split → print files", "make X into a glue-free snap-fit kit". Single-step requests (modeling only, splitting only, baking only) go straight to the parametric-print-model skill.
metadata:
  type: skill
  scope: global
  agent_created: true
---

# Image → Printable Kit: End-to-End Pipeline

## Positioning and Dependencies

This skill is an **orchestration-layer runbook**: it turns "one image" into a 3MF kit that is "one color per plate, ready to print on open". All tooling and deeper details live in the sibling skill **`parametric-print-model`** (path `~/.workbuddy/skills/parametric-print-model/`, the same copy soft-linked from `~/.claude/skills/`):

| Tool/Doc | Purpose |
|---|---|
| `scripts/splitter.py` | trimesh primitive modeling, booleans, `lay_flat`/`orient("face")`, ball-head joints |
| `scripts/joints.py` | Glue-free snap-fit joints (elastic collet male peg, **bead peg = slide-fit body + male-side bead catch**, legacy press-fit peg kept as fallback only) |
| `scripts/mate_profile.py` | **Assembly gate**: volume clearance / bead grip / axial margin / blade strain, measured layer by layer from the actual solids |
| `scripts/pose_brute.py` | Per-part orientation: offline ranking + slicer black-box verdict (~65 s for 240 poses) |
| `scripts/bake_project.py` | Slice bake → 3MF (embedded config + gcode + thumbnails), brim / object-level support, verdict gate |
| `scripts/overhang.py` | Layer-by-layer overhang scan (catches horizontal steps the slicer never warns about) |
| `scripts/warp.py` | Large-flat-surface edge-lift gate (`lift = span²/depth` square law) |
| `scripts/audit_3mf.py` / `weigh_3mf.py` | Connectivity audit / weight re-check in grams |
| `references/snap-fit.md` | Glue-free snap-fit design rules, four red lines, fit coupon |
| `references/split-to-print.md` | Orientation verdicts, `warning_message` criteria, convex hull trap |
| `references/warp.md` | Edge-lift criteria and redesign line |
| `references/printability-checklist.md` / `delivery.md` | Printability checklist / delivery spec |

Environment: use `~/.workbuddy/binaries/python/envs/default/bin/python` for python (numpy/trimesh/shapely/scipy/matplotlib/PIL all live there). Slicer = Bambu Studio CLI (the GUI binary, **never exits**, no `timeout` on macOS — poll for output then `killpg`).

## Pipeline Overview (Eight Stages, Each With an Exit Criterion)

| # | Stage | Exit criterion | Action on failure |
|---|---|---|---|
| 0 | Route freeze | Modeling route + split route decided and communicated to the user | — |
| 1 | Blueprinting | Parts list + interface list + color table written down | Align with the user, then freeze |
| 2 | Parametric modeling | Every part watertight, booleans valid, multi-view render check passes | Fix geometry, not slicer settings |
| 3 | Color split into plates | Every part assigned to exactly one plate; brim/support target tables written into the bake script | — |
| 4 | Per-part orientation | Every part passes the slicer black-box verdict or the warp gate | Change pose / add object-level support / redesign |
| 5 | Bake | Every plate has `baked.3mf` on disk and `result.json` verdict on disk | Fix inputs, not gates |
| 6 | Six gates | All PASS (see `references/gates.md`) | Handle gate by gate; skipping is forbidden |
| 7 | Doc sync | README / parts-list numbers match measured values | Re-grep all md files for stale numbers |
| 8 | Delivery | present_files + print notes (support material, part-removal technique) | — |

**Pacing rule**: advance one stage at a time; self-check each stage's output before showing it to the user. The modeling script is the single source of geometric truth (`snapkit_<name>.py`); every deliverable is regenerated from it — to change geometry, always change the script, never the STL.

## Stage 0: Route Freeze (Decide, Don't Default)

Both decisions are in the decision tables of `parametric-print-model/SKILL.md`; complete them before doing anything:

1. **Parametric modeling vs AI image-to-3D**: shapes buildable from spheres/capsules/cylinders go parametric; only fur/faces/organic surfaces go AI image-to-3D. When the user says "I want this printable", give the conclusion with the reasoning up front — don't interrogate requirements first.
2. **Single AMS plate / glue-assembled split / glue-free snap-fit**: three independent routes. "Color-split parts" = glue; "no glue / snap / can be taken apart" = snap-fit; if unspecified, route by "AMS available or not" and mention the other two variants exist.

Write the routing decision into the header of the project README; every later stage references it.

## Stage 1: Blueprinting (Turn the Image Into Numbers)

Extract from the image: three-view proportions, parts list (one connected printable solid per part), color table, interface list (what plugs into what, press fit or ball head). Produce a written "parts table" pasted into the header comment of the modeling script. Rules:

- **The unit of splitting is "a single connected printable solid"; color is just an attribute**. Never split by color shell — a given color's shell is often a dozen floating fragments.
- Interlocking parts share a plate or account for filament-change cost; one color per plate is the default layout.
- Interface dimensions follow the tables in `snap-fit.md` (ball-head spec, neck closure interference 0.30 mm; small parts use the **bead peg**: peg body slide fit +0.10~0.15 per side, bead +0.10 per side — **do not use negative-clearance interference press fits**, they measure as ~0.45 mm of interference on the actual print); **all pegs/posts get a 2–2.5 mm embedded section** (boolean union silently drops the connection when "the seat sits exactly on the mating face").

## Stage 2: Parametric Modeling

- Use the primitives of `splitter.py` (sphere/capsule/cylinder/cone/torus) + `P.union/cut` boolean composition; one `parts["NN_name"] = dict(name=<Chinese label>, colour=..., orient=..., mesh=...)` per part.
- After booleans, check each part for watertightness and connectivity (parse vertex indices yourself and build `Trimesh(process=False)` to test connectivity; **do not use `trimesh.load()` to judge** — its silent repair can fail a good part as fragments, see `audit_3mf.py`).
- Render multi-view previews with `preview.py` and **get manual user verification** before the next stage.
- Glue-free route: joints come from `joints.py`; the male peg is a slotted elastic collet (elasticity lives on the male side, not in the female hole); print a **fit coupon** first to set the interference before producing the full set — coupon steps are marked with through-holes, not bumps.

## Stage 3: Color Split Into Plates

- Plate = color; the `PLATES` table in `bake_project.py` defines each plate's filename and brim target parts; the `SUPPORT` table defines object-level support target parts.
- Write explicit brim only for parts with first-layer contact < ~30 mm²; enable object-level support only for parts with "no pose solution found by black-box search" (keep the process-global switch at 0).

## Stage 4: Per-Part Print Orientation

- Default `lay_flat`; for suspicious parts (curved tubes, sharp tips, large overhangs) run `pose_brute.py` — **offline metrics are for ranking only; the verdict is always the slicer's `warning_message`** (the black-box verdict is usually bimodal: solution exists → switch to `orient="face"`; no solution → object-level support).
- For `orient="face"`, `dirvec` is an **increment applied on top of lay_flat**, not a rack-frame absolute direction — feeding it wrong rotates twice (see `references/gates.md` §G2).
- Run every part through `warp.py`: `lift = span²/depth`; >1200 → redesign, don't tune parameters.
- **No slicer warning ≠ printable**: a horizontal single-layer step at mid-part (cross-section jumps in one step) is invisible to both the slicer and the first-layer gate; you must catch it with `overhang.py` (see `references/gates.md` §G3, class-④ scrap).

## Stage 5: Bake

`bake_project.py` bakes one plate per run: always pass `--load-settings` (otherwise it hangs), `--export-3mf` takes only names, and after polling `baked.3mf` **keep waiting until the `result.json` verdict lands** before `killpg`. Object-level brim/support XML fragments must be appended **after** the entire `<metadata key="name" .../>` tag (wrong position → invalid XML → the slicer drops every object name). Other pitfalls are in the script docstring (filament `inherits` chain, hand-filled density lying by 1.6%, single-instance-lock illusion, etc.).

## Stage 6: Six Gates

Run per plate; criteria and commands in `references/gates.md`. G1 slicer verdict (three-state, `None` ≠ pass) → G2 orientation/black-box → G3 layer-by-layer overhang scan → G4 edge lift → G5 connectivity and weight → **G6 assembly fit** (`mate_profile.py`: volume clearance / bead grip / axial margin / blade strain — an interface that bottoms out will never close no matter how loose, and CAD can't show it). Gate discipline:

- **Independent re-slice + independent scan**; never trust result files left behind by the pipeline.
- Gate code must treat "verdict not read" as failure (three-state handling), otherwise a failed slice masquerades as a clean pass and overwrites deliverables.
- Gates are code, not eyeballs; every gate is scriptable with an exit code.

## Stage 7: Doc Sync

Deliver three artifacts: `README.md` (human-facing usage doc), a design-decision document (parts table, failure log), verification images (before/after gate comparisons). **All measured numbers (weight in grams, plate footprint, time, support annotations) get backfilled from actual slicing measurements**; after changing one part, `grep` the whole text for stale numbers.

## Stage 8: Delivery and Print Notes

Deliver all 3mf files + verification images via `present_files`. Print notes always include: cut supports flush at the root; to tear tree supports off by hand, load Bambu **Support for PLA/PETG** breakaway material in the B extruder and select it as "Support/raft interface" in the slicer (the tree trunk still uses base material, **never use support material for the base**); assemble glue-free parts per the technique in `snap-fit.md`.
