# Printability Checklist (FDM / Multicolor AMS)

## Geometry

- [ ] Within a single color shell, **everything boolean-`union`ed** into a watertight solid. Overlapping independent shells look fine but produce extra inner walls and zero-thickness ramps when sliced
- [ ] Different color shells may overlap, but **no membrane-wrapping** (a shell only 0.5–1 mm thick enclosing another solid). The correct way is to cut **at the same seam**: `intersection(part, halfspace(z_lo=seam-0.3))` + `intersection(part, halfspace(z_hi=seam))`, with the seam overlapping only 0.2–0.4 mm
- [ ] Color-change seams should **land on horizontal planes**. With per-object color, changes happen only at whole-layer boundaries → minimal purge; slanted seams and per-layer interleaving raise the change count by orders of magnitude
- [ ] No face below the bed (`vertices[:,2].min() >= 0`)
- [ ] Minimum feature **≥ 2 mm** (fingers/antennae/tips). Below that FDM simply can't print it; don't count on "printing it finer"
- [ ] Count the parts overhanging >45°: palm underside, horn backs, splayed arms. Few → tree supports; many → change the pose
- [ ] **Run `scripts/overhang.py` along the print axis**, and read `Δr` (equivalent-radius single-layer growth) per layer. **A horizontal step around the part's midsection is its own failure class — don't conflate it with the item above:**
      - It connects to material above and below → **the slicer reports no floating regions**
      - It's not the first layer → **`warp.py` doesn't cover it**
      - But below that ring is air, and the first few layers extrude into the void → drooping / stringing / snaps at a bend
      Measured: a Ø36 sphere section placed directly on top of a Ø28 column, `z=21.0`, single layer **Δr = +4.035 mm (= a 406 mm² overhang ring)**, the slicer said "no warnings", the physical part had a blob of spaghetti at the junction. → **Every "neck-down + flare-out" transition must become a 45° shoulder**, built with `ptools.frustum()`.
      - The criterion is **Δr ≤ layer height** (0.2 mm layer → 45°). The alarm threshold is `0.35 mm/layer` (≈60°) + area gain ≥ 25 mm²; both must hold.
      - **Both ends of the frustum must be buried into the adjacent solids** (extend 3 mm each), or the end faces land on surfaces → **coplanar overlapping faces** in the boolean union, silently non-watertight.
      - The same prescription also cures a sneakier disease: two solids' faces **exactly coincident** (here `COLLAR_TOP == Z_SKIRT_LO == 21.0`). A union of two solids sharing one face is a classic silent failure; wrapping them into the shoulder makes it vanish.
- [ ] Legless / floating designs must get a base or be color-split, or they can neither stand nor be printed

## Color

- [ ] Colors split by **shell**, not by parts into files. One color = one object = one AMS slot
- [ ] Color values **sampled** from the reference image (PIL `quantize(colors=8, method=MEDIANCUT)`), never eyeballed hex
- [ ] Color count ≤ 4 (typical AMS slot config). Merge close dark colors (near-black ink blue / pure black) when possible — saves a slot and a color change
- [ ] Deliver the HEX for every color so the user can look up filaments by HEX (Polymaker etc.)

## Slicer setting suggestions (FDM + AMS)

| Item | Value | Reason |
|---|---|---|
| Layer height | 0.12 mm | Curved seams and color boundaries are cleanest at 0.12; 0.20 shows visible steps |
| Walls / top-bottom | 3 / 5 | Decor doesn't bear loads |
| Infill | 12–15% Gyroid | Solid volume × 0.28 ≈ actual weight |
| Supports | Tree auto, threshold 30° | |
| Support material | Dual-nozzle machines: Support for PLA on the aux nozzle | Main nozzle prints the model; support removal is zero post-processing |
| Adhesion | Brim 8 mm | Keeps tall thin parts upright |
| Verification | Print a 40% scale sample first | A dozen minutes to validate color and seam positions |

**Minimum scale**: compute minimum feature × scale ratio. A part with 2.2 mm fingers at 50% has 1.1 mm fingers — straight to scrap. This must go in the delivery notes.

## Rendering and export traps (all actually hit)

1. **One Poly3DCollection per color in matplotlib → the last color paints over everything**. Depth sorting happens only within a collection, never across. Merge into **one** collection with per-face facecolors + `zsort="average"`
2. **Don't project vertices yourself before calling `view_init()`** — it double-projects, producing a 90° wrong view that looks like a bird's-eye shot. Feed world coordinates and let `view_init` do the work
3. **matplotlib azimuth convention**: the camera sits at `(cos(az)cos(elev), sin(az)cos(elev), sin(elev))`. With the character's front facing +Y, use `az=90` to see the front. **Left and right are mirrored** (like looking at a person face-to-face) — if the reference image's left/right asymmetry must match, flip the model on X
4. **`trimesh.creation.capsule()` takes `count=[n1, n2]`; there is no `sections`**. Passing `sections` raises `revolve() got multiple values for keyword argument 'sections'`
5. **`<basematerials>` does nothing in Bambu Studio** — this trap disguises itself as success: the 3MF structure is valid, `--info` reads `manifold = yes`, the slicer loads the geometry, **but all colors are lost**, and object names degrade from `03_body` to `Object_3`.
   - The only thing Bambu reads is **`Metadata/model_settings.config`**; each object's `<metadata key="extruder" value="N">` is the **filament slot number (1-based)**. Write it, and object names and slots survive into the slicer (round-trip-verified with its own exporter)
   - **Don't try to embed `Metadata/project_settings.config`**: Bambu keeps its own presets and drops yours — in round-trip tests `filament_colour` stayed its default `#00AE42`. **Don't pretend to deliver print parameters**: deliver geometry + names + slot numbers and let the user load filament in the documented order
   - Still write `<basematerials>` (OrcaSlicer etc. read it), but **don't count on it**. Corollary: **slot order is part of the delivery contract**; the caller must pass the color order explicitly, not gamble on "order of first appearance"
6. **Structural validation after export is mandatory, but it only proves "a valid zip/XML", not that the slicer accepts it**. The real acceptance is running the slicer's own engine:

   ```bash
   BambuStudio --info model.3mf          # per-object manifold / number_of_parts
   BambuStudio --export-3mf rt.3mf --outputdir /tmp/out model.3mf
   # then read Metadata/model_settings.config from rt.3mf: names and extruder must come back intact
   ```

   `--info`'s `number_of_parts` = each object's **connected body count**, normally 1 (deliberately multi-part excepted). The first fit-part version reported 5 — five collar sockets each independent, never merged into one body — **pure geometry checks can't see it; the slicer reports it at a glance**.
   ⚠️ The CLI is a GUI program and **never exits**, and macOS has no `timeout`: background start + `sleep 20` + `kill`.
   ⚠️ These two (`--info` / `--export-3mf`) need no presets; but **`--slice` must come with
   `--load-settings "<machine>.json;<process>.json"` + `--load-filaments "<filament>.json"`**,
   or it hangs forever at `Initializing StaticPrintConfigs` (no error, no gcode, exit code 0).
   Don't misdiagnose it as "the GUI holds the single-instance lock". See `references/warp.md` section 6 and the `weigh_3mf.py` header comment.
6b. **Don't use `trimesh.load()` to judge "one connected solid"** — its STL/3MF load paths merge vertices and drop degenerate faces, and **the repair itself wrecks edge pairing on good parts**: `is_watertight` goes False, `split()` invents dozens of zero-volume "fragments". Same kit, measured: the load path called 8 of 10 parts broken; rebuilt from each file's own vertices/indices (`Trimesh(process=False)`), all 10 were watertight, one solid each, and the slicer engine likewise judged `manifold = yes`. Use `scripts/audit_3mf.py`, and close with the slicer's `number_of_parts`.
6c. **Two solids' end faces exactly coplanar → the boolean produces zero-volume fragments**. Invisible at the `.stl` level, invisible to in-process connectivity checks, **only the slicer's `number_of_parts` flags it** (horn reported 5, every other part 1). Wherever "a pin inserts into a boss/collar", the pin must extend **2–3 mm further into the host** to create real overlap — the same red line as "bury the pin root", seen from the other side.
7. Visual verification cannot be bbox numbers only — **you must look at the image**. The first version shipped "eyes like portholes, horns like nails" because only numbers were read
8. **Cones/spheres lying on the plate contact along a "line", not an "area"** — any stability check that computes "downward-facing ground area" reports 0 or near-0 for them (i.e. "unstable"). Not a defect: small part + 8 mm brim is plenty. **Don't change the orientation to silence this alarm** — the new orientation often points the pin down and makes it unprintable
9. **A frustum's "lying angle" computes differently for the two mirrored parts** — principal-axis decomposition is noise-sensitive. Compute one part only, and reuse the same transform for the other with `M = mirror_x @ T @ mirror_x` so both sides are exactly symmetric
10. **Flat/wide parts need a top-down preview at true-aspect framing**. `preview.render`'s `views` entries accept a third element = elevation (`("top", 90, 74)`); in the default 8° side view, holes and slots are invisible. Set `box_aspect` from real extents (floor 0.35); a fixed cube squeezes flat parts into a slit
11. **"Number markers" on parts go as through-holes, not bumps**. Same-color bumps are unreadable in top-down renders and in hand (same top normal = same brightness), and a 0.4 mm diameter difference is beyond the eye — **on fit coupons this is the only thing that distinguishes grades**. Through-holes cast wall shadows + transmit light; countable at a glance from above

## Deliverables

- Main file: `.3mf`, **must carry `Metadata/model_settings.config`** (object names + filament slots), with `<basematerials>` present but not relied on
- Backup: one `.stl` per color (import with "load multiple parts as one object")
- Single-color version: fully merged `.stl`
- Preview: four-view `.png` (top-down for flat parts)
- Scripts: the three commands for modeling / export / rendering, re-runnable
- `README.md`: file list, specs, color table, slicer settings, minimum scale, known tradeoffs, **copyright note**, **whether the config-invalid popup appears on open, and in what order to load filaments**
- Color-split / glue-free routes additionally: parts table (part no. / color / print size / est. weight / joint), assembly order, assembled-state four views, **fit coupon (with grade markers)**
- Acceptance record: the slicer engine's `--info` output (`manifold` / `number_of_parts`) + export round-trip verifying names and slots
