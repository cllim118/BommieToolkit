#!/bin/bash
set -euo pipefail

# Inputs
freq=30.0
target="files/april_10x6.yaml"
output_folder="calibration_output"
images_folder_left="${output_folder}/cam0"
images_folder_right="${output_folder}/cam1"
verbose=0
create_bag=1
manual_focal_length_init="${KALIBR_MANUAL_FOCAL_LENGTH_INIT:-0}"

# Check inputs
split_and_assign() {
  local input=$1
  local key=$(echo $input | cut -d'=' -f1)
  local value=$(echo $input | cut -d'=' -f2-)
  case "$key" in
    freq|target|output_folder|images_folder_left|images_folder_right|verbose|create_bag|manual_focal_length_init)
      printf -v "$key" '%s' "$value"
      ;;
    *)
      echo "Unknown argument: $key" >&2
      exit 2
      ;;
  esac
}

# Split the input string into individual components
for ((i=1; i<=$#; i++)); do
    split_and_assign "${!i}"
done

# Create folder structure
output_bag="${output_folder}/calibration.bag"
expected_camchain="${output_folder}/calibration-camchain.yaml"
bag_input_folder="$(dirname "$images_folder_left")"

if [ "$(dirname "$images_folder_right")" != "$bag_input_folder" ]; then
  echo "images_folder_left and images_folder_right must share one parent folder for kalibr_bagcreater." >&2
  exit 2
fi

if [ "$(basename "$images_folder_left")" != "cam0" ] || [ "$(basename "$images_folder_right")" != "cam1" ]; then
  echo "Kalibr expects camera folders named cam0 and cam1; got '$images_folder_left' and '$images_folder_right'." >&2
  exit 2
fi


if [ "$create_bag" -eq 1 ] && [ -f "$output_bag" ]; then
  rm "$output_bag"
fi

verbose_cmd=""
if [ "$verbose" -eq 1 ]; then
  verbose_cmd="--verbose"
fi

# Run calibration steps
set +u
source catkin_ws/devel/setup.bash
set -u
kalibr_bin="${KALIBR_BIN:-catkin_ws/devel/.private/kalibr/lib/kalibr}"

if [ "$create_bag" -eq 1 ]; then
  "${kalibr_bin}/kalibr_bagcreater" --folder "$bag_input_folder" --output-bag "$output_bag"
fi

if [ ! -f "$output_bag" ]; then
  echo "Calibration bag not found: $output_bag" >&2
  echo "Run with create_bag=1, or provide an existing bag at that path when create_bag=0." >&2
  exit 1
fi

if [ "$manual_focal_length_init" -eq 1 ]; then
  export KALIBR_MANUAL_FOCAL_LENGTH_INIT=1
else
  unset KALIBR_MANUAL_FOCAL_LENGTH_INIT
fi

"${kalibr_bin}/kalibr_calibrate_cameras" --target "$target" --models pinhole-radtan pinhole-radtan --topics /cam0/image_raw /cam1/image_raw --bag "$output_bag" --bag-freq "$freq" ${verbose_cmd}

if [ ! -f "$expected_camchain" ]; then
  echo "Kalibr completed without producing expected camchain: $expected_camchain" >&2
  exit 1
fi
