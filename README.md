<div align="center">
    <h1>BommieToolkit</h1>
    <a href="https://github.com/BommieToolkit/BommieToolkit"><img src="https://img.shields.io/badge/Linux-FCC624?logo=linux&logoColor=black" /></a>
    <a href="https://github.com/BommieToolkit/BommieToolkit"><img src="https://img.shields.io/badge/macOS-000000?style=flat&logo=apple&logoColor=white" /></a>
    <br />
</div>

<p align="center">
    <a href="https://scholar.google.com/citations?user=SDtnGogAAAAJ&hl=en"><strong>Alejandro Fontan</strong></a>
    ·
    <a href="https://scholar.google.com/citations?user=MNrMUPMAAAAJ&hl=en"><strong>Emilio Olivastri</strong></a>
</p>

## Pipeline

The toolkit runs in five stages (refraction removal only for underwater data). Each stage is driven by **one yaml file** (templates in `configs/`):

| Stage | Command | Config | Main output |
|---|---|---|---|
| Image extraction | `pixi run run-images <cfg>` | `configs/images.yaml` | `syncd/cam{i}` |
| Rig calibration (Kalibr) | `pixi run run-calibration <cfg>` | `configs/calibration.yaml` | `calibration-camchain.yaml`, `rig_config.json` |
| Refraction removal (Refrax) | `pixi run run-refraction <cfg>` | `configs/refraction.yaml` | corrected `syncd/cam{i}`, `calibration-camchain.yaml` |
| COLMAP reconstruction | `pixi run run-colmap <cfg>` | `configs/colmap.yaml` | `sparse/0`, `transforms.json` |
| Gaussian splatting (nerfstudio) | `pixi run run-3dgs <cfg>` | `configs/3dgs.yaml` | `splat_metric.ply` |

Copy a template, edit the paths, and run it. Per-dataset configs can live in `configs/local/` (git-ignored):

```bash
cp configs/colmap.yaml configs/local/my_scene.yaml
pixi run run-colmap configs/local/my_scene.yaml
```

Each config has a `steps:` section to choose which steps run. Override it from the command line, e.g. to
re-run only part of a stage:

```bash
pixi run run-images configs/local/my_scene_images.yaml --steps sync
pixi run run-calibration configs/local/my_rig.yaml --steps calibrate,rig_config
pixi run run-refraction configs/local/my_scene_refraction.yaml --steps camchain
pixi run run-colmap configs/local/my_scene.yaml --steps colmap,colmap2nerf
pixi run run-3dgs configs/local/my_scene_3dgs.yaml --steps export,transform
```

Stages are chained through paths: `calibration.yaml`, `refraction.yaml` and `colmap.yaml` point at a
`run-images` `output_dir` (`images_dir`); `refraction.yaml` and `colmap.yaml` point at the calibration
`output_dir` (`calibration_dir`); and `3dgs.yaml` points at the COLMAP `output_dir` (`colmap_dir`). For
underwater data, `colmap.yaml` uses the refraction `output_dir` as both `images_dir` and `calibration_dir`.

### 0. Image extraction

Run `run-images` once per video set, with one config each: the calibration target videos (e.g.
`output_dir: images_calibration`) and the scene videos (e.g. `output_dir: images_scene`).

`run-images` steps:
- `extract`: video → frames (`{output_dir}/frames/cam{i}`), named by the GoPro timecode in ns.
- `sync`: keeps frames that have a timestamp match in every camera (`{output_dir}/syncd/cam{i}`);
  `sync.sample_step` subsamples the matched sets.

Any number of cameras is supported; `cam0` (first entry in `cameras:`) is the reference. For calibration,
`extract.gray: true`, `extract.skip: 5.0` and `sync.sample_step: 3` are good defaults.

### 1. Rig calibration

Build Kalibr once:
```bash
pixi run -e kalibr build
```

`run-calibration` reads the synced frames from `images_dir`. Steps:
- `calibrate`: Kalibr multi-camera calibration → `calibration-camchain.yaml`, report PDF.
- `rig_config`: camchain → COLMAP `rig_config.json`.

- `calibrate.verbose: true` shows Kalibr's plots, which needs a display.
- `calibrate.create_bag: false` reuses an existing `calibration.bag`.
- If Kalibr fails to initialise the focal length, `manual_focal_length_init: true` makes it ask for one per
  camera; a good guess is half the image height. Set `focal_length_guesses: [...]` to answer automatically.

For underwater data, calibrate the rig **in air** (with `camera_model: pinhole-radtan`) and remove the
refraction in the next stage.

### 2. Refraction removal (underwater only)

[Refrax](https://github.com/cllim118/Refrax) warps each underwater frame behind a flat port into a
refraction-free pinhole image. It lives in its own pixi project (`refrax/`); the first `run-refraction`
clones and sets it up.

`run-refraction` reads the synced underwater frames from `images_dir`, the in-air
`calibration-camchain.yaml` from `calibration_dir` and the housing from `housing` (default
`files/housing.yaml`: port normal, refractive indices, camera-to-port distance `d0` and glass thickness
`tglass`, in metres). Steps:
- `correct`: for each camera, scales the in-air intrinsics to the frame size and runs Refrax →
  `{output_dir}/syncd/cam{i}` (PNG) and `{output_dir}/refrax/cam{i}/new_calibration.yaml`.
- `camchain`: writes `{output_dir}/calibration-camchain.yaml`: the in-air camchain with each camera's
  intrinsics replaced by the Refrax pinhole ones (no distortion, new resolution). The rig extrinsics are kept.

The correction is exact at the scene depth `correction.z0_fixed`. `correction.zoom: null` picks the zoom per
camera; `fit_canvas` and `crop_valid_bbox` set the output size (see the Refrax README). Point `colmap.yaml`'s
`images_dir` and `calibration_dir` at the refraction `output_dir`.

### 3. COLMAP reconstruction

`run-colmap` reads the synced frames from `images_dir`. Steps:
- `import`: copies them into `{output_dir}/images/rig1/camera{i+1}`.
- `masks`: (only if `use_masks: true`) opens the SAM3 app at http://0.0.0.0:7997. It needs a Hugging Face
  login first (`pixi run -e sam hf auth login`). Save masks to `{output_dir}/masks`, mirroring the `images` layout.
- `colmap`: builds `rig_config.json` with intrinsics scaled to the frame size, then runs feature extraction,
  rig configuration, matching (sequential or exhaustive; SIFT or ALIKED/LightGlue) and the mapper. It also
  exports a TXT model and `mesh.ply`.
- `colmap2nerf`: writes `transforms.json` for nerfstudio.

For a mask that is the same in every frame (housing edges, rig parts in view), set `static_mask` to one
image for all cameras or to a list with one per camera. White pixels are kept and black ones ignored, and
the mask must match the frame size. COLMAP skips the masked area, and nerfstudio leaves it out of the loss.

Visualize a reconstruction with the COLMAP GUI or with [Rerun](https://rerun.io):
```bash
pixi run -e colmap colmap gui --database_path colmap_output/database.db \
  --image_path colmap_output/images --import_path colmap_output/sparse/0

pixi run visualize_colmap --sparse_dir colmap_output/sparse/0 --images_dir colmap_output/images
#   --serve           open the viewer in the browser
#   --resize 1280x720 downscale images before streaming
```

### 4. Gaussian splatting

nerfstudio lives in its own pixi project (`nerfstudio/`); the first `run-3dgs` clones and sets it up.

`run-3dgs` steps:
- `train`: `ns-train splatfacto` → `{output_dir}/train/{method}/{run_name}/`.
- `export`: `ns-export gaussian-splat` → `{output_dir}/exports/splat.ply`.
- `transform`: nerfstudio normalizes the scene for training; this step applies the inverse
  `dataparser_transforms.json` to get back the COLMAP metric frame → `{output_dir}/splat_metric.ply`.

Set `train.masked: true` if the COLMAP stage used masks (random background). Inspect a trained model with:
```bash
pixi run --manifest-path nerfstudio/pixi.toml ns-viewer --load-config 3dgs_output/train/splatfacto/latest/config.yml
```

### Individual tools

The pipeline scripts can also be run on their own (`--help` for options): `extract_images`,
`match_images_by_ns`, `get_rig_config_json`, `colmap2nerf`, `transform_splats`, `visualize_colmap`
(default env) and `calibrate-rig` (`-e kalibr`).

## BommieToolkit Roadmap

- [ ] Make Kalibr a Conda package
- [X] Implement one end-to-end command, from videos to GS.
- [ ] Documentation for the intermediate outputs
- [ ] Documentation on recording calibration/reconstruction data
- [ ] Documentation on gopro settings
- [ ] How to build an underwater calibration pattern
- [X] Refraction Removal
