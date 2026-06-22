#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Export subway tunnels by instancing Mesh.001-Mesh.041 from subway01.blend."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PATH_SCRIPT = ROOT / "scripts" / "05_generate_cim_subway_template_paths.py"
BLENDER_SCRIPT = ROOT / "scripts" / "07_export_cim_subway_template_fbx_blender.py"
DEFAULT_TEMPLATE = Path.home() / "Desktop" / "chk" / "\u5730\u94c1" / "subway01.blend"


def blender_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("BLENDER_EXE")
    if env_path:
        candidates.append(Path(env_path))
    path_blender = shutil.which("blender")
    if path_blender:
        candidates.append(Path(path_blender))
    for base in (Path("C:/Program Files/Blender Foundation"), Path("D:/Program Files/Blender Foundation")):
        if base.exists():
            candidates.extend(sorted(base.glob("Blender */blender.exe"), reverse=True))
    candidates.append(Path("C:/Program Files/Blender Foundation/Blender 5.1/blender.exe"))
    return candidates


def find_blender(explicit_path: str | None = None) -> Path:
    candidates = [Path(explicit_path)] if explicit_path else blender_candidates()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Blender executable not found. Pass --blender or set BLENDER_EXE.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", help="Full path to blender.exe.")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument(
        "--line",
        action="append",
        default=[],
        help="Only export subway records whose line name/source id contains this text. Repeat for multiple lines.",
    )
    parser.add_argument(
        "--no-merge-connected",
        action="store_true",
        help="Keep source railway records split instead of merging connected same-name tunnel segments.",
    )
    parser.add_argument(
        "--no-collapse-parallel",
        action="store_true",
        help="Keep parallel same-name source tunnel axes instead of collapsing them to the center axis for the twin-tunnel template.",
    )
    parser.add_argument(
        "--chunk-length",
        type=float,
        default=None,
        help=(
            "Chunk length in meters. Omit to use Mesh.004 template length; "
            "0 means one Mesh.001-Mesh.041 template set per tunnel corridor."
        ),
    )
    parser.add_argument(
        "--extend-template",
        action="store_true",
        help="Use one stretched Mesh.001-Mesh.041 template set per tunnel corridor instead of tiling chunks.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=float,
        default=None,
        help="Overlap adjacent template chunks in meters to hide visible seams. Omit for the default 2m overlap.",
    )
    parser.add_argument(
        "--exact-template-geometry",
        action="store_true",
        help=(
            "Default behavior. Do not scale or stretch Mesh.001-Mesh.041. The source geometry is copied exactly; "
            "template blocks may overrun corridor ends slightly."
        ),
    )
    parser.add_argument(
        "--fit-template-to-chunks",
        action="store_true",
        help=(
            "Opt out of exact geometry and longitudinally scale each template block to the path chunk. "
            "Use only when endpoint fit matters more than source-geometry identity."
        ),
    )
    parser.add_argument(
        "--export-fbx",
        action="store_true",
        help="Also export FBX. A .blend is always saved; FBX may be large.",
    )
    args = parser.parse_args(argv)

    path_command = [sys.executable, str(PATH_SCRIPT)]
    for line_filter in args.line:
        path_command.extend(["--line", line_filter])
    if args.no_merge_connected:
        path_command.append("--no-merge-connected")
    if args.no_collapse_parallel:
        path_command.append("--no-collapse-parallel")
    subprocess.run(path_command, cwd=ROOT, check=True)

    blender = find_blender(args.blender)
    env = os.environ.copy()
    env["CIM_SUBWAY_TEMPLATE_BLEND"] = str(args.template)
    if args.extend_template:
        env["CIM_SUBWAY_TEMPLATE_CHUNK_LENGTH_M"] = "0"
    elif args.chunk_length is not None:
        env["CIM_SUBWAY_TEMPLATE_CHUNK_LENGTH_M"] = str(args.chunk_length)
    if args.chunk_overlap is not None:
        env["CIM_SUBWAY_TEMPLATE_CHUNK_OVERLAP_M"] = str(args.chunk_overlap)
    env["CIM_SUBWAY_TEMPLATE_EXACT_GEOMETRY"] = "0" if args.fit_template_to_chunks else "1"
    env["CIM_SUBWAY_TEMPLATE_EXPORT_FBX"] = "1" if args.export_fbx else "0"
    command = [str(blender), "--background", "--python", str(BLENDER_SCRIPT)]
    print(f"[1/1] Exporting template-instanced subway tunnels with Blender: {blender}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True, env=env)
    print("Template-instanced subway outputs:")
    print(f"- blend: {ROOT / 'output' / 'blend' / 'modules' / 'cim4' / 'subway_tunnels_template.blend'}")
    print(f"- fbx:   {ROOT / 'output' / 'fbx' / 'modules' / 'cim4' / 'subway_tunnels_template.fbx'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
