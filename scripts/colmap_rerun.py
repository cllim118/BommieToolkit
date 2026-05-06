#!/usr/bin/env python3
"""Visualize COLMAP sparse reconstruction output using Rerun.

Reads cameras, images and 3-D points from a COLMAP sparse model directory
(either text or binary format) and streams the data to a Rerun viewer so that
camera poses, point clouds and reprojection keypoints can be inspected
interactively.

Usage
-----
    pixi run visualize_colmap \\
        --sparse_dir monkey_output/sparse/0 \\
        --images_dir monkey_output/colmap_images

See `pixi run visualize_colmap --help` for all options.
"""

from __future__ import annotations

import collections
import os
import re
import struct
from argparse import ArgumentParser
from pathlib import Path
from typing import Mapping

import cv2
import numpy as np
import numpy.typing as npt

import rerun as rr
import rerun.blueprint as rrb

# ---------------------------------------------------------------------------
# COLMAP data structures (adapted from the official colmap/scripts/python)
# ---------------------------------------------------------------------------

CameraModel = collections.namedtuple("CameraModel", ["model_id", "model_name", "num_params"])
Camera = collections.namedtuple("Camera", ["id", "model", "width", "height", "params"])
BaseImage = collections.namedtuple("Image", ["id", "qvec", "tvec", "camera_id", "name", "xys", "point3D_ids"])
Point3D = collections.namedtuple("Point3D", ["id", "xyz", "rgb", "error", "image_ids", "point2D_idxs"])

CAMERA_MODELS = {
    CameraModel(model_id=0,  model_name="SIMPLE_PINHOLE",       num_params=3),
    CameraModel(model_id=1,  model_name="PINHOLE",              num_params=4),
    CameraModel(model_id=2,  model_name="SIMPLE_RADIAL",        num_params=4),
    CameraModel(model_id=3,  model_name="RADIAL",               num_params=5),
    CameraModel(model_id=4,  model_name="OPENCV",               num_params=8),
    CameraModel(model_id=5,  model_name="OPENCV_FISHEYE",       num_params=8),
    CameraModel(model_id=6,  model_name="FULL_OPENCV",          num_params=12),
    CameraModel(model_id=7,  model_name="FOV",                  num_params=5),
    CameraModel(model_id=8,  model_name="SIMPLE_RADIAL_FISHEYE", num_params=4),
    CameraModel(model_id=9,  model_name="RADIAL_FISHEYE",       num_params=5),
    CameraModel(model_id=10, model_name="THIN_PRISM_FISHEYE",   num_params=12),
}
CAMERA_MODEL_IDS   = {m.model_id:   m for m in CAMERA_MODELS}
CAMERA_MODEL_NAMES = {m.model_name: m for m in CAMERA_MODELS}


class Image(BaseImage):
    def qvec2rotmat(self) -> npt.NDArray[np.float64]:
        return qvec2rotmat(self.qvec)


def qvec2rotmat(qvec: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    return np.array([
        [1 - 2*qvec[2]**2 - 2*qvec[3]**2,
         2*qvec[1]*qvec[2] - 2*qvec[0]*qvec[3],
         2*qvec[3]*qvec[1] + 2*qvec[0]*qvec[2]],
        [2*qvec[1]*qvec[2] + 2*qvec[0]*qvec[3],
         1 - 2*qvec[1]**2 - 2*qvec[3]**2,
         2*qvec[2]*qvec[3] - 2*qvec[0]*qvec[1]],
        [2*qvec[3]*qvec[1] - 2*qvec[0]*qvec[2],
         2*qvec[2]*qvec[3] + 2*qvec[0]*qvec[1],
         1 - 2*qvec[1]**2 - 2*qvec[2]**2],
    ])


# --- binary helpers ---------------------------------------------------------

def _read_bytes(fid, num_bytes, fmt, endian="<"):
    data = fid.read(num_bytes)
    return struct.unpack(endian + fmt, data)


# --- text readers -----------------------------------------------------------

def read_cameras_text(path: Path) -> dict[int, Camera]:
    cameras: dict[int, Camera] = {}
    with open(path) as fid:
        for line in fid:
            line = line.strip()
            if not line or line[0] == "#":
                continue
            elems = line.split()
            camera_id = int(elems[0])
            model     = elems[1]
            width     = int(elems[2])
            height    = int(elems[3])
            params    = np.array(list(map(float, elems[4:])))
            cameras[camera_id] = Camera(id=camera_id, model=model, width=width, height=height, params=params)
    return cameras


def read_images_text(path: Path) -> dict[int, Image]:
    images: dict[int, Image] = {}
    with open(path) as fid:
        while True:
            line = fid.readline()
            if not line:
                break
            line = line.strip()
            if not line or line[0] == "#":
                continue
            elems      = line.split()
            image_id   = int(elems[0])
            qvec       = np.array(list(map(float, elems[1:5])))
            tvec       = np.array(list(map(float, elems[5:8])))
            camera_id  = int(elems[8])
            image_name = elems[9]
            elems2     = fid.readline().split()
            xys         = np.column_stack([
                list(map(float, elems2[0::3])),
                list(map(float, elems2[1::3])),
            ])
            point3D_ids = np.array(list(map(int, elems2[2::3])))
            images[image_id] = Image(
                id=image_id, qvec=qvec, tvec=tvec,
                camera_id=camera_id, name=image_name,
                xys=xys, point3D_ids=point3D_ids,
            )
    return images


def read_points3D_text(path: Path) -> dict[int, Point3D]:
    points3D: dict[int, Point3D] = {}
    with open(path) as fid:
        for line in fid:
            line = line.strip()
            if not line or line[0] == "#":
                continue
            elems       = line.split()
            point3D_id  = int(elems[0])
            xyz         = np.array(list(map(float, elems[1:4])))
            rgb         = np.array(list(map(int,   elems[4:7])))
            error       = float(elems[7])
            image_ids   = np.array(list(map(int, elems[8::2])))
            point2D_idxs = np.array(list(map(int, elems[9::2])))
            points3D[point3D_id] = Point3D(
                id=point3D_id, xyz=xyz, rgb=rgb, error=error,
                image_ids=image_ids, point2D_idxs=point2D_idxs,
            )
    return points3D


# --- binary readers ---------------------------------------------------------

def read_cameras_binary(path: Path) -> dict[int, Camera]:
    cameras: dict[int, Camera] = {}
    with open(path, "rb") as fid:
        (num_cameras,) = _read_bytes(fid, 8, "Q")
        for _ in range(num_cameras):
            props     = _read_bytes(fid, 24, "iiQQ")
            camera_id = props[0]
            model_id  = props[1]
            width     = props[2]
            height    = props[3]
            model_name = CAMERA_MODEL_IDS[model_id].model_name
            num_params = CAMERA_MODEL_IDS[model_id].num_params
            params    = np.array(_read_bytes(fid, 8 * num_params, "d" * num_params))
            cameras[camera_id] = Camera(id=camera_id, model=model_name, width=width, height=height, params=params)
    return cameras


def read_images_binary(path: Path) -> dict[int, Image]:
    images: dict[int, Image] = {}
    with open(path, "rb") as fid:
        (num_images,) = _read_bytes(fid, 8, "Q")
        for _ in range(num_images):
            props     = _read_bytes(fid, 64, "idddddddi")
            image_id  = props[0]
            qvec      = np.array(props[1:5])
            tvec      = np.array(props[5:8])
            camera_id = props[8]
            name = ""
            while True:
                char = fid.read(1)
                if char == b"\x00":
                    break
                name += char.decode("utf-8")
            (num_pts,) = _read_bytes(fid, 8, "Q")
            xyi = _read_bytes(fid, 24 * num_pts, "ddq" * num_pts)
            xys         = np.column_stack([list(map(float, xyi[0::3])), list(map(float, xyi[1::3]))])
            point3D_ids = np.array(list(map(int, xyi[2::3])))
            images[image_id] = Image(
                id=image_id, qvec=qvec, tvec=tvec,
                camera_id=camera_id, name=name,
                xys=xys, point3D_ids=point3D_ids,
            )
    return images


def read_points3D_binary(path: Path) -> dict[int, Point3D]:
    points3D: dict[int, Point3D] = {}
    with open(path, "rb") as fid:
        (num_points,) = _read_bytes(fid, 8, "Q")
        for _ in range(num_points):
            props      = _read_bytes(fid, 43, "QdddBBBd")
            point3D_id = props[0]
            xyz        = np.array(props[1:4])
            rgb        = np.array(props[4:7])
            error      = props[7]
            (num_track,) = _read_bytes(fid, 8, "Q")
            track      = _read_bytes(fid, 8 * num_track, "ii" * num_track)
            image_ids    = np.array(list(map(int, track[0::2])))
            point2D_idxs = np.array(list(map(int, track[1::2])))
            points3D[point3D_id] = Point3D(
                id=point3D_id, xyz=xyz, rgb=rgb, error=error,
                image_ids=image_ids, point2D_idxs=point2D_idxs,
            )
    return points3D


# --- model loader -----------------------------------------------------------

def read_model(path: Path) -> tuple[
    dict[int, Camera],
    dict[int, Image],
    dict[int, Point3D],
]:
    """Load a COLMAP sparse model from *path*.

    Tries binary format first (.bin), then text format (.txt).
    """
    bin_cameras = path / "cameras.bin"
    txt_cameras = path / "cameras.txt"

    if bin_cameras.exists():
        cameras  = read_cameras_binary(path / "cameras.bin")
        images   = read_images_binary(path / "images.bin")
        points3D = read_points3D_binary(path / "points3D.bin")
    elif txt_cameras.exists():
        cameras  = read_cameras_text(path / "cameras.txt")
        images   = read_images_text(path / "images.txt")
        points3D = read_points3D_text(path / "points3D.txt")
    else:
        raise FileNotFoundError(
            f"No COLMAP model found in {path}. "
            "Expected cameras.bin/cameras.txt, images.bin/images.txt, points3D.bin/points3D.txt."
        )
    return cameras, images, points3D


# ---------------------------------------------------------------------------
# Camera intrinsics helpers
# ---------------------------------------------------------------------------

def _single_focal(p: npt.NDArray) -> npt.NDArray:
    return np.array([p[0], p[0]])


# Maps model name → (focal_length_xy, principal_point_xy) extractors.
# All extractors accept the `params` array and return numpy arrays of length 2.
_FOCAL_EXTRACTORS: dict[str, tuple] = {
    # Single focal length: params[0] = f
    "SIMPLE_PINHOLE":        (_single_focal, lambda p: p[1:3]),
    "SIMPLE_RADIAL":         (_single_focal, lambda p: p[1:3]),
    "RADIAL":                (_single_focal, lambda p: p[1:3]),
    "SIMPLE_RADIAL_FISHEYE": (_single_focal, lambda p: p[1:3]),
    "RADIAL_FISHEYE":        (_single_focal, lambda p: p[1:3]),
    # Two focal lengths: params[0] = fx, params[1] = fy
    "PINHOLE":        (lambda p: p[0:2], lambda p: p[2:4]),
    "OPENCV":         (lambda p: p[0:2], lambda p: p[2:4]),
    "OPENCV_FISHEYE": (lambda p: p[0:2], lambda p: p[2:4]),
    "FULL_OPENCV":    (lambda p: p[0:2], lambda p: p[2:4]),
    "FOV":            (lambda p: p[0:2], lambda p: p[2:4]),
    "THIN_PRISM_FISHEYE": (lambda p: p[0:2], lambda p: p[2:4]),
}


def camera_focal_and_principal(camera: Camera) -> tuple[npt.NDArray, npt.NDArray]:
    """Return (focal_length_xy [2], principal_point_xy [2]) for *camera*."""
    extractors = _FOCAL_EXTRACTORS.get(camera.model)
    if extractors is None:
        raise ValueError(f"Unsupported camera model: {camera.model!r}")
    focal_fn, pp_fn = extractors
    return np.asarray(focal_fn(camera.params), dtype=float), np.asarray(pp_fn(camera.params), dtype=float)


# ---------------------------------------------------------------------------
# Rerun logging
# ---------------------------------------------------------------------------

DESCRIPTION = """
# COLMAP Sparse Reconstruction — BommieToolkit

Visualized with [Rerun](https://rerun.io).

**3-D view**: coloured point cloud + camera frustums (one per registered image).
**Camera view**: image frame with projected keypoints overlaid.
**Plot**: per-frame average reprojection error.
""".strip()


def log_reconstruction(
    sparse_dir: Path,
    images_dir: Path | None,
    filter_output: bool,
    resize: tuple[int, int] | None,
) -> None:
    print(f"Reading COLMAP model from {sparse_dir} …")
    cameras, images, points3D = read_model(sparse_dir)
    print(f"  {len(cameras)} cameras, {len(images)} images, {len(points3D)} 3-D points")

    if filter_output:
        points3D = {
            pid: pt for pid, pt in points3D.items()
            if pt.rgb.any() and len(pt.image_ids) > 4
        }
        print(f"  {len(points3D)} points after filtering")

    # Static logs
    rr.log("description", rr.TextDocument(DESCRIPTION, media_type=rr.MediaType.MARKDOWN), static=True)
    rr.log("/", rr.ViewCoordinates.RIGHT_HAND_Y_DOWN, static=True)
    rr.log("plot/avg_reproj_err", rr.SeriesLines(colors=[240, 45, 58]), static=True)
    
    full_pointcloud = np.array([pt.xyz for pt in points3D.values()]) if points3D else np.zeros((0, 3))
    full_colors     = np.array([pt.rgb for pt in points3D.values()], dtype=np.uint8) if points3D else np.zeros((0, 3), dtype=np.uint8)
    rr.log("Full Model", rr.Points3D(full_pointcloud, colors=full_colors), static=True)

    for image in sorted(images.values(), key=lambda im: im.name):
        # Determine frame index from the numeric suffix in the image name
        idx_match = re.search(r"\d+", image.name)
        frame_idx = int(idx_match.group(0)) if idx_match else image.id
        rr.set_time("frame", sequence=frame_idx)

        camera  = cameras[image.camera_id]
        focal, principal = camera_focal_and_principal(camera)

        # Optionally resize
        if resize:
            scale  = np.array([resize[0] / camera.width, resize[1] / camera.height])
            focal     = focal * scale
            principal = principal * scale
            cam_w, cam_h = resize
        else:
            scale  = np.array([1.0, 1.0])
            cam_w, cam_h = camera.width, camera.height

        # Visible 3-D points
        visible_mask    = (image.point3D_ids != -1) & np.array(
            [points3D.get(pid) is not None for pid in image.point3D_ids]
        )
        visible_ids     = image.point3D_ids[visible_mask]
        visible_pts     = [points3D[pid] for pid in visible_ids]
        visible_xys     = image.xys[visible_mask] * scale

        # Point cloud
        pt_xyzs   = np.array([pt.xyz for pt in visible_pts]) if visible_pts else np.zeros((0, 3))
        #pt_colors = np.array([pt.rgb for pt in visible_pts], dtype=np.uint8) if visible_pts else np.zeros((0, 3), dtype=np.uint8)
        pt_colors = np.array([np.array([255, 0, 0], dtype=np.uint8) for pt in visible_pts], dtype=np.uint8) if visible_pts else np.zeros((0, 3), dtype=np.uint8)
        pt_errors = [pt.error for pt in visible_pts]

        rr.log("plot/avg_reproj_err", rr.Scalars(float(np.mean(pt_errors)) if pt_errors else 0.0))
        rr.log("points", rr.Points3D(pt_xyzs, colors=pt_colors), rr.AnyValues(error=pt_errors))

        # Camera pose — COLMAP stores camera-from-world
        quat_xyzw = image.qvec[[1, 2, 3, 0]]
        rr.log(
            "camera",
            rr.Transform3D(
                translation=image.tvec,
                rotation=rr.Quaternion(xyzw=quat_xyzw),
                relation=rr.TransformRelation.ChildFromParent,
            ),
        )
        rr.log("camera", rr.ViewCoordinates.RDF, static=True)  # X=Right, Y=Down, Z=Forward

        # Camera intrinsics
        rr.log(
            "camera/image",
            rr.Pinhole(
                resolution=[cam_w, cam_h],
                focal_length=focal,
                principal_point=principal,
            ),
        )

        # Image frame
        if images_dir is not None:
            image_file = images_dir / image.name
            # For rig setups the image may live in a sub-folder mirroring the
            # COLMAP image path (e.g. rig1/camera1/<name>).
            if not image_file.exists():
                image_file = images_dir / Path(image.name).name
            if image_file.exists():
                if resize:
                    bgr = cv2.imread(str(image_file))
                    if bgr is not None:
                        bgr = cv2.resize(bgr, resize)
                        rr.log("camera/image", rr.Image(bgr, color_model="BGR").compress(jpeg_quality=75))
                else:
                    rr.log("camera/image", rr.EncodedImage(path=image_file))

        # Reprojected keypoints
        rr.log("camera/image/keypoints", rr.Points2D(visible_xys, colors=[34, 138, 167]))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = ArgumentParser(
        description="Visualize COLMAP sparse reconstruction output with Rerun.",
    )
    parser.add_argument(
        "--sparse_dir", type=Path, required=True,
        help="Path to the COLMAP sparse model directory (contains cameras/images/points3D files).",
    )
    parser.add_argument(
        "--images_dir", type=Path, default=None,
        help="Path to the directory containing the original images (optional).",
    )
    parser.add_argument(
        "--unfiltered", action="store_true",
        help="Disable point-cloud filtering (show all points, including noisy ones).",
    )
    parser.add_argument(
        "--resize", default=None,
        help="Resize images before logging, e.g. '1280x720'.",
    )
    rr.script_add_args(parser)
    args = parser.parse_args()

    resize: tuple[int, int] | None = None
    if args.resize:
        w, h = args.resize.split("x")
        resize = (int(w), int(h))

    blueprint = rrb.Vertical(
        rrb.Spatial3DView(name="3D", origin="/", line_grid=False),
        rrb.Horizontal(
            rrb.TextDocumentView(name="README", origin="/description"),
            rrb.Spatial2DView(name="Camera", origin="/camera/image"),
            rrb.TimeSeriesView(origin="/plot"),
        ),
        row_shares=[3, 2],
    )

    rr.script_setup(args, "rerun_bommietoolkit_sfm", default_blueprint=blueprint)
    log_reconstruction(
        sparse_dir=args.sparse_dir,
        images_dir=args.images_dir,
        filter_output=not args.unfiltered,
        resize=resize,
    )
    rr.script_teardown(args)


if __name__ == "__main__":
    main()
