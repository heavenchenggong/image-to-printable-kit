# Case Log: Winston Mascot Kit (2026-09)

The first case to run this pipeline end to end. X2D dual nozzle, PETG, glue-free snap-fit across three plates (P1 green / P2 dark / P3 accents), 10 parts. Below are the pitfalls actually hit — the kind you only learn by hitting — archived by stage.

## Modeling / Part Splitting

- **A peg's seat sitting exactly on the mating face = boolean union silently drops the connection**. When two solids share a face, the `union` result can be two solids merely "touching"; all pegs/posts/bosses must keep a 2–2.5 mm embedded section.
- Coplanar booleans (neck opening and skirt bottom both at z=21) fail silently too — same root cause as above.
- Split by "connected solid", not by color shell; one color's shell splits into a dozen floating islands.

## Orientation

- Convex-hull grounded area errs in both directions (153 vs real 18.4, 8× inflated; 1 vs real 4.35, 4× deflated).
- Horns (curved tube): only 1 of 240 poses passed, and it stood 45.6 mm tall → impractical; kept the low stable pose +
  object-level tree support. Ear fins (sharp tip): 106 of 240 passed, but the best solution (23.44 mm²) was not on
  the 240-point sphere sampling — it came from an offline fine search. When black-box sampling density is insufficient, remember to search offline too.
- The first version of `orient="face"` treated dirvec as a rack-frame absolute direction → rotated twice, 16.1 mm tall, still warned.

## Scrap Classification (Four Classes, Gate Coverage Matrix)

| # | Scrap mode | Caught by |
|---|---|---|
| ① | First-layer point contact (ear fin 1.33 mm²) | Slicer G1 |
| ② | Mid-body overhang for ≥2 consecutive layers (arm 9.4 mm³) | Slicer G1 |
| ③ | Large flat-surface edge lift | warp.py G4 |
| ④ | **Single-layer horizontal step at mid-part (base +406 mm²)** | **Only overhang.py G3** — slicer doesn't warn (material connected above and below, not an island), not the first layer, warp doesn't look |

Class ④ is the only scrap "invisible to all three gates"; on the physical print it shows up as a tangle of loose filaments around the step.

## Slicer / Gates

- `result.json` is written last; killing the process as soon as `baked.3mf` appears → no verdict → `not None` is true
  → a failed slice masquerades as a pass and overwrites deliverables. Fix: poll for `result.json`, three-state verdict.
- Only the first warning per plate is reported; `--orient` makes warnings lie.
- XML fragment appended in the wrong place → the slicer drops every object name (becomes Object_3…), the file still slices, extremely hard to notice.
- No `timeout` on macOS; the CLI never exits, poll + killpg; STLs must be passed as `abspath`.

## Verification and Delivery

- The user's photos of failed physical prints are the highest-weight evidence — the filament tangle located the step layer at z=21 exactly,
  retroactively confirming class-④ scrap exists.
- After delivery, every doc number (weights 31.9→32.5→32.6, total 81.2→81.8→83.1) changed with each fix round;
  grep the whole text for stale numbers, never believe "only one place changed".
- The P2 base shoulder fixed by another session was confirmed via "independent re-slice + independent overhang scan";
  the verifier always re-runs themselves, never reads the other party's result files.
- P1 re-scan used 2.5× stricter thresholds as a line check; 0 warnings before release (measured worst ΔA on the body 30.6 mm²,
  far below the incident layer's 406).

## Easy-Release Support (X2D Dual Nozzle)

- Bambu **Support for PLA/PETG** breakaway material: polarity difference means it doesn't bond to PETG, tears off by hand;
  official parameters top interface 0 / Z distance 0; RFID auto-configures.
- Tree support: support material **only for the "Support/raft interface"**, not for the base (official guidance,
  saves material and releases better); the tree trunk still uses base material.
- In the delivered 3mf files `enable_support` is already written; the user changes the interface material in the slicer and re-slices,
  no re-bake needed.

## Assembly Regression: Press-Fit Peg Fails on the Physical Print (2026-09-27)

- Physical feedback from the user: **arm would not go into the palm at all (peg had to be cut)**, pupils just barely off; ear fin had the same defect but didn't fail.
  This is a physical assembly veto of the design rule — all three print gates PASS and assembly still fails,
  because the gates all ask "can it print"; nobody asked "can it assemble".
- Two root causes, both detectable before printing:
  1. CAD's −0.10 interference measures ≈ 0.45 mm of diameter on an FDM print (hole prints small: extrusion squish + elephant foot;
     peg prints fat);
  2. **Peg longer than the hole**: the old `press_hole` used usable depth `length+0.8` vs `press_peg`'s
     `length+0.6`, end faces bottom out by 0.30/0.10 mm.
- Fix: all three joints unified to **slide-fit peg body + male-side elastic bead catch** (`joints.bead_peg`/`bead_hole`),
  elasticity still on the male side (cross slots + relief holes), just narrowed from "gripping an Ø10.6 ball" to "holding one +0.10 bead".
  Measured: volume clearance +0.03~0.15/side, bead interference 0.10~0.13/side, axial margin 0.45~0.95 mm,
  strain 1.36–1.70%.
- **New G6 assembly gate `mate_profile.py`**: radial (volume clearance / bead grip) + axial margin + strain,
  all four pass or FAIL. The axial rule was the true culprit this time — bottoming out is invisible in CAD.
- Incidental geometry fix: palm pad 7.5 → 8.5 mm (the original thickness couldn't fit the 6.8 mm hole depth; the hole would break out through the palm).
- Documentation lesson: the "Ø6.2 hole" written in the file did not match the exported Ø5.80 — **diameters must be measured from the mesh**;
  `mate_profile`/layer-by-layer cross-sections exist to do exactly that.
- Coupon v3: 8 steps × male/female = 16 loose parts (no shared base plate; both sides mark the step with through-holes),
  **sharing the same constants** as the final parts. Best-feel steps from hand testing: wrist 3 / pupils 6 / ear fins 7 (awaiting user read-back).
