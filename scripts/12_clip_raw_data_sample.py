#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Create a clipped raw-data copy for fast CIM city test runs.

The script does not modify ``data/Data``. It writes a new folder with the same
relative vector-file layout, clipped to a center window whose area is roughly
``--fraction`` of the source road extent. The default fraction is 0.1.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
from shapely.geometry import box


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "Data"
DEFAULT_OUTPUT = ROOT / "data" / "Data_clip_1_10"
TARGET_CRS = "EPSG:4547"
SOURCE_PROJECTED_CRS = "EPSG:4547"
VECTOR_SUFFIXES = {".shp", ".geojson", ".json", ".gpkg"}
ROAD_LAYER_PATTERNS = [
    "road50kms/*.shp",
    "road_centerline.geojson",
    "**/road_centerline.geojson",
    "**/*道路*.shp",
    "**/*road*.shp",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Raw data folder to clip.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="Output folder for the clipped copy.")
    parser.add_argument("--fraction", type=float, default=0.1, help="Target area fraction of road extent, e.g. 0.1.")
    parser.add_argument("--center-x", type=float, default=None, help="Optional clip center X in target CRS.")
    parser.add_argument("--center-y", type=float, default=None, help="Optional clip center Y in target CRS.")
    parser.add_argument("--overwrite", action="store_true", help="Replace the output folder if it already exists.")
    return parser.parse_args()


def infer_crs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is not None or gdf.empty:
        return gdf
    bounds = gdf.total_bounds
    finite = [abs(float(value)) for value in bounds if np.isfinite(value)]
    max_abs_coord = max(finite) if finite else 0.0
    return gdf.set_crs(SOURCE_PROJECTED_CRS if max_abs_coord > 1000.0 else "EPSG:4326")


def read_vector(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.empty:
        return gdf
    gdf = infer_crs(gdf)
    gdf = gdf.to_crs(TARGET_CRS)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if not gdf.empty:
        gdf = gdf[gdf.geometry.is_valid].copy()
    return gdf


def vector_files(source: Path) -> list[Path]:
    files = []
    for path in source.rglob("*"):
        if path.is_file() and path.suffix.lower() in VECTOR_SUFFIXES:
            files.append(path)
    return sorted(files)


def find_road_layer(source: Path) -> Path:
    for pattern in ROAD_LAYER_PATTERNS:
        matches = sorted(source.glob(pattern))
        if matches:
            return matches[0]
    shp_files = sorted(source.rglob("*.shp"))
    if shp_files:
        return shp_files[0]
    raise FileNotFoundError(f"No vector road layer found under {source}")


def clip_bounds_from_roads(
    road_layer: Path,
    fraction: float,
    center_x: float | None = None,
    center_y: float | None = None,
):
    roads = read_vector(road_layer)
    if roads.empty:
        raise ValueError(f"Road layer is empty: {road_layer}")
    minx, miny, maxx, maxy = [float(value) for value in roads.total_bounds]
    fraction = max(0.0, min(float(fraction), 1.0))
    scale = math.sqrt(fraction) if fraction > 0.0 else math.sqrt(0.1)
    width = max((maxx - minx) * scale, 1.0)
    height = max((maxy - miny) * scale, 1.0)
    cx = float(center_x) if center_x is not None else (minx + maxx) / 2.0
    cy = float(center_y) if center_y is not None else (miny + maxy) / 2.0
    return box(cx - width / 2.0, cy - height / 2.0, cx + width / 2.0, cy + height / 2.0)


def output_path_for(source_root: Path, output_root: Path, path: Path) -> Path:
    relative = path.relative_to(source_root)
    return output_root / relative


def write_vector(gdf: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".shp":
        gdf.to_file(path, driver="ESRI Shapefile", encoding="utf-8")
    elif suffix in {".geojson", ".json"}:
        gdf.to_file(path, driver="GeoJSON")
    elif suffix == ".gpkg":
        gdf.to_file(path, driver="GPKG")
    else:
        raise ValueError(f"Unsupported vector output: {path}")


def prepare_output_dir(source: Path, output: Path, overwrite: bool) -> None:
    source_resolved = source.resolve()
    output_resolved = output.resolve()
    if output_resolved == source_resolved:
        raise ValueError("Output folder must be different from source folder.")
    if output.exists():
        if not overwrite:
            raise FileExistsError(f"Output folder already exists: {output}. Use --overwrite to replace it.")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    output = args.out.resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    road_layer = find_road_layer(source)
    clip_geom = clip_bounds_from_roads(road_layer, args.fraction, args.center_x, args.center_y)
    clip_gdf = gpd.GeoDataFrame(geometry=[clip_geom], crs=TARGET_CRS)

    prepare_output_dir(source, output, args.overwrite)
    manifest: dict[str, Any] = {
        "source": source,
        "output": output,
        "road_layer_for_bounds": road_layer,
        "target_crs": TARGET_CRS,
        "fraction": args.fraction,
        "clip_bounds": [round(float(value), 3) for value in clip_geom.bounds],
        "layers": [],
    }

    for path in vector_files(source):
        relative = path.relative_to(source)
        try:
            gdf = read_vector(path)
            original_count = int(len(gdf))
            clipped = gpd.clip(gdf, clip_gdf) if not gdf.empty else gdf
            clipped = clipped[clipped.geometry.notna() & ~clipped.geometry.is_empty].copy()
            clipped_count = int(len(clipped))
            if clipped_count > 0:
                out_path = output_path_for(source, output, path)
                write_vector(clipped, out_path)
            status = "written" if clipped_count > 0 else "empty_after_clip"
        except Exception as exc:
            original_count = None
            clipped_count = None
            status = f"error: {exc}"
        manifest["layers"].append(
            {
                "path": str(relative),
                "original_count": original_count,
                "clipped_count": clipped_count,
                "status": status,
            }
        )
        print(f"{relative}: {status} ({original_count} -> {clipped_count})", flush=True)

    manifest_path = output / "clip_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, default=json_safe)
    print(f"Clipped data written to: {output}")
    print(f"Manifest written to: {manifest_path}")


if __name__ == "__main__":
    main()
