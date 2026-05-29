#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Geodata loading, localization, and basemap helpers for the city pipeline."""

from __future__ import annotations

from pathlib import Path
import struct

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
import trimesh
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, Polygon, MultiPolygon

from road import generator as road_gen


TARGET_CRS = road_gen.TARGET_CRS
SOURCE_PROJECTED_CRS = "EPSG:4547"


def _dbf_encoding(path: Path) -> str:
    cpg_path = path.with_suffix(".cpg")
    if cpg_path.exists():
        try:
            value = cpg_path.read_text(encoding="ascii", errors="ignore").strip()
            if value:
                return value
        except OSError:
            pass
    return "utf-8"


def _decode_dbf_text(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="replace").strip()


def _parse_dbf_value(raw: bytes, field_type: str, decimals: int, encoding: str):
    text = _decode_dbf_text(raw, encoding)
    if text == "":
        return None
    if field_type in {"N", "F"}:
        try:
            value = float(text)
        except ValueError:
            return text
        if decimals == 0 and value.is_integer():
            return int(value)
        return value
    if field_type == "L":
        return text.upper() in {"Y", "T"}
    return text


def _read_dbf_records(path: Path) -> list[dict[str, object] | None]:
    dbf_path = path.with_suffix(".dbf")
    if not dbf_path.exists():
        return []
    data = dbf_path.read_bytes()
    if len(data) < 33:
        return []
    encoding = _dbf_encoding(dbf_path)
    record_count = struct.unpack("<I", data[4:8])[0]
    header_length = struct.unpack("<H", data[8:10])[0]
    record_length = struct.unpack("<H", data[10:12])[0]
    fields: list[tuple[str, str, int, int, int]] = []
    offset = 32
    field_pos = 1
    while offset + 32 <= len(data) and data[offset] != 13:
        name = data[offset : offset + 11].split(b"\0", 1)[0].decode(encoding, errors="replace")
        field_type = chr(data[offset + 11])
        length = int(data[offset + 16])
        decimals = int(data[offset + 17])
        fields.append((name, field_type, length, decimals, field_pos))
        field_pos += length
        offset += 32

    records: list[dict[str, object] | None] = []
    for index in range(record_count):
        start = header_length + index * record_length
        end = start + record_length
        if end > len(data):
            break
        raw_record = data[start:end]
        if raw_record[:1] == b"*":
            records.append(None)
            continue
        record: dict[str, object] = {}
        for name, field_type, length, decimals, field_pos in fields:
            raw_value = raw_record[field_pos : field_pos + length]
            record[name] = _parse_dbf_value(raw_value, field_type, decimals, encoding)
        records.append(record)
    return records


def _shape_parts(points: list[tuple[float, float]], parts: tuple[int, ...]) -> list[list[tuple[float, float]]]:
    if not parts:
        return [points]
    starts = list(parts)
    ends = starts[1:] + [len(points)]
    return [points[start:end] for start, end in zip(starts, ends) if end > start]


def _parse_shp_geometries(path: Path) -> list[object | None]:
    data = path.read_bytes()
    if len(data) < 100:
        return []
    geometries: list[object | None] = []
    offset = 100
    while offset + 8 <= len(data):
        content_words = struct.unpack(">i", data[offset + 4 : offset + 8])[0]
        content_start = offset + 8
        content_end = content_start + content_words * 2
        if content_end > len(data) or content_start + 4 > len(data):
            break
        shape_type = struct.unpack("<i", data[content_start : content_start + 4])[0]
        body = data[content_start:content_end]
        geom = None
        try:
            if shape_type == 0:
                geom = None
            elif shape_type in {1, 11, 21} and len(body) >= 20:
                x, y = struct.unpack("<2d", body[4:20])
                geom = Point(x, y)
            elif shape_type in {3, 5, 13, 15, 23, 25} and len(body) >= 44:
                num_parts = struct.unpack("<i", body[36:40])[0]
                num_points = struct.unpack("<i", body[40:44])[0]
                parts_start = 44
                points_start = parts_start + num_parts * 4
                points_end = points_start + num_points * 16
                if points_end <= len(body):
                    parts = struct.unpack(f"<{num_parts}i", body[parts_start:points_start]) if num_parts else ()
                    point_values = struct.unpack(f"<{num_points * 2}d", body[points_start:points_end])
                    points = list(zip(point_values[0::2], point_values[1::2]))
                    split_parts = _shape_parts(points, parts)
                    if shape_type in {3, 13, 23}:
                        lines = [LineString(part) for part in split_parts if len(part) >= 2]
                        if len(lines) == 1:
                            geom = lines[0]
                        elif lines:
                            geom = MultiLineString(lines)
                    else:
                        polygons = [Polygon(part) for part in split_parts if len(part) >= 3]
                        if len(polygons) == 1:
                            geom = polygons[0]
                        elif polygons:
                            geom = MultiPolygon(polygons)
            elif shape_type in {8, 18, 28} and len(body) >= 40:
                num_points = struct.unpack("<i", body[36:40])[0]
                points_start = 40
                points_end = points_start + num_points * 16
                if points_end <= len(body):
                    point_values = struct.unpack(f"<{num_points * 2}d", body[points_start:points_end])
                    geom = MultiPoint(list(zip(point_values[0::2], point_values[1::2])))
        except Exception:
            geom = None
        geometries.append(geom)
        offset = content_end
    return geometries


def _read_shapefile_fallback(path: Path) -> gpd.GeoDataFrame:
    geometries = _parse_shp_geometries(path)
    records = _read_dbf_records(path)
    rows: list[dict[str, object]] = []
    for index, geom in enumerate(geometries):
        record = records[index] if index < len(records) else {}
        if record is None:
            continue
        row = dict(record)
        row["geometry"] = geom
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry")


def load_layer(path: Path | None) -> gpd.GeoDataFrame:
    if path is None or not path.exists():
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)

    used_fallback_reader = False
    try:
        gdf = gpd.read_file(path)
    except Exception as exc:
        if path.suffix.lower() != ".shp":
            raise
        print(f"[geodata] Falling back to pure-Python shapefile reader for {path}: {exc}", flush=True)
        gdf = _read_shapefile_fallback(path)
        used_fallback_reader = True
    if gdf.empty:
        return gdf
    if gdf.crs is None:
        bounds = gdf.total_bounds
        max_abs_coord = max(abs(float(value)) for value in bounds if np.isfinite(value))
        inferred_crs = SOURCE_PROJECTED_CRS if max_abs_coord > 1000.0 else "EPSG:4326"
        if not (used_fallback_reader and inferred_crs == TARGET_CRS):
            gdf = gdf.set_crs(inferred_crs)
    if gdf.crs is not None and str(gdf.crs).upper() != TARGET_CRS:
        gdf = gdf.to_crs(TARGET_CRS)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf = gdf[gdf.geometry.is_valid].copy()
    return gdf


def combine_layers(*layers: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    non_empty = [layer for layer in layers if layer is not None and not layer.empty]
    if not non_empty:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    return gpd.GeoDataFrame(pd.concat(non_empty, ignore_index=True), crs=TARGET_CRS)


def localize(gdf: gpd.GeoDataFrame, origin: tuple[float, float]) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf
    result = gdf.copy()
    result["geometry"] = result.geometry.apply(
        lambda geom: None
        if geom is None or geom.is_empty
        else shapely.affinity.translate(geom, xoff=-origin[0], yoff=-origin[1])
    )
    result = result[result.geometry.notna()].copy()
    return result


def compute_origin(*layers: gpd.GeoDataFrame) -> tuple[float, float]:
    bounds = [layer.total_bounds for layer in layers if not layer.empty]
    if not bounds:
        return (0.0, 0.0)
    stacked = np.array(bounds)
    minx = float(stacked[:, 0].min())
    miny = float(stacked[:, 1].min())
    maxx = float(stacked[:, 2].max())
    maxy = float(stacked[:, 3].max())
    return ((minx + maxx) / 2.0, (miny + maxy) / 2.0)


def localized_bounds(*layers: gpd.GeoDataFrame) -> tuple[float, float, float, float] | None:
    bounds = [layer.total_bounds for layer in layers if not layer.empty]
    if not bounds:
        return None
    stacked = np.array(bounds)
    return (
        float(stacked[:, 0].min()),
        float(stacked[:, 1].min()),
        float(stacked[:, 2].max()),
        float(stacked[:, 3].max()),
    )


def build_gis_basemap_meshes(*layers: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    bounds = localized_bounds(*layers)
    center = None
    if bounds is not None:
        center = road_gen.projected_xy_to_latlon((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)
    return road_gen.make_gis_basemap_meshes(
        bounds,
        z=-0.08,
        padding=80.0,
        grid_spacing=100.0,
        google_center_latlon=center,
    )
