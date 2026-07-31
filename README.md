# DJI RTK → PPK Pipeline

One command turns a **DJI RTK drone flight** into **centimetre-accurate, geotagged
photos** — ready for photogrammetry and 3D reconstruction (COLMAP, RealityScan,
Gaussian Splatting, NeRF).

It reprocesses the drone's raw GNSS log against a ground base station using **PPK**
(Post-Processed Kinematic) and writes the corrected position into each photo's EXIF —
the same result you'd get by hand in **Emlid Studio**, but scriptable, batchable, and free.

> **Validated to ~1 cm** against Emlid Studio on the reference flight
> (609 photos, 99.70% FIX epochs, 609/609 camera events FIX).

## Quick start

```bash
cd ppk_cli
pip install piexif
python ppk_pipeline.py "<flight_folder>" --base "<base_station_folder>"
```

Corrected, geotagged photos land in `ppk_cli/output/<flight>/tagged/`.

## What it does

For each flight, in one command:

1. **Finds** the raw drone (`_D.OBS/.NAV/.MRK` + JPGs) and base-station RINEX files.
2. **Builds** the RTKLIB config, auto-filling the base antenna height.
3. **Injects** each photo's shutter time into the GNSS data as an event mark.
4. **Solves** the PPK positions with a live progress bar.
5. **Reports** the FIX / FLOAT accuracy percentage.
6. **Writes** the corrected GPS position into a copy of each JPG's EXIF.

Your original files are never touched — everything is written to `output/`.

## 📖 Full documentation

See **[`ppk_cli/README.md`](ppk_cli/README.md)** for the complete guide: setup, command
reference, the config file explained line-by-line, pipeline internals, validation
results, the tolerance-testing tool, and troubleshooting.

## Repository layout

```
ppk_cli/              the pipeline, tools, and full docs
├── ppk_pipeline.py     run this
├── compare_tags.py     per-photo tolerance comparison (testing)
├── compare_pos.py      .pos comparison helper
├── configs/            the validated RTKLIB recipe
└── README.md           full documentation
scripts/
└── parse_mrk.py        DJI .MRK parser — the pipeline's only external dependency
```

## Requirements

- **Windows**, **Python 3.9+**, and the `piexif` package (`pip install piexif`).
- **RTKLIB-EX** (`rnx2rtkp.exe`) — download from
  [rtklibexplorer releases](https://github.com/rtklibexplorer/RTKLIB/releases) and set
  its path at the top of `ppk_cli/ppk_pipeline.py`.

## License

See [LICENSE](LICENSE).
