#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Export the generated CIM city OBJ to FBX with simple, FBX-friendly materials.

Input:
- output/obj/cim_city.obj

Output:
- output/fbx/cim_city.fbx
- output/fbx/modules/cim_city_basemap.fbx
- output/fbx/modules/cim_city_roads.fbx
- output/fbx/modules/cim_city_buildings.fbx
- output/fbx/modules/cim_city_subway_tunnels.fbx
- output/fbx/modules/cim_city_subway_stations.fbx
- output/fbx/modules/cim_city_bus_stops.fbx
- output/fbx/modules/cim_city_utility_pipes.fbx
"""

from __future__ import annotations
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OBJ_PATH = ROOT / "output" / "obj" / "cim_city.obj"
FBX_PATH = ROOT / "output" / "fbx" / "cim_city.fbx"
MODULE_OBJ_DIR = ROOT / "output" / "obj" / "modules"
MODULE_FBX_DIR = ROOT / "output" / "fbx" / "modules"
GOOGLE_MAP_TEXTURE_PATH = ROOT / "output" / "textures" / "google_static_map.png"
MODULE_EXPORTS = {
    "basemap": (
        MODULE_OBJ_DIR / "cim_city_basemap.obj",
        MODULE_FBX_DIR / "cim_city_basemap.fbx",
    ),
    "roads": (
        MODULE_OBJ_DIR / "cim_city_roads.obj",
        MODULE_FBX_DIR / "cim_city_roads.fbx",
    ),
    "buildings": (
        MODULE_OBJ_DIR / "cim_city_buildings.obj",
        MODULE_FBX_DIR / "cim_city_buildings.fbx",
    ),
    "subway_tunnels": (
        MODULE_OBJ_DIR / "cim_city_subway_tunnels.obj",
        MODULE_FBX_DIR / "cim_city_subway_tunnels.fbx",
    ),
    "subway_stations": (
        MODULE_OBJ_DIR / "cim_city_subway_stations.obj",
        MODULE_FBX_DIR / "cim_city_subway_stations.fbx",
    ),
    "bus_stops": (
        MODULE_OBJ_DIR / "cim_city_bus_stops.obj",
        MODULE_FBX_DIR / "cim_city_bus_stops.fbx",
    ),
    "utility_pipes": (
        MODULE_OBJ_DIR / "cim_city_utility_pipes.obj",
        MODULE_FBX_DIR / "cim_city_utility_pipes.fbx",
    ),
}

MATERIALS = {
    "GIS_GoogleMap": ("CIM_Google_Map", (1.0, 1.0, 1.0, 1.0), 0.9),
    "GIS_BaseMap": ("CIM_GIS_BaseMap", (0.22, 0.32, 0.24, 1.0), 0.9),
    "GIS_Grid": ("CIM_GIS_Grid", (0.42, 0.48, 0.42, 1.0), 0.82),
    "Road_Surface": ("CIM_Road_Asphalt", (0.03, 0.03, 0.03, 1.0), 0.78),
    "Lane_Surface": ("CIM_Lane_Asphalt", (0.04, 0.04, 0.038, 1.0), 0.74),
    "Sidewalk": ("CIM_Sidewalk_Concrete", (0.46, 0.46, 0.43, 1.0), 0.72),
    "Curb": ("CIM_Curb_Light_Concrete", (0.74, 0.72, 0.66, 1.0), 0.7),
    "Lane_Marking": ("CIM_Lane_Marking_White", (0.95, 0.92, 0.78, 1.0), 0.35),
    "Stop_Line": ("CIM_Stop_Line_White", (0.95, 0.92, 0.78, 1.0), 0.35),
    "Crosswalk": ("CIM_Crosswalk_White", (0.95, 0.92, 0.78, 1.0), 0.35),
    "Lane_Guide": ("CIM_Lane_Guide_White", (0.95, 0.92, 0.78, 1.0), 0.35),
    "Turn_Arrow": ("CIM_Turn_Arrow_White", (0.95, 0.92, 0.78, 1.0), 0.35),
    "Channelization_Island": ("CIM_Channelization_Island", (0.72, 0.70, 0.62, 1.0), 0.62),
    "Junction_Surface": ("CIM_Road_Asphalt", (0.03, 0.03, 0.03, 1.0), 0.78),
    "Median": ("CIM_Median_Concrete", (0.38, 0.42, 0.34, 1.0), 0.68),
    "Guardrail": ("CIM_Guardrail_Galvanized", (0.68, 0.70, 0.70, 1.0), 0.36),
    "Street_Light": ("CIM_Street_Light_Painted_Metal", (0.34, 0.33, 0.30, 1.0), 0.42),
    "Tree": ("CIM_Tree_Canopy", (0.18, 0.42, 0.20, 1.0), 0.72),
    "Bridge": ("CIM_Bridge_Concrete", (0.48, 0.48, 0.44, 1.0), 0.76),
    "Bridge_Deck": ("CIM_Bridge_Deck_Concrete", (0.44, 0.44, 0.40, 1.0), 0.76),
    "Bridge_Pier": ("CIM_Bridge_Pier_Concrete", (0.56, 0.56, 0.52, 1.0), 0.78),
    "Building": ("CIM_Building_Concrete", (0.72, 0.71, 0.68, 1.0), 0.72),
    "Subway_Tunnel": ("CIM_Subway_Tunnel_Dark_Concrete", (0.26, 0.26, 0.30, 1.0), 0.85),
    "Subway_Station": ("CIM_Subway_Station_Blue", (0.12, 0.22, 0.48, 1.0), 0.58),
    "Bus_Stop": ("CIM_Bus_Stop_Green", (0.05, 0.55, 0.28, 1.0), 0.42),
    "Utility_Water": ("CIM_Utility_Water_Blue", (0.02, 0.34, 0.90, 1.0), 0.36),
    "Utility_Sewer": ("CIM_Utility_Sewer_Brown", (0.36, 0.23, 0.12, 1.0), 0.66),
    "Utility_Power": ("CIM_Utility_Power_Yellow", (0.94, 0.68, 0.06, 1.0), 0.28),
    "Utility_Telecom": ("CIM_Utility_Telecom_Magenta", (0.88, 0.12, 0.64, 1.0), 0.34),
}

DEFAULT_MATERIAL = ("CIM_Default", (0.58, 0.58, 0.58, 1.0), 0.6)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def import_obj(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing OBJ input: {path}")

    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        bpy.ops.import_scene.obj(filepath=str(path))


def create_material(name: str, color: tuple[float, float, float, float], roughness: float) -> bpy.types.Material:
    material = bpy.data.materials.get(name)
    if material is not None:
        return material

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = color

    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = 0.0

    return material


def ensure_planar_uv(obj: bpy.types.Object) -> None:
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="GIS_UV")
    uv_layer = mesh.uv_layers.active
    if not mesh.vertices:
        return
    xs = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
    minx = min(co.x for co in xs)
    miny = min(co.y for co in xs)
    maxx = max(co.x for co in xs)
    maxy = max(co.y for co in xs)
    width = max(maxx - minx, 1e-6)
    height = max(maxy - miny, 1e-6)
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            co = obj.matrix_world @ mesh.vertices[vertex_index].co
            uv_layer.data[loop_index].uv = ((co.x - minx) / width, (co.y - miny) / height)


def create_google_map_material() -> bpy.types.Material:
    material = create_material("CIM_Google_Map", (1.0, 1.0, 1.0, 1.0), 0.9)
    if not GOOGLE_MAP_TEXTURE_PATH.exists():
        return material
    nodes = material.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    image_node = nodes.get("Google_Static_Map_Texture")
    if image_node is None:
        image_node = nodes.new(type="ShaderNodeTexImage")
        image_node.name = "Google_Static_Map_Texture"
    image_node.image = bpy.data.images.load(str(GOOGLE_MAP_TEXTURE_PATH), check_existing=True)
    if bsdf is not None:
        material.node_tree.links.new(image_node.outputs["Color"], bsdf.inputs["Base Color"])
    return material


def material_for_object(obj_name: str) -> bpy.types.Material:
    if obj_name.startswith("GIS_GoogleMap"):
        return create_google_map_material()
    if obj_name.startswith("Junction_Label"):
        if "MAJOR_ARTERIAL" in obj_name:
            return create_material("CIM_Junction_Label_Major", (0.86, 0.12, 0.08, 1.0), 0.45)
        if "SECONDARY_COLLECTOR" in obj_name:
            return create_material("CIM_Junction_Label_Secondary", (0.95, 0.58, 0.08, 1.0), 0.45)
        if "RAMP_OR_GRADE_SEPARATED" in obj_name:
            return create_material("CIM_Junction_Label_Ramp", (0.20, 0.36, 0.90, 1.0), 0.45)
        if "COMPLEX_MULTI_ARM" in obj_name or "ROUNDABOUT_JUNCTION" in obj_name:
            return create_material("CIM_Junction_Label_Complex", (0.55, 0.18, 0.75, 1.0), 0.45)
        return create_material("CIM_Junction_Label_Local", (0.12, 0.55, 0.22, 1.0), 0.45)
    for prefix, material_spec in MATERIALS.items():
        if obj_name.startswith(prefix):
            return create_material(*material_spec)
    return create_material(*DEFAULT_MATERIAL)


def apply_city_materials() -> None:
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        if obj.name.startswith("GIS_GoogleMap"):
            ensure_planar_uv(obj)
        obj.data.materials.clear()
        obj.data.materials.append(material_for_object(obj.name))


def export_fbx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=False,
        apply_unit_scale=True,
        bake_space_transform=False,
        object_types={"MESH"},
        mesh_smooth_type="FACE",
        add_leaf_bones=False,
        path_mode="COPY",
        embed_textures=False,
    )


def export_obj_to_fbx(obj_path: Path, fbx_path: Path) -> bool:
    if not obj_path.exists():
        print(f"[SKIP] Missing OBJ input: {obj_path}")
        return False

    clear_scene()
    import_obj(obj_path)
    apply_city_materials()

    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0

    export_fbx(fbx_path)
    print(f"FBX exported: {fbx_path}")
    return True


def main() -> None:
    export_obj_to_fbx(OBJ_PATH, FBX_PATH)

    for _, (obj_path, fbx_path) in MODULE_EXPORTS.items():
        export_obj_to_fbx(obj_path, fbx_path)


if __name__ == "__main__":
    main()
