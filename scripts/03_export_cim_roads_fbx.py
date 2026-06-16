#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Launch Blender to export only the generated CIM road OBJ module as FBX."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BLENDER_EXPORT_SCRIPT = ROOT / "scripts" / "04_export_cim_roads_fbx_blender.py"
JUNCTION_DEBUG_FBX_DIR = ROOT / "output" / "fbx" / "junctions"


def road_fbx_path(level: str) -> Path:
    return ROOT / "output" / "fbx" / "modules" / level / "city_roads.fbx"


def blender_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("BLENDER_EXE")
    if env_path:
        candidates.append(Path(env_path))

    path_blender = shutil.which("blender")
    if path_blender:
        candidates.append(Path(path_blender))

    for base in (
        Path("C:/Program Files/Blender Foundation"),
        Path("D:/Program Files/Blender Foundation"),
        Path("D:/ruanjian"),
    ):
        if not base.exists():
            continue
        candidates.extend(sorted(base.glob("Blender */blender.exe"), reverse=True))

    candidates.extend(
        [
            Path("C:/Program Files/Blender Foundation/Blender 5.1/blender.exe"),
            Path("C:/Program Files/Blender Foundation/Blender 5.0/blender.exe"),
            Path("D:/ruanjian/Blender 5.1/blender.exe"),
        ]
    )
    return candidates


def find_blender(explicit_path: str | None = None) -> Path:
    candidates = [Path(explicit_path)] if explicit_path else blender_candidates()
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser()
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Blender executable not found. Set BLENDER_EXE or pass --blender "
        "with the full path to blender.exe."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", help="Full path to blender.exe. Overrides BLENDER_EXE and PATH lookup.")
    parser.add_argument(
        "--level",
        choices=["cim3", "cim4", "both"],
        default="cim4",
        help="Road generation detail level to export. Use 'both' to export cim3 and cim4. Default: cim4.",
    )
    args = parser.parse_args(argv)

    blender = find_blender(args.blender)
    levels = ["cim3", "cim4"] if args.level == "both" else [args.level]
    for level in levels:
        env = os.environ.copy()
        env["CIM_ROAD_LEVEL"] = level
        command = [
            str(blender),
            "--background",
            "--python",
            str(BLENDER_EXPORT_SCRIPT),
        ]
        print(f"[1/1] Exporting {level} road FBX with Blender: {blender}", flush=True)
        subprocess.run(command, cwd=ROOT, check=True, env=env)
        print(f"Road FBX output: {road_fbx_path(level)}", flush=True)
    if str(os.environ.get("CIM_ROAD_EXPORT_JUNCTION_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}:
        print(f"Junction debug FBX directory: {JUNCTION_DEBUG_FBX_DIR}", flush=True)
    else:
        print("Junction debug FBX export: skipped (set CIM_ROAD_EXPORT_JUNCTION_DEBUG=1 to enable)", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
