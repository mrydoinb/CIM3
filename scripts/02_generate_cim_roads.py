#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Generate the CIM road OBJ and semantics.

Separate junction debug models are opt-in with CIM_ROAD_EXPORT_JUNCTION_DEBUG=1.
Tree generation for cim4 is controlled with --trees / --no-trees or the
CIM_ROAD_GENERATE_TREES environment variable.

Examples:
    python scripts/02_generate_cim_roads.py --source expressway2 --level cim4
    python scripts/02_generate_cim_roads.py --source full --level both
    python scripts/02_generate_cim_roads.py --source expressway2 --level cim4 --no-trees
    python scripts/02_generate_cim_roads.py --roads-file path/to/roads.shp
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ROAD_LAYER_RELATIVE_PATH = Path("道路中心线300km_增加名称") / "道路中心线300km_增加名称.shp"
SOURCE_PRESETS = {
    "expressway2": {
        "data_dir": ROOT / "data" / "Data_快速路2_sample",
        "roads_file": ROOT / "data" / "Data_快速路2_sample" / ROAD_LAYER_RELATIVE_PATH,
    },
    "full": {
        "data_dir": ROOT / "data" / "Data",
        "roads_file": ROOT / "data" / "Data" / ROAD_LAYER_RELATIVE_PATH,
    },
    "clip-1-10": {
        "data_dir": ROOT / "data" / "Data_clip_1_10",
        "roads_file": ROOT / "data" / "Data_clip_1_10" / "road50kms" / "道路修改50kms.shp",
    },
}

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=sorted(SOURCE_PRESETS),
        default="expressway2",
        help="Named source-data preset. Default: expressway2.",
    )
    parser.add_argument(
        "--level",
        choices=["cim3", "cim4", "both"],
        default="cim4",
        help="Road generation detail level. Use 'both' to generate cim3 and cim4. Default: cim4.",
    )
    parser.add_argument(
        "--roads-file",
        type=Path,
        help="Explicit road vector file. Overrides the selected preset road layer.",
    )
    tree_group = parser.add_mutually_exclusive_group()
    tree_group.add_argument(
        "--trees",
        dest="trees",
        action="store_true",
        help="Enable cim4 tree generation. Default: enabled.",
    )
    tree_group.add_argument(
        "--no-trees",
        dest="trees",
        action="store_false",
        help="Disable cim4 tree generation.",
    )
    parser.set_defaults(trees=None)
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Explicit source-data directory for related layers. Defaults to the preset directory.",
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
    roads_file = (
        args.roads_file.expanduser().resolve()
        if args.roads_file is not None
        else Path(preset["roads_file"]).resolve()
    )
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
    if args.trees is not None:
        os.environ["CIM_ROAD_GENERATE_TREES"] = "1" if args.trees else "0"
    print(f"Source preset: {args.source}", flush=True)
    levels = ["cim3", "cim4"] if args.level == "both" else [args.level]
    print(f"Road generation level: {args.level}", flush=True)
    if args.trees is not None:
        print(f"CIM4 tree generation: {'enabled' if args.trees else 'disabled'}", flush=True)
    print(f"Source data directory: {data_dir}", flush=True)
    print(f"Road source file: {roads_file}", flush=True)

    # Import after configuring the environment because road.generator resolves
    # its source paths at module import time.
    from city.pipeline import generate_roads_only

    for level in levels:
        if len(levels) > 1:
            print(f"\n=== Generating {level.upper()} roads ===", flush=True)
        generate_roads_only(level, generate_trees=args.trees)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
