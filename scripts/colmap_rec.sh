#!/bin/bash
set -euo pipefail

pc() 
{ 
    pixi run -e colmap "$@"
}

# WORKSPACE STRUCTURE NEEDS TO BE ALWAYS THE SAME
WS="" # PATH TO THE WORKSPACE
CALIB_WS="" # PATH TO THE CALIBRATION WORKSPACE

if [[ "${1:-}" == "--ws" ]]; then
  WS="$2"
fi

if [[ "${3:-}" == "--calib_ws" ]]; then
  CALIB_WS="$4"
fi

mask_flag=""
if [[ "${5:-}" == "--mask" ]]; then
  mask_flag="--ImageReader.mask_path ${WS}/images/masks"
fi

featext_args=(
  --image_path ${WS}/images/colmap_images
  --database_path ${WS}/database.db
  --ImageReader.single_camera 1
  --ImageReader.single_camera_per_folder 1
  --ImageReader.single_camera_per_image 0
  --ImageReader.existing_camera_id -1
  --ImageReader.default_focal_length_factor 1.2
  ${mask_flag}
)

pc colmap feature_extractor "${featext_args[@]}"

pc colmap rig_configurator --database_path ${WS}/database.db --rig_config_path ${CALIB_WS}/rig_config.json

pc colmap exhaustive_matcher --database_path ${WS}/database.db

# NEED TO HANDLE CASE IN WHICH THIS FOLDER ALREADY EXISTS
mkdir -p ${WS}/sparse

mapper_args=(
  --database_path ${WS}/database.db
  --image_path ${WS}/images/colmap_images
  --output_path ${WS}/sparse
  --Mapper.num_threads 10
  --Mapper.ba_refine_focal_length 1
  --Mapper.ba_refine_principal_point 0
  --Mapper.ba_refine_extra_params 0
  --Mapper.ba_refine_sensor_from_rig 0

)
pc colmap mapper "${mapper_args[@]}"

# EXTRACTING COLMAP RESULTS IN TXT FOR CONVERSION TO NERF/SPLAT FORMAT
mkdir -p ${WS}/sparse/0_txt
modconv_args=(
  --input_path ${WS}/sparse/0
  --output_path ${WS}/sparse/0_txt
  --output_type TXT
)
pc colmap model_converter "${modconv_args[@]}"

# EXTRACTING COLMAP RESULTS IN PLY FOR NERF/SPLAT INTIALIZATION
modconv_args=(
  --input_path ${WS}/sparse/0
  --output_path ${WS}/sparse/0_txt/mesh.ply
  --output_type PLY
)
pixi run -e colmap colmap model_converter "${modconv_args[@]}"


if [[ "${5:-}" == "--mask" ]]; then
  mask_flag="--masks ${WS}/images/masks"
fi

renderconv_args=(
  --text ${WS}/sparse/0_txt
  --images ${WS}/images/colmap_images
  --out ${WS}/transforms.json
  --keep_colmap_coords
  --ply_file_path ${WS}/sparse/0_txt/mesh.ply
  ${mask_flag}
)
pc python scripts/colmap2nerf.py "${renderconv_args[@]}"