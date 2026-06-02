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
echo "       CIM road iteration workflow"
echo "==================================================="

echo "[1/2] Generating road OBJ and separate junction debug models..."
"$PYTHON_EXE" scripts/02_generate_cim_roads.py

echo "[2/2] Exporting road and junction debug FBX..."
"$PYTHON_EXE" scripts/03_export_cim_roads_fbx.py --blender "$BLENDER_EXE"

echo "==================================================="
echo "Road workflow finished."
echo "OBJ: output/obj/modules/cim_city_roads.obj"
echo "FBX: output/fbx/modules/cim_city_roads.fbx"
echo "Junction debug FBX directory: output/fbx/junctions"
