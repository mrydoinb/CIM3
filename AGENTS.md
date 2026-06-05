# CIM Road Codex Guide

## Project Focus

- This repository generates CIM road meshes, junction debug models, OBJ, and FBX outputs.
- The user validates final geometry visually in Blender. Do not run slow QC flows unless explicitly requested.
- Most current issues are road/junction geometry issues. When the user names a junction such as `J0006`, treat that as the primary debug target.

## Preferred Workflow

- Read the existing code before changing logic. Prefer `src/city/pipeline.py`, `src/road/generator.py`, and existing helpers over new abstractions.
- Keep iterations fast:
  - Use `py_compile` and `git diff --check` for lightweight checks.
  - Run full model generation/export only when a geometry change needs visual inspection.
  - Do not run full QC reports by default.
- After changing road generation logic, use:
  - `D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\02_generate_cim_roads.py`
  - `D:\ProgramData\miniconda3\envs\cim-road\python.exe scripts\03_export_cim_roads_fbx.py --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"`

## Junction Debugging

- For a named junction, check:
  - `output/semantic/cim_city_junctions_debug_manifest.json`
  - `output/obj/junctions/Jxxxx.obj`
  - `output/fbx/junctions/Jxxxx.fbx`
- Keep junction debug exports separate so the user can load and inspect one problem area directly.
- When the issue is a gap, overlap, or misaligned road element, analyze the polygon/mesh generation path before editing.

## Geometry Principles

- Roadside elements include `Sidewalk`, `Facility_Belt`, and `Green_Belt`.
- Different road classes should meet cleanly at intersections. Do not rely on many tiny patch-like fixes when a shared geometric construction is possible.
- For junction surfaces and roadside components, prefer planar geometry operations such as union, intersection, and difference over ad hoc string or vertex tweaks.
- Keep sidewalks, facility belts, and green belts consistent with straight-road cross-section logic when they enter an intersection.

## Git And Editing

- Do not revert user changes unless the user explicitly asks.
- Do not use destructive Git commands such as `git reset --hard`.
- Keep edits scoped to the requested model behavior and avoid unrelated cleanup.
