#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NO_DISPLAY_STUB="${SCRIPT_DIR}/no_display.so"

puw()
{
    LD_PRELOAD="${NO_DISPLAY_STUB}" pixi run -e uwsfm "$@"
}

pk()
{
    pixi run -e kalibr "$@"
}

WS="" # PATH TO THE WORKSPACE
KALIB_WS="" # PATH TO THE CAMERA CALIBRATION WORKSPACE

if [[ "${1:-}" == "--ws" ]]; then
  WS="$2"
fi

if [[ "${3:-}" == "--calib_ws" ]]; then
  KALIB_WS="$4"
fi


args=(--camchain ${KALIB_WS}/calibration-camchain.yaml
      --april files/april_10x6.yaml
      --housing ${WS}/housing.yaml)

# Generate calibration files for both cameras
pk python scripts/camchain_to_calibration.py "${args[@]}" --camera cam0 --output ${WS}/calibration_cam0.yaml
pk python scripts/camchain_to_calibration.py "${args[@]}" --camera cam1 --output ${WS}/calibration_cam1.yaml

# Run housing calibration on cam0
puw housing_calib ${WS}/calibration_cam0.yaml ${WS}/images/synced/cam0
mv ${WS}/d0.yaml ${WS}/d0_cam0.yaml

# Run housing calibration on cam1
puw housing_calib ${WS}/calibration_cam1.yaml ${WS}/images/synced/cam1
mv ${WS}/d0.yaml ${WS}/d0_cam1.yaml

# Compute optimal virtual distance for refraction removal
puw python pinax/scripts/d0_estimation.py --cfg ${WS}/d0_cam0.yaml
puw python pinax/scripts/d0_estimation.py --cfg ${WS}/d0_cam1.yaml

# Compute distortion correction maps for both cameras
im0=$(ls ${WS}/images/synced/cam0 | head -n 1)
im1=$(ls ${WS}/images/synced/cam1 | head -n 1)

puw undistort_estim  ${WS}/calibration_cam0.yaml ${WS}/images/synced/cam0/${im0} ${WS}/d0_cam0.yaml 
mv ./undistort_map.yaml ${WS}/undistort_map_cam0.yaml

puw undistort_estim  ${WS}/calibration_cam1.yaml ${WS}/images/synced/cam1/${im1} ${WS}/d0_cam1.yaml 
mv ./undistort_map.yaml ${WS}/undistort_map_cam1.yaml

mkdir -p ${WS}/images/undistorted/cam0
mkdir -p ${WS}/images/undistorted/cam1

puw undistorter ${WS}/undistort_map_cam0.yaml ${WS}/images/synced/cam0 ${WS}/images/undistorted/cam0
puw undistorter ${WS}/undistort_map_cam1.yaml ${WS}/images/synced/cam1 ${WS}/images/undistorted/cam1

# Recalibrate the stereo rig using the undistorted images to get the final camchain with housing effects removed
pk bash scripts/calibrate_stereo_rig.sh output_folder=${WS} images_folder_left=${WS}/images/undistorted/cam0 images_folder_right=${WS}/images/undistorted/cam1
