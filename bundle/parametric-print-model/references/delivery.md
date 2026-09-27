# Delivery: make files print the moment they open (baking)

`threemf.write_3mf()` produces a **pure-geometry** 3MF (`3D/3dmodel.model` + `Metadata/model_settings.config`).
Bambu Studio opens it with a popup:

> **3mf file configuration invalid, loading geometry data only**
> (official English: "The 3mf is not from Bambu Lab, load geometry data only")

It's an **identity notice, not an error** — a forum moderator's words: "Consider it as 'information'"; Bambu pops it for any
3MF **not saved by itself**. But for a file "delivered to a user to print", it's pure noise: the user sees
red text and comes asking, and all you can say is "just click through it". **Anything you can do in one pass, don't leave to the user.**

Helper: `scripts/bake_project.py`.

---

## 1. Root cause and the two layouts

3MF has two layouts; **you must tell them apart before delivery**:

| | Geometry-style (hand-written) | Project-style (the slicer's `--export-3mf` output) |
|---|---|---|
| Where the geometry lives | Inline in `3D/3dmodel.model`'s `<mesh>` | Part files `3D/Objects/object_N.model`; the main model holds only `<component p:path="..."/>` |
| Placement | Vertices are world coordinates | Parts stored in **local coordinates**, placed via `<build><item transform="...">` |
| `Metadata/project_settings.config` | **Absent** ← why the popup | Present (569 keys) |
| Member count | ~4 | 17–20 (adds gcode, plate thumbnails, slice_info) |

⚠️ **These two differences make validation scripts silently produce false negatives.** Early `audit_3mf.py` / `warp.py` read only
`3D/3dmodel.model`; faced with project-style files:

- Component references skipped → zero objects read → gate prints "all clear" while **checking nothing**
- `<item transform>` not applied → parts report negative z → false "verts below the bed"

**A false negative is far more dangerous than a false positive**: it makes you believe you validated. Both ends are now fixed to "read both layouts + apply the build transform".

---

## 2. Baking: let the slicer write its own config

Hand-assembling `project_settings.config` is asking for trouble (version strings, preset ids, 569 keys, inheritance chains).
The right fix is **to let the slicer write its own file** — what it writes, it necessarily accepts:

```bash
BambuStudio --load-settings "<machine>.json;<process>.json" \
            --load-filaments "<filament>.json" \
            --slice 1 --export-3mf out.3mf --outputdir <dir> in.3mf
```

One call produces: `project_settings.config`, `model_settings.config` (with object names and slots),
plate gcode, plate thumbnails, `slice_info.config`. The file no longer pops up, **and it's already sliced** —
the user drags it in and sees the preview and filament usage immediately.

`bake_project.py` wraps this into one command and does three extra things:

1. **Pure-geometry source snapshot** (`geo_src/`) — the deliverable itself is project-style now; without keeping a pure-geometry original,
   the second bake **feeds the previous output to the slicer**;
2. Before slicing, injects **per-object** brim per the gate's verdict (only parts `warp.py` bands `brim` get injected);
3. After slicing, reads `project_settings.config` back and verifies: `config` present, gcode present,
   `close_fan_the_first_x_layers`, `curr_bed_type`, `filament_type` / `filament_density` correct.

### What to bake into the file

| Parameter | Why it must be pinned in the file |
|---|---|
| `close_fan_the_first_x_layers = 3` | **Default is 1**: Bambu turns the fan on at layer 2 (measured `M106 S104`/`S255`); the bottom gets chilled and shrinks before it sets. The #1 cause of "ruined bottoms", and it hides in the GUI under "Cooling → no fan for the first N layers" |
| `curr_bed_type` | Preset default is Cool Plate (35 ℃). Changing filament without changing the plate type throws the work away — PETG won't stick at 35 ℃ |
| `filament_type` / `filament_density` | The CLI doesn't resolve `inherits` (next section); without writing these explicitly the project shows **0 g** |
| Per-object brim | The slicer's `auto_brim` criterion is "will the nozzle knock it loose"; the gate `warp.py`'s criterion is warping (`lift`). **The two don't overlap**: thin plates often get no automatic brim and must be injected explicitly |

> **Don't treat automatic brim as a substitute for the gate.** Measured on one plate: `06 ear fin`/`05 horn`/`07 arm` got
> automatic brim, while `04 pupil`, banded `brim` by the gate, **did not**. Run both; whoever says brim, add brim.

#### Per-object tree supports (`SUPPORT`)

`SUPPORT = { "<plate filename>": ["<object name substring>", ...] }` → injects into those objects
`enable_support=1` + `support_type=tree(auto)` + `support_threshold_angle`.

**When to add: the slicer decides.** Its own words are the criterion, quoted into code comments for reference:

```
It seems object 07 armR has floating regions.
Please re-orient the object or enable support generation.
```

It is the **only** authority on "is anything holding this extrusion path up" — geometric heuristics (including
`overhang.py`) are proxies, and measured the two sides **don't overlap**: the slicer flagged `07 arm` as floating
while `overhang.py` gave only 4 mm span / 2 mm² (too small to be structural), and vice versa.
So the division: `warp.py` owns warping, `overhang.py` owns mid-body steps, **the slicer owns floating regions**.

Two disciplines:

- **Try re-posing first; supports last.** If the pose came out of a 200–300-direction search (`pose_brute.py`), the search is done —
  only then do supports take over. The arm was the winner among 240 directions (first-layer contact 132 mm²); nothing left to try → supports on.
- **Left/right mirrored parts with the same name must both match** (needle `"arm"` matches `07 armL` / `07 armR` simultaneously),
  or you support half and the other half still reports floating.

⚠️ `inject_support` and `inject_brim` are the same splice: **it must follow a complete tag**.
Replacing only the matched substring produces `<metadata key="name" value="x" <metadata .../>`,
the XML is no longer valid → the slicer **drops all object names**, and the deliverable shows `Object_3`, `Object_4`.

### Changing machine / filament / plate

`--printer` / `--process` / `--filament` / `--bed` / `--fan-layers` / `--brim` / `--brim-width`.
`--filament-type` and `--density` **don't need hand-filling** — the script resolves them along the `inherits` chain (below).

---

## 3. Seven pitfalls (each cost one run)

### 1. `--slice` without `--load-settings` **hangs forever**

Stalls on `Initializing StaticPrintConfigs`: no error, no gcode, **exit code 0**, process won't exit.
**It looks exactly like "the GUI holds the single-instance lock" — the first diagnosis said so, wrongly**: the GUI was long gone and it still hung
(`lsof -c Bambu` empty, config mtime frozen at shutdown), hung outside sandboxes too. The real cause: missing printer presets.
Add them and **the same command produces gcode in ~2 s**.

> Side note: **sandboxes may block `ps`** (`operation not permitted`); a `ps`-only pre-check silently reports
> "not running". Switch to `lsof`, and treat it as a hint, not a gate.

⚠️ **That log line is not diagnostic evidence.** The CLI's stdout/stderr is **always exactly one line**
(`Initializing StaticPrintConfigs`, 92 bytes in both measured scenarios), because we `SIGKILL` the process group
before it can output more — **the successful run's log and the failed run's log are identical**. Guessing causes from logs is a dead end;
go back and check the inputs. Suspects actually ruled out (**none** was the cause):

| Hypothesis | Measured |
|---|---|
| Embedded `filament_density: 0` causing an infinite loop | Sliced with 0 fine (only the weight reads `0.00 g`) |
| Project file missing `plate_*.gcode` | Deleted the gcode, still sliced |
| Both of the above | Still sliced |
| `winston_fit_coupon_v1_bambu_DO_NOT_PRINT.3mf` in the archive | **Culprit found later**: it embedded `curr_bed_type = Cool Plate`, and the slicer **refused** the PETG → see pit 7 |

**Conclusion: keep the timeout, report errors on failure and list the inputs to check; don't trust logs.** For an unexplained hang,
re-bake once (`bake_project.py`) and it usually clears.

### 2. `--export-3mf` takes a **filename**, not a path

The CLI appends it to `--outputdir`. Passing an absolute path → `<outputdir>/<abs path>` →
`Unable to open the file ...tmp`.

### 3. Never `rmtree` the script's own cwd

The subprocess `cwd=` points at a deleted inode; the slicer reports
`Could not determine canonical path to application directory` (or `setup params error`).
Use `/tmp/<something>` as the working directory, not the project directory.

### 4. The CLI **does not resolve** filament presets' `inherits`

Loading only `Bambu PETG Basic @BBL X2D 0.4 nozzle.json`:

```
; filament_density: 0
; total filament weight [g]: 0.00
```

The project shows **0 g**. Meanwhile **the fields that actually drive toolpaths pass through fine**: fan 60%, nozzle temperature, flow ratio…
**the fields you'd casually check are all correct, which is exactly why the hole is hard to spot.** Before slicing, write `filament_type` /
`filament_density` explicitly into the preset copy.

### 5. Density **must not be hand-written** — nearest definition wins, and it isn't the category value

Measured (`profiles/BBL/filament/`):

```
Bambu PETG Basic @BBL X2D 0.4 nozzle   -- (none)
  Bambu PETG Basic @base                1.25   ← takes effect
    fdm_filament_pet                    1.27   ← category value, does not take effect
      fdm_filament_common               0      ← placeholder, skip
Bambu PLA Basic @base                   1.26
  fdm_filament_pla                      1.24
```

Hand-writing the category value 1.27 makes the delivered file's weight read **1.6%** higher than the slicer's — and the user
uses that number to judge "do I have enough filament". Both `bake_project.py` / `weigh_3mf.py` now have `resolve_filament()` /
`resolve_density()` walking the chain (skipping the 0 placeholder).

### 6. Per-object metadata must be inserted **after a complete tag**

Replacing only the regex hit produces
`<metadata key="name" value="x" <metadata .../>` — **invalid XML**.

The slicer responds by **silently dropping all object names**, and the deliverable shows `Object_3`, `Object_4`…,
with no relation to your carefully named `04 pupil`. `inject_brim()` now replaces **the complete tag** and gained an
`ET.fromstring(ms)` validity guard.

> By the way: injection must be **idempotent** (skip if `key="brim_type"` already exists). The deliverable is itself project-style,
> and "bake again" is routine; non-idempotent injection stacks a second pair of `brim_type`/`brim_width` onto the same object.

### 7. When the slicer **refuses** a job it doesn't fail — it disguises itself as a hang

This is the true culprit behind that archived "ran out the timeout, cause never found" file, and the easiest to conflate with pit 1.

On a plate/filament mismatch (PETG on Cool Plate), the slicer prints

```
got error when validate: Plate 1: Cool Plate does not support filament 1
run found error, exit
```

and writes the reason into **`result.json`** in the working directory:

```json
{"error_string": "Filaments are not compatible with the plate type.",
 "return_code": -61}
```

**And then the process doesn't exit.** From outside, "refused" and "hung" are identical — so pit 1's fix (add presets)
does nothing for it, and people keep circling the presets.

The fix is **polling `result.json` every round** (`presets.read_result_error()`; wired into both `weigh_3mf.py` and
`bake_project.py`): an unsupported plate drops from a **180 s timeout to an error in 1.2 s**.

> ⚠️ **The criterion is `return_code`, not the truthiness of `error_string`.** On success `error_string`
> is also `"Success."` — the first version wrote `if d.get("error_string"): return it`, so every successful slice
> reported `refused: Success.`.
>
> ⚠️ `slice.log` is **entirely useless** here: it is always exactly one line (`Initializing StaticPrintConfigs`,
> 92 bytes), **identical for success and failure**. `result.json` is the only true log for this scenario.

**So `--bed` / `curr_bed_type` is not optional.** The preset default is Cool Plate, and PETG gets refused outright;
meanwhile the bed temperature lives only in the **filament** chain (`textured_plate_temp`: PETG Basic 70 / PLA Basic 55),
and when the CLI fails to resolve `inherits` it falls back to a hardcoded **45 ℃** — 25 ℃ off, PETG won't stick,
while nozzle temperature / fan and the other "casually checked" fields all look normal. All three chains must be **fully flattened** before feeding the slicer.

---

## 4. Pre-delivery acceptance checklist

```bash
python scripts/warp.py  out.3mf      # warping + stance; redesign exits 2 → do not deliver
python scripts/audit_3mf.py out.3mf  # each part watertight + 1 connected solid (reads both layouts)
BambuStudio --info out.3mf           # the slicer's own engine: manifold / number_of_parts
```

Three more checks on the baked output (`bake_project.py` does them automatically):

| Check | Expected |
|---|---|
| `Metadata/project_settings.config` present | Yes ← popup gone |
| `.gcode` in the plate | 1 |
| `close_fan_the_first_x_layers` | The value you chose (not `1`) |
| `curr_bed_type` | Your actual plate |
| `M190` bed temperature | **The plate × filament combined value** (PETG + Textured PEI = `70`; seeing `45` means the preset chain wasn't flattened) |
| `filament_type` / `filament_density` | Match the preset chain (not `PLA` / `0`) |
| First 3 layers `M106 S0` | Yes (grep the gcode directly) |
| `enable_support` + the gcode `FEATURE:` list | A plate claimed "support-free" must have `enable_support = 0` **and** no `Support` in `FEATURE:` — `grep -c '; FEATURE: Support'` must be 0 |
| Per-object `extruder` | A single-color plate must be `{1}` |
| Object names | Chinese names preserved, not degraded to `Object_N` |

> **"Support-free" must be verified from the gcode, not by looking at the shape.** `enable_support = 0` is sufficient evidence,
> but it's easy to overlook yourself (`support_threshold_angle = 30` still sits in the config, looking enabled). The hard evidence is the absence of `Support`
> in the gcode's `FEATURE:` type set. Don't count with `grep -ci support` — the config comment block at the gcode tail
> copies all fifty-odd `support_*` keys; the first version counted a false positive of "52 support lines" that way.
>
> Support-free parts clear their overhangs via **bridging + slowdown + extra fan at the overhang**, not luck: measured on the P2 plate
> `enable_overhang_bridge_fan = 1`, `overhang_4_4_speed = 10` (the steepest band slowed to 10 mm/s),
> `overhang_fan_speed = 50%`, `bridge_speed = 50`, with `Bridge` and `Overhang wall` features appearing in the gcode. **Don't manually enable supports in the slicer** —
> a ring of trees grows around the Ø10.6 ball pin, pure post-processing burden.

Round-trip once more (**do slots and names actually survive**):

```bash
BambuStudio --export-3mf rt.3mf --outputdir /tmp/rt out.3mf
```

The CLI is a GUI program and **never exits**, and macOS has no `timeout` → background start + `sleep` + `kill`.

---

## 5. In one sentence

**Anything you can do in one pass, don't leave to the user.** Delivering "open and print" files costs one extra slicer run;
it saves one round of "why is this popup back", and one spool wasted by default parameters (fan on at layer 2).
