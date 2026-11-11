#!/usr/bin/env python3
"""
Multi-camera rig calibration with Kalibr, driven by a single yaml config.

Input: the synced frames of run_images.py ({images_dir}/syncd/rig1/cam{i}).

Steps (toggle with the yaml `steps:` section or --steps):
  calibrate   Kalibr on the synced frames -> {output_dir}/calibration-camchain.yaml
  rig_config  camchain -> {output_dir}/rig_config.json (COLMAP rig config)

Usage:
    pixi run run-calibration configs/calibration.yaml
"""
from functools import cached_property
from pathlib import Path

from common import REPO_ROOT, fail, get_synced_dirs, load_config, pixi, run_script, run_steps, to_path

STEPS = ["calibrate", "rig_config"]


class Calibration:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.out = to_path(cfg["output_dir"])
        self.calib_cfg = cfg.get("calibrate", {})
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

    def calibrate(self) -> None:
        guesses = self.calib_cfg.get("focal_length_guesses") or []
        if guesses and len(guesses) != self.n:
            fail(f"calibrate.focal_length_guesses has {len(guesses)} values but there are {self.n} cameras.")
        manual_init = bool(guesses) or bool(self.calib_cfg.get("manual_focal_length_init", False))
        # With manual init Kalibr asks for one focal length per camera on stdin; feed the guesses if given.
        input_text = "\n".join(str(v) for v in guesses) + "\n" if guesses else None

        cmd = [f"images_folder_{i}={d}" for i, d in enumerate(self.synced_dirs)]
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


def main() -> None:
    cfg, active = load_config("Kalibr multi-camera rig calibration", STEPS, "configs/calibration.yaml")
    calib = Calibration(cfg)
    calib.out.mkdir(parents=True, exist_ok=True)

    run_steps([
        ("calibrate", "Kalibr calibration", calib.calibrate),
        ("rig_config", "Generating rig_config.json", calib.make_rig_config),
    ], active)

    print("\nDone. Outputs:")
    for p in (calib.camchain, calib.out / "calibration-results-cam.txt",
              calib.out / "calibration-report-cam.pdf", calib.rig_config):
        print(f"  {p}")


if __name__ == "__main__":
    main()
