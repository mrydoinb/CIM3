#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Check CIM subway tunnel corridor separation."""

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
        "--data-dir",
        type=Path,
        help="Explicit source-data directory for railway layers. Defaults to the preset directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = (args.data_dir or SOURCE_PRESETS[args.source]).expanduser().resolve()
    if not data_dir.exists():
        raise FileNotFoundError(f"Source data directory not found: {data_dir}")
    os.environ["CIM_ROAD_DATA_DIR"] = str(data_dir)

    from subway.checks import SUBWAY_TUNNEL_SEPARATION_CHECK_PATH, write_subway_tunnel_separation_check

    report = write_subway_tunnel_separation_check()
    print(f"Subway tunnel separation check: {report['status']}")
    print(f"- generated corridors: {report['summary']['generated_corridor_count']}")
    print(f"- merged or ignored candidates: {report['summary']['merged_or_ignored_candidate_count']}")
    print(f"- issues: {report['summary']['issue_count']}")
    print(f"- report: {SUBWAY_TUNNEL_SEPARATION_CHECK_PATH}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
