#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pnf()
{
    pixi run --manifest-path "$SCRIPT_DIR/../nerfstudio/pixi.toml" "$@"
}

# WORKSPACE STRUCTURE NEEDS TO BE ALWAYS THE SAME
WS="" # PATH TO THE WORKSPACE
COLMAP_WS=""
NAME="latest"

if [[ "${1:-}" == "--ws" ]]; then
  WS="$2"
fi

if [[ "${3:-}" == "--colmap_ws" ]]; then
  COLMAP_WS="$4"
fi

mask_flag=""
if [[ "${5:-}" == "--mask" ]]; then
  mask_flag="--pipeline.model.background_color random"
fi

train_args=(
    --data "${COLMAP_WS}"
    --output-dir "${WS}/outputs"
    --timestamp "${NAME}"
    --vis viewer
    --machine.device_type cuda #[mps, cpu]
    ${mask_flag}
)

pnf ns-train splatfacto "${train_args[@]}"

pnf ns-export gaussian-splat --load-config "${WS}/outputs/colmap_ws/splatfacto/${NAME}/config.yml" --output-dir "${WS}/exports/splat/" 

