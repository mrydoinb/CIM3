#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Small geometry and mesh helpers used by the city pipeline."""

from __future__ import annotations

from typing import Any, Iterable
import math

import geopandas as gpd
import numpy as np
import pandas as pd
import trimesh
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon

from road import generator as road_gen


def safe_float(value: Any, default: float) -> float:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip().lower().replace("m", "")
    try:
        return float(text)
    except ValueError:
        return default


def iter_lines(geom) -> Iterable[LineString]:
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, LineString):
        yield geom
    elif isinstance(geom, MultiLineString):
        for line in geom.geoms:
            if not line.is_empty:
                yield line
    elif isinstance(geom, GeometryCollection):
        for part in geom.geoms:
            yield from iter_lines(part)


def iter_polygons(geom) -> Iterable[Polygon]:
    yield from road_gen.iter_polygons(geom)


def representative_point(geom) -> Point | None:
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Point):
        return geom
    if isinstance(geom, MultiPoint):
        return next(iter(geom.geoms), None)
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom.representative_point()
    if isinstance(geom, (LineString, MultiLineString)):
        return geom.interpolate(0.5, normalized=True)
    return None


def make_box(name: str, center: tuple[float, float, float], size: tuple[float, float, float], color) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=size)
    mesh.apply_translation(center)
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def cylinder_between(name: str, start, end, radius: float, color, sections: int = 16) -> trimesh.Trimesh | None:
    p0 = np.array(start, dtype=float)
    p1 = np.array(end, dtype=float)
    if np.linalg.norm(p1 - p0) <= 0.05:
        return None
    mesh = trimesh.creation.cylinder(radius=radius, sections=sections, segment=np.vstack([p0, p1]))
    mesh.metadata["name"] = name
    mesh.visual.face_colors = color
    return mesh


def offset_segment(a, b, offset: float) -> tuple[tuple[float, float], tuple[float, float]]:
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    dx = bx - ax
    dy = by - ay
    length = math.hypot(dx, dy)
    if length <= 0.05:
        return (ax, ay), (bx, by)
    nx = -dy / length
    ny = dx / length
    return (ax + nx * offset, ay + ny * offset), (bx + nx * offset, by + ny * offset)


def json_safe_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def scene_from_meshes(meshes: dict[str, trimesh.Trimesh]) -> trimesh.Scene:
    scene = trimesh.Scene()
    for name, mesh in meshes.items():
        scene.add_geometry(mesh.copy(), node_name=name, geom_name=name)
    return scene


def combine_mesh_list(name: str, meshes: list[trimesh.Trimesh], color: list[int] | None = None) -> trimesh.Trimesh | None:
    valid = [mesh for mesh in meshes if mesh is not None and len(mesh.vertices) > 0]
    if not valid:
        return None
    combined = trimesh.util.concatenate(valid)
    combined.metadata["name"] = name
    combined.metadata["part_count"] = len(valid)
    if color is not None:
        combined.visual.face_colors = color
    return combined
