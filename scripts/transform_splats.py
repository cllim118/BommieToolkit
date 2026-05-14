#!/usr/bin/env python3
'''
Nerfstudio applies:
    p_nerfstudio = scale * (R @ p_colmap + t)

This script inverts to recover original coordinates:
    p_colmap = R.T @ (p_nerfstudio / scale) - R.T @ t
'''

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement


def load_transform(path: Path) -> tuple[np.ndarray, float]:
    with open(path) as f:
        data = json.load(f)
    R = np.array(data["transform"])[:, :3]   # 3×3
    t = np.array(data["transform"])[:, 3]    # 3
    scale: float = data["scale"]
    return R, t, scale


def apply_inverse(positions: np.ndarray, R: np.ndarray, t: np.ndarray, scale: float) -> np.ndarray:
    return (R.T @ (positions / scale).T).T - R.T @ t


def main() -> None:
    parser = ArgumentParser(description="Invert a nerfstudio dataparser transform on a PLY point cloud.")
    parser.add_argument("--splat_path",     type=Path, required=True, help="Input PLY file (nerfstudio space).")
    parser.add_argument("--transform_path", type=Path, required=True, help="dataparser_transforms.json from nerfstudio.")
    parser.add_argument("--output_path",    type=Path, required=True, help="Output PLY file (COLMAP space).")
    args = parser.parse_args()

    R, t, scale = load_transform(args.transform_path)

    print(f"\Transforming {args.splat_path} to its original space …")
    ply = PlyData.read(str(args.splat_path))
    v = ply["vertex"]
    print(f"  {len(v)} points")

    positions = np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64)
    positions = apply_inverse(positions, R, t, scale)

    # Rebuild vertex data with transformed positions, preserving all other properties
    new_data = {}
    for prop in v.properties:
        if prop.name == "x":
            new_data["x"] = positions[:, 0].astype(np.float32)
        elif prop.name == "y":
            new_data["y"] = positions[:, 1].astype(np.float32)
        elif prop.name == "z":
            new_data["z"] = positions[:, 2].astype(np.float32)
        else:
            new_data[prop.name] = v[prop.name]

    vertex_dtype = [(prop.name, new_data[prop.name].dtype) for prop in v.properties]
    vertex_array = np.empty(len(v), dtype=vertex_dtype)
    for name, arr in new_data.items():
        vertex_array[name] = arr

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    PlyData([PlyElement.describe(vertex_array, "vertex")], text=False).write(str(args.output_path))
    print(f"\nSaved to {args.output_path}")


if __name__ == "__main__":
    main()
