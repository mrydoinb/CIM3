#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Generate a CIM city massing model from OSM-derived inputs.

Inputs:
- data/raw/road_centerline.geojson
- data/raw/building_footprint.geojson
- data/raw/transport_points.geojson
- data/raw/railway_centerline.geojson

Output:
- output/obj/cim_city.obj
- output/obj/modules/cim_city_roads.obj
- output/obj/modules/cim_city_buildings.obj
- output/obj/modules/cim_city_subway_tunnels.obj
- output/obj/modules/cim_city_subway_stations.obj
- output/obj/modules/cim_city_bus_stops.obj
- output/obj/modules/cim_city_utility_pipes.obj
"""

from __future__ import annotations

from pathlib import Path
import math
import sys
from typing import Iterable, Any
import importlib.util

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon, MultiPolygon, GeometryCollection
import trimesh

ROOT = Path(__file__).resolve().parents[1]
ROAD_GEN_PATH = ROOT / "scripts" / "01_generate_cim3_road.py"

spec = importlib.util.spec_from_file_location("cim3_road_generator", ROAD_GEN_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load road generator: {ROAD_GEN_PATH}")
road_gen = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = road_gen
spec.loader.exec_module(road_gen)

RAW_DIR = ROOT / "data" / "raw"
OUT_PATH = ROOT / "output" / "obj" / "cim_city.obj"
MODULE_OBJ_DIR = ROOT / "output" / "obj" / "modules"
MODULE_OBJ_PATHS = {
    "basemap": MODULE_OBJ_DIR / "cim_city_basemap.obj",
    "roads": MODULE_OBJ_DIR / "cim_city_roads.obj",
    "buildings": MODULE_OBJ_DIR / "cim_city_buildings.obj",
    "subway_tunnels": MODULE_OBJ_DIR / "cim_city_subway_tunnels.obj",
    "subway_stations": MODULE_OBJ_DIR / "cim_city_subway_stations.obj",
    "bus_stops": MODULE_OBJ_DIR / "cim_city_bus_stops.obj",
    "utility_pipes": MODULE_OBJ_DIR / "cim_city_utility_pipes.obj",
}
TARGET_CRS = road_gen.TARGET_CRS

BUILDING_DEFAULT_HEIGHT_M = 12.0
BUILDING_LEVEL_HEIGHT_M = 3.2
SUBWAY_TUNNEL_RADIUS_M = 2.6
SUBWAY_TUNNEL_DEPTH_M = -14.0
SUBWAY_STATION_DEPTH_M = -11.0
SUBWAY_STATION_SIZE_M = (34.0, 16.0, 7.0)

UTILITY_TYPES = [
    ("Water", -2.0, -1.4, 0.22, [40, 120, 230, 255]),
    ("Sewer", -3.2, 0.0, 0.32, [120, 90, 55, 255]),
    ("Power", -1.5, 1.4, 0.16, [230, 190, 40, 255]),
    ("Telecom", -1.1, 2.2, 0.10, [230, 80, 180, 255]),
]

COLORS = {
    "gis_basemap": [88, 118, 94, 255],
    "gis_grid": [132, 150, 132, 255],
    "road_surface": [45, 45, 45, 255],
    "sidewalk": [135, 135, 130, 255],
    "curb": [190, 185, 170, 255],
    "lane_marking": [245, 245, 220, 255],
    "building": [185, 185, 178, 255],
    "subway_tunnel": [95, 95, 105, 255],
    "subway_station": [70, 95, 150, 255],
    "bus_stop": [40, 170, 95, 255],
}


def safe_float(value: Any, default: float) -> float:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip().lower().replace("m", "")
    try:
        return float(text)
    except ValueError:
        return default


def load_layer(path: Path) -> gpd.GeoDataFrame:
    if not path.exists():
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)

    gdf = gpd.read_file(path)
    if gdf.empty:
        return gdf
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf.to_crs(TARGET_CRS)


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


def localize(gdf: gpd.GeoDataFrame, origin: tuple[float, float]) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf
    result = gdf.copy()
    result["geometry"] = result.geometry.apply(
        lambda geom: shapely.affinity.translate(geom, xoff=-origin[0], yoff=-origin[1])
    )
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


def building_height(row) -> float:
    height = safe_float(row.get("height"), 0.0)
    if height > 0:
        return height
    levels = safe_float(row.get("building:levels"), 0.0)
    if levels > 0:
        return max(levels * BUILDING_LEVEL_HEIGHT_M, BUILDING_LEVEL_HEIGHT_M)
    return BUILDING_DEFAULT_HEIGHT_M


def prepare_roads_for_surfaces(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if roads.empty:
        return roads

    prepared = roads.copy()
    prepared = prepared[prepared.geometry.notna()].copy()
    prepared = prepared[prepared.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()
    prepared = prepared.explode(index_parts=False).reset_index(drop=True)
    prepared = prepared[prepared.geometry.geom_type == "LineString"].copy()
    prepared = road_gen.deduplicate_bidirectional_osm_edges(prepared)

    if "osmid" in prepared.columns:
        prepared["road_id"] = prepared["osmid"].apply(road_gen.normalize_osmid)
    elif "id" in prepared.columns:
        prepared["road_id"] = prepared["id"].astype(str)
    else:
        prepared["road_id"] = [f"R{i:05d}" for i in range(len(prepared))]

    prepared["road_name"] = prepared["name"].fillna("unknown") if "name" in prepared.columns else "unknown"
    prepared["road_class"] = prepared["highway"] if "highway" in prepared.columns else "unclassified"
    prepared["lane_count"] = prepared["lanes"] if "lanes" in prepared.columns else None
    prepared["lanes_forward"] = prepared["lanes:forward"] if "lanes:forward" in prepared.columns else None
    prepared["lanes_backward"] = prepared["lanes:backward"] if "lanes:backward" in prepared.columns else None
    prepared["osm_width"] = prepared["width"] if "width" in prepared.columns else None
    prepared["maxspeed"] = prepared["maxspeed"] if "maxspeed" in prepared.columns else None
    prepared["oneway"] = prepared["oneway"] if "oneway" in prepared.columns else None
    prepared["junction_type"] = prepared["junction"] if "junction" in prepared.columns else None
    prepared["road_ref"] = prepared["ref"] if "ref" in prepared.columns else None
    prepared["access"] = prepared["access"] if "access" in prepared.columns else None
    prepared["lane_count"] = road_gen.normalize_corridor_lane_counts(prepared)
    prepared["is_bridge"] = prepared["bridge"].apply(road_gen.check_is_bridge) if "bridge" in prepared.columns else False
    prepared["bridge_clearance"] = prepared["road_class"].apply(road_gen.get_bridge_clearance)
    prepared["ground_z_start"] = 0.0
    prepared["ground_z_end"] = 0.0
    prepared["road_z_start"] = prepared["bridge_clearance"].where(prepared["is_bridge"], 0.0)
    prepared["road_z_end"] = prepared["road_z_start"]
    prepared["road_z_mean"] = prepared["road_z_start"]
    prepared["elevation"] = prepared["road_z_mean"]
    prepared["length_m"] = prepared.geometry.length
    prepared["height_mode"] = "city_surface"
    prepared["height_source"] = "osm_layer_rule"
    return prepared


def build_road_surface_meshes(roads: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    if roads.empty:
        return {}

    prepared_roads = prepare_roads_for_surfaces(roads)
    if prepared_roads.empty:
        return {}

    rules = road_gen.load_rules()
    default_rule = rules.get("default_road")
    if default_rule is None:
        raise ValueError("Missing default_road rule in road_rules.json")

    layers = road_gen.generate_planar_geometries(prepared_roads, rules)
    _, road_meshes = road_gen.make_scene(layers, default_rule, include_basemap=False)
    road_meshes.update(road_gen.build_cim3_road_asset_meshes(prepared_roads, rules))
    return {name: mesh.copy() for name, mesh in road_meshes.items()}


def build_building_meshes(buildings: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    meshes = {}
    for idx, row in buildings.iterrows():
        height = building_height(row)
        name = f"Building_{idx}"
        mesh = road_gen.polygon_to_extruded_mesh(
            row.geometry,
            z_bottom=0.0,
            z_top=height,
            name=name,
            visual_color=COLORS["building"],
        )
        if len(mesh.vertices) == 0:
            continue
        meshes[name] = mesh
    return meshes


def subway_like(row) -> bool:
    railway = str(row.get("railway", "")).lower()
    tunnel = str(row.get("tunnel", "")).lower()
    layer = safe_float(row.get("layer"), 0.0)
    return railway == "subway" or tunnel in {"yes", "true"} or layer < 0


def build_subway_tunnel_meshes(railways: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    meshes = {}
    for idx, row in railways.iterrows():
        if not subway_like(row):
            continue
        for line in iter_lines(row.geometry):
            coords = list(line.coords)
            for part_idx, (a, b) in enumerate(zip(coords, coords[1:])):
                mesh = cylinder_between(
                    f"Subway_Tunnel_{idx}_{part_idx}",
                    (a[0], a[1], SUBWAY_TUNNEL_DEPTH_M),
                    (b[0], b[1], SUBWAY_TUNNEL_DEPTH_M),
                    SUBWAY_TUNNEL_RADIUS_M,
                    COLORS["subway_tunnel"],
                    sections=24,
                )
                if mesh is None:
                    continue
                meshes[mesh.metadata["name"]] = mesh
    return meshes


def is_subway_station(row) -> bool:
    values = " ".join(str(row.get(col, "")) for col in ["railway", "station", "subway", "public_transport", "name"])
    values = values.lower()
    return "subway" in values or "u-bahn" in values or "station" in values


def build_transit_node_meshes(
    transport: gpd.GeoDataFrame,
) -> tuple[dict[str, trimesh.Trimesh], dict[str, trimesh.Trimesh]]:
    subway_station_meshes = {}
    bus_stop_meshes = {}
    for idx, row in transport.iterrows():
        point = representative_point(row.geometry)
        if point is None:
            continue
        if is_subway_station(row):
            name = f"Subway_Station_{idx}"
            mesh = make_box(
                name,
                (point.x, point.y, SUBWAY_STATION_DEPTH_M),
                SUBWAY_STATION_SIZE_M,
                COLORS["subway_station"],
            )
            subway_station_meshes[name] = mesh
        elif str(row.get("highway", "")).lower() == "bus_stop" or str(row.get("bus", "")).lower() == "yes":
            name = f"Bus_Stop_{idx}"
            base = make_box(f"{name}_Base", (point.x, point.y, 0.12), (4.2, 1.8, 0.24), COLORS["bus_stop"])
            shelter = make_box(f"{name}_Shelter", (point.x, point.y, 1.45), (3.6, 0.18, 2.3), COLORS["bus_stop"])
            canopy = make_box(f"{name}_Canopy", (point.x, point.y, 2.65), (4.4, 2.0, 0.18), COLORS["bus_stop"])
            mesh = trimesh.util.concatenate([base, shelter, canopy])
            mesh.metadata["name"] = name
            mesh.visual.face_colors = COLORS["bus_stop"]
            bus_stop_meshes[name] = mesh
    return subway_station_meshes, bus_stop_meshes


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


def build_utility_pipe_meshes(roads: gpd.GeoDataFrame) -> dict[str, trimesh.Trimesh]:
    meshes = {}
    for road_idx, row in roads.iterrows():
        for line in iter_lines(row.geometry):
            coords = list(line.coords)
            for seg_idx, (a, b) in enumerate(zip(coords, coords[1:])):
                for pipe_name, z, lateral_offset, radius, color in UTILITY_TYPES:
                    start_xy, end_xy = offset_segment(a, b, lateral_offset)
                    name = f"Utility_{pipe_name}_{road_idx}_{seg_idx}"
                    mesh = cylinder_between(
                        name,
                        (start_xy[0], start_xy[1], z),
                        (end_xy[0], end_xy[1], z),
                        radius,
                        color,
                        sections=12,
                    )
                    if mesh is None:
                        continue
                    meshes[name] = mesh
    return meshes


def scene_from_meshes(meshes: dict[str, trimesh.Trimesh]) -> trimesh.Scene:
    scene = trimesh.Scene()
    for name, mesh in meshes.items():
        scene.add_geometry(mesh.copy(), node_name=name, geom_name=name)
    return scene


def compose_city_scene(modules: dict[str, dict[str, trimesh.Trimesh]]) -> trimesh.Scene:
    scene = trimesh.Scene()
    for meshes in modules.values():
        for name, mesh in meshes.items():
            scene.add_geometry(mesh.copy(), node_name=name, geom_name=name)
    return scene


def export_module_scenes(modules: dict[str, dict[str, trimesh.Trimesh]]) -> dict[str, Path | None]:
    exported = {}
    MODULE_OBJ_DIR.mkdir(parents=True, exist_ok=True)
    for module_name, meshes in modules.items():
        path = MODULE_OBJ_PATHS[module_name]
        if not meshes:
            exported[module_name] = None
            continue
        scene_from_meshes(meshes).export(path)
        exported[module_name] = path
    return exported


def main() -> None:
    roads = load_layer(RAW_DIR / "road_centerline.geojson")
    buildings = load_layer(RAW_DIR / "building_footprint.geojson")
    transport = load_layer(RAW_DIR / "transport_points.geojson")
    railways = load_layer(RAW_DIR / "railway_centerline.geojson")

    origin = compute_origin(roads, buildings, transport, railways)
    roads = localize(roads, origin)
    buildings = localize(buildings, origin)
    transport = localize(transport, origin)
    railways = localize(railways, origin)

    subway_station_meshes, bus_stop_meshes = build_transit_node_meshes(transport)
    modules = {
        "basemap": build_gis_basemap_meshes(roads, buildings, transport, railways),
        "roads": build_road_surface_meshes(roads),
        "buildings": build_building_meshes(buildings),
        "subway_tunnels": build_subway_tunnel_meshes(railways),
        "subway_stations": subway_station_meshes,
        "bus_stops": bus_stop_meshes,
        "utility_pipes": build_utility_pipe_meshes(roads),
    }
    stats = {module_name: len(meshes) for module_name, meshes in modules.items()}

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    scene = compose_city_scene(modules)
    scene.export(OUT_PATH)
    module_outputs = export_module_scenes(modules)

    print("CIM city OBJ generated:")
    print(f"- {OUT_PATH}")
    print("- module OBJ outputs:")
    for module_name, path in module_outputs.items():
        output_text = str(path) if path is not None else "skipped (no geometry)"
        print(f"  - {module_name}: {output_text}")
    for key, value in stats.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
