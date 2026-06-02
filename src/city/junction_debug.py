#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Export lightweight, independently inspectable junction debug models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
from shapely.geometry import Point
import trimesh

from road import generator as road_gen

from city.mesh_utils import combine_mesh_list, json_safe_value, scene_from_meshes


ROOT = Path(__file__).resolve().parents[2]
MODULE_OBJ_DIR = ROOT / "output" / "obj" / "modules"
CITY_JUNCTION_DEBUG_OBJ_DIR = ROOT / "output" / "obj" / "junctions"
CITY_JUNCTION_DEBUG_BUNDLE_OBJ_PATH = MODULE_OBJ_DIR / "cim_city_junctions_debug.obj"
CITY_JUNCTION_DEBUG_MANIFEST_PATH = ROOT / "output" / "semantic" / "cim_city_junctions_debug_manifest.json"
JUNCTION_DEBUG_CONTEXT_RADIUS_M = 72.0


def crop_mesh_to_junction_window(
    mesh: trimesh.Trimesh,
    point: Point,
    radius_m: float,
) -> trimesh.Trimesh | None:
    """Keep finalized mesh faces whose XY bounds intersect a junction window."""
    if mesh is None or len(mesh.vertices) == 0 or len(mesh.faces) == 0:
        return None
    radius = max(float(radius_m), 1.0)
    min_x = float(point.x) - radius
    max_x = float(point.x) + radius
    min_y = float(point.y) - radius
    max_y = float(point.y) + radius
    bounds = mesh.bounds
    if (
        float(bounds[1][0]) < min_x
        or float(bounds[0][0]) > max_x
        or float(bounds[1][1]) < min_y
        or float(bounds[0][1]) > max_y
    ):
        return None
    vertices_xy = np.asarray(mesh.vertices, dtype=float)[:, :2]
    faces = np.asarray(mesh.faces, dtype=int)
    face_xy = vertices_xy[faces]
    keep = (
        (face_xy[:, :, 0].max(axis=1) >= min_x)
        & (face_xy[:, :, 0].min(axis=1) <= max_x)
        & (face_xy[:, :, 1].max(axis=1) >= min_y)
        & (face_xy[:, :, 1].min(axis=1) <= max_y)
    )
    face_indices = np.flatnonzero(keep)
    if len(face_indices) == 0:
        return None
    cropped = mesh.submesh([face_indices], append=True, repair=False)
    if cropped is None or len(cropped.vertices) == 0:
        return None
    cropped.metadata.update(dict(mesh.metadata or {}))
    return cropped


def connected_roads(
    prepared_roads: gpd.GeoDataFrame,
    surface: dict[str, Any],
) -> list[dict[str, Any]]:
    records = []
    road_indices = sorted(
        {road_idx for road_idx, _ in surface.get("members", [])},
        key=lambda value: str(value),
    )
    for road_idx in road_indices:
        if road_idx not in prepared_roads.index:
            continue
        row = prepared_roads.loc[road_idx]
        records.append(
            {
                "road_idx": json_safe_value(road_idx),
                "road_id": road_gen.safe_str(row.get("road_id")) or "",
                "road_name": road_gen.safe_str(row.get("road_name")) or "",
                "road_class": road_gen.safe_str(row.get("road_class")) or "",
                "road_category": road_gen.road_asset_category(row),
            }
        )
    return records


def write_junction_debug_models(
    prepared_roads: gpd.GeoDataFrame,
    surface_geometries: list[dict[str, Any]],
    source_mesh_groups: dict[str, list[trimesh.Trimesh]],
) -> dict[str, Any]:
    """Export one finalized-geometry OBJ per topology bucket plus a bundle."""
    CITY_JUNCTION_DEBUG_OBJ_DIR.mkdir(parents=True, exist_ok=True)
    MODULE_OBJ_DIR.mkdir(parents=True, exist_ok=True)
    for pattern in ("J*.obj", "J*.mtl"):
        for old_path in CITY_JUNCTION_DEBUG_OBJ_DIR.glob(pattern):
            old_path.unlink()

    source_parts = [
        (layer_name, mesh)
        for layer_name, meshes in source_mesh_groups.items()
        for mesh in meshes
        if mesh is not None and len(mesh.vertices) > 0 and len(mesh.faces) > 0
    ]
    bundle_meshes: dict[str, trimesh.Trimesh] = {}
    records = []
    for surface in surface_geometries:
        point = surface.get("point")
        if point is None or point.is_empty:
            continue
        junction_idx = int(surface["index"])
        junction_id = f"J{junction_idx:04d}"
        model_meshes: dict[str, list[trimesh.Trimesh]] = {}
        for layer_name, mesh in source_parts:
            cropped = crop_mesh_to_junction_window(mesh, point, JUNCTION_DEBUG_CONTEXT_RADIUS_M)
            if cropped is not None:
                model_meshes.setdefault(layer_name, []).append(cropped)

        exported_meshes = {}
        for layer_name, meshes in sorted(model_meshes.items()):
            name = f"{layer_name}_{junction_id}"
            combined = combine_mesh_list(name, meshes)
            if combined is None:
                continue
            combined.metadata.update(
                {
                    "name": name,
                    "junction_debug_model": True,
                    "junction_id": junction_id,
                    "junction_index": junction_idx,
                    "layer_name": layer_name,
                }
            )
            exported_meshes[name] = combined
            bundle_meshes[name] = combined
        if not exported_meshes:
            continue

        obj_path = CITY_JUNCTION_DEBUG_OBJ_DIR / f"{junction_id}.obj"
        scene_from_meshes(exported_meshes).export(obj_path)
        records.append(
            {
                "junction_id": junction_id,
                "junction_index": junction_idx,
                "junction_type": surface.get("junction_type", "UNKNOWN"),
                "junction_hierarchy": surface.get("junction_hierarchy", "LOCAL_JUNCTION"),
                "center_local": {
                    "x": round(float(point.x), 3),
                    "y": round(float(point.y), 3),
                    "z": 0.0,
                },
                "context_radius_m": float(JUNCTION_DEBUG_CONTEXT_RADIUS_M),
                "connected_roads": connected_roads(prepared_roads, surface),
                "layer_names": sorted(model_meshes),
                "mesh_count": len(exported_meshes),
                "obj_path": obj_path.relative_to(ROOT).as_posix(),
            }
        )

    if bundle_meshes:
        scene_from_meshes(bundle_meshes).export(CITY_JUNCTION_DEBUG_BUNDLE_OBJ_PATH)
    elif CITY_JUNCTION_DEBUG_BUNDLE_OBJ_PATH.exists():
        CITY_JUNCTION_DEBUG_BUNDLE_OBJ_PATH.unlink()

    manifest = {
        "project": "cim_road_poc",
        "model": "cim_city_junctions_debug",
        "policy": "one independently inspectable finalized-geometry model per junction topology bucket",
        "bundle_obj_path": CITY_JUNCTION_DEBUG_BUNDLE_OBJ_PATH.relative_to(ROOT).as_posix(),
        "junction_obj_dir": CITY_JUNCTION_DEBUG_OBJ_DIR.relative_to(ROOT).as_posix(),
        "summary": {
            "junction_model_count": len(records),
            "bundle_mesh_count": len(bundle_meshes),
            "context_radius_m": float(JUNCTION_DEBUG_CONTEXT_RADIUS_M),
        },
        "objects": records,
    }
    CITY_JUNCTION_DEBUG_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CITY_JUNCTION_DEBUG_MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest
