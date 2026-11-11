"""Shared helpers for the yaml-driven pipeline runners (run_calibration / run_colmap / run_3dgs)."""
import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def fail(msg: str) -> None:
    sys.exit(f"[ERROR] {msg}")


def run(cmd: list, env: dict | None = None, cwd: Path | None = None, input_text: str | None = None) -> None:
    cmd = [str(c) for c in cmd]
    print(f"\n$ {' '.join(cmd)}\n", flush=True)
    result = subprocess.run(cmd, env=env, cwd=cwd, input=input_text, text=input_text is not None)
    if result.returncode != 0:
        fail(f"command failed (exit={result.returncode}): {' '.join(cmd)}")


def run_script(name: str, *args, **kwargs) -> None:
    """Run one of the python scripts in scripts/ with the current (default pixi env) interpreter."""
    run([sys.executable, SCRIPTS_DIR / name, *args], **kwargs)


def pixi(env: str, *args, **kwargs) -> None:
    """Run a command inside another pixi environment of this repo (works from any cwd)."""
    run(["pixi", "run", "--manifest-path", REPO_ROOT / "pixi.toml", "-e", env, *args], **kwargs)


def to_path(value) -> Path:
    """Config paths are relative to the directory the runner is launched from (the repo root for pixi tasks)."""
    return Path(value).expanduser().resolve()


def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)


def load_config(description: str, steps: list[str], default_config: str) -> tuple[dict, list[str]]:
    """Parse --config/--steps and return (cfg, active steps in pipeline order).

    Step selection: --steps on the CLI > `steps:` section of the yaml > all steps.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("config", type=Path, nargs="?", default=REPO_ROOT / default_config,
                        help=f"Path to the yaml config (default: {default_config})")
    parser.add_argument("--steps", default=None,
                        help=f"Comma-separated steps to run (overrides the yaml `steps:`). Choices: {','.join(steps)}")
    args = parser.parse_args()

    if not args.config.is_file():
        fail(f"Config file not found: {args.config}")
    cfg = yaml.safe_load(args.config.read_text()) or {}

    if args.steps:
        requested = {s.strip() for s in args.steps.split(",") if s.strip()}
        unknown = requested - set(steps)
        if unknown:
            fail(f"Unknown step(s) in --steps: {sorted(unknown)}. Valid steps: {steps}")
    else:
        steps_cfg = cfg.get("steps") or {}
        unknown = set(steps_cfg) - set(steps)
        if unknown:
            fail(f"Unknown step(s) in yaml `steps:`: {sorted(unknown)}. Valid steps: {steps}")
        requested = {s for s in steps if steps_cfg.get(s, True)}

    return cfg, [s for s in steps if s in requested]


def run_steps(pipeline: list[tuple[str, str, Callable[[], None]]], active: list[str]) -> None:
    print(f"Active steps: {active}")
    for idx, (name, title, fn) in enumerate(pipeline, start=1):
        header = f"=== {idx}/{len(pipeline)} [{name}] {title}"
        if name in active:
            print(f"\n{header} ===", flush=True)
            fn()
        else:
            print(f"\n{header} (skipped) ===", flush=True)


def get_videos(cfg: dict) -> list[Path]:
    cameras = cfg.get("cameras") or []
    if not cameras:
        fail("Config must have a non-empty `cameras:` list.")
    return [to_path(cam["video"]) for cam in cameras]


def synced_dirs(images_dir: Path, n: int) -> list[Path]:
    """Per-camera synced frame folders written by run_images.py."""
    return [images_dir / "syncd" / f"cam{i}" for i in range(n)]


def get_synced_dirs(cfg: dict) -> list[Path]:
    """Synced frame folders of the run_images.py output referenced by `images_dir` (cam0, cam1, ...)."""
    if not cfg.get("images_dir"):
        fail("Config must set `images_dir` (output_dir of run-images).")
    images_dir = to_path(cfg["images_dir"])
    n = 0
    while (images_dir / "syncd" / f"cam{n}").is_dir():
        n += 1
    if n == 0:
        fail(f"No synced frames in {images_dir / 'synced'} - run `pixi run run-images` first.")
    dirs = synced_dirs(images_dir, n)
    for d in dirs:
        if not list_images(d):
            fail(f"No images in {d} - run `pixi run run-images` first.")
    return dirs


def extract_images(videos: list[Path], out_dirs: list[Path], extract_cfg: dict) -> None:
    """Video -> timestamp-named frames (scripts/vid2imgs.py), one output folder per camera."""
    for video, out_dir in zip(videos, out_dirs):
        cmd = ["--video", video, "--output", out_dir, "--resolution", extract_cfg.get("resolution", "medium")]
        if extract_cfg.get("gray", False):
            cmd.append("--gray")
        if extract_cfg.get("skip"):
            cmd += ["--skip", extract_cfg["skip"]]
        if extract_cfg.get("max_frames"):
            cmd += ["--max_frames", extract_cfg["max_frames"]]
        # No per-video frame subsampling here: offsets between cameras would break timestamp matching.
        # Subsample matched sets with sync.sample_step instead.
        run_script("vid2imgs.py", *cmd)


def sync_images(in_dirs: list[Path], out_dirs: list[Path], sync_cfg: dict) -> None:
    """Keep only frames that have a timestamp match in every camera (scripts/match_images_by_ns.py).

    With a single camera this just copies (and subsamples) the frames.
    """
    cmd = []
    for i, (src, dst) in enumerate(zip(in_dirs, out_dirs)):
        cmd += [f"--images_folder_{i}", src, f"--colmap_folder_{i}", dst]
    cmd += [
        "--threshold-ns", sync_cfg.get("threshold_ns", 5_000_000),
        "--sample_step", sync_cfg.get("sample_step", 1),
    ]
    run_script("match_images_by_ns.py", *cmd)
