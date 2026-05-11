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

echo "[1/3] Generating CIM3 road geometry and topology..."
"$PYTHON_EXE" scripts/01_generate_cim3_road.py

echo "[2/3] Running Blender background service: applying PBR/procedural materials..."
"$BLENDER_EXE" --background --python scripts/03_apply_materials_blender.py

echo "[3/3] Running Blender background service: exporting FBX..."
"$BLENDER_EXE" --background --python scripts/02_export_fbx_blender.py

echo "==================================================="
echo "Workflow finished."
echo "Material GLB: output/gltf/road_test_realistic.glb"
echo "Material FBX: output/fbx/road_test.fbx"
echo "Base GLB without PBR postprocess: output/gltf/road_test.glb"
