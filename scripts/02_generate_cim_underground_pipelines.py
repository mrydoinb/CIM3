#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Generate underground pipeline BIM OBJ modules and semantic sidecars.

Examples:
    python scripts/02_generate_cim_underground_pipelines.py --dataset all --level cim4
    python scripts/02_generate_cim_underground_pipelines.py --dataset sys02 --level cim4
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from city.underground_pipelines import DATASET_LAYERS, DEFAULT_SHP_DIR, generate_underground_pipelines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        choices=["all", *sorted(DATASET_LAYERS)],
        default="all",
        help="Dataset to generate. Default: all.",
    )
    parser.add_argument(
        "--level",
        choices=["cim3", "cim4"],
        default="cim4",
        help="Generation detail level. Default: cim4.",
    )
    parser.add_argument(
        "--shp-dir",
        type=Path,
        default=DEFAULT_SHP_DIR,
        help="Directory containing Excel-derived underground Shapefiles.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    datasets = sorted(DATASET_LAYERS) if args.dataset == "all" else [args.dataset]
    print(f"Underground generation level: {args.level}", flush=True)
    print(f"Underground source shp directory: {args.shp_dir}", flush=True)
    print(f"Underground datasets: {', '.join(datasets)}", flush=True)

    result = generate_underground_pipelines(datasets, args.level, args.shp_dir)
    print("CIM underground pipeline OBJ generated:", flush=True)
    for dataset, item in result["datasets"].items():
        print(f"- {dataset} OBJ: {item['obj_path']}", flush=True)
        print(f"- {dataset} semantic objects: {len(item['records'])} -> {item['semantic_path']}", flush=True)
        print(f"- {dataset} mesh objects: {len(item['meshes'])} -> {item['mesh_attributes_path']}", flush=True)
        print(f"- {dataset} QC: {item['qc_path']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
