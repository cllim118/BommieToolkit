#!/bin/bash

# DESCRIPTION: This script is used to run the entire pipeline of the project.  It will be broken down into multiple scripts corresponding to each step of the pipeline.
# This script will call those scripts in the correct order and pass the necessary arguments to them.

set -euo pipefail

################### AIR CAMERA CALIBRATION ###################
CAMERA_CALIB_WS="/home/slamemix/Data/BioAdheseve/bommie_ws/ari_calibration_ws" # PATH TO THE CALIBRATION WORKSPACE

echo "Video preprocessing for in air camera calibration..."

export CALIB=true
FORCE_EXTR=false # Set to true when you want to re-extract the images
if [[ "${FORCE_EXTR:-}" == "true" ]]; then
  bash scripts/video_preprocessing.sh --ws ${CAMERA_CALIB_WS}
fi

echo "Preprocessing complete. Starting stereo rig calibration..."

FORCE_CALIB=false # Set to true to force recalibration even if calibration results already exist
if [[ "${FORCE_CALIB:-}" == "true" ]]; then
  pixi run -e kalibr bash scripts/calibrate_stereo_rig.sh output_folder=${CAMERA_CALIB_WS}
  
  # Creating the rig config json for colmap reconstruction
  pixi run get_rig_config_json ${CAMERA_CALIB_WS}/calibration-camchain.yaml ${CAMERA_CALIB_WS}/rig_config.json
fi

################# UNDERWATER HOUSING CALIBRATION + REFRACTION REMOVAL ###################
HOUSING_CALIB_WS="/media/slamemix/DATA/BioAdheseve/bommie_ws/housing_calib_ws" # PATH TO THE CALIBRATION WORKSPACE
FORCE_EXTR=false # Set to true when you want to re-extract the images
if [[ "${FORCE_EXTR:-}" == "true" ]]; then
  bash scripts/video_preprocessing.sh --ws ${HOUSING_CALIB_WS}
fi

FORCE_CALIB=true
UW_ESTIM=false
if [[ "${FORCE_CALIB:-}" == "true" ]]; then
  #bash scripts/housing_calibration.sh --ws ${HOUSING_CALIB_WS} --calib_ws ${CAMERA_CALIB_WS}
  UW_ESTIM=true
  # Updated rig config json for colmap reconstruction with housing effects removed
  pixi run get_rig_config_json ${HOUSING_CALIB_WS}/calibration-camchain.yaml ${HOUSING_CALIB_WS}/rig_config.json
  CAMERA_CALIB_WS=${HOUSING_CALIB_WS}
fi


################### COLMAP RECONSTRUCTION ###################
COLMAP_WS="/home/slamemix/Data/BioAdheseve/bommie_ws/colmap_ws" # PATH TO THE COLMAP WORKSPACE
export MASKING=false # Set true activate masking using SAM3
export CALIB=false

echo "Video preprocessing for colmap reconstruction..."
FORCE_EXTR=false # Set to true when you want to re-extract the images
if [[ "${FORCE_EXTR:-}" == "true" ]]; then
  bash scripts/video_preprocessing.sh --ws ${COLMAP_WS}
fi

if [[ "${UW_ESTIM:-}" == "true" ]]; then
  # Apply underwater undistortiom to the images before feeding them to colmap
  IMAGE_PATH=${COLMAP_WS}/images/colmap_images/rig1
  pixi run -e uwsfm undistorter ${HOUSING_CALIB_WS}/undistort_map_cam0.yaml ${IMAGE_PATH}/camera1 ${IMAGE_PATH}/camera1
  pixi run -e uwsfm undistorter ${HOUSING_CALIB_WS}/undistort_map_cam1.yaml ${IMAGE_PATH}/camera2 ${IMAGE_PATH}/camera2
fi

if [[ "${MASKING:-}" == "true" ]]; then
  echo "Masking images using SAM3..."
  pixi run -e sam create_masks
fi

echo "Preprocessing complete. Starting colmap reconstruction..."
FORCE_COLMAP=false # Set to true to force rerunning colmap reconstruction even if results
USE_MASKS="--mask" # If need to use, set to the flag, otherwise leave as empty string
if [[ "${FORCE_COLMAP:-}" == "true" ]]; then
  bash scripts/colmap_rec.sh --ws ${COLMAP_WS} --calib_ws ${CAMERA_CALIB_WS} ${USE_MASKS}
fi

################ NEURAL RENDERING ###################
RENDER_WS="/home/slamemix/Data/BioAdheseve/bommie_ws/nn_ws" # PATH TO THE NEURAL RENDERING WORKSPACE

mkdir -p ${RENDER_WS}

echo "Colmap reconstruction complete. Starting neural rendering preparation..."
FORCE_NN=false # Set to true to force rerunning the neural rendering preparation even if results already exist
if [[ "${FORCE_NN:-}" == "true" ]]; then
  bash scripts/neural_rendering.sh --ws ${RENDER_WS} --colmap_ws ${COLMAP_WS} ${USE_MASKS}
fi  