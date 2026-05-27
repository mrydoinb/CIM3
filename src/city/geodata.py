#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Geodata loading, localization, and basemap helpers for the city pipeline."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
import trimesh

from road import generator as road_gen


TARGET_CRS = road_gen.TARGET_CRS
SOURCE_PROJECTED_CRS = "EPSG:4547"


def load_layer(path: Path | None) -> gpd.GeoDataFrame:
    if path is None or not path.exists():
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)

    gdf = gpd.read_file(path)
    if gdf.empty:
        return gdf
    if gdf.crs is None:
        bounds = gdf.total_bounds
        max_abs_coord = max(abs(float(value)) for value in bounds if np.isfinite(value))
        gdf = gdf.set_crs(SOURCE_PROJECTED_CRS if max_abs_coord > 1000.0 else "EPSG:4326")
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
