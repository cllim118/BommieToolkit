#!/usr/bin/env python3
import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image
import yaml 


def rotmat_to_quat(R):
    """
    Convert 3x3 rotation matrix to quaternion [w, x, y, z].
    R is a list-of-lists [[r00, r01, r02], ...]
    """
    r00, r01, r02 = R[0]
    r10, r11, r12 = R[1]
    r20, r21, r22 = R[2]

    trace = r00 + r11 + r22

    if trace > 0.0:
        S = math.sqrt(trace + 1.0) * 2.0  # S=4*qw
        qw = 0.25 * S
        qx = (r21 - r12) / S
        qy = (r02 - r20) / S
        qz = (r10 - r01) / S
    elif (r00 > r11) and (r00 > r22):
        S = math.sqrt(1.0 + r00 - r11 - r22) * 2.0  # S=4*qx
        qw = (r21 - r12) / S
        qx = 0.25 * S
        qy = (r01 + r10) / S
        qz = (r02 + r20) / S
    elif r11 > r22:
        S = math.sqrt(1.0 + r11 - r00 - r22) * 2.0  # S=4*qy
        qw = (r02 - r20) / S
        qx = (r01 + r10) / S
        qy = 0.25 * S
        qz = (r12 + r21) / S
    else:
        S = math.sqrt(1.0 + r22 - r00 - r11) * 2.0  # S=4*qz
        qw = (r10 - r01) / S
        qx = (r02 + r20) / S
        qy = (r12 + r21) / S
        qz = 0.25 * S

    return [qw, qx, qy, qz]


def topic_to_prefix(topic: str) -> str:
    """
    Turn a ROS topic like '/cam0/image_raw' into an image_prefix like 'cam0/'.
    Adjust this if you want 'rig1/camera1/' etc.
    """
    # e.g. '/cam0/image_raw' -> 'cam0/'
    parts = topic.strip("/").split("/")
    if not parts:
        return ""
    return parts[0] + "/"


def first_image_size(image_dir: Path):
    if image_dir is None:
        return None
    if not image_dir.is_dir():
        raise FileNotFoundError(f"Image directory not found for rig scaling: {image_dir}")

    image_paths = sorted(
        p for p in image_dir.iterdir()
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
    )
    if not image_paths:
        raise FileNotFoundError(f"No images found for rig scaling in: {image_dir}")

    with Image.open(image_paths[0]) as image:
        return image.size


def scaled_camera_params(cam_data, target_size=None):
    intrinsics = list(cam_data["intrinsics"])  # [fx, fy, cx, cy]
    if target_size is not None:
        source_width, source_height = cam_data["resolution"]
        target_width, target_height = target_size
        scale_x = target_width / source_width
        scale_y = target_height / source_height
        intrinsics = [
            intrinsics[0] * scale_x,
            intrinsics[1] * scale_y,
            intrinsics[2] * scale_x,
            intrinsics[3] * scale_y,
        ]

    dist = cam_data["distortion_coeffs"]  # [k1, k2, p1, p2] for radtan
    return intrinsics + list(dist)


# Kalibr (camera_model, distortion_model) -> COLMAP camera model. Params: intrinsics + distortion_coeffs.
COLMAP_MODELS = {
    ("pinhole", "radtan"): "OPENCV",  # fx, fy, cx, cy, k1, k2, p1, p2
}


def colmap_model(cam_key, cam_data):
    key = (cam_data.get("camera_model", "pinhole"), cam_data.get("distortion_model", "radtan"))
    if key not in COLMAP_MODELS:
        supported = ", ".join(f"{c}-{d}" for c, d in COLMAP_MODELS)
        raise ValueError(f"{cam_key}: camera model {key[0]}-{key[1]} is not supported (supported: {supported}).")
    return COLMAP_MODELS[key]


def matmul4(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def yaml_to_rig_config(yaml_path: Path, json_path: Path, image_dirs=None, rig_without_poses=False):
    """
    Write a COLMAP rig_config.json from a Kalibr camchain:
      - every camera gets its calibrated intrinsics/distortion (scaled to the frame size if image_dirs is given);
      - if every camera after cam0 has T_cn_cnm1: one rig with cam0 as reference and cam_from_rig poses;
      - otherwise: one single-camera rig per camera (intrinsics only, no rig constraint), or with
        rig_without_poses one multi-camera rig without poses (for rig_configurator --input_path).
    Returns True if the rig poses came from the camchain.
    """
    if not yaml_path.is_file():
        raise FileNotFoundError(
            f"Camchain file not found: {yaml_path}. Run kalibr calibration first or pass camchain_path."
        )

    with yaml_path.open("r") as f:
        calib = yaml.safe_load(f)

    # Sort camera keys like cam0, cam1, ...
    cam_keys = sorted(
        (k for k in calib.keys() if k.startswith("cam")),
        key=lambda k: int(k[3:])  # assumes 'cam<number>'
    )

    image_dirs = image_dirs or []
    image_sizes = [first_image_size(path) for path in image_dirs]
    has_poses = all("T_cn_cnm1" in calib[k] for k in cam_keys[1:])

    cameras_out = []
    T_cam_from_ref = [[float(i == j) for j in range(4)] for i in range(4)]  # cam0 -> cam0
    for idx, cam_key in enumerate(cam_keys):
        cam_data = calib[cam_key]
        target_size = image_sizes[idx] if idx < len(image_sizes) else None

        cam_out = {
            "image_prefix": f"rig1/cam{idx}/",
            "camera_model_name": colmap_model(cam_key, cam_data),
            "camera_params": scaled_camera_params(cam_data, target_size),
        }

        if idx == 0:
            cam_out["ref_sensor"] = True
        elif has_poses:
            # Kalibr T_cn_cnm1 maps cam{n-1} -> cam{n}; chain them to get cam{n} from cam0 (the rig frame).
            T_cam_from_ref = matmul4(cam_data["T_cn_cnm1"], T_cam_from_ref)
            R = [row[:3] for row in T_cam_from_ref[:3]]
            cam_out["cam_from_rig_rotation"] = rotmat_to_quat(R)
            cam_out["cam_from_rig_translation"] = [T_cam_from_ref[i][3] for i in range(3)]

        cameras_out.append(cam_out)

    if has_poses or rig_without_poses:
        rig_config = [{"cameras": cameras_out}]
    else:
        rig_config = [{"cameras": [{**cam, "ref_sensor": True}]} for cam in cameras_out]

    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w") as f:
        json.dump(rig_config, f, indent=2)
    return has_poses


def main():
    ap = argparse.ArgumentParser(
        description="Convert Kalibr camchain.yaml to rig_config.json format."
    )
    ap.add_argument(
        "yaml_in",
        type=Path,
        help="Input calibration-camchain.yaml from Kalibr",
    )
    ap.add_argument(
        "json_out",
        type=Path,
        help="Output rig_config.json path",
    )
    ap.add_argument(
        "--image-dirs",
        nargs="*",
        type=Path,
        default=None,
        help="Optional image directories, in camchain order, used to scale intrinsics to extracted image size.",
    )
    ap.add_argument(
        "--rig-without-poses",
        action="store_true",
        help="If the camchain has no T_cn_cnm1, write one multi-camera rig without poses (to derive them with "
             "colmap rig_configurator --input_path) instead of one single-camera rig per camera.",
    )
    args = ap.parse_args()

    try:
        has_poses = yaml_to_rig_config(args.yaml_in, args.json_out, args.image_dirs, args.rig_without_poses)
        print(f"Wrote {args.json_out} ({'rig poses from the camchain' if has_poses else 'no rig poses in the camchain'})")
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
