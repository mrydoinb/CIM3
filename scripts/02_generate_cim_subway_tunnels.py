#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Generate CIM subway interval tunnel OBJ and semantics.

This script intentionally stays separate from road generation. It shares the
same source-data presets, but only reads railway layers and only writes subway
tunnel outputs. For the full dataset, the railway layer is discovered as
data/Data/轨道线和站点转坐标2000/轨道线2000.shp.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SOURCE_PRESETS = {
    "full": ROOT / "data" / "Data",
    "clip-1-10": ROOT / "data" / "Data_clip_1_10",
    "expressway2": ROOT / "data" / "Data_快速路2_sample",
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
        choices=["cim4"],
        default="cim4",
        help="Subway tunnel generation detail level. Default: cim4.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Explicit source-data directory for railway layers. Defaults to the preset directory.",
    )
    parser.add_argument(
        "--line",
        help="Optional subway line-name substring filter, e.g. 深圳地铁4号线.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List configured source presets and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_sources:
        for name, data_dir in SOURCE_PRESETS.items():
            print(f"{name}:")
            print(f"  data_dir: {data_dir}")
        return 0

    data_dir = (args.data_dir or SOURCE_PRESETS[args.source]).expanduser().resolve()
    if not data_dir.exists():
        raise FileNotFoundError(f"Source data directory not found: {data_dir}")

    os.environ["CIM_ROAD_DATA_DIR"] = str(data_dir)
    print(f"Source preset: {args.source}", flush=True)
    print(f"Subway tunnel generation level: {args.level}", flush=True)
    if args.line:
        print(f"Subway line filter: {args.line}", flush=True)
    print(f"Source data directory: {data_dir}", flush=True)

    from city.pipeline import generate_subway_tunnels_only

    generate_subway_tunnels_only(args.level, line_filter=args.line)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
