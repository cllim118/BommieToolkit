#!/bin/bash
set -euo pipefail

pk() 
{ 
    pixi run -e kalibr "$@"
}

# WORKSPACE STRUCTURE NEEDS TO BE ALWAYS THE SAME
WS="" # PATH TO THE WORKSPACE
gray_flag=""

if [[ "${1:-}" == "--ws" ]]; then
  WS="$2"
fi

if [[ -z "$WS" ]]; then
  echo "Usage: $0 --ws <path_to_workspace>"
  exit 1
fi

colmap_folder_left="${WS}/images/colmap_images/rig1/camera1"
colmap_folder_right="${WS}/images/colmap_images/rig1/camera2"
if [[ "${CALIB:-}" == "true" ]]; then
  gray_flag="--gray"
  colmap_folder_left="${WS}/images/synced/cam0"
  colmap_folder_right="${WS}/images/synced/cam1"
fi

# Step 1: Convert videos to images
pk python scripts/vid2imgs.py --video ${WS}/videos/left.mp4 --output ${WS}/images/raw/cam0 ${gray_flag} --resolution "low" --skip 10.0 --max_frames 0
pk python scripts/vid2imgs.py --video ${WS}/videos/right.mp4 --output ${WS}/images/raw/cam1 ${gray_flag} --resolution "low" --skip 10.0 --max_frames 0

# Step 2: Synchronize images
args=(
  --images_folder_left "${WS}/images/raw/cam0"
  --images_folder_right "${WS}/images/raw/cam1"
  --colmap_folder_left "${colmap_folder_left}"
  --colmap_folder_right "${colmap_folder_right}"
  --threshold-ns 5000000
  --sample_step 5
)
pk scripts/match_images_by_ns.py "${args[@]}"
