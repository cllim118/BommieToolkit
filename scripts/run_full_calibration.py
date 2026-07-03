#!/usr/bin/env python3
"""
Full pipeline runner for Kalibr stereo calibration.

Steps:
  1) Extract images from left/right videos (extract_images)
  2) Sync image pairs by timestamp (match_images_by_ns)
  3) Run Kalibr stereo rig calibration (kalibr-calibrate-stereo-rig)
  4) Generate the final calibration.yaml

All parameters are read from a yaml config file.

pixi run -e kalibr calibrate-full configs/calibration.yaml
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml


def run(cmd: list[str], env: dict | None = None) -> None:
    print(f"\n$ {' '.join(cmd)}\n", flush=True)
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        print(f"[ERROR] command failed (exit={result.returncode}): {' '.join(cmd)}")
        sys.exit(result.returncode)


def extract_images(cfg: dict) -> None:
    extract_cfg = cfg["extract"]
    calib_dir = Path(cfg["calibration_dir"])

    pairs = [("left", "cam0"), ("right", "cam1")]
    for side, cam_folder in pairs:
        video = cfg["videos"][side]
        output = calib_dir / cam_folder

        cmd = [
            "pixi", "run", "-e", "default", "extract_images",
            "--video", video,
            "--output", str(output),
            "--resolution", str(extract_cfg["resolution"]),
        ]
        if extract_cfg.get("gray", True):
            cmd.append("--gray")
        if extract_cfg.get("skip") not in (None, ""):
            cmd += ["--skip", str(extract_cfg["skip"])]
        if extract_cfg.get("max_frames") not in (None, "", 0, "0"):
            cmd += ["--max_frames", str(extract_cfg["max_frames"])]

        run(cmd)


def sync_images(cfg: dict) -> dict:
    sync_cfg = cfg.get("sync", {})
    calib_dir = Path(cfg["calibration_dir"])
    left_dir = calib_dir / "cam0"
    right_dir = calib_dir / "cam1"

    if not sync_cfg.get("enabled", False):
        return {"left": left_dir, "right": right_dir}

    synced_left = calib_dir / "syncd" / "cam0"
    synced_right = calib_dir / "syncd" / "cam1"

    cmd = [
        "pixi", "run", "-e", "default", "match_images_by_ns",
        "--images_folder_left", str(left_dir),
        "--images_folder_right", str(right_dir),
        "--colmap_folder_left", str(synced_left),
        "--colmap_folder_right", str(synced_right),
        "--threshold-ns", str(sync_cfg.get("threshold_ns", 5_000_000)),
        "--sample_step", str(sync_cfg.get("sample_step", 5)),
    ]
    run(cmd)
    return {"left": synced_left, "right": synced_right}


def calibrate(cfg: dict, image_dirs: dict) -> None:
    calib_cfg = cfg["calibrate"]
    cmd = [
        "pixi", "run", "-e", "kalibr", "kalibr-calibrate-stereo-rig",
        f"images_folder_left={image_dirs['left']}",
        f"images_folder_right={image_dirs['right']}",
        f"output_folder={cfg['calibration_dir']}",
        f"target={calib_cfg['target']}",
        f"freq={calib_cfg['freq']}",
        f"verbose={calib_cfg.get('verbose', 0)}",
        f"create_bag={calib_cfg.get('create_bag', 1)}",
    ]

    env = os.environ.copy()
    if str(calib_cfg.get("manual_focal_length_init", 0)) == "1":
        env["KALIBR_MANUAL_FOCAL_LENGTH_INIT"] = "1"
    else:
        env.pop("KALIBR_MANUAL_FOCAL_LENGTH_INIT", None)

    run(cmd, env=env)


def generate_rig_config(cfg: dict) -> None:
    rig_cfg = cfg.get("rig_config", {})
    if not rig_cfg.get("enabled", False):
        return
 
    calib_dir = Path(cfg["calibration_dir"])
    camchain = calib_dir / "calibration-camchain.yaml"
    output = rig_cfg.get("output") or str(calib_dir / "rig_config.json")
 
    cmd = ["pixi", "run", "-e", "default", "get_rig_config_json", str(camchain), str(output)]
    run(cmd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalibr full pipeline runner")
    parser.add_argument("--config", default="calibration_config.yaml", help="Path to the config yaml")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[ERROR] Config file not found: {config_path}")
        sys.exit(1)

    cfg = yaml.safe_load(config_path.read_text())
    Path(cfg["calibration_dir"]).mkdir(parents=True, exist_ok=True)

    print("=== 1/4: Extracting images ===")
    extract_images(cfg)

    print("=== 2/4: Syncing images ===")
    image_dirs = sync_images(cfg)

    print("=== 3/4: Running calibration ===")
    calibrate(cfg, image_dirs)

    print("=== 4/4: Generating rig_config.json ===")
    generate_rig_config(cfg)

    print("\nDone. Outputs:")
    print(f"  {cfg['calibration_dir']}/calibration-camchain.yaml")
    print(f"  {cfg['calibration_dir']}/calibration-results-cam.txt")
    print(f"  {cfg['calibration_dir']}/calibration-report-cam.pdf")
    if cfg.get("rig_config", {}).get("enabled", False):
        out = cfg["rig_config"].get("output") or f"{cfg['calibration_dir']}/rig_config.json"
        print(f"  {out}")
 

if __name__ == "__main__":
    main()