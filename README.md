# Image → Printable Kit

End-to-end pipeline skill: **from one image to a set of print-ready 3MF files** — parametric modeling, color-split parts (three routes: single AMS plate / glue / glue-free snap-fit), per-part print orientation, slice bake, six quality gates, delivery docs and verification images.

> Claude Code / WorkBuddy skill. Shaped by one full pass through the pitfalls, not assembled from templates.

## Why This Is a Skill

Turning a mascot from an image into something printable: the real difficulty is not modeling, it's that **the failure modes defy intuition**:

- Slicer warning "parts floating" — only one class of scrap
- Large flat surface lifting at the edges — caught by `warp.py`
- **A single-layer horizontal step at mid-part**: cross-section jumps +406 mm² within one layer, the slicer doesn't warn (material connected above and below, not an island), it's outside the first-layer edge-lift gate's view, and the physical result is a tangle of loose filaments around the step
- **Won't assemble**: all three print gates PASS and assembly still fails. Physical trial assembly found "the arm would not go into the palm at all" — the peg was longer than the hole and the end faces bottomed out, invisible in CAD → `mate_profile.py` assembly gate (volume clearance / bead grip / axial margin / blade strain)

The fourth class is this pipeline's most expensive lesson and can only be caught by layer-by-layer overhang scanning; the fifth (assembly) is likewise only exposed by the physical print. The whole skill is organized around the coverage matrix of "failure mode × six gates".

## Pipeline

| # | Stage | Exit criterion |
|---|---|---|
| 0 | Route freeze (parametric vs AI image-to-3D; AMS vs glue vs glue-free) | Route decided and communicated |
| 1 | Blueprinting: parts list / interface list / color table | Written down and frozen |
| 2 | Parametric modeling + multi-view render check | Every part watertight and connected |
| 3 | Color split into plates | Every part on exactly one plate |
| 4 | Per-part orientation (slicer black-box verdict + edge-lift gate) | Every part solved or supported |
| 5 | Slice bake → 3MF (embedded config + gcode + thumbnails) | Verdict file on disk |
| 6 | Six gates (including assembly fit `mate_profile.py`) | All PASS |
| 7 | Doc sync (weight/footprint/time measured values backfilled) | No stale numbers left |
| 8 | Delivery + print notes | present_files |

## Install

### ClawHub

```bash
clawhub install image-to-printable-kit
```

### Manual

```bash
# Main skill (pipeline orchestration)
git clone https://github.com/heavenchenggong/image-to-printable-kit ~/.claude/skills/image-to-printable-kit

# Dependent skill (all scripts for modeling / orientation / baking); install alongside if missing on this machine
cp -R ~/.claude/skills/image-to-printable-kit/bundle/parametric-print-model ~/.claude/skills/
```

The main skill is the **orchestration layer**: it invokes the tool scripts by path `~/.claude/skills/parametric-print-model/`; `bundle/` holds a copy of the same dependency so the install never runs broken. When both copies match, either works.

For WorkBuddy users the skills directory is usually a soft link to `~/.claude/skills/`; install once and both work.

### Environment

```bash
pip install numpy trimesh shapely scipy matplotlib pillow   # python ≥ 3.10
```

The slicer needs Bambu Studio CLI (on macOS point at the GUI binary:
`/Applications/BambuStudio.app/Contents/MacOS/BambuStudio`).

## Layout

```
SKILL.md                        # Eight-stage runbook + dependency notes
references/gates.md             # Six gates: criteria, commands, discipline
references/case-winston.md      # Case log: pitfalls hit by the Winston mascot kit
bundle/parametric-print-model/  # Dependent skill (scripts + deep docs)
  references/cases.md           # Index of five reverse-engineered models + four costly reprints
  references/mechanisms.md      # Slider reveal (4-layer stack) / print-in-place telescoping taper
  references/organic-thin.md    # Thin organic parts: pose, feature density, minimum printable size
  references/modeling-pitfalls.md  # 23 modelling pitfalls, each one paid for
  scripts/reveal_slider.py      # Sizing calculator: travel = cavity travel ≈ pattern pitch ≈ window Ø
  scripts/telescoping_taper.py  # Sizing calculator: constant slide gap + line-to-line anti-pullout
```

## Field Test: Winston Mascot Kit

X2D dual nozzle / PETG / glue-free snap-fit, 10 parts / three plates (P1 green · P2 dark · P3 accents); all three plates printed successfully on real hardware. Both scrap events along the way and their fixes are documented in `references/case-winston.md`:

- `05 horns` (curved tube, mid-body overhang): only 1 of 240 candidate poses passed and it stood 45.6 mm tall → kept the low stable pose + object-level tree support for **this one part only**
- `01 base + lower-body shell` (horizontal step at the neck): 45° shoulder frustum, each end embedded 3 mm into the adjacent solid, 406 mm² → 11.4 mm²
- Arm ↔ palm (assembly failure): negative-clearance interference press fit measured ≈ 0.45 mm of interference on the physical print, and the peg was longer than the hole so the end faces bottomed out → all joints rebuilt as **slide-fit peg body + male-side elastic bead catch**, new assembly gate added

## Easy-Release Support

To tear tree supports off by hand: load Bambu **Support for PLA/PETG** breakaway material in the B extruder and select it as "Support/raft interface" in the slicer; the tree trunk still uses base material (official guidance: never use support material for the base). In the delivered 3mf files `enable_support` is already written; change the interface material and re-slice, no re-bake needed.

## Version History

### v0.2.0 — 2026-11-21

Bundled skill gains three new mechanism classes, an index, and three pitfalls that had only lived in code comments.

**New**
- `references/cases.md` — index of five reverse-engineered models (slider reveal, telescoping taper, organic twig, off-the-shelf receiver, short/flat part): measured numbers, the one rule each taught, where it lives. Plus the four reprints that cost real filament and the defaults each case changed.
- `references/mechanisms.md` + `scripts/reveal_slider.py` — four-layer reveal slider. Two independent travel equations must agree within 0.2 mm, and travel ≈ pattern pitch ≈ window diameter. Damping comes from the Z-direction clamp, **not** from XY interference (all three XY gaps are 0.10–0.17 mm slide fits).
- `references/organic-thin.md` + `scripts/telescoping_taper.py` — print-in-place tapered telescoping tubes (constant 0.70 mm single-side slide gap, line-to-line anti-pullout at each stage top) and thin-organic-part guidance: surface/volume ratio, lying-down printing, and the shrink floor `s ≥ 0.84 / thinnest feature`.
- `references/modeling-pitfalls.md` — 23 pitfalls. Two that produce plausible-but-wrong numbers: editing vertices without re-measuring (a silent no-op fix), and `shapely.polygonize` dropping unclosed rings so cross-section area comes out an order of magnitude too small.

**Changed**
- `references/delivery.md` — eighth pitfall: `--fan-layers` written only to the process preset is **silently overridden** by the filament chain (`--load-filaments` wins). Verify in gcode, not in the round-tripped config.
- `references/printability-checklist.md` — short/flat parts now get their own section (three failure modes happen at once), and a new rule: never build a patch's thickness by subtracting two curved shells (it makes a barrel that islands the first layer → `floating regions`, whole plate rejected).
- `references/snap-fit.md` — clearance budget for printed parts that receive off-the-shelf hardware, where no squeeze can be calculated and the designer's own mating surface is the measurement.
- `scripts/presets.py` — fan override written to both preset chains; `scripts/overhang.py` — faster layer scan.

### v0.1.0 — 2026-09-27

First release: eight-stage runbook, six gates, Winston mascot kit field test.

## License

MIT
