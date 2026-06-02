# CIM Road POC Architecture

## Goal

The active workflow is optimized for fast road and junction iteration:

- `scripts/` contains only four ordered command-line entry points.
- `src/` contains reusable modeling and export logic.
- Junction debugging is exported as an independent side path, without adding
  patch logic to the main road geometry pipeline.
- Expensive QC is opt-in through `CIM_ROAD_RUN_QC=1`.

## Active Scripts

| Step | Script | Responsibility |
|---|---|---|
| 01 | `scripts/01_clip_raw_data_sample.py` | Rebuild the clipped test dataset when needed. |
| 02 | `scripts/02_generate_cim_roads.py` | Generate road OBJ, semantics, classifications, and separate junction OBJ models. |
| 03 | `scripts/03_export_cim_roads_fbx.py` | Launch Blender and export road and junction-debug FBX files. |
| 04 | `scripts/04_export_cim_roads_fbx_blender.py` | Blender-side helper called by step 03. |

## Core Modules

```text
src/
  road/generator.py
  city/pipeline.py
  city/junction_debug.py
  city/mesh_utils.py
  blender/fbx_export.py
```

`src/city/pipeline.py` owns road generation. `src/city/junction_debug.py`
crops finalized meshes into independently inspectable `Jxxxx` models.

## Run

```bash
python scripts/02_generate_cim_roads.py
python scripts/03_export_cim_roads_fbx.py
```

Regenerate clipped data only when the source dataset changes:

```bash
python scripts/01_clip_raw_data_sample.py --overwrite
```

## Outputs

```text
output/obj/modules/cim_city_roads.obj
output/obj/junctions/J0000.obj
output/fbx/modules/cim_city_roads.fbx
output/fbx/modules/cim_city_junctions_debug.fbx
output/semantic/cim_city_junctions_debug_manifest.json
```
