#!/usr/bin/env python3
"""
Synced rig frames -> COLMAP sparse reconstruction -> transforms.json, driven by a single yaml config.

Input: the synced frames of run_images.py ({images_dir}/syncd/cam{i}) - or of run_refraction.py for underwater
data, whose output_dir is then both images_dir and calibration_dir.

Steps (toggle with the yaml `steps:` section or --steps):
  import       copy the synced frames -> {output_dir}/images/rig1/camera{i+1}
  masks        (only if use_masks) launch the interactive SAM3 app; save masks to {output_dir}/masks
  colmap       rig_config.json (scaled to the frames) -> feature extraction -> rig config -> matching
               -> mapper -> {output_dir}/sparse/0 (+ TXT model and mesh.ply in sparse/0_txt)
  colmap2nerf  sparse/0_txt + images -> {output_dir}/transforms.json (input for run_3dgs.py)

Usage:
    pixi run run-colmap configs/colmap.yaml
"""
import shutil
from pathlib import Path

from PIL import Image

from common import fail, get_synced_dirs, list_images, load_config, pixi, run_script, run_steps, to_path

STEPS = ["import", "masks", "colmap", "colmap2nerf"]


class Reconstruction:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.out = to_path(cfg["output_dir"])
        self.calib_dir = to_path(cfg["calibration_dir"])
        self.synced_dirs = get_synced_dirs(cfg)
        self.n = len(self.synced_dirs)
        self.colmap_cfg = cfg.get("colmap", {})
        self.use_masks = bool(cfg.get("use_masks", False))
        # Fixed mask for every frame: one path for all cameras, or a list with one per camera.
        # Stored as one entry per camera ([] when unused).
        static = cfg.get("static_mask") or []
        static = [static] if isinstance(static, str) else static
        if len(static) not in (0, 1, self.n):
            fail(f"static_mask needs 1 path or {self.n} (one per camera), got {len(static)}.")
        if static and self.use_masks:
            fail("use_masks (SAM, per image) and static_mask cannot be combined.")
        self.static_masks = [to_path(p) for p in static] * (self.n if len(static) == 1 else 1)

        # Must match the image_prefix written by convert_camchain_to_rig_config.py
        self.images = self.out / "images"
        self.image_dirs = [self.images / "rig1" / f"camera{i + 1}" for i in range(self.n)]
        self.masks = self.out / "masks"
        self.static_mask_dir = self.out / "static_masks"
        self.colmap_masks = self.out / "colmap_masks"
        self.rig_config = self.out / "rig_config.json"
        self.database = self.out / "database.db"
        self.sparse = self.out / "sparse"
        self.model = self.sparse / "0"
        self.model_txt = self.sparse / "0_txt"
        self.mesh = self.model_txt / "mesh.ply"
        self.transforms = self.out / "transforms.json"

    def import_images(self) -> None:
        for src, dst in zip(self.synced_dirs, self.image_dirs):
            if dst.exists():
                shutil.rmtree(dst)
            dst.mkdir(parents=True)
            for image in list_images(src):
                shutil.copy2(image, dst / image.name)
            print(f"{src} -> {dst}")

    def make_masks(self) -> None:
        if not self.use_masks:
            print("(use_masks is false - nothing to do)")
            return
        print("Launching the SAM3 app (requires a prior `pixi run -e sam hf auth login`).")
        print(f"Open http://0.0.0.0:7997, load each folder under {self.images} and save the masks to the "
              f"matching folder under {self.masks}. Close the app (Ctrl+C) when done.")
        pixi("sam", "create_masks")

    def colmap(self) -> None:
        cfg = self.colmap_cfg
        camchain = self.calib_dir / "calibration-camchain.yaml"
        if not camchain.is_file():
            fail(f"{camchain} not found - check calibration_dir.")

        # Fresh reconstruction every time: COLMAP would otherwise append to an old database.
        self.database.unlink(missing_ok=True)
        if self.sparse.exists():
            shutil.rmtree(self.sparse)
        self.sparse.mkdir(parents=True)

        # Scale the calibrated intrinsics to the resolution of the extracted frames.
        run_script("convert_camchain_to_rig_config.py", camchain, self.rig_config,
                   "--image-dirs", *self.image_dirs)

        cmd = [
            "feature_extractor",
            "--image_path", self.images,
            "--database_path", self.database,
            "--ImageReader.single_camera_per_folder", 1,
            "--FeatureExtraction.type", cfg.get("feature_type", "SIFT"),
        ]
        if self.use_masks or self.static_masks:
            cmd += ["--ImageReader.mask_path", self.link_colmap_masks()]
        colmap(*cmd, *cfg.get("feature_extractor_args", []))

        colmap("rig_configurator", "--database_path", self.database, "--rig_config_path", self.rig_config)

        matcher = cfg.get("matcher", "sequential")
        if matcher not in ("sequential", "exhaustive"):
            fail(f"colmap.matcher must be 'sequential' or 'exhaustive', got '{matcher}'")
        colmap(f"{matcher}_matcher",
               "--database_path", self.database,
               "--FeatureMatching.type", cfg.get("matching_type", "SIFT_BRUTEFORCE"),
               *cfg.get("matcher_args", []))

        mapper_opts = [arg for k, v in (cfg.get("mapper") or {}).items() for arg in (f"--Mapper.{k}", v)]
        colmap("mapper",
               "--database_path", self.database,
               "--image_path", self.images,
               "--output_path", self.sparse,
               *mapper_opts)

        if not self.model.is_dir():
            fail(f"COLMAP mapper did not produce {self.model}")
        self.model_txt.mkdir()
        colmap("model_converter", "--input_path", self.model, "--output_path", self.model_txt, "--output_type", "TXT")
        colmap("model_converter", "--input_path", self.model, "--output_path", self.mesh, "--output_type", "PLY")

    def colmap2nerf(self) -> None:
        cmd = [
            "--text", self.model_txt,
            "--images", self.images,
            "--out", self.transforms,
            "--ply_file_path", self.mesh,
            "--keep_colmap_coords",
        ]
        if self.use_masks:
            cmd += ["--masks", self.masks]
        elif self.static_masks:
            for image_dir, mask in zip(self.image_dirs, self.copy_static_masks()):
                cmd += ["--static_mask", f"{image_dir.relative_to(self.images)}={mask}"]
        run_script("colmap2nerf.py", *cmd)

    def copy_static_masks(self) -> list[Path]:
        """Copy each camera's static mask to {output_dir}/static_masks/camera{i+1}.png, so transforms.json
        only references files inside output_dir. Checks the mask matches the frame size."""
        self.static_mask_dir.mkdir(parents=True, exist_ok=True)
        copies = []
        for src, image_dir in zip(self.static_masks, self.image_dirs):
            frame = first_frame(image_dir)
            dst = self.static_mask_dir / f"{image_dir.name}.png"
            with Image.open(src) as mask, Image.open(frame) as image:
                if mask.size != image.size:
                    fail(f"static_mask {src} is {mask.size[0]}x{mask.size[1]} but the frames in {image_dir} "
                         f"are {image.size[0]}x{image.size[1]}.")
                mask.convert("L").save(dst)
            copies.append(dst)
        return copies

    def link_colmap_masks(self) -> Path:
        """COLMAP looks for {mask_path}/{image_name}.png (e.g. rig1/camera1/0001.png.png). Link every frame
        to its mask - the SAM mask {masks}/rig1/camera1/0001.png or the camera's static mask."""
        camera_masks = self.copy_static_masks() if self.static_masks else []
        if self.colmap_masks.exists():
            shutil.rmtree(self.colmap_masks)

        for i, image_dir in enumerate(self.image_dirs):
            for image in list_images(image_dir):
                name = image.relative_to(self.images)
                mask = camera_masks[i] if self.static_masks else (self.masks / name).with_suffix(".png")
                if not mask.is_file():
                    fail(f"Mask not found for {name}: {mask}")
                link = self.colmap_masks / f"{name}.png"
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(mask)
        return self.colmap_masks


def colmap(*args) -> None:
    pixi("colmap", "colmap", *args)


def first_frame(image_dir: Path) -> Path:
    images = list_images(image_dir) if image_dir.is_dir() else []
    if not images:
        fail(f"No frames in {image_dir} - run the import step first.")
    return images[0]


def main() -> None:
    cfg, active = load_config("Synced rig frames -> COLMAP reconstruction -> transforms.json", STEPS, "configs/colmap.yaml")
    rec = Reconstruction(cfg)
    rec.out.mkdir(parents=True, exist_ok=True)

    run_steps([
        ("import", "Importing synced frames", rec.import_images),
        ("masks", "Creating masks with SAM3", rec.make_masks),
        ("colmap", "COLMAP reconstruction", rec.colmap),
        ("colmap2nerf", "Converting to transforms.json", rec.colmap2nerf),
    ], active)

    print("\nDone. Outputs:")
    for p in (rec.images, rec.database, rec.model, rec.transforms):
        print(f"  {p}")
    print("\nInspect with:")
    print(f"  pixi run visualize_colmap --sparse_dir {rec.model} --images_dir {rec.images}")


if __name__ == "__main__":
    main()
