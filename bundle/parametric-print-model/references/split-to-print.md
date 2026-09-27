# Color-split (split-to-print) — when to do it and how

AMS single-piece multicolor (`threemf.write_3mf` + per-object color) and color-split are **two different routes**,
not two names for the same thing.

| | AMS single-piece | Color-split |
|---|---|---|
| Form | One piece, in-layer color changes | N independent parts, each single-color, assembled after printing |
| Needs AMS | Yes | **No** |
| Purge waste | Every color change flushes; often comparable to the model's own weight | **0** |
| Per-part orientation | One orientation for the whole; overhangs handled by supports | **Each part gets its own optimal orientation** |
| Cost | Wipe tower + color bleed | Extra joint material + one assembly step |

**Decision line**: want one-shot forming, no assembly → AMS single-piece; want zero waste / no AMS / controllable print time and orientation → color-split.

> ⚠️ **Delivery form: one plate per color, not one packed bed.** The color-split deliverable is N **single-slot** 3MFs
> (`splitter.write_colour_plates()`), one color per plate, every object on each plate locked to filament slot 1.
> Laying several colors on one bed = handing back both wins in the table above (purge 0 / no AMS needed): the wipe tower returns,
> every color change purges again (small parts' changeover waste often outweighs the part itself), and dual-nozzle machines
> **refuse to slice** when the two spools have different temperature classes — "printing high-temperature and low-temperature materials simultaneously may clog the nozzle or damage the printer".
> The mixed-color single-plate version can be kept as a rendering / layout reference; the delivery notes must say "do not print this one".
> Colors with only one part get folded into another plate with `merge={drop: keep}`; don't give a single eye piece its own plate.

---

## Rule 1: You cannot "split by color"

In a color-assigned model, one color's shell is often **a dozen-plus disconnected fragments**
(horns, ear fins, pupils, shoulder balls, fingers, base…). "Parts" split by color are scrap full of overhang islands;
the slicer adds supports per island and there's nowhere to start assembly.

> **A part is one connected printable solid (color is just an attribute).**

Chunk by geometry first (where can it separate, where should the joint faces be), then assign colors to chunks;
allow recoloring a small chunk to merge parts when needed (fold the joint ball's color into the main part), and document the tradeoff.

## Rule 2: Appendage parts must not penetrate the body

Everything that grows on a sphere/curved surface (horns, ear fins, arms) — **the root must be taken on the main surface**:

- Difference against the body's own surface to get a **concave back** that matches the main surface (the part "sits" on the surface);
- The pin goes inward from an opening on the main surface, its end staying inside the part; **do not** bury the part's solid body into the main body — it would never fit on.

```python
outer = P.difference([P.union([base_collar, chain]), body_sphere])  # concave back
part  = P.union([outer, pin])                                       # pin on the inside
```

## Rule 3: A pin hole must not be fatter than the limb it passes through

**The sneakiest rule.** Making a Ø12.4 pin for a palm with a Ø12.4 hole in the forearm — the forearm is a Ø12 capsule,
tapering toward the wrist (down to Ø6.5); after the boolean difference the arm becomes 2 disconnected bodies.

> Before cutting the hole, ask: how thick is the limb at that spot? A capsule **tapers toward its spherical ends**;
> computing with the mid-section diameter gets it wrong.

When the hole is too large or sits too close to the thin end, switch to a **flat glued joint** (mating faces made as mutually perpendicular flat alignments) —
the easier correct answer.

## Rule 4: A pin hole buried inside a solid = sealed cavity, not a hole

A lateral pin hole on a spherical surface, if the whole run lies inside the sphere, yields a **sealed cavity** after the boolean difference —
the pin can never enter, and the main part gets judged disconnected by n_components (an internal cavity counts as a second shell).

Fix: **first cut an assembly plane on the body**, and let the hole open on that plane.

```python
body = P.difference([body,
                     slab(d_shoulder, 30.0, "above"),          # cut a Ø22 plane
                     cyl_d(5.2, 20.0, 30.5, d_shoulder, C)])   # hole opens on the plane
shoulder = P.intersection([P.sphere(8.4, C + d_shoulder * 33, subdivisions=4),
                           slab(d_shoulder, 30.0, "above")])   # sphere cut flat as the back
```

## Acceptance checklist (run for every color-split)

1. **`n_components(m) == 1`** — connectivity welded by coordinates (index-based checks misreport)
2. **`m.is_watertight`**
3. **`footprint(m)`** — ground area + is COM inside the ground convex hull
4. **Parts must not interpenetrate each other** (render the assembled state, compare against the reference)
5. **Minimum feature ≥ 2 × layer height**; for scaled parts, derive scale from the minimum feature (not overall height)
6. **Close with the slicer engine**: `BambuStudio --info`; each part's `number_of_parts` must be **1**.
   ⚠️ Items 1–2 above **cannot catch things like "the pin's end face is exactly coplanar with the host boss"** — the boolean leaves zero-volume fragments,
   watertightness and connectivity still pass; only `number_of_parts` flags it (measured: horn reported 5).
   Wherever a pin inserts into a collar/boss, the pin must extend **2–3 mm further into the host**.
   ⚠️ Also don't read STLs with `trimesh.load()` and judge watertightness — the load path's auto-repair grades good parts as scattered.
   Use `scripts/audit_3mf.py` (parses vertex indices itself).

## Joint specifications (FDM field values)

| Form | Dimensions |
|---|---|
| Integrated pin | Ø8–10 × 8–12 deep, **0.2 mm radial clearance** |
| Small-part pin | Ø6 (don't use Ø10 on tiny parts) |
| Alignment pin hole | Ø3.2, insert a 3 mm filament stub, 6–9 deep |
| Large flat butt joint | Direct glue + 2 × Ø3.2 alignment pin holes |
| Small-part butt joint | Pure flat glue (a Ø19.6 face is plenty) |

## Print orientation

- Body/skirt: **cut plane down** (dome up), naturally support-free
- Parts sitting on curved surfaces: `upright(m, that part's assembly normal)` — it leans 60° when assembled; let it stand straight when printed
- Long curved parts (horns): `lay_flat` lying down + brim + tree supports — the only part that needs supports
- **Mirrored parts reuse orientation**: `mirror_twin(mesh_L, T_of_R)`; otherwise the two sides lie at different angles, visible to the eye
- Slender parts (fingers, horn tips): standing prints weak, lying down prints brittle — choose by the in-assembly load direction

## The final orientation verdict belongs to the slicer (proxy metrics only rank candidates)

`footprint()` measures "the convex hull of vertices within 0.8 mm of the plate" — a **proxy**. On concave undersides it's **wrong in both directions**.
Same Winston kit, measured:

| Part | Convex-hull basis | Real first-layer section (z = 0.2 mm) | Error |
|---|---|---|---|
| Horn (bent tube + flange) | 153 mm² | **18.4 mm²** | 8× too high (the concave face cut from a sphere gets filled in) |
| Ear fin (cone shell + pin) | 1 mm² | **4.35 mm²** | Too low (the hull only bites the crescent's two tips) |

Too high grades a "balance-on-a-knife-edge" pose as "standing fine" (the `MIN_CONTACT = 5.0` gate becomes a rubber stamp); too low kills good poses.

### The final criterion = the slicer's own `warning_message`

```
result.json → sliced_plates[0].warning_message
  clean = "" (empty string, not a missing field)
  problem = "It seems object <name> has floating regions. ..."
```

Slicing one 45 mm bent tube costs **1.4 s**, so you can **sample 200–300 candidate poses over the whole sphere and judge each one**;
8-way parallel finishes in about a minute (`scripts/pose_brute.py`). Results are usually **bimodal**:

- **Pose salvageable** (Winston ear fin: **106 of 240 passed**) → pick the largest real first layer and write it
  into the part definition with `orient="face"`
- **Pose unsalvageable** (Winston horn: only **1 of 240** passed, and it stands 45.6 mm tall — slow and wobbly) →
  **stop re-posing; turn on object-level supports**: under that part's `<object>` in the delivered 3MF's
  `Metadata/model_settings.config`, add `enable_support=1` / `support_type=tree(auto)` / `support_threshold_angle=30`.
  ⚠️ **Object-level only**; don't touch the process-wide switches, or every part on the plate gets wrapped in supports.

### Five pitfalls

1. **`--orient` makes `warning_message` lie.** With `--orient 1`, a pose that would still be flagged reports an
   empty string; re-slice the same geometry exported as-is and the warning comes back. **Only trust this field under plain `--slice 1`.**
   (By the way: `--orient 1` also only does a **pure Z yaw**; the output mesh's z size is unchanged — don't expect it to lay parts flat.)
2. **`grep "Floating vertical shell"` is not a criterion.** `02 upper body` had 160 layers with this tag and
   `01 base` had 68.6 mm³ of floating volume; both passed — those are just normal classifications of thin walls / bridges.
   The correct algorithm counts only floating regions **connected over ≥2 layers** (single-layer floating = a bridge, allowed).
3. **`orient="face"`'s `dirvec` is relative.** Candidates are judged on the **exported print-pose STL** (`parts_snap/*.stl`),
   which is already in `lay_flat`'s coordinate frame. So `face` is implemented as
   `base, T = lay_flat(mesh)` followed by `upright(base, -dirvec)` — **an increment stacked on the solver**,
   not an absolute frame direction. Feeding it as absolute **rotates twice** (measured: ear fin landed at 16.1 mm tall and still flagged;
   the correct value is 10.7 mm).
4. **`result.json` is written last, and the CLI must be `killpg`-ed — the two race.**
   Polling and killing the process group the instant `baked.3mf` appears can race result.json to disk, **losing the entire verdict**.
   A lost verdict is more dangerous than a lost file: `read_warning()` returns `None`, and `not None` is `True`,
   so **a never-judged slice passes the gate and overwrites the deliverable** (measured: three plates re-sliced;
   P1's round had no result.json at all, yet was recorded OK and overwrote the delivery). Fix both:
   ① poll **for result.json as the endpoint**, not baked.3mf — wait up to `settle` seconds extra after the geometry lands;
   ② the verdict is **three-state**: `""` = pass, `"..."` = flagged, `None` = **no verdict, does not count as a pass**;
   write the check as `warning is not None`, never `not warning`.
   ⚠️ Don't forget "only the first offender is reported": fix the named part and the warning jumps to the next one.
5. **The slicer is a GUI binary and never exits.** Poll for `baked.3mf` and `killpg` (start the process group with
   `start_new_session=True`); the CLI's cwd is `--outputdir`, so STLs fed to it must go through `os.path.abspath()`,
   or it reports `input files to the slicer are not found`.

### The correct offline floating-volume algorithm (ranking only)

`detect_floating_line(ThickPolyline, ExPolygons, gap, bool)` — take the **outermost contour** per layer,
subtract the lower layer's material dilated by `gap` (0.7 mm); the remaining length is the floating wall. Two traps:

- `polygons_full` also returns **cavities** as independent polygons; summing every polygon's `.exterior` makes
  an internal ceiling count as "exposed overhang" — `01 base` over-reported 113 mm this way. Filter out polygons
  `covers`-ed by sibling polygons and keep only the outermost.
- In trimesh 5.1, `Path3D` no longer has `polygons_full` (only `Path2D`). Use
  `trimesh.intersections.mesh_multiplane` to get segments, then
  `Path2D(entities=[Line(points=[2k,2k+1])...], vertices=seg.reshape(-1,2))`
  + `merge_vertices()` + `process()` → `.polygons_full`.
  `paths_to_polygons()` only takes **closed loops**; feeding 2-point segments gets silently skipped for `len(path) < 4`.

**Rank with it, never verdict with it**: one candidate measured **0.04 mm³** offline floating volume (cleaner than any passing part in the kit)
and was still flagged by the slicer.
