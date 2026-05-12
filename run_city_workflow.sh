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
echo "       CIM city automatic modeling workflow"
echo "==================================================="

echo "[1/3] Downloading Munich Hauptbahnhof OSM source data..."
"$PYTHON_EXE" scripts/00_download_allianz_arena_osm.py

echo "[2/3] Generating CIM city OBJ with roads, buildings, subway, bus stops, and utilities..."
"$PYTHON_EXE" scripts/05_generate_cim_city.py

echo "[3/3] Exporting materialized CIM city FBX..."
"$BLENDER_EXE" --background --python scripts/06_export_cim_city_fbx_blender.py

echo "==================================================="
echo "City workflow finished."
echo "OBJ: output/obj/cim_city.obj"
echo "FBX: output/fbx/cim_city.fbx"
