#!/usr/bin/env python3
"""
Multi-camera rig calibration with Kalibr, driven by a single yaml config.

Input: the synced frames of run_images.py ({images_dir}/synced/cam{i}).

Steps (toggle with the yaml `steps:` section or --steps):
  housing     (only if housing.enabled) underwater refraction removal starting from an in-air
              calibration -> {output_dir}/undistort_map_cam{i}.yaml + {output_dir}/undistorted/cam{i}
  calibrate   Kalibr on the synced (or undistorted) frames -> {output_dir}/calibration-camchain.yaml
  rig_config  camchain -> {output_dir}/rig_config.json (COLMAP rig config)

Usage:
    pixi run run-calibration configs/calibration.yaml
"""
import os
import shutil
from functools import cached_property
from pathlib import Path

from common import (REPO_ROOT, SCRIPTS_DIR, fail, get_synced_dirs, list_images, load_config, pixi,
                    run_script, run_steps, to_path)

STEPS = ["housing", "calibrate", "rig_config"]


class Calibration:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.out = to_path(cfg["output_dir"])
        self.calib_cfg = cfg.get("calibrate", {})
        self.housing_cfg = cfg.get("housing") or {}
        self.target = to_path(self.calib_cfg.get("target", "files/april_10x6.yaml"))

        self.camchain = self.out / "calibration-camchain.yaml"
        self.rig_config = self.out / "rig_config.json"

    # Resolved on first use, so steps that don't need frames (e.g. rig_config) run without them.
    @cached_property
    def synced_dirs(self) -> list[Path]:
        return get_synced_dirs(self.cfg)

    @property
    def n(self) -> int:
        return len(self.synced_dirs)

    @property
    def undistorted_dirs(self) -> list[Path]:
        return [self.out / "undistorted" / f"cam{i}" for i in range(self.n)]

    @property
    def calib_image_dirs(self) -> list[Path]:
        return self.undistorted_dirs if self.housing_cfg.get("enabled", False) else self.synced_dirs

    def housing(self) -> None:
        """Port of the old housing_calibration.sh: per camera, estimate the flat-port refraction
        from the in-air intrinsics, build an undistortion map and undistort the synced frames.
        The `calibrate` step then re-runs Kalibr on the undistorted frames."""
        if not self.housing_cfg.get("enabled", False):
            print("(housing.enabled is false - nothing to do)")
            return

        air_camchain = to_path(self.housing_cfg["air_calibration_dir"]) / "calibration-camchain.yaml"
        params = to_path(self.housing_cfg.get("params", "files/housing.yaml"))
        for f in (air_camchain, params):
            if not f.is_file():
                fail(f"Housing input not found: {f}")

        pixi("uwsfm", "pinax-post-install")
        pixi("uwsfm", "build-no-display-stub")
        # uwsfm tools try to open windows; the stub makes them work headless.
        env = {**os.environ, "LD_PRELOAD": str(SCRIPTS_DIR / "no_display.so")}

        # uwsfm tools drop d0.yaml / undistort_map.yaml in the cwd (or next to their input),
        # so run them inside a scratch folder and move the results out.
        work = self.out / "housing"
        work.mkdir(parents=True, exist_ok=True)

        for i in range(self.n):
            synced, undistorted = self.synced_dirs[i], self.undistorted_dirs[i]
            calibration = work / f"calibration_cam{i}.yaml"
            d0 = work / f"d0_cam{i}.yaml"
            undistort_map = self.out / f"undistort_map_cam{i}.yaml"

            run_script("camchain_to_calibration.py",
                       "--camchain", air_camchain, "--april", self.target, "--housing", params,
                       "--camera", f"cam{i}", "--output", calibration)

            pixi("uwsfm", "housing_calib", calibration, synced, env=env, cwd=work)
            move(work / "d0.yaml", d0)
            pixi("uwsfm", "python", REPO_ROOT / "pinax" / "scripts" / "d0_estimation.py", "--cfg", d0,
                 env=env, cwd=work)

            images = list_images(synced)
            if not images:
                fail(f"No synced images in {synced}")
            pixi("uwsfm", "undistort_estim", calibration, images[0], d0, env=env, cwd=work)
            move(work / "undistort_map.yaml", undistort_map)

            if undistorted.exists():
                shutil.rmtree(undistorted)
            undistorted.mkdir(parents=True)
            pixi("uwsfm", "undistorter", undistort_map, synced, undistorted, env=env, cwd=work)

    def calibrate(self) -> None:
        guesses = self.calib_cfg.get("focal_length_guesses") or []
        if guesses and len(guesses) != self.n:
            fail(f"calibrate.focal_length_guesses has {len(guesses)} values but there are {self.n} cameras.")
        manual_init = bool(guesses) or bool(self.calib_cfg.get("manual_focal_length_init", False))
        # With manual init Kalibr asks for one focal length per camera on stdin; feed the guesses if given.
        input_text = "\n".join(str(v) for v in guesses) + "\n" if guesses else None

        cmd = [f"images_folder_{i}={d}" for i, d in enumerate(self.calib_image_dirs)]
        cmd += [
            f"output_folder={self.out}",
            f"target={self.target}",
            f"freq={self.calib_cfg.get('freq', 30)}",
            f"camera_model={self.calib_cfg.get('camera_model', 'pinhole-radtan')}",
            f"verbose={int(bool(self.calib_cfg.get('verbose', False)))}",
            f"create_bag={int(bool(self.calib_cfg.get('create_bag', True)))}",
            f"manual_focal_length_init={int(manual_init)}",
        ]
        # calibrate_multi_rig.sh sources catkin_ws/ relative to the repo root.
        pixi("kalibr", "./scripts/calibrate_multi_rig.sh", *cmd, cwd=REPO_ROOT, input_text=input_text)

    def make_rig_config(self) -> None:
        run_script("convert_camchain_to_rig_config.py", self.camchain, self.rig_config)


def move(src: Path, dst: Path) -> None:
    if not src.is_file():
        fail(f"Expected output not found: {src}")
    src.replace(dst)


def main() -> None:
    cfg, active = load_config("Kalibr multi-camera rig calibration", STEPS, "configs/calibration.yaml")
    calib = Calibration(cfg)
    calib.out.mkdir(parents=True, exist_ok=True)

    run_steps([
        ("housing", "Underwater housing calibration / refraction removal", calib.housing),
        ("calibrate", "Kalibr calibration", calib.calibrate),
        ("rig_config", "Generating rig_config.json", calib.make_rig_config),
    ], active)

    print("\nDone. Outputs:")
    for p in (calib.camchain, calib.out / "calibration-results-cam.txt",
              calib.out / "calibration-report-cam.pdf", calib.rig_config):
        print(f"  {p}")


if __name__ == "__main__":
    main()
