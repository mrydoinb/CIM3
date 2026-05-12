#!/bin/bash
set -e

PYTHON_EXE="${PYTHON_EXE:-/d/kaifahuanjing/anaconda3/envs/cim-road/python.exe}"
if [ ! -x "$PYTHON_EXE" ]; then
  PYTHON_EXE="python"
fi
BLENDER_EXE="${BLENDER_EXE:-/d/ruanjian/Blender 5.1/blender.exe}"

if [ ! -x "$BLENDER_EXE" ]; then
  if command -v blender >/dev/null 2>&1; then
    BLENDER_EXE="blender"
  else
    echo "[ERROR] Blender executable not found: $BLENDER_EXE"
    echo "Set BLENDER_EXE before running this script, for example:"
    echo "  export BLENDER_EXE='/d/path/to/blender.exe'"
    exit 1
  fi
fi

echo "==================================================="
echo "       CIM3 road automatic modeling workflow"
echo "==================================================="

echo "[1/2] Generating CIM3 road geometry and topology..."
"$PYTHON_EXE" scripts/01_generate_cim3_road.py

echo "[2/2] Running Blender background service: applying materials and exporting FBX..."
"$BLENDER_EXE" --background --python scripts/02_export_fbx_blender.py

echo "==================================================="
echo "Workflow finished."
echo "OBJ: output/obj/road_test.obj"
echo "FBX: output/fbx/road_test.fbx"
