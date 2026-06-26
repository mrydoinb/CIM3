#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Generate CIM underground utility pipe OBJ and semantics.

Examples:
    python scripts/02_generate_cim_utility_pipes.py --source full --level both
    python scripts/02_generate_cim_utility_pipes.py --source clip-1-10 --level cim4
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ROAD_LAYER_RELATIVE_PATH = Path("閬撹矾涓績绾?00km_澧炲姞鍚嶇О") / "閬撹矾涓績绾?00km_澧炲姞鍚嶇О.shp"
SOURCE_PRESETS = {
    "expressway2": {
        "data_dir": ROOT / "data" / "Data_蹇€熻矾2_sample",
        "roads_file": ROOT / "data" / "Data_蹇€熻矾2_sample" / ROAD_LAYER_RELATIVE_PATH,
    },
    "full": {
        "data_dir": ROOT / "data" / "Data",
        "roads_file": ROOT / "data" / "Data" / ROAD_LAYER_RELATIVE_PATH,
    },
    "clip-1-10": {
        "data_dir": ROOT / "data" / "Data_clip_1_10",
        "roads_file": ROOT / "data" / "Data_clip_1_10" / "road50kms" / "閬撹矾淇敼50kms.shp",
    },
}

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=sorted(SOURCE_PRESETS),
        default="full",
        help="Named source-data preset. Default: full.",
    )
    parser.add_argument(
        "--level",
        choices=["cim3", "cim4", "both"],
        default="both",
        help="Utility generation detail level. Use 'both' to generate cim3 and cim4. Default: both.",
    )
    parser.add_argument(
        "--roads-file",
        type=Path,
        help="Explicit road vector file for shared model origin. Overrides the selected preset road layer.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Explicit source-data directory for utility layers. Defaults to the preset directory.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List configured source presets and exit.",
    )
    return parser.parse_args(argv)


def source_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    preset = SOURCE_PRESETS[args.source]
    data_dir = (args.data_dir or preset["data_dir"]).expanduser().resolve()
    if args.roads_file is not None:
        roads_file = args.roads_file.expanduser().resolve()
    else:
        roads_file = Path(preset["roads_file"]).resolve()
        if not roads_file.exists():
            road_candidates = sorted(data_dir.glob("**/*中心线*.shp")) + sorted(data_dir.glob("**/*道路修改*.shp"))
            if road_candidates:
                roads_file = road_candidates[0].resolve()
    return data_dir, roads_file


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_sources:
        for name, preset in SOURCE_PRESETS.items():
            print(f"{name}:")
            print(f"  data_dir: {preset['data_dir']}")
            print(f"  roads_file: {preset['roads_file']}")
        return 0

    data_dir, roads_file = source_paths(args)
    if not data_dir.exists():
        raise FileNotFoundError(f"Source data directory not found: {data_dir}")
    if not roads_file.exists():
        raise FileNotFoundError(f"Road source file not found: {roads_file}")

    os.environ["CIM_ROAD_DATA_DIR"] = str(data_dir)
    os.environ["CIM_ROAD_ROADS_FILE"] = str(roads_file)
    print(f"Source preset: {args.source}", flush=True)
    print(f"Utility generation level: {args.level}", flush=True)
    print(f"Source data directory: {data_dir}", flush=True)
    print(f"Road source file: {roads_file}", flush=True)

    from city.pipeline import generate_utility_pipes_only

    levels = ["cim3", "cim4"] if args.level == "both" else [args.level]
    for level in levels:
        if len(levels) > 1:
            print(f"\n=== Generating {level.upper()} utility pipes ===", flush=True)
        generate_utility_pipes_only(level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
