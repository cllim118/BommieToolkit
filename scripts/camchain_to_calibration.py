#!/usr/bin/env python3
"""
Convert kalibr camchain YAML + AprilGrid config → calibration.yaml (OPENCV + FLATPORT format).

The non_svp_parameters describe the physical flat port:
  [Nx, Ny, Nz, int_dist, int_thick, na, ng, nw]
  - (Nx, Ny, Nz): port normal unit vector (calibrated; defaults to [0, 0, 1])
  - int_dist: distance from camera to glass interface [m]
  - int_thick: glass thickness [m]
  - na/ng/nw: refractive indices of air / glass / water
"""

import argparse
import math
import sys
from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def average_cameras(cam0: dict, cam1: dict) -> tuple:
    i0, i1 = cam0["intrinsics"], cam1["intrinsics"]
    d0, d1 = cam0["distortion_coeffs"], cam1["distortion_coeffs"]
    avg_intrinsics = [(a + b) / 2 for a, b in zip(i0, i1)]
    avg_distortion = [(a + b) / 2 for a, b in zip(d0, d1)]
    return avg_intrinsics, avg_distortion, cam0["resolution"]


def build_target_comment(april: dict) -> str:
    cols = april["tagCols"]
    rows = april["tagRows"]
    size = april["tagSize"]
    spacing = april["tagSpacing"]
    return f"aruco grid board, APRILTAG_36h11, {cols}, {rows}, {size:.6f}, {spacing:.6f}, 2, bl, hor"


def fmt(v: float) -> str:
    """Format float without trailing zeros, keeping enough precision."""
    s = f"{v:.10g}"
    return s


def write_calibration(
    out_path: Path,
    intrinsics: list,
    distortion: list,
    distortion_model: str,
    resolution: list,
    non_svp_params: list,
    target_comment: str,
):
    fx, fy, cx, cy = intrinsics
    k1, k2, p1, p2 = distortion
    width, height = resolution

    def fmtlist(vals):
        return "[" + ", ".join(fmt(v) for v in vals) + "]"

    str_model = "OPENCV"
    str_params = "# fx, fy, cx, cy, k1, k2, p1, p2" 
    if distortion_model != "radtan":
        str_model = "OPENCV_FISHEYE"
        str_params = "# fx, fy, cx, cy, k1, k2, k3, k4"

    lines = [
        f"model: {str_model}",
        str_params,
        f"parameters: {fmtlist([fx, fy, cx, cy, k1, k2, p1, p2])}",
        "non_svp_model: FLATPORT",
        "# Nx, Ny, Nz, int_dist, int_thick, na, ng, nw (Note that [Nx, Ny, Nz] must be unit vector)",
        f"non_svp_parameters: {fmtlist(non_svp_params)}",
        f"width: {width}",
        f"height: {height}",
        f"# target: {target_comment}",
    ]
    out_path.write_text("\n".join(lines) + "\n")
    print(f"Written: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate calibration.yaml from kalibr camchain + AprilGrid config")
    
    parser.add_argument("--camchain", type=Path, required=True, help="Path to calibration-camchain.yaml")
    parser.add_argument("--april", type=Path, required=True, help="Path to april.yaml")
    parser.add_argument("--housing", type=Path, required=True, help="Path to housing_params.yaml")
    parser.add_argument("--output", type=Path, default=Path("calibration.yaml"), help="Output path (default: calibration.yaml)")
    parser.add_argument("--camera", choices=["cam0", "cam1"], default="cam0", help="Which camera to use")
    args = parser.parse_args()

    # Camera Parameters
    camchain = load_yaml(args.camchain)
    cam = camchain.get(args.camera)

    intrinsics = cam["intrinsics"]
    distortion = cam["distortion_coeffs"]
    distortion_model = cam["distortion_model"]
    resolution = cam["resolution"]

    # Housing Parameters
    housing = load_yaml(args.housing)

    normal = [0.0, 0.0, 1.0]
    type = housing["housing"]
    int_dist = housing["distance_to_glass"]
    int_thick = housing["glass_thickness"]
    ng = housing["glass_refractive_index"]
    nw = housing["water_refractive_index"]
    non_svp_params = [normal[0], normal[1], normal[2], int_dist, int_thick, 1.0, ng, nw]

    # AprilGrid Parameters
    april = load_yaml(args.april)
    target_comment = build_target_comment(april)

    # Writing the output
    write_calibration(args.output, intrinsics, distortion, distortion_model, resolution, non_svp_params, target_comment)


if __name__ == "__main__":
    main()
