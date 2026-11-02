#!/usr/bin/env python3
"""
transforms.json -> Gaussian splatting (nerfstudio) -> metric-scale .ply, driven by a single yaml config.

Steps (toggle with the yaml `steps:` section or --steps):
  train      ns-train on {colmap_dir}/transforms.json -> {output_dir}/train/{method}/{run_name}/
  export     ns-export gaussian-splat -> {output_dir}/exports/splat.ply
  transform  undo nerfstudio's normalization (dataparser_transforms.json) -> {output_dir}/splat_metric.ply

nerfstudio lives in its own pixi project (nerfstudio/pixi.toml); it is cloned and set up on first use.

Usage:
    pixi run run-3dgs configs/3dgs.yaml
"""
from common import REPO_ROOT, fail, load_config, pixi, run, run_script, run_steps, to_path

STEPS = ["train", "export", "transform"]


class Splatting:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.train_cfg = cfg.get("train", {})
        self.colmap_dir = to_path(cfg["colmap_dir"])
        self.out = to_path(cfg["output_dir"])
        self.method = self.train_cfg.get("method", "splatfacto")
        self.run_name = str(self.train_cfg.get("run_name", "latest"))

        self.run_dir = self.out / "train" / self.method / self.run_name
        self.export_dir = self.out / "exports"
        self.splat = self.export_dir / "splat.ply"
        self.metric_splat = self.out / "splat_metric.ply"

    def train(self) -> None:
        transforms = self.colmap_dir / "transforms.json"
        if not transforms.is_file():
            fail(f"{transforms} not found - run run-colmap first.")

        pixi("nerfstudio", "nerfstudio-post-install")
        cmd = [
            self.method,
            "--data", self.colmap_dir,
            # Fixed layout {output_dir}/train/{method}/{run_name} so later steps can find the run.
            "--output-dir", self.out,
            "--experiment-name", "train",
            "--timestamp", self.run_name,
            "--vis", self.train_cfg.get("vis", "viewer"),
            "--viewer.quit-on-train-completion", "True",
        ]
        if self.train_cfg.get("max_num_iterations"):
            cmd += ["--max-num-iterations", self.train_cfg["max_num_iterations"]]
        if self.train_cfg.get("masked", False):
            cmd += ["--pipeline.model.background_color", "random"]
        ns("ns-train", *cmd, *self.train_cfg.get("extra_args", []))

        print("\nTo inspect the trained model interactively:")
        print(f"  pixi run --manifest-path nerfstudio/pixi.toml ns-viewer --load-config {self.run_dir / 'config.yml'}")

    def export(self) -> None:
        config_yml = self.run_dir / "config.yml"
        if not config_yml.is_file():
            fail(f"{config_yml} not found - run the train step first (or fix train.run_name).")
        ns("ns-export", "gaussian-splat", "--load-config", config_yml, "--output-dir", self.export_dir)

    def transform(self) -> None:
        dataparser_transforms = self.run_dir / "dataparser_transforms.json"
        for f in (self.splat, dataparser_transforms):
            if not f.is_file():
                fail(f"{f} not found - run the train and export steps first.")
        run_script("transform_splats.py",
                   "--splat_path", self.splat,
                   "--transform_path", dataparser_transforms,
                   "--output_path", self.metric_splat)


def ns(*args) -> None:
    """Run a command in nerfstudio's own pixi project."""
    run(["pixi", "run", "--manifest-path", REPO_ROOT / "nerfstudio" / "pixi.toml", *args])


def main() -> None:
    cfg, active = load_config("transforms.json -> Gaussian splatting -> metric .ply", STEPS, "configs/3dgs.yaml")
    gs = Splatting(cfg)
    gs.out.mkdir(parents=True, exist_ok=True)

    run_steps([
        ("train", f"Training {gs.method} (nerfstudio)", gs.train),
        ("export", "Exporting gaussian splat (.ply)", gs.export),
        ("transform", "Recovering metric scale", gs.transform),
    ], active)

    print("\nDone. Outputs:")
    for p in (gs.run_dir, gs.splat, gs.metric_splat):
        print(f"  {p}")


if __name__ == "__main__":
    main()
