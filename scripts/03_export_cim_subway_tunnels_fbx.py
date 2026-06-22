#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Launch Blender to export the generated CIM subway tunnel module as FBX.

This is the subway counterpart of scripts/03_export_cim_roads_fbx.py. By
default it exports the full hybrid tunnel model: compact procedural OBJ from
scripts/02_generate_cim_subway_tunnels.py plus sample-derived small components.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BLENDER_EXPORT_SCRIPT = ROOT / "scripts" / "04_export_cim_subway_tunnels_fbx_blender.py"
PATH_SCRIPT = ROOT / "scripts" / "05_generate_cim_subway_template_paths.py"
DEFAULT_TEMPLATE = Path.home() / "Desktop" / "chk" / "\u5730\u94c1" / "subway01.blend"
MODULE_OBJ_DIR = ROOT / "output" / "obj" / "modules" / "cim4"
MODULE_FBX_DIR = ROOT / "output" / "fbx" / "modules" / "cim4"
MODULE_BLEND_DIR = ROOT / "output" / "blend" / "modules" / "cim4"
SEMANTIC_DIR = ROOT / "output" / "semantic" / "cim4"
FINAL_OBJ = MODULE_OBJ_DIR / "subway_tunnels.obj"
PROCEDURAL_OBJ = MODULE_OBJ_DIR / "subway_tunnels_procedural.obj"
FINAL_FBX = MODULE_FBX_DIR / "subway_tunnels.fbx"
FINAL_BLEND = MODULE_BLEND_DIR / "subway_tunnels.blend"
MESH_ATTRIBUTES_JSON = SEMANTIC_DIR / "subway_tunnels_mesh_attributes.json"
TEMPLATE_PATH_JSON = SEMANTIC_DIR / "subway_tunnel_template_paths.json"


def subway_fbx_path(level: str) -> Path:
    return ROOT / "output" / "fbx" / "modules" / level / "subway_tunnels.fbx"


def subway_obj_path(level: str) -> Path:
    return ROOT / "output" / "obj" / "modules" / level / "subway_tunnels.obj"


def subway_semantic_path(level: str) -> Path:
    return ROOT / "output" / "semantic" / level / "subway_tunnels_semantic.json"


def infer_line_filters(level: str) -> list[str]:
    semantic_path = subway_semantic_path(level)
    if not semantic_path.exists():
        return []
    with semantic_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    line_names: list[str] = []
    for record in data.get("objects", []):
        line_name = str(record.get("line_name") or "").strip()
        if line_name and line_name not in line_names:
            line_names.append(line_name)
    return line_names


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
        choices=["cim4"],
        default="cim4",
        help="Subway tunnel generation detail level to export. Default: cim4.",
    )
    parser.add_argument(
        "--line",
        action="append",
        default=[],
        help=(
            "Optional subway line-name substring filter for hybrid path reconstruction. "
            "If omitted, line names are inferred from the generated subway_tunnels_semantic.json."
        ),
    )
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="subway01.blend template path.")
    parser.add_argument(
        "--save-blend",
        action="store_true",
        help="Also save output/blend/modules/cim4/subway_tunnels.blend.",
    )
    parser.add_argument(
        "--export-hybrid-obj",
        action="store_true",
        help=(
            "Also export the fully expanded hybrid OBJ. This is usually huge because OBJ has no instancing; "
            "the default OBJ remains the compact road-style module while the FBX carries full detail."
        ),
    )
    parser.add_argument(
        "--procedural-only",
        action="store_true",
        help="Use the old direct OBJ-to-FBX path and skip sample-derived small components.",
    )
    args = parser.parse_args(argv)

    blender = find_blender(args.blender)

    if args.procedural_only:
        env = os.environ.copy()
        env["CIM_SUBWAY_LEVEL"] = args.level
        env["CIM_SUBWAY_PROCEDURAL_ONLY"] = "1"
        command = [
            str(blender),
            "--background",
            "--python",
            str(BLENDER_EXPORT_SCRIPT),
        ]
        print(f"[1/1] Exporting {args.level} procedural subway tunnel FBX with Blender: {blender}", flush=True)
        subprocess.run(command, cwd=ROOT, check=True, env=env)
        print(f"Subway tunnel FBX output: {subway_fbx_path(args.level)}", flush=True)
        return 0

    if not subway_obj_path(args.level).exists():
        raise FileNotFoundError(
            f"Subway OBJ not found: {subway_obj_path(args.level)}. "
            "Run scripts/02_generate_cim_subway_tunnels.py first, matching the road workflow."
        )

    line_filters = list(args.line or [])
    if not line_filters:
        line_filters = infer_line_filters(args.level)

    procedural_input_obj = PROCEDURAL_OBJ if args.export_hybrid_obj else FINAL_OBJ
    if args.export_hybrid_obj:
        PROCEDURAL_OBJ.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FINAL_OBJ, PROCEDURAL_OBJ)

    path_command = [
        sys.executable,
        str(PATH_SCRIPT),
        "--no-collapse-parallel",
        "--no-merge-connected",
    ]
    for line_filter in line_filters:
        path_command.extend(["--line", line_filter])
    subprocess.run(path_command, cwd=ROOT, check=True)

    env = os.environ.copy()
    env["CIM_SUBWAY_TEMPLATE_BLEND"] = str(args.template)
    env["CIM_SUBWAY_PROCEDURAL_OBJ"] = str(procedural_input_obj)
    env["CIM_SUBWAY_PROCEDURAL_MESH_ATTRIBUTES_JSON"] = str(MESH_ATTRIBUTES_JSON)
    env["CIM_SUBWAY_HYBRID_OUTPUT_OBJ"] = str(FINAL_OBJ)
    env["CIM_SUBWAY_HYBRID_OUTPUT_FBX"] = str(FINAL_FBX)
    env["CIM_SUBWAY_HYBRID_OUTPUT_BLEND"] = str(FINAL_BLEND)
    env["CIM_SUBWAY_HYBRID_MESH_ATTRIBUTES_JSON"] = str(MESH_ATTRIBUTES_JSON)
    env["CIM_SUBWAY_HYBRID_EXPORT_OBJ"] = "1" if args.export_hybrid_obj else "0"
    env["CIM_SUBWAY_HYBRID_EXPORT_FBX"] = "1"
    env["CIM_SUBWAY_HYBRID_SAVE_BLEND"] = "1" if args.save_blend else "0"
    env["CIM_SUBWAY_PROCEDURAL_ONLY"] = "0"

    command = [
        str(blender),
        "--background",
        "--python",
        str(BLENDER_EXPORT_SCRIPT),
    ]

    if line_filters:
        print(f"Inferred subway line filters: {line_filters}", flush=True)
    else:
        print("Subway line filters: all subway records from source preset", flush=True)
    print(f"[1/1] Exporting {args.level} hybrid subway tunnel FBX with Blender: {blender}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True, env=env)

    for stale_path in (TEMPLATE_PATH_JSON,):
        if stale_path.exists():
            stale_path.unlink()
    if args.export_hybrid_obj and PROCEDURAL_OBJ.exists():
        PROCEDURAL_OBJ.unlink()
    if not args.save_blend and FINAL_BLEND.exists():
        FINAL_BLEND.unlink()

    print(f"Subway tunnel FBX output: {subway_fbx_path(args.level)}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
