#!/usr/bin/env python3
"""
Rig videos -> timestamp-synced frames, driven by a single yaml config.
The output folder is the `images_dir` input of run_calibration.py and run_colmap.py.

Steps (toggle with the yaml `steps:` section or --steps):
  extract  videos -> {output_dir}/frames/cam{i}, named by the GoPro timecode in ns
  sync     keep frames that have a timestamp match in every camera -> {output_dir}/synced/cam{i}

Usage:
    pixi run run-images configs/images.yaml
"""
from common import (extract_images, get_videos, load_config, run_steps, synced_dirs, sync_images, to_path)

STEPS = ["extract", "sync"]


def main() -> None:
    cfg, active = load_config("Rig videos -> timestamp-synced frames", STEPS, "configs/images.yaml")
    out = to_path(cfg["output_dir"])
    videos = get_videos(cfg)
    frame_dirs = [out / "frames" / f"cam{i}" for i in range(len(videos))]
    out_dirs = synced_dirs(out, len(videos))
    out.mkdir(parents=True, exist_ok=True)

    run_steps([
        ("extract", "Extracting frames from videos",
         lambda: extract_images(videos, frame_dirs, cfg.get("extract", {}))),
        ("sync", "Syncing frames by timestamp",
         lambda: sync_images(frame_dirs, out_dirs, cfg.get("sync", {}))),
    ], active)

    print("\nDone. Synced frames:")
    for d in out_dirs:
        print(f"  {d}")


if __name__ == "__main__":
    main()
