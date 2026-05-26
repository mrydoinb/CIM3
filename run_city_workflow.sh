#!/bin/bash
set -e

PYTHON_EXE="${PYTHON_EXE:-/d/ProgramData/miniconda3/envs/cim-road/python.exe}"
if [ ! -x "$PYTHON_EXE" ]; then
  PYTHON_EXE="python"
fi
BLENDER_EXE="${BLENDER_EXE:-/c/Program Files/Blender Foundation/Blender 5.1/blender.exe}"

if [ ! -x "$BLENDER_EXE" ]; then
  if [ -x "/d/ruanjian/Blender 5.1/blender.exe" ]; then
    BLENDER_EXE="/d/ruanjian/Blender 5.1/blender.exe"
  elif command -v blender >/dev/null 2>&1; then
    BLENDER_EXE="blender"
  else
    echo "[ERROR] Blender executable not found: $BLENDER_EXE"
    echo "Set BLENDER_EXE before running this script, for example:"
    echo "  export BLENDER_EXE='/d/path/to/blender.exe'"
    exit 1
  fi
fi

echo "==================================================="
echo "       CIM city automatic modeling workflow"
echo "==================================================="

echo "[1/2] Generating CIM city OBJ with roads, buildings, subway, bus stops, and utilities..."
"$PYTHON_EXE" scripts/05_generate_cim_city.py

echo "[2/2] Exporting materialized CIM city FBX..."
"$BLENDER_EXE" --background --python scripts/06_export_cim_city_fbx_blender.py

echo "==================================================="
echo "City workflow finished."
echo "OBJ: output/obj/cim_city.obj"
echo "FBX: output/fbx/cim_city.fbx"
