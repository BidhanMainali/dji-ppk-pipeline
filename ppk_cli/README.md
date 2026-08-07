# DJI RTK → PPK Pipeline

One command turns a DJI RTK drone flight into **centimetre-accurate, geotagged
photos** — ready for photogrammetry / 3D reconstruction (COLMAP, RealityScan,
Gaussian Splatting, NeRF).

It reprocesses the drone's raw GNSS log against a ground base station using
**PPK** (Post-Processed Kinematic) and writes the corrected position into each
photo's EXIF — the same result you'd get by hand in **Emlid Studio**, but
scriptable, batchable, and free.

The pipeline was validated to match Emlid Studio to **~1 cm** on our reference
flight (see [Validation](#validation)).

---

## Table of contents

- [What it does](#what-it-does)
- [Requirements & setup](#requirements--setup)
- [Quick start](#quick-start)
- [Command reference](#command-reference)
- [Input: what each flight folder needs](#input-what-each-flight-folder-needs)
- [Output: what you get](#output-what-you-get)
- [The config file explained](#the-config-file-explained)
- [How the pipeline works (internals)](#how-the-pipeline-works-internals)
- [Validation](#validation)
- [Testing & comparison (not part of the pipeline)](#testing--comparison-not-part-of-the-pipeline)
- [Troubleshooting](#troubleshooting)
- [Background: what PPK is and why](#background-what-ppk-is-and-why)

---

## What it does

For each flight, in one command:

1. **Finds** the flight's raw files and the base station's RINEX files.
2. **Builds** a processing config, auto-filling the base antenna height.
3. **Injects** every photo's shutter timestamp into the GNSS data as an event mark.
4. **Solves** the PPK positions with a live progress bar.
5. **Reports** the FIX / FLOAT percentage (how many positions are cm-accurate).
6. **Writes** the corrected GPS position into a copy of each JPG's EXIF.

Your original files are **never touched** — everything is written to `output/`.

```
Flight 1/1: DJI_202605221253_006_Create-Area-Route3
  config   : base antenna height 1.895 m -> flight.conf
  events   : 609 shutter marks injected from MRK
  solving  [###############               ]  50%   20:28:10   fix-so-far  99.4%
  Solution : FIX 99.70% (4046/4058)   FLOAT 0.30% (12/4058)
  Events   : FIX 100.00% (609/609)
  Tagged   : 609 JPGs (antenna->camera lever arm applied) -> output\...\tagged

========================================================================
  flight                                        FIX%  evFIX%  tagged
  DJI_202605221253_006_Create-Area-Route3     99.70% 100.00%  609/609  OK
========================================================================
```

---

## Requirements & setup

This pipeline currently targets **Windows** (paths and the RTKLIB binary are
Windows-style).

### 1. Python

Python 3.9+ (developed on 3.13). One third-party package:

```bash
pip install piexif
```

Everything else is Python standard library.

### 2. RTKLIB (the PPK engine)

The pipeline calls `rnx2rtkp.exe` from **RTKLIB-EX** (the rtklibexplorer /
"Demo5" fork, which matches Emlid Studio's engine more closely than stock
RTKLIB).

1. Download the latest Windows release ZIP from
   <https://github.com/rtklibexplorer/RTKLIB/releases>
   (validated on **v2.5.1**).
2. Unzip it anywhere.
3. Open `ppk_pipeline.py` and set the path near the top to your `rnx2rtkp.exe`:

   ```python
   RNX2RTKP = r"C:\ACIS\tools\RTKLIB_EX_2.5.1\RTKLIB_EX_2.5.1\rnx2rtkp.exe"
   ```

> **Pin your version.** Different RTKLIB builds can give different results.
> Record which release you used so runs stay reproducible.

### 3. Files in this folder

```
<repo root>/
├── ppk_cli/
│   ├── ppk_pipeline.py     # the pipeline (run this)
│   ├── compare_pos.py      # helper: compares two .pos files (used for validation)
│   ├── compare_tags.py     # helper: compares two sets of tagged photos (testing only)
│   ├── configs/
│   │   └── iter02.conf     # the validated processing recipe (config template)
│   ├── output/             # created on first run — all results land here (git-ignored)
│   └── README.md           # this file
└── scripts/
    └── parse_mrk.py        # DJI .MRK parser — imported by the pipeline
```

**About the `scripts/` folder.** The pipeline's only dependency outside `ppk_cli/`
is `scripts/parse_mrk.py` (it reuses the `.MRK` photo-mark parser). Keep the two
folders side by side — `ppk_pipeline.py` adds `../scripts` to its import path at
startup, so cloning the repo as-is just works.

> **Why only one file is in `scripts/`:** this repo intentionally ships **only**
> `parse_mrk.py`. In the original project the `scripts/` folder also held unrelated
> tools (camera-intrinsics and 3D-reconstruction helpers from other tasks); the PPK
> pipeline does **not** import any of them, so they're left out to keep the repo focused.

---

## Quick start

```bash
# process one flight
python ppk_pipeline.py "DJI_202605221253_006_Create-Area-Route3" --base "Reach_base_raw_20260522194356_RINEX_3_03"
```

Corrected, geotagged photos appear in
`output/DJI_202605221253_006_Create-Area-Route3/tagged/`.

---

## Command reference

### Process flights

```bash
python ppk_pipeline.py <flight_dir> [<flight_dir2> ...] --base <base_dir> [--min-fix 95]
```

| Argument | Meaning |
|---|---|
| `<flight_dir>` | One DJI flight folder (images + `_D.OBS/.NAV/.MRK`). **Required, one or more.** |
| `--base <base_dir>` | Folder with the base station's RINEX (`.yyO` + `.yyP`). **Required.** |
| `--min-fix <pct>` | Warn (and exit non-zero) if a flight's FIX % is below this. Default `95`. |

**Batch** = just list several flight folders. They must all share the **same
base** (i.e. flown while that one base was logging):

```bash
python ppk_pipeline.py Route1 Route2 Route3 --base Reach_base_..._RINEX_3_03
```

Each flight gets its own progress bar, report, and `tagged/` folder, then a
summary table prints at the end.

### Delete output

```bash
python ppk_pipeline.py clean                  # delete ALL of output/
python ppk_pipeline.py clean <flight_name>    # delete one flight's output only
```

Everything in `output/` is regenerable, so this is safe. It refuses to delete
anything outside `output/`.

> **Note:** a bare `clean` also removes any Phase-1 validation files
> (`iter01*.pos`, `iter02*.pos`) that sit directly in `output/`. To keep those,
> delete by flight name instead.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | All flights processed and all met `--min-fix`. |
| `1` | A hard error (missing file, solver failed, count mismatch) — stops loudly. |
| `2` | Finished, but at least one flight was below `--min-fix` — needs human review. |

---

## Input: what each flight folder needs

A DJI RTK flight folder straight off the drone. **The pipeline targets DJI RTK
*photogrammetry* drones that export their own RINEX** — see
[Requirements & what won't work](#requirements--what-wont-work) below for the
LiDAR / binary-GNSS limits. The pipeline looks for:

| Pattern | What it is |
|---|---|
| `*_D.OBS` | Rover (drone) raw GNSS observations, RINEX |
| `*_D.NAV` | Rover navigation / satellite ephemeris, RINEX |
| `*_D.MRK` | Mark file — one line per photo (shutter time + position) |
| `DJI_*_V.JPG` | The photos |

And in the **base** folder (from Emlid Flow → export RINEX):

| Pattern | What it is |
|---|---|
| `*.??O` | Base station observations (e.g. `.26O`) |
| `*.??P` | Base station navigation (e.g. `.26P`) |

If any of these is missing or ambiguous (e.g. two `.OBS` files), the pipeline
**stops with a clear message** rather than guessing.

### Requirements & what won't work

The pipeline's one non-negotiable input is the **drone's own GNSS log, in RINEX
form** — the rover `*_D.OBS` + `*_D.NAV` listed above. These are the *drone's*
observations and must be **distinct from the base**. DJI RTK **photogrammetry**
drones export exactly this (e.g. the M4TD reference flight: `_D.OBS/.NAV/.MRK` +
`DJI_*_V.JPG`). If your data doesn't contain a genuine drone RINEX, the pipeline
can't run — no amount of base data substitutes for it.

**Not supported (and why):**

| Data | Why it won't run | What to do instead |
|---|---|---|
| DJI **LiDAR payloads** (Zenmuse L1 / L2) | The drone GNSS is stored only in DJI binaries (`.RTB`, `.RTK`, `.RTL`, `.RPOS`) — there is **no `_D.OBS`**, and RTKLIB can't read those formats. | Export a RINEX observation from the raw payload in **DJI Terra** first, then run this pipeline — or geotag straight from the onboard `.MRK` positions. |
| Dual-camera captures (`*_LV.JPG` / `*_RV.JPG`) | The pipeline pairs single `DJI_*_V.JPG` frames one-to-one with MRK events; stereo `_LV/_RV` naming isn't recognised. | Not currently handled. |
| A "drone obs" that is really a **copy of the base** | Same receiver and static position as the base → the solve compares the base against itself and the result is meaningless. | See the trap below. |

> **⚠️ The "base-copy" trap.** Make sure the file you pass as the drone `_D.OBS` is
> *actually the drone's* — a moving rover — and **not a renamed copy of the base
> station**. We hit exactly this: a flight folder held a `..._L.obs` that turned out
> to be byte-for-byte identical to the base `.26O`. Quick checks:
> - Its RINEX header `REC # / TYPE` and `APPROX POSITION XYZ` should **differ** from
>   the base, and the position should **move** over the flight, not sit at one static point.
> - If `md5sum drone.obs base.??O` returns the **same hash**, it's a copy — discard it.

> **Time overlap.** The base log must span the flight's time window, or you'll get lots
> of FLOAT/single epochs and a low FIX %. See [Troubleshooting](#troubleshooting).

---

## Output: what you get

Everything lands in `output/<flight_name>/`:

| File / folder | What it is |
|---|---|
| `tagged/` | **Copies of every JPG with the PPK position written into EXIF GPS.** This is the deliverable. |
| `camera_positions.csv` | Per-photo `lat, lon, ellh, quality` (camera-center, lever-arm applied). |
| `solution.pos` | Full 5 Hz position track (every epoch of the flight). |
| `solution_events.pos` | One position per photo (at each shutter instant). |
| `report.txt` | The FIX/FLOAT breakdown that printed to screen. |
| `flight.conf` | The exact config used for this flight (for traceability). |
| `rover_events.obs` | The rover OBS with shutter marks injected (intermediate). |

The **originals are read-only** to the pipeline — the tagged JPGs are copies,
and the pixel data is byte-for-byte identical to the originals (only the EXIF
metadata segment is changed — no recompression).

---

## The config file explained

`configs/iter02.conf` is the **recipe** handed to the PPK solver — it tells
`rnx2rtkp` *how* to process the data. Same data + different recipe = different
answer, so this is the file you tune if you ever need to.

> **Auto-override:** the pipeline copies this template per flight but
> **recomputes `ant2-antdelu`** (the base antenna height) from each flight's
> base RINEX header — so you don't hand-edit it per flight. Every other setting
> comes from the template as-is.

### Every setting, in plain terms

**Positioning basics (`pos1-*`)**

| Setting | Value | Meaning |
|---|---|---|
| `pos1-posmode` | `kinematic` | The rover is **moving** (a drone in flight). |
| `pos1-frequency` | `l1+l2` | Use both GPS carrier frequencies (dual-frequency → faster, stronger fixes). |
| `pos1-soltype` | `forward` | Process the timeline once, front to back. |
| `pos1-elmask` | `15` | Ignore satellites lower than **15° above the horizon** (low ones are noisy). |
| `pos1-snrmask_r` / `_b` | `off` | No extra signal-strength filtering (rover / base). |
| `pos1-dynamics` | `on` | Model the drone's velocity/acceleration (helps hold the fix through motion). |
| `pos1-ionoopt` | `brdc` | Remove ionosphere delay using the broadcast model. |
| `pos1-tropopt` | `saas` | Remove troposphere delay using the Saastamoinen model. |
| `pos1-sateph` | `brdc` | Get satellite positions from broadcast ephemeris (the `.NAV` file). |
| `pos1-navsys` | `61` | Which GNSS systems to use — see the bitmask note below. |

**Ambiguity resolution — "locking the fix" (`pos2-*`)**

RTK/PPK gets to centimetres by locking each satellite's whole-wavelength count
to an integer. These control that.

| Setting | Value | Meaning |
|---|---|---|
| `pos2-armode` | `fix-and-hold` | Once locked, **hold** the fix (Emlid Studio's default behaviour). |
| `pos2-gloarmode` | `fix-and-hold` | Same, for GLONASS. |
| `pos2-bdsarmode` | `on` | Resolve BeiDou ambiguities too. |
| `pos2-arthres` | `3` | Confidence ratio required to **accept** a fix (higher = stricter). |
| `pos2-arlockcnt` | `5` | Epochs a satellite must be tracked before it's used for a fix. |
| `pos2-arelmask` | `15` | Only use satellites above 15° for fixing. |
| `pos2-aroutcnt` | `20` | After this many epochs of signal loss, reset the ambiguities. |
| `pos2-maxage` | `30` | Max age (s) of base data before a rover epoch is untrusted. |
| `pos2-rejionno` | `1000` | Outlier rejection threshold. |

**Output format (`out-*`)** — LLH (lat/lon/height), GPST time, degrees,
ellipsoidal height. Don't change these; the pipeline's parsers expect this layout.

**Measurement statistics (`stats-*`)** — `eratio1/2 = 300` is the carrier-phase
vs pseudorange weighting.

**Antenna positions (`ant*`)**

| Setting | Value | Meaning |
|---|---|---|
| `ant1-postype` | `single` | Rover's starting position from a rough single-point solve. |
| `ant2-postype` | `rinexhead` | Base position comes from the base RINEX header. |
| `ant2-antdele/n/u` | `0 / 0 / 1.895` | Base antenna offset E/N/**Up**. **`antdelu` is the tripod + antenna height** — recomputed per flight (see below). |

### The `navsys` bitmask

`pos1-navsys` is a sum of flags — add the ones you want:

| System | Value |
|---|---|
| GPS | 1 |
| SBAS | 2 |
| GLONASS | 4 |
| Galileo | 8 |
| QZSS | 16 |
| BeiDou | 32 |

`61 = 1 + 4 + 8 + 16 + 32` = GPS + GLONASS + Galileo + QZSS + BeiDou (everything
except SBAS).

### The antenna height (the one auto-computed setting)

`ant2-antdelu` is the base antenna's height above the survey mark. It's the
**one setting the pipeline fills in automatically per flight**:

```
ant2-antdelu = <ANTENNA: DELTA H from base RINEX header> + 0.095
```

- The `ANTENNA: DELTA H` value is the pole height you entered in Emlid Flow
  (1.800 m on our reference flight).
- `0.095` m is the Emlid Reach RS4's internal L1 phase-center offset (from
  Emlid's spec). It's the constant `RS4_APC_L1_M` in `ppk_pipeline.py` — change
  it if you use a different base receiver.

**Why it matters:** the first config version ignored this and every position
came out ~1.9 m too low. This single line fixed it.

### How to modify the config

1. Edit `configs/iter02.conf` (or copy it to a new name and point the
   `TEMPLATE` path in `ppk_pipeline.py` at your copy).
2. Change a setting — e.g. raise `pos1-elmask` to `20` in a high-multipath area,
   or drop a troublesome constellation from `pos1-navsys`.
3. Re-run and check the FIX % and the Emlid-Studio comparison (below) didn't
   regress.

Full parameter reference: the RTKLIB-EX manual,
<https://rtkexplorer.com/pdfs/manual_demo5.pdf>.

---

## How the pipeline works (internals)

`ppk_pipeline.py` runs six stages per flight. Each is one function.

1. **`discover`** — glob the flight and base folders for the required files;
   fail loudly if anything is missing or ambiguous.

2. **`build_config`** — copy the template config, reading the base antenna
   height out of the base RINEX header and writing it into `ant2-antdelu`.

3. **`inject_events`** — read the `.MRK` file (one row per photo, with a GPS
   week + seconds-of-week timestamp), convert each to calendar GPS time, and
   insert it into a copy of the rover `.OBS` as a **RINEX event mark** (epoch
   flag `5`). This is exactly how Emlid Studio associates photos with
   positions — RTKLIB then interpolates a position at each shutter instant. We
   write **no** interpolation code ourselves; RTKLIB does it.

4. **`solve`** — run `rnx2rtkp`, streaming its progress. RTKLIB prints
   `processing : <time> Q=<n>` lines; we parse them to draw the progress bar and
   the running "fix-so-far" number. It produces `solution.pos` (every epoch) and
   `solution_events.pos` (one per photo).

5. **`report`** — count the quality flags (Q=1 FIX, Q=2 FLOAT, …) in both files
   and print the percentages. Hard-fails if the event count ≠ photo count.

6. **`tag_photos`** — pair each event position with its photo, apply the
   **antenna → camera lever arm** (the per-photo N/E/V offset stored in the MRK
   — this is what moves the position from the GPS antenna to the actual camera
   center, and it's what makes our output match Emlid Studio), then copy the JPG
   and write the position into its EXIF GPS tags using `piexif` (which edits only
   the metadata segment, leaving the image untouched).

### Why not `dji-geotagger`?

An earlier plan considered the `dji-geotagger` library. We didn't use it: it
outputs **CSV only** (it never writes EXIF into photos), and its rover input is
DJI's `*_PPKRAW.bin`, which the **M4TD doesn't produce** (we already have RINEX
`.OBS`). Replicating Emlid Studio's event-mark mechanism directly was both
simpler and gave us photos tagged in place.

---

## Validation

Measured against a trusted **Emlid Studio 1.9** solution of the same reference
flight (`DJI_202605221253_006`, 609 photos, Victoria BC):

| Metric | Emlid Studio | This pipeline |
|---|---|---|
| FIX epochs | 96.08% | **99.70%** (no ES-FIX epoch lost) |
| Camera events FIX | 601 / 609 | **609 / 609** |
| Camera position vs ES (FIX events) | — | **0.98 cm** mean horizontal, **0.99 cm** mean vertical |
| Run-to-run | — | **bit-identical** (deterministic) |
| Tagged JPG pixels | — | byte-identical to originals |

You can reproduce the position comparison yourself with the bundled helper:

```bash
python compare_pos.py <emlid_studio_events.pos> output/<flight>/solution_events.pos --label-a ES --label-b PIPE
```

> The reference flight also came out **100% FIX** — including the frames that
> were FLOAT in the live flight — because PPK reprocesses against the full base
> log. (What to do with previously-FLOAT frames is tracked separately.)

---

## Testing & comparison (not part of the pipeline)

> **This is a side tool, not a pipeline stage.** `compare_tags.py` was built for a
> one-off **tolerance sign-off** — proving to the team that this pipeline's tagged
> photos land in the same place **Emlid Studio's** do. The production pipeline
> (`ppk_pipeline.py`) does **not** call it and doesn't need it. It's kept in the repo
> for reference, and because it's handy any time you want to re-check a flight against a
> trusted source. If you just want geotagged photos, ignore this section.

### What it does

Takes **two sets of geotagged photos** — a trusted reference (e.g. Emlid Studio's tagged
output) and this pipeline's — matches them **by filename**, and reports how far apart
each photo's position is, in **centimetres**.

Each side can be **either**:

- a **folder** of tagged JPGs (it runs exiftool to read the GPS), or
- a **CSV** — it understands both an exiftool `-csv` dump *and* the pipeline's own
  `camera_positions.csv`.

Because the pipeline already writes `camera_positions.csv`, you usually only need
exiftool on the **reference** (Emlid) side.

### Requirements

- **exiftool** on your PATH — only needed when an input is a JPG *folder* (not a CSV).
  Download: <https://exiftool.org/>. If a folder input can't find it, the tool says so.
- No extra Python packages (standard library only; reuses the delta maths from
  `compare_pos.py`).

### Run it

From inside `ppk_cli/`:

```bash
python compare_tags.py "<reference>" "<test>" [--label-a ES] [--label-b PPK] [--tol-h 5] [--tol-v 10] [--out deltas.csv]
```

Example — Emlid Studio's tagged folder vs this pipeline's positions:

```bash
python compare_tags.py "..\DJI_..._Route3\tagged_DJI_..._Route3" "output\DJI_..._Route3\camera_positions.csv" --label-a ES --label-b PPK --out deltas.csv
```

**Which is which:** the **trusted answer first**, the **answer you're checking second** —
i.e. `compare_tags.py "<Emlid's output>" "<this pipeline's output>"`. Think of it as
grading a student (your pipeline) against an answer key (Emlid). `<test>` below means
*"the one being checked,"* **not** "the test procedure."

| Argument | Meaning |
|---|---|
| `<reference>` | trusted side — the answer key — a folder or a CSV (e.g. Emlid Studio). |
| `<test>` | the side being checked — **this pipeline's output** — a folder or a CSV. |
| `--label-a` / `--label-b` | names shown in the report (cosmetic only). |
| `--tol-h` / `--tol-v` | pass/fail thresholds in cm (default `5` / `10`). |
| `--out <file>` | also write a per-photo delta table (CSV) for Excel. |

### What it reports

- **Matched count** (photos that line up by filename) plus any unmatched on either side.
- **Horizontal** and **vertical** gap: mean / rms / max, in cm. Horizontal is the clean
  tolerance number.
- **Datum auto-detect:** if the two sides measure height from different references (e.g.
  ellipsoidal vs sea-level), the vertical gap is a *constant* ~18 m — the Earth's shape,
  **not** an error. The tool detects that offset, calls it out, and removes it (a
  "de-biased" vertical) so it can't cause a false FAIL.
- The **worst 10** photos by horizontal gap, and a **PASS / FAIL** against the tolerances.
- Exit codes: `0` within tolerance, `2` out of tolerance, `1` hard error.

`--out deltas.csv` writes one row per photo
(`photo, dE_cm, dN_cm, dU_cm, dU_debiased_cm, horiz_cm`) — the evidence file to hand a team.

### Is the result good or bad? (how to read the numbers)

**Don't judge by the PASS/FAIL stamp alone** — judge by three things:

| Check | Good ✅ | Bad ❌ |
|---|---|---|
| **1. Average gap** (the headline) | `mean` ≤ ~2 cm — the two solutions agree | `mean` of many cm / decimetres |
| **2. A constant lean?** | datum offset ≈ 0 **and** the worst-photo dE/dN signs are **mixed** (+ and −) → random GNSS noise, harmless | every photo shifted the *same* direction → a **systematic** error (wrong antenna position or lever arm) |
| **3. Spread** | almost all photos close, only a handful of outliers | many photos far off |

The **mean** is what matters most. The **direction check** (#2) is the one people miss:
scan the "Worst 10" table — if `dN` is `+6, −6, −5, +4 …` (mixed signs), that's healthy
random scatter. If it were `+20, +19, +21 …` (all one way), that's a real bug to chase.

**"FAIL" does not mean bad data.** The verdict is just a strict *max* rule: if the single
worst photo crosses `--tol-h`, it says FAIL — even if the average is ~1 cm and only a few
photos cross the line. Read the mean and the spread before reacting to the stamp. Then set
`--tol-h`/`--tol-v` to **your project's actual spec** (e.g. `--tol-h 7`) and re-run — the
stamp reflects your requirement, not an arbitrary default.

> **Sanity floor:** two independent PPK solutions of the *same* flight *should* land within
> a couple of cm. A mean of ~1 cm with mixed-sign scatter means the pipeline is reproducing
> the reference correctly. A large mean, a constant directional offset, or unmatched photos
> are the things that signal a genuine problem.

### Our result

Emlid Studio 1.9 tagged folder vs this pipeline, reference flight (609 photos):

| | Horizontal | Vertical |
|---|---|---|
| mean | **1.01 cm** | 1.02 cm |
| max | 6.03 cm | 6.32 cm |

609/609 photos matched, and the **same height datum** (offset 0.001 m — no ellipsoidal
vs sea-level trap this time). 587 of 609 photos agree within 3 cm; the default 5 cm gate
"fails" only on 5 outlier photos while the average is ~1 cm — i.e. a **threshold choice,
not a data problem**. Set `--tol-h`/`--tol-v` to whatever your team agrees on.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `rnx2rtkp not found at ...` | Set the `RNX2RTKP` path at the top of `ppk_pipeline.py` (see [setup](#requirements--setup)). |
| `expected exactly one base obs (.yyO) ... found 0` | Wrong `--base` folder, or the RINEX export didn't include obs/nav. |
| `expected exactly one rover OBS ... found 2` | Two flights merged in one folder — split them, one flight per folder. |
| All positions ~2 m off vertically | Base antenna height wrong — check `ANTENNA: DELTA H` in the base RINEX header and the `RS4_APC_L1_M` constant. |
| FIX % much lower than expected | Base and rover times may not overlap, or a bad base position. Check the two RINEX headers cover the same time window. |
| `... event positions but N photos — refusing to tag` | The solve dropped some events (e.g. photos taken outside the GNSS window). Investigate before trusting the output. |
| Low FIX warning / exit code 2 | Genuinely marginal flight — review it; don't silently ship it. |

---

## Background: what PPK is and why

Plain GPS is accurate to a few metres. **RTK** and **PPK** get to centimetres
by comparing the drone's GNSS against a **base station** sitting still on a known
point — the base measures how much the sky is "lying" right now and that same
error is cancelled out of the drone's data.

- **RTK** does this **live, in the air** (the base radios corrections to the
  drone). Your `.MRK` already holds these live-corrected positions.
- **PPK** does the same maths **afterward, at a desk**, using both devices' full
  recorded logs. Because it can look forwards *and* backwards in time and use the
  complete base log, PPK often recovers positions the live link missed — which is
  why this pipeline can turn live-FLOAT frames into FIX.

This pipeline is the automated, scriptable version of what you'd otherwise click
through in Emlid Studio.
