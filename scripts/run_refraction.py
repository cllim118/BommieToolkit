#!/usr/bin/env python3
"""
Flat-port refraction removal with Refrax (https://github.com/cllim118/Refrax), driven by a single yaml config.

Inputs: the synced underwater frames of run_images.py ({images_dir}/syncd/cam{i}) and the in-air rig
calibration of run_calibration.py ({calibration_dir}/calibration-camchain.yaml).

Steps (toggle with the yaml `steps:` section or --steps):
  correct   per camera: in-air intrinsics (scaled to the frame size) + housing -> Refrax
            -> corrected frames {output_dir}/syncd/cam{i} + {output_dir}/refrax/cam{i}/new_calibration.yaml
  camchain  in-air camchain with each camera's intrinsics replaced by the Refrax pinhole ones (no distortion,
            new resolution); rig extrinsics unchanged -> {output_dir}/calibration-camchain.yaml

The output folder has the same layout as run-images + run-calibration output, so colmap.yaml uses it as both
`images_dir` and `calibration_dir`.

Usage:
    pixi run run-refraction configs/refraction.yaml
"""
import shutil
from pathlib import Path

import yaml
from PIL import Image

from common import REPO_ROOT, fail, get_synced_dirs, list_images, load_config, pixi, run, run_steps, synced_dirs, to_path

STEPS = ["correct", "camchain"]
REFRAX_DIR = REPO_ROOT / "refrax"
HOUSING_KEYS = ("n_port", "mu_a", "mu_g", "mu_w", "d0", "tglass")


class Refraction:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.out = to_path(cfg["output_dir"])
        self.air_camchain = to_path(cfg["calibration_dir"]) / "calibration-camchain.yaml"
        self.correction = cfg.get("correction") or {}
        if self.correction.get("depth_dir"):
            fail("Per-image depth maps are not supported: every image would get its own intrinsics.")

        self.camchain = self.out / "calibration-camchain.yaml"
        self.work = self.out / "refrax"

    def load_air_camchain(self) -> dict:
        if not self.air_camchain.is_file():
            fail(f"{self.air_camchain} not found - run run-calibration (in air) first, or check calibration_dir.")
        return yaml.safe_load(self.air_camchain.read_text())

    def load_housing(self) -> dict:
        housing = self.cfg.get("housing")
        if isinstance(housing, str):
            path = to_path(housing)
            if not path.is_file():
                fail(f"Housing file not found: {path}")
            housing = yaml.safe_load(path.read_text())
        missing = [k for k in HOUSING_KEYS if k not in (housing or {})]
        if missing:
            fail(f"housing is missing {missing} (expected keys: {list(HOUSING_KEYS)})")
        return {k: housing[k] for k in HOUSING_KEYS}

    def calib_path(self, i: int) -> Path:
        return self.work / f"cam{i}" / "new_calibration.yaml"

    def correct(self) -> None:
        in_dirs = get_synced_dirs(self.cfg)
        camchain = self.load_air_camchain()
        if len(camchain) != len(in_dirs):
            fail(f"{self.air_camchain} has {len(camchain)} cameras but {self.cfg['images_dir']} has {len(in_dirs)}.")
        housing = self.load_housing()

        pixi("refrax", "refrax-clone")
        out_dirs = synced_dirs(self.out, len(in_dirs))
        for i, (src, dst) in enumerate(zip(in_dirs, out_dirs)):
            cam = camchain[f"cam{i}"]
            if cam.get("camera_model") != "pinhole" or cam.get("distortion_model") != "radtan":
                fail(f"cam{i}: Refrax needs a pinhole-radtan calibration, got "
                     f"{cam.get('camera_model')}-{cam.get('distortion_model')}.")
            with Image.open(list_images(src)[0]) as image:
                W, H = image.size
            fx, fy, cx, cy = scaled_intrinsics(cam, W, H)
            k1, k2, p1, p2 = cam["distortion_coeffs"]  # normalised coordinates: resolution independent

            # Fresh output every run: Refrax skips images that already exist.
            work = self.work / f"cam{i}"
            for d in (dst, work):
                if d.exists():
                    shutil.rmtree(d)
            work.mkdir(parents=True)

            refrax_cfg = {
                "camera": {"W": W, "H": H, "fx": fx, "fy": fy, "cx": cx, "cy": cy,
                           "k1": k1, "k2": k2, "p1": p1, "p2": p2},
                "housing": housing,
                "paths": {
                    "rgb_dir": str(src),
                    "output_dir": str(dst),
                    "mask_dir": str(work / "mask"),
                    "depth_dir": None,
                    "calib_path": str(self.calib_path(i)),
                },
                "correction": {
                    "z0_fixed": self.correction.get("z0_fixed", 1.0),
                    "zoom": self.correction.get("zoom"),
                    "fit_canvas": self.correction.get("fit_canvas", True),
                    "crop_valid_bbox": self.correction.get("crop_valid_bbox", True),
                    "step_size": 1,
                },
            }
            refrax_cfg_path = work / "config.yaml"
            refrax_cfg_path.write_text(yaml.safe_dump(refrax_cfg, sort_keys=False))

            print(f"\n--- cam{i}: {src} -> {dst}")
            run(["pixi", "run", "--manifest-path", REFRAX_DIR / "pixi.toml", "remove-refraction", refrax_cfg_path])

            n_in, n_out = len(list_images(src)), len(list_images(dst))
            if n_out != n_in:
                fail(f"cam{i}: Refrax wrote {n_out} of {n_in} images to {dst} (see the log above).")

    def write_camchain(self) -> None:
        camchain = self.load_air_camchain()
        for i in range(len(camchain)):
            path = self.calib_path(i)
            if not path.is_file():
                fail(f"{path} not found - run the correct step first.")
            new = yaml.safe_load(path.read_text())
            cam = camchain[f"cam{i}"]
            cam["intrinsics"] = [*new["focal_length"], *new["principal_point"]]
            cam["distortion_coeffs"] = [0.0, 0.0, 0.0, 0.0]
            cam["resolution"] = list(new["image_dimension"])
            print(f"cam{i}: zoom {new['zoom']:.4f}, {cam['resolution'][0]}x{cam['resolution'][1]}, "
                  f"intrinsics {[round(v, 2) for v in cam['intrinsics']]}")
        self.camchain.write_text(yaml.safe_dump(camchain, sort_keys=False))
        print(f"Wrote {self.camchain} (rig extrinsics from {self.air_camchain})")


def scaled_intrinsics(cam: dict, W: int, H: int) -> list[float]:
    """Kalibr intrinsics [fx, fy, cx, cy] scaled from the calibration resolution to the frame size."""
    fx, fy, cx, cy = cam["intrinsics"]
    sx, sy = W / cam["resolution"][0], H / cam["resolution"][1]
    return [fx * sx, fy * sy, cx * sx, cy * sy]


def main() -> None:
    cfg, active = load_config("Flat-port refraction removal (Refrax)", STEPS, "configs/refraction.yaml")
    refr = Refraction(cfg)
    refr.out.mkdir(parents=True, exist_ok=True)

    run_steps([
        ("correct", "Removing refraction (Refrax)", refr.correct),
        ("camchain", "Writing the refraction-free calibration", refr.write_camchain),
    ], active)

    print("\nDone. Outputs (use as images_dir and calibration_dir in colmap.yaml):")
    for p in (refr.out / "syncd", refr.camchain):
        print(f"  {p}")


if __name__ == "__main__":
    main()
