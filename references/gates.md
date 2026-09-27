# Six Gates: Criteria, Commands, Discipline

Every gate is scriptable and has an exit code. All gates must PASS before delivery.
The `python` in every command is `~/.workbuddy/binaries/python/envs/default/bin/python`.

## G1 Slicer Verdict (Sole Authority)

Built into `bake_project.py`. The core is `result.json → sliced_plates[0].warning_message`:

- `""` (empty string) = pass; `"..."` = warning (the text is the original of the GUI's orange banner, naming objects);
- **`None` (file missing / never written) = no verdict = failure**. The three states must be strictly distinguished —
  killing the process as soon as `baked.3mf` appears misses the verdict (it is written last). If `not None` is treated as true,
  a slice that was never judged counts as "pass" and overwrites the deliverable. Delivered files must be **re-sliced
  independently** for verification; never trust the readout taken at bake time.
- The verdict covers **two independent rules**: ① first-layer contact area too small (point-contact type);
  ② floating volume over threshold for ≥2 consecutive layers (mid-body overhang type). A single floating layer = bridging, doesn't count.
- **Only the first warning per plate is reported**: after fixing one part, the warning moving to the next part is normal, not "still failing after the fix".
- `--orient` makes `warning_message` lie; the verdict is only valid for a plain `--slice 1`.

## G2 Orientation Verdict

- Offline metrics (first-layer contact, floating volume, convex-hull `footprint()`) **rank candidates only, never judge**:
  the convex-hull measure errs in both directions (measured: same part inflated 8× / deflated 4×; inflation can pass a blade-tip pose).
- The final call is the `pose_brute.py` slicer black box: a single 45 mm part takes ~1.4 s per pose; the full sphere of 240 poses
  across 8 processes takes ~65 s. The result is usually bimodal: solution exists → switch to `orient="face"` + `dirvec`;
  no solution → object-level tree support (`SUPPORT` table, `enable_support=1 / support_type=tree(auto) /
  support_threshold_angle=30`, keep the process-global `enable_support` at 0).
- **For `orient="face"`, `dirvec` is a relative quantity**: the candidate STL is already in `lay_flat` coordinates;
  the correct implementation is `base, T = lay_flat(mesh); upright(base, -dirvec)`. Treating it as a rack-frame absolute
  direction rotates twice (measured: landed at 16.1 mm tall instead of 10.7, and still warned).
- When reading reference tool output, beware "not finished writing yet": read `result.json` with retries + a `judged` flag.

## G3 Layer-by-Layer Overhang Scan (Catches Class-④ Scrap)

`overhang.py <plate or part> [--json out.json]`, computes per-layer cross-section jumps:

- It catches the scrap that **neither the slicer nor the first-layer gate reports**: a horizontal step at mid-part —
  the cross-section radius jumps from r1 to r2 within one layer, an entire ring floating (measured on Winston: +406 mm² in
  a single layer; printed, it comes out as a tangle of loose filaments around the neck opening). The fix is a 45° shoulder
  (embed each end of the frustum 3 mm into the adjacent solid), which also fixes silent coplanar boolean failure.
- Default thresholds `--area-warn 150 --span-warn 3.0 --darea-warn 25`; before delivery re-run at
  2.5× stricter (60 / 2.0 / 15) to confirm no part sits on the line.
- For hollow parts like `01 base` that take the `area-only` fallback path, `polygons_full` returns the cavity
  as an independent polygon — filter out polygons contained by a sibling
  polygon with `q.covers(p.representative_point())`, otherwise the inner-cavity ceiling gets falsely reported as exposed overhang.

## G4 Edge Lift

`warp.py`: `contact` (grounded convex-hull area) / `span` (longest span of the bottom face) /
`depth` (part volume ÷ grounded area, **never the bounding box or the vertex z range** — a cylinder gets estimated as a thin sheet).
Criterion `lift = span²/depth` (square law): ≤400 ok; 400–1200 → brim + fan off for the first 3 layers;
**>1200 → change the model, don't tune parameters**; `depth ≤ 4.5` thin sheets get forced brim. Exit codes work as a CI gate.

## G5 Connectivity and Weight

- Connectivity: `audit_3mf.py` parses vertex indices itself and builds `Trimesh(process=False)` (**don't use
  `trimesh.load()`**; its automatic repair can fail a good part as fragments); close the loop with the slicer's
  `--info` `number_of_parts` (each part should be 1; `<basematerials>` is not read by Bambu,
  color relies on the `extruder` slot in `Metadata/model_settings.config`).
- Weight: `weigh_3mf.py` compares the embedded `slice_info` prediction against the gcode footer; filament density
  must resolve through the `inherits` chain (a hand-filled density is a 1.6% lie).
- Before delivery, **independently re-slice** every 3mf (G1) + run an **independent overhang scan** (G3);
  run both yourself, never read intermediate pipeline artifacts.

## G6 Assembly Fit (Bead / Press-Fit Interfaces)

`python -m scripts.mate_profile` (parametric-print-model skill). Measures four things along the axis, layer by layer,
all taken from the built solids, never copied from parameters:

- **Volume clearance** > 0.02/side — the peg body must slide in;
- **Bead grip** ≥ 0.05/side — only the bead section interferes; the rest of the length must be a slide fit;
- **Axial margin** > 0.3 mm (hole bottom − peg tip) — **an interface that bottoms out will never close no matter how loose, and CAD can't show it**.
  The physical Winston failure "arm would not go into the palm at all, peg had to be cut" was a 5.60 peg forced into a 5.30 hole;
- **Blade strain** < 2% — beyond that the leaf's spring set fails and it stays loose after the first removal.

All four must pass, exit code usable as a gate. Two measured pitfalls:

- Boolean solids carry T-junctions; cross-section coordinates must be `np.round(..., 4)` before merging rings,
  otherwise `polygonize` returns 0 polygons and every readout is `nan` — nan is not "clean", it means nothing was measured.
- Measuring the hole bottom needs a coarse scan then a fine scan (a 0.25 mm step reads a 0.45 mm margin as 0.28,
  and the gate kills a good part).

Companion discipline: **numbers that depend on machine precision (neck closure, bead interference, hole diameter) must be set via a fit coupon first**
— 8 steps × male/female loose parts, steps marked by the count of through-holes (same-color bumps can't be read), marked on
both male and female sides, and the coupon **shares the same constants** as the final parts. See `references/snap-fit.md` and
winston's `fit_coupon_v3.py`.

## Discipline (Cross-Gate)

1. A failed slice masquerades as a clean pass — any "not read" counts as failure.
2. Never trust intermediate result files; verification always re-runs.
3. Deliverable files modified by a gate must be restorable from a backup point (back up the six deliverables before touching them).
4. Re-running the modeling script overwrites every deliverable — sync only the changed plate into the delivery directory each time,
   restore the rest from backup.
