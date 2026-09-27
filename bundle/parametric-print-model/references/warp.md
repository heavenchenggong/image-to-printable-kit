# Warping: fix the model first, then touch parameters

A triage order for "what the bottom looks like". **90% of warping is a geometry problem; only 10% is slicer settings** —
get the order backwards and you end up in the infinite loop of "first-layer speed from 50 to 20, then 20 to 15".

## 1. Triage by where it fails, before touching geometry

Look at the photo / the physical part, classify by failure location:

| Failure location | Root cause | Action |
|---|---|---|
| **One long straight edge** going mushy, stringing, piling material | Geometry. Shrinkage stress along the long edge exceeds section stiffness, the edge lifts, the nozzle scrapes it every pass | **Fix the model**, see section 2 |
| The **whole patch** touching the bed not sticking / shoved off | Settings. Z offset too high, first layer too slow or too fast, oily plate | Settings, see section 3 |
| **Sharp corners / all four corners** lifting on their own | Half geometry, half settings | Add R2–3 fillets + brim |
| A **thin thread** spanning a gap | Stringing, unrelated to warping. Insufficient retraction or wet filament | PETG retraction 4–6 mm, dry 4 h at 70℃ |
| Rim **bulging outward** (elephant foot) | Hot bed softens the first layers; upper layers press the material out | Elephant foot compensation 0.15 mm, or bed −5℃ |

Winston v1's coupon failure was **row one**: the plate's 102 mm long edge.

## 2. Geometry: three numbers decide everything

Measure first, don't guess. `scripts/warp.py` gives you the numbers directly:

```bash
python <skill>/scripts/warp.py part.3mf
```

```
part                    contact  span x  span y  depth   lift  band
coupon v1 (one plate)     2856    102.0    28.0    6.0   1734  redesign
coupon v1 (Ø20 disc)       314     20.0    20.0    3.8    105  brim
coupon v2 (each socket)    172     14.8    14.8   21.0     10  ok
```

- **contact** — ground area, mm². Grip on the plate, and also the source of shrinkage force.
- **span** — the **longest continuous straight run** of the bottom face along X/Y, mm. This is the lever arm.
- **depth** — the **average material depth** standing on the ground face = part volume ÷ contact area, mm.
  Bending resistance comes from it. **Don't estimate thickness from the vertex bounding box**: a trimesh cylinder
  only has vertices at its two end caps, so a Ø16×18.5 solid column gets estimated as a 0.6 mm sheet; volume ÷ area doesn't.
- **lift = span² ÷ depth** — this is the criterion.
  | Condition | Verdict |
  |---|---|
  | span ≤ 15 mm | ok — shrinkage stress has no lever arm |
  | lift ≤ 400 | ok, print as is |
  | depth ≤ 4.5 mm | brim, always (thin plates are held down by brim, not by their own stiffness) |
  | 400 < lift ≤ 1200 | brim 5–8 mm + fan off for the first 3 layers |
  | lift > 1200 | **fix the model, don't tune settings** |

### Why span **squared**, not span/depth

Residual lift h ∝ ε · span² / depth: grows with the square of the free span, falls linearly with thickness.

**This one cost us: it nearly sent good parts back for rework.** The first criterion was `ratio = span/depth`,
which judged the **Ø35 × 2.8 pupil disc "redesign"**, while the plate that actually failed at 102 mm scored 17.0.
Both "over the line", seemingly the same, but the risk differs by an order of magnitude:

| | span | Relative risk (∝ span²) | lift |
|---|---|---|---|
| v1 plate | 102 mm | 1.00 | **1734** |
| pupil Ø35 | 35 mm | (35/102)² = **0.12** | **438** |

The pupil carries only **1/11** of the plate's risk; a brim and it prints. **A ratio criterion can't tell "one size smaller" from "an order of magnitude smaller".**

Also add a floor: **any plate with depth ≤ 4.5 mm gets brim**, no matter how low the lift —
its own stiffness won't save it; brim holds it down.

### The other half: **will it stand up?** (COM margin)

`warp.py` now also reports **COM = margin from center of mass to the ground outline edge** (mm).
**Negative = COM falls outside the ground outline = this part tips over on its own, and the slicer can't see it at all.**

Three parts caught across Winston's glue-free / glued versions:

| Part | Original pose | Changed to |
|---|---|---|
| 05 horn (glue-free) | Standing on the flange edge's tangent point: ground 1 mm², COM **−2.1 mm** | Flange face down, COM **+2.8**, ground 108 mm² |
| 06 ear fin (glue-free) | Lying on one tangent line: ground 1 mm², COM **−2.4 mm** | Convex-hull face down, COM **+1.9**, ground 24 mm² |
| 06 horn (glued) | Only **3 vertices** touching the plate, nearest ring of vertices **0.55 mm high**, COM −1.6 | Assembly face down, COM **+4.5**, ground 78 mm², and **no supports needed anymore** |

**Don't judge stability by "contact area"**: a cylinder lying down has **line contact** — area ≈ 0, yet perfectly stable;
while a barrel can have a large contact patch yet rest on one edge. The only honest criterion is whether COM is inside the ground outline.

**Fix: change the pose, not the parameters.** `splitter.rest_flat(m)` — a rigid body truly rests with a
**convex-hull face** against the plate, so search the convex-hull faces for "which one points down", require COM to land inside the outline,
then score by ground area / COM margin / height / support volume. `splitter.lay_flat()` **auto-falls back** to it when
the verdict is "can't stand or margin < 1 mm", so calling `lay_flat` normally is enough.

#### ⚠️ Two rulers: plate contact at 0.1 mm, COM balance at 0.6–0.8 mm

**This is the most expensive pit in this problem class; it gets its own section.**

`footprint(m, tol)` means "vertices within `tol` of the plate count as contact; their convex hull is the support polygon".
With `tol` too big, it **invents a support surface the part doesn't have**. Three real numbers (glued 06 horn):

| Tolerance | Vertices within that distance of the plate | Reported ground area | Conclusion read out |
|---|---|---|---|
| **0.10 mm** | 5 | **0.07 mm²** | It stands on a few points |
| 0.40 mm | 11 | 0.32 mm² | Still a few points |
| 0.60 mm | 26 | 80.7 mm² | **"Standing fine"** ← false |
| 0.80 mm (`footprint` default) | 26 | 114.9 mm² | **"Standing fine"** ← false |

All 26 vertices sit **0.52–0.60 mm** high — half a millimeter short of the plate. Measured with the 0.6 ruler,
a part with **only 3 vertices touching the plate** becomes "80 mm², seated solidly",
and `rest_flat`'s trigger condition never holds — which is why it was **only caught in round two**.

**Division of labor; both numbers live in `splitter.py`:**

- **`CONTACT_TOL = 0.10`** — judges "does this pose actually have a face touching the plate". A truly seated face
  has vertices at z ≈ 0.
- **0.6–0.8 mm** — judges "is COM off-center" (defaults of `footing_margin` / `footprint`).
  Be lenient here: curved parts (a cylinder lying down) only ever contact along a line;
  tightened too far, even their support polygon can't be constructed.

**The same quantity must use the same ruler in both the gate and the packer**, or you get "the gate says it tips, the packer says it's fine"
and deadlock — exactly how Winston's ear fin's first rebuild didn't move a millimeter.

⚠️ **Don't copy the solver into project scripts.** `snapkit_winston.py` carried its own copy of `lay_flat`;
fixing the library version had zero effect on the script (both poses identical after rebuild). **The solver exists in exactly one copy.**

### Four fixes, ranked by value

1. **Remove non-load-bearing base plates.** The most common move. If a plate only "merges a few small parts into one object",
   it's trading a whole large flat surface's warping risk for a bit of handling convenience. **Ask: is this plate actually load-bearing?**
   Winston v1 → v2 was exactly deleting the plate: contact area −65%, span 102 → 14.8 mm.
2. **Break long edges.** Cut holes / slots in the bottom face, splitting a 100 mm continuous edge into islands.
   Shrinkage stress distributes over several small regions instead of accumulating along the edge.
   (This is also why "shallow grid patterns on the bottom" work.)
3. **Thicken.** Bending stiffness ∝ t³; 2.5 → 4 mm is 4× stiffness. But for already-wide plates it pays less than the first two.
4. **Chamfer / round.** A flat plate's **sharp bottom edge** is the least supported spot on the whole bottom face, and where lifting starts first.
   A 0.6 mm 45° base chamfer kills the sharp edge and shrinks the contact patch at once.
   Use `ptools.chamfered_base(r, h, cham)`.
   ⚠️ The chamfer must be **cut**, not unioned — the cylinder's own full circular bottom face is still there;
   unioning a taper ring underneath accomplishes nothing.

### Two traps that silently drop connections

- **Two solids merely "touching" (coplanar) → the boolean union drops the connection.** Every pin, post, boss
  keeps a **2–2.5 mm buried section** (true volumetric overlap); end-face-to-end-face is not enough.
  Winston v1's pin neck `bury` equaled exactly the plate thickness — the pin's end face and the plate's bottom face were the same plane.
- **The `_at` semantics of frustums / cylinders.** `trimesh.creation.cone`'s base is at z=0 (not centered);
  forgetting `-h/2` quietly detaches the part positionally.

## 3. Slicer settings (Bambu Studio)

Use this set when geometry passes and lift lands in 400–1200. **Remember the order**: bed → fan → placement → speed.
Speed is last because it matters least.

| Where | Parameter | Value | Why |
|---|---|---|---|
| Process → Quality → Precision | **Elephant foot compensation** | 0.15 mm | Kills first-layer bulge; pairs with geometric chamfer |
| Process → Quality | **Initial layer line width** | 0.5 mm (≈120%) | Wider first-layer line = pressed harder, grips better |
| Process → Cooling | **No fan for the first N layers** | **3** | The most underrated setting. Bambu's default blows from layer 2; the bottom gets chilled and shrinks before it sets |
| Process → Cooling | Fan speed | PLA 100% / **PETG 30–50%** | Over-blowing PETG delaminates it |
| Process → Others | **Brim type / width / gap** | Outer brim only / 5 mm / 0.1 mm | Mandatory when lift is 400–1200 or the part is thin (depth ≤ 4.5) |
| Process → Speed | **Initial layer speed** | **20–25 mm/s** | Default 50. Slower helps, but **is not the main factor** |
| Process → Speed | Initial layer infill speed | 25 mm/s | Same as above |
| Process → Speed | Overall speed (large flat parts) | Reduce 20–30% | Leaves time for layer bonding |
| Printer → Extruder | **Z hop type / height** | Normal (or Spiral) / **0.4 mm** | Lifts the nozzle on travel. Once warping starts, this decides "a scrape" vs "the whole patch peeled off" |
| Filament | Bed | **PLA 55–60℃ / PETG 75–80℃** | Take the upper bound for large flat parts |
| Filament | Nozzle | PLA 210–220 / PETG 240–250 | |

**X2D specifics:**

- **Chamber**: printing PLA — **open the top lid / front door** (prevents heat creep); printing PETG — **closed** (holds heat, shrinks the temperature gradient).
  The two directions are opposite; don't mix them up.
- **Auxiliary fan**: off (especially the first layers).
- **Dual nozzle**: test parts are always **single-color, single-slot**. Mixed colors on a dual-nozzle machine triggers the
  "high-temperature and low-temperature materials simultaneously" refusal to slice, plus a wipe tower and per-layer purge.

## 4. Placement and environment (free, and usually ignored)

- **Put the part at the center of the print plate**. Edge positions are coldest with the largest temperature gradient; warping starts there.
- **Let the bed soak for a few minutes** before printing so the whole plate is at uniform temperature (especially the large 256 plate).
- **Close doors and windows; keep away from AC vents**. One draft makes a large flat part cool on one side first.
  Large parts (any dimension > 150 mm) deserve an enclosure; a cardboard box is enough.
- **Keep the plate clean**: wash with warm water + dish soap; alcohol alone doesn't cut it (alcohol removes grease, not sugars/resins).
- **Don't rush to remove the part after printing**: wait until the plate cools below 30℃; prying while hot warps the freshly printed flat part.

## 5. Material: this affects "usable or not", not just "printable or not"

| | PLA | PETG |
|---|---|---|
| Warping | Low | **Markedly worse** (large flat parts especially) |
| Dimensional accuracy | Good | Slightly worse |
| Toughness | Brittle | **Tough** |
| Elastic strain limit | ~2.5% | **~4%** |
| Moisture uptake | Slow | Fast (48–72 h); dry 65℃/4–6 h before printing |

**Coupons must use the same material as the final part.** Two reasons: shrinkage rate sets hole diameters; elastic modulus sets the "feel" of pressing a joint in.
Validating a PETG ball pin in a PLA hole measures the wrong thing.

For the **elastic chuck of a glue-free snap-fit**, PETG is actually the better material — the leaves flex repeatedly,
and PLA at 0.3 mm interference tends to snap the leaves outright. The price is dealing with its warping head-on:
not one of the settings above is optional.

## 6. Acceptance

```bash
python <skill>/scripts/warp.py out.3mf          # geometry + stance gate; redesign exits non-zero
python <skill>/scripts/audit_3mf.py out.3mf     # each part watertight + 1 connected solid
python <skill>/scripts/weigh_3mf.py out.3mf     # true weight on the slicer's basis
```

`warp.py`'s exit code works directly as a CI gate: any part judged `redesign` blocks delivery.
(`brim` exits 1, `redesign` exits 2, all ok exits 0.)

⚠️ **`--slice` must have presets, or it hangs forever.** With only `--slice`, the CLI stalls on the
`Initializing StaticPrintConfigs` line: **no error, no gcode, exit code 0, process won't exit**,
and you just wait out the timeout (we burned two 5-minute waits here).

**It looks exactly like "the GUI holds the single-instance lock" — the first diagnosis said so, wrongly.** The GUI was long gone
(`lsof -c Bambu` empty, config mtime frozen at shutdown), and it still hung. The real culprit: missing printer presets.
Pass machine + process and filament, and **the same command produces `plate_1.gcode` in ~2 s**:

```bash
--load-settings "<machine>.json;<process>.json" --load-filaments "<filament>.json"
```

`weigh_3mf.py` now auto-resolves the three presets
X2D 0.4 nozzle / 0.20mm Standard @BBL X2D / PLA Basic from the app bundle's `profiles/BBL/`
(`--printer` / `--process` / `--filament` can override). One side lesson:
**sandboxes may block `ps`** (`operation not permitted`); a `ps`-only probe silently fails and reports
"not running" — it now uses `lsof`, and it's a **hint only**, no longer a blocker.

(`--info` / `--export-3mf` need no presets and work as usual.)

**One thing that invalidates all three gates above**: the slicer's `--export-3mf` output is a **project-style** 3MF —
geometry is not in `3D/3dmodel.model` but in `3D/Objects/object_N.model`, the main model holds only
`<component p:path="..."/>` references, and placement rides `<build><item transform="...">` (parts are stored in **local
coordinates**; z can be negative). A script reading only the main model **reads zero objects**, then prints "all clear" —
**it validated nothing**. Both scripts now read both layouts and apply the build transform; don't step on this when writing your own checks.
Baking geometry-style files into project-style (`scripts/bake_project.py`) is routine before delivery, so you will hit this.
Details in `delivery.md`.
