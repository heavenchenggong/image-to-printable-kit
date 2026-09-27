# Glue-Free Snap-Fit Joints (snap / press fit)

After color-splitting, there are three ways to connect parts: **gluing · press/snap · screwing**. This document covers press/snap.

## First decision: which joint

| Host condition | Use | Why |
|---|---|---|
| Bulky solid, room to carve a 9 mm cavity | **Ball joint (slotted elastic chuck on the male side)** | One joint delivers "fixing + angle adjustability + repeated assembly"; the male side is elastic, the female side rigid, so assembly never overstresses the socket |
| Thin plate / thin pad (3–8 mm) | **Bead-catch pin (slide-fit body + bead catch on the male side)** | No room for a 9 mm cantilever; the elastic chuck overstrains here; **do not use a negative-clearance interference press fit** — see the next section |
| Truly too thin (<3 mm), pin <4 mm long | Bead-catch pin, or **redesign outright** | Too short a pin can't wedge; all you get is a sliver of friction |

Picking in reverse (be clear about who yields):
- **The male side yields** (the ball joint's elastic chuck) → the female side is rigid; assemble a hundred times without deforming it.
- **The female side yields** (thin-wall tube + internal bumps) → only valid if the female side really is thin-walled. When the socket is surrounded by solid,
  making it "spread open" = yielding it permanently: it fits the first time, then it's loose forever. **This is the easiest mistake to make.**

---

## Ball joint: four numbers decide success or failure

```
squeeze  δ = r_ball − r_mouth          single-side squeeze as the ball equator passes the collar
free len L = standoff                  free bending length from the anchor to the equator
leaf     t = r_ball − bore_r           leaf radial thickness
strain   ε = 1.5 · δ · t / L²          PLA yield ≈3.5%, design target <2%
```

`check(joint)` in `scripts/joints.py` computes this set and returns GO/FAIL.

| Number | Should be | If wrong |
|---|---|---|
| δ (single-side interference) | **0.15–0.35 mm** | Ø10.6 ball pin in a Ø9.0 collar = δ0.8, ε straight to 68% — snaps on the first assembly |
| L (standoff) | **≥8 mm** (Ø10 class) | At standoff 5 mm, ε doubles. A deeper cavity costs nothing — the solid is full of material |
| t (leaf thickness) | 1.5–4 mm | Too thick: ε over limit; too thin (leaves become a 0.2 mm sheet): shears off at the neck |
| bore_r (relief hole) | **Mandatory**, Ø2–3.5 | Without it the four leaves converge at the ball top and can only press each other — they can't bend |

**The relief hole is required, not an optional optimization.** It's the precondition for the slot to work at all.

## Ball joint: three geometric red lines

1. **The collar must run from the seat all the way to the cavity center**. Cut only halfway and the ball cavity's own wall
   becomes the real collar; the true interference becomes `r_ball − socket wall radius`, **far tighter than designed, and invisible**.
2. **The ball equator must land behind the collar** (`standoff > throat depth`), or there's no catch and it falls right off.
   Throat depth = `standoff − sqrt(r_socket² − r_mouth²)` (where the collar cylinder meets the ball cavity).
3. **The socket mouth must open on the surface**. Fully buried inside a solid = sealed cavity; the pin can never enter.
   Likewise, the hole's **start face must be cut 1 mm proud of the seat**, or the boolean leaves a membrane.

## Press pegs: **don't use interference** (this section was overturned by physical assembly)

The old version here said "0.10 mm single-side interference". **Physical assembly on 2026-09-27 killed it**; all three joints failed:

| Joint | Design | Physical feedback |
|---|---|---|
| Arm → palm | Pin Ø4.60 / hole Ø4.40 (0.10 single-side interference) | **Would not go in at all**; the user cut the pin off to force it on |
| Pupil → eye plate | Pin Ø4.00 / hole Ø3.80 | Barely short |
| Ear fin → body | Pin Ø6.00 / hole Ø5.80 | Same defect, never even got started |

**Two independent arithmetic errors**, both computable before printing:

1. **CAD interference is not physical interference**. Holes print small (extrusion squish, plus elephant foot on bed-surface holes)
   and pins print fat, so a −0.10 radius interference lands on the part close to **0.45 mm of diameter** — ~10% on a Ø4.6 pin,
   and that's **all three pins at once**. How to measure: before slicing, take layer sections from the exported STL;
   the pin/hole diameter gap is plain to see (this project's tool: back out diameters from inner-ring areas of layer sections).
2. **The pin is longer than the hole**. `press_peg` builds `length + 0.6` (tip chamfer); `press_hole`
   **in the old version** built `length + 1.8`, of which 1.0 sits outside the seat, so the **usable depth was only `length + 0.8`**
   — just 0.2 mm more than the pin:

   | Joint | Pin total length | Hole usable depth (old version) | Result |
   |---|---|---|---|
   | Arm→palm | 5.00 + 0.60 = 5.60 | 5.30 | Bottoms out 0.30 mm |
   | Pupil→eye plate | 4.50 + 0.60 = 5.10 | 5.00 | Bottoms out 0.10 mm |

   **A joint that bottoms out won't close no matter how loose it is.**
   **Library fixed 2026-09-27**: `press_hole` now builds `length + 2.4` (1.0 outside the seat),
   usable depth `length + 1.4`, leaving 0.8 mm of margin for `press_peg`. **But retention is still zero** —
   for "goes in and won't pull out", use the bead-catch pin below.

### The correct approach: slide fit + elastic bead catch on the male side

- **Slide-fit pin body**: `r_bore = r_body + 0.10~0.15` (single side). The body should never "grab" at any point.
- **Retention comes from the bead catch**: bead radius = hole radius + 0.09~0.13 (single side); only the bead section interferes.
- **The bead must sit on an elastic male side**: two crossed slots + an axial relief hole. Pressing a solid bead into a rigid hole just
  yields the **hole** — loose permanently after one assembly.
- **Leaf strain check** `eps = 1.5 · δ · t / L²` (δ = bead interference, t = leaf thickness = r_body − r_relief,
  L ≈ slot depth − bead radius), **kept under 2 %**; this project measured 1.4–1.9 %.
- **Hole depth = pin length + chamfer length + 0.8**, with a 45°×0.8 mouth chamfer (bed-surface holes especially need it —
  that's where elephant foot is heaviest).
- If you need axial locking, cut a **groove ring** at the bead's landing point (`bead_hole(groove=...)`); otherwise friction alone,
  and one tug releases it.

Tools are in `joints.py`: `bead_peg()` (returns `(add, cut)`; the slots must be subtracted **after the union**,
or they won't reach the bead) and `bead_hole()`. **Note `P.difference()` takes only one list**, i.e.
`P.difference([union(...)] + cuts)`.

### One joint, one set of numbers; don't share

Bead diameter / hole diameter / slot depth **scale with the pin diameter** (this project: bead Ø5.00 wrist / Ø4.35 pupil / Ø6.45 ear fin);
changing one joint must not spill into another. **The coupon and the final part must share the same set of numbers**,
or the coupon stops predicting the part.


---

## Four universal red lines (shared with split-to-print)

1. **The pin's root must be buried in the host part**. The seat landing exactly on the mating face = two solids sharing one face,
   and the boolean union **silently drops** the connection; the part becomes a floating island. Give the pin a **2–2.5 mm buried section**.
   (The easiest rule to miss in this project: the part looks connected, then the connectivity check finds 2 islands.)
2. **A pin hole must not be fatter than the limb it passes through**. A Ø12.4 hole through a Ø12 capsule tapering to Ø6.5 at the end,
   and the boolean difference cuts the part into two disconnected bodies. **Measure the thinnest spot first.**
3. **Appendage parts (horns/ear fins/arms) must not penetrate the body sphere**; they must "sit on" the spherical surface — `difference`
   against the sphere first, then union the pin.
4. **Orientation rule: parts may only carry male features that can face up or sideways when printed.** A downward-facing pin hits the bed.
   So eye plates/pupils print face down (pins up), palms print back down (holes opening on the bed surface).

---

## Always print the fit coupon first

Three numbers depend on machine precision: the **collar diameter** (ball joint), and the **bead interference** and **hole diameter** (press joints).
Extrusion, flow calibration, and material shrinkage differ per machine; an interference that's "just right" on someone else's machine
won't go in — or falls out — on yours.

**Method**: produce one small part carrying **5 collars** (design value ±0.2 in two steps, e.g. Ø9.6→Ø10.4),
with 2 ball pins. Test each after printing, pick "needs a firm press, no wobble when seated", set `r_mouth` to that value, then produce the full set.
Cost: half an hour and 8 g; it replaces "discover at 100 g that nothing fits".

**Press-joint coupon (v3, 12–16 loose pieces)**: 2–4 grades per joint (pure slide fit / slide fit + end collar ring /
bead catch / bead catch + groove ring), one male and one female each, **laid loose** for the user to pair and test. Two conclusions that differ from the ball-pin coupon:
- **They don't have to share one base plate**. The ball-pin coupon must share a plate because the Ø16×21 columns are narrow-tall parts;
  the press coupon's males and females are all **flat discs** (Ø14–16 × 6–9), measured ground 130–174 mm², span 13–15 mm,
  all `ok` on warp. Loose printing saves material and lets you reprint a single piece.
- **The male side carries nothing beyond the hole markers**: grades go as through-holes (1 hole = grade 1), marked on both male and female,
  or pairs can't be matched after separating.

**Four details that are easy to botch** (the first coupon version hit every one):

1. **The five collars must grow on one base plate.** Separate discs = five independent islands: brim each individually,
   the VAT can knock one over mid-print, and the slicer will tell you `number_of_parts = 5`.
   Add one 2.5 mm plate, and while at it thicken the wall below each ball cavity from 1.5 mm to 4 mm.
2. **Grades must be marked on the part**. Ø9.6 vs Ø10.4 differ by 0.4 mm — indistinguishable to the eye.
   Without a marker the coupon is scrap once printed. Use **through-holes** (1 hole = grade 1 … 5 holes = grade 5),
   never bumps: bumps are the same color as the plate and unreadable top-down or in hand.
3. **The interference gradient must show the usable range**, or users assume tighter is better. Leaf strain of the Ø10.6 ball pin across the five grades:

   | Collar | Ø9.6 | Ø9.8 | Ø10.0 | Ø10.2 | Ø10.4 |
   |---|---|---|---|---|---|
   | Single-side interference | 0.50 | 0.40 | 0.30 | 0.20 | 0.10 mm |
   | Leaf strain | 3.33 | 2.67 | 2.00 | 1.33 | 0.67 % |
   | Verdict | Don't use | Tight | Design point | Loose | Wobbles |

   **Grade 1 looks the "strongest", but 3.33% sits at PLA's yield point — one insertion kills the leaf spring, and it only gets looser with use.**
   The usable range is grades 2–4; start from the design point.
4. **The ball pin goes into the same file as an independent object** (not attached to the plate); the user needs to poke it into every hole.

**Acceptance**: the slicer's own engine confirms the plate is **1 connected body** (`BambuStudio --info`, check
`number_of_parts`), and the measured hole diameters match nominal (cut a thin slice, back out radii from ring areas).

## Verification (never arithmetic alone)

**Assembly gate (run for every joint): `python -m scripts.mate_profile`.** It builds the joint as solids and
measures four things layer by layer along the axis, all taken off the mesh, no parameters copied:

- **Body clearance** (pin body section, minimum within the lower 60% of the pin axis) — must be >0.02, or it won't go in;
- **Bead grip** (max single-side interference over the full length) — must be ≥0.05, or one tug releases it;
- **Axial margin** (hole bottom − pin tip, hole bottom also measured) — must be >0.3; **a joint that bottoms out won't close no matter how loose it is,
  and this error is invisible in CAD** (exactly how the winston arm died);
- **Leaf strain** — <2%; beyond that the leaves take a set and fail.

All four pass = PASS; the exit code works as a CI gate. Pitfall: boolean solids carry T-junctions; section coordinates must be
`np.round(..., 4)` before the loop closes, else `polygonize` returns 0 polygons and every reading is `nan`.

`verify_snap.py`'s strain/interference math only proves **the leaves can bend**; it doesn't prove the solids actually mate.
Also run an **insert/pull sweep** once: translate the male side stepwise along its own axis, computing the volume of `male ∩ female solid` at each step.

- Fully seated (offset 0) must be **0** — otherwise the assembled state is already chewing on itself.
- During pull-out the volume must **rise then fall** back to 0 — the peak is that squeeze, and the peak should land near
  `−sqrt(r_socket² − r_mouth²)` (the moment the ball equator crosses the collar).
- If the peak climbs monotonically and never falls, the male side's **buried section** is chewing the female side — the joint isn't working.

## How to set the assembly order

**Assemble from the most load-bearing end toward the decorative end.** Example: base → upper body → (horns/ear fins/arms) → palms →
eye plates → pupils. Ball joints stay rotatable after assembly, which is exactly what lets you fine-tune poses as the last step —
so decorative parts that need angles get ball joints, and anything that must be fixed gets a press fit.
