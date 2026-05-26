#!/bin/bash

# DESCRIPTION: This script is used to run the entire pipeline of the project.  It will be broken down into multiple scripts corresponding to each step of the pipeline.
# This script will call those scripts in the correct order and pass the necessary arguments to them.

set -euo pipefail

################### AIR CAMERA CALIBRATION ###################
CAMERA_CALIB_WS="/home/slamemix/Data/BioAdheseve/bommie_ws/ari_calibration_ws" # PATH TO THE CALIBRATION WORKSPACE

echo "Video preprocessing for in air camera calibration..."

export CALIB=true
FORCE_EXTR=true # Set to true when you want to re-extract the images
if [[ "${FORCE_EXTR:-}" == "true" ]]; then
  bash scripts/video_preprocessing.sh --ws ${CAMERA_CALIB_WS}
fi

echo "Preprocessing complete. Starting stereo rig calibration..."

FORCE_CALIB=true # Set to true to force recalibration even if calibration results already exist
if [[ "${FORCE_CALIB:-}" == "true" ]]; then
  pixi run -e kalibr bash scripts/calibrate_stereo_rig.sh output_folder=${CAMERA_CALIB_WS}
  
  # Creating the rig config json for colmap reconstruction
  pixi run get_rig_config_json ${CAMERA_CALIB_WS}/calibration-camchain.yaml ${CAMERA_CALIB_WS}/rig_config.json
fi

################# UNDERWATER HOUSING CALIBRATION + REFRACTION REMOVAL ###################

#TODO: TO BE FILLED

################### COLMAP RECONSTRUCTION ###################
COLMAP_WS="/home/slamemix/Data/BioAdheseve/bommie_ws/colmap_ws" # PATH TO THE COLMAP WORKSPACE
export MASKING=true # Set true activate masking using SAM3
export CALIB=false

echo "Video preprocessing for colmap reconstruction..."
FORCE_EXTR=true # Set to true when you want to re-extract the images
if [[ "${FORCE_EXTR:-}" == "true" ]]; then
  bash scripts/video_preprocessing.sh --ws ${COLMAP_WS}
fi

if [[ "${MASKING:-}" == "true" ]]; then
  echo "Masking images using SAM3..."
  pixi run -e sam create_masks
fi

echo "Preprocessing complete. Starting colmap reconstruction..."
FORCE_COLMAP=true # Set to true to force rerunning colmap reconstruction even if results
USE_MASKS="--mask" # If need to use, set to the flag, otherwise leave as empty string
if [[ "${FORCE_COLMAP:-}" == "true" ]]; then
  bash scripts/colmap_rec.sh --ws ${COLMAP_WS} --calib_ws ${CAMERA_CALIB_WS} ${USE_MASKS}
fi

################ NEURAL RENDERING ###################
RENDER_WS="/home/slamemix/Data/BioAdheseve/bommie_ws/nn_ws" # PATH TO THE NEURAL RENDERING WORKSPACE

mkdir -p ${RENDER_WS}

echo "Colmap reconstruction complete. Starting neural rendering preparation..."
FORCE_NN=true # Set to true to force rerunning the neural rendering preparation even if results already exist
if [[ "${FORCE_NN:-}" == "true" ]]; then
  bash scripts/neural_rendering.sh --ws ${RENDER_WS} --colmap_ws ${COLMAP_WS} ${USE_MASKS}
fi  