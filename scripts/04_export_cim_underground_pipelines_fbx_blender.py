#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Blender-side exporter for generated underground pipeline OBJ modules."""

from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from blender.fbx_export import export_obj_to_fbx


DATASETS = ("sys02", "ws")


def export_level() -> str:
    level = str(os.environ.get("CIM_UNDERGROUND_LEVEL", "cim4")).strip().lower()
    return level if level in {"cim3", "cim4"} else "cim4"


def export_datasets() -> list[str]:
    raw = str(os.environ.get("CIM_UNDERGROUND_DATASET", "all")).strip().lower()
    if raw in {"", "all"}:
        return sorted(DATASETS)
    return [item.strip() for item in raw.split(",") if item.strip() in DATASETS]


def obj_path(dataset: str, level: str) -> Path:
    return ROOT / "output" / "obj" / "modules" / level / f"city_underground_pipelines_{dataset}.obj"


def fbx_path(dataset: str, level: str) -> Path:
    return ROOT / "output" / "fbx" / "modules" / level / f"city_underground_pipelines_{dataset}.fbx"


def main() -> None:
    level = export_level()
    for dataset in export_datasets():
        export_obj_to_fbx(
            obj_path(dataset, level),
            fbx_path(dataset, level),
        )


if __name__ == "__main__":
    main()
