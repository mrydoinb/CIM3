#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Export the generated CIM city OBJ to FBX with simple, FBX-friendly materials.

Input:
- output/obj/cim_city.obj

Output:
- output/fbx/cim_city.fbx
- output/fbx/modules/cim_city_roads.fbx
- output/fbx/modules/cim_city_buildings.fbx
- output/fbx/modules/cim_city_subway_tunnels.fbx
- output/fbx/modules/cim_city_subway_stations.fbx
- output/fbx/modules/cim_city_bus_stops.fbx
- output/fbx/modules/cim_city_utility_pipes.fbx
"""

from __future__ import annotations
import json
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[2]
OBJ_PATH = ROOT / "output" / "obj" / "cim_city.obj"
FBX_PATH = ROOT / "output" / "fbx" / "cim_city.fbx"
MODULE_OBJ_DIR = ROOT / "output" / "obj" / "modules"
MODULE_FBX_DIR = ROOT / "output" / "fbx" / "modules"
SEMANTIC_DIR = ROOT / "output" / "semantic"
JUNCTION_DEBUG_OBJ_DIR = ROOT / "output" / "obj" / "junctions"
JUNCTION_DEBUG_FBX_DIR = ROOT / "output" / "fbx" / "junctions"
ROAD_MESH_ATTRIBUTES_PATH = SEMANTIC_DIR / "cim_city_roads_mesh_attributes.json"
JUNCTION_DEBUG_MANIFEST_PATH = SEMANTIC_DIR / "cim_city_junctions_debug_manifest.json"
LEGACY_JUNCTION_DEBUG_BUNDLE_OBJ_PATH = MODULE_OBJ_DIR / "cim_city_junctions_debug.obj"
LEGACY_JUNCTION_DEBUG_BUNDLE_FBX_PATH = MODULE_FBX_DIR / "cim_city_junctions_debug.fbx"
GOOGLE_MAP_TEXTURE_PATH = ROOT / "output" / "textures" / "google_static_map.png"
WORLD_IMAGERY_TEXTURE_PATH = ROOT / "output" / "textures" / "world_imagery_basemap.png"
MODULE_EXPORTS = {
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
    "GIS_WorldImagery": ("CIM_World_Imagery", (1.0, 1.0, 1.0, 1.0), 0.9),
    "GIS_BaseMap": ("CIM_GIS_BaseMap", (0.22, 0.32, 0.24, 1.0), 0.9),
    "GIS_Grid": ("CIM_GIS_Grid", (0.42, 0.48, 0.42, 1.0), 0.82),
    "Road_Surface_Main": ("CIM_Road_Asphalt_Fine", (0.045, 0.047, 0.045, 1.0), 0.84),
    "Road_Surface_Service": ("CIM_Road_Asphalt_Fine", (0.045, 0.047, 0.045, 1.0), 0.84),
    "Road_Surface_Branch": ("CIM_Road_Asphalt_Fine", (0.045, 0.047, 0.045, 1.0), 0.84),
    "Road_Surface": ("CIM_Road_Asphalt_Fine", (0.045, 0.047, 0.045, 1.0), 0.84),
    "Lane_Surface": ("CIM_Lane_Asphalt_Fine", (0.052, 0.052, 0.049, 1.0), 0.80),
    "Non_Motor_Lane": ("CIM_Non_Motor_Lane_Asphalt", (0.12, 0.18, 0.16, 1.0), 0.82),
    "Parking_Lane": ("CIM_Parking_Lane_Asphalt", (0.19, 0.20, 0.19, 1.0), 0.78),
    "Sidewalk": ("CIM_Sidewalk_Warm_Concrete", (0.64, 0.62, 0.56, 1.0), 0.72),
    "Green_Belt": ("CIM_Roadside_Green_Belt", (0.16, 0.40, 0.20, 1.0), 0.86),
    "Facility_Belt": ("CIM_Facility_Belt_Stone_Green", (0.34, 0.39, 0.31, 1.0), 0.76),
    "Side_Divider": ("CIM_Side_Divider_Low_Green", (0.18, 0.34, 0.17, 1.0), 0.82),
    "Curb": ("CIM_Curb_Light_Concrete", (0.76, 0.74, 0.68, 1.0), 0.68),
    "Lane_Marking_White": ("CIM_Lane_Marking_White", (0.95, 0.94, 0.86, 1.0), 0.32),
    "Lane_Marking_Yellow": ("CIM_Lane_Marking_Yellow", (0.96, 0.76, 0.10, 1.0), 0.35),
    "Lane_Marking": ("CIM_Lane_Marking_White", (0.95, 0.94, 0.86, 1.0), 0.32),
    "Stop_Line": ("CIM_Stop_Line_White", (0.95, 0.92, 0.78, 1.0), 0.35),
    "Crosswalk": ("CIM_Crosswalk_White", (0.95, 0.92, 0.78, 1.0), 0.35),
    "Lane_Guide": ("CIM_Lane_Guide_White", (0.95, 0.92, 0.78, 1.0), 0.35),
    "Turn_Arrow": ("CIM_Turn_Arrow_White", (0.95, 0.92, 0.78, 1.0), 0.35),
    "Channelization_Island": ("CIM_Channelization_Island", (0.72, 0.70, 0.62, 1.0), 0.62),
    "Junction_Surface": ("CIM_Road_Asphalt_Fine", (0.045, 0.047, 0.045, 1.0), 0.84),
    "Median": ("CIM_Median_Concrete", (0.38, 0.42, 0.34, 1.0), 0.68),
    "Guardrail": ("CIM_Guardrail_Galvanized", (0.68, 0.70, 0.70, 1.0), 0.36),
    "Street_Light_Lamp": ("CIM_Street_Light_Warm_Lamp", (1.0, 0.78, 0.34, 1.0), 0.18),
    "Street_Light": ("CIM_Street_Light_Painted_Metal", (0.30, 0.30, 0.28, 1.0), 0.46),
    "Tree_Trunk": ("CIM_Tree_Bark", (0.34, 0.22, 0.13, 1.0), 0.86),
    "Tree_Crown": ("CIM_Tree_Canopy_Varied", (0.18, 0.38, 0.20, 1.0), 0.88),
    "Tree": ("CIM_Tree_Canopy_Varied", (0.18, 0.38, 0.20, 1.0), 0.88),
    "Bridge": ("CIM_Bridge_Concrete", (0.48, 0.48, 0.44, 1.0), 0.76),
    "Bridge_Deck": ("CIM_Bridge_Deck_Concrete", (0.44, 0.44, 0.40, 1.0), 0.76),
    "Bridge_Pier": ("CIM_Bridge_Pier_Concrete", (0.56, 0.56, 0.52, 1.0), 0.78),
    "Building": ("CIM_Building_Concrete", (0.72, 0.71, 0.68, 1.0), 0.72),
    "Subway_Tunnel": ("CIM_Subway_Tunnel_Dark_Concrete", (0.26, 0.26, 0.30, 1.0), 0.85),
    "Subway_Station": ("CIM_Subway_Station_Blue", (0.12, 0.22, 0.48, 1.0), 0.58),
    "Bus_Stop": ("CIM_Bus_Stop_Green", (0.05, 0.55, 0.28, 1.0), 0.42),
    "Utility_Water": ("CIM_Utility_Water_Blue", (0.02, 0.34, 0.90, 1.0), 0.36),
    "Utility_Sewer": ("CIM_Utility_Sewer_Brown", (0.36, 0.23, 0.12, 1.0), 0.66),
    "Utility_Gas": ("CIM_Utility_Gas_Orange", (0.90, 0.42, 0.08, 1.0), 0.42),
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
        try:
            bpy.ops.wm.obj_import(filepath=str(path), forward_axis="Y", up_axis="Z")
        except TypeError:
            bpy.ops.wm.obj_import(filepath=str(path))
    else:
        try:
            bpy.ops.import_scene.obj(filepath=str(path), axis_forward="Y", axis_up="Z")
        except TypeError:
            bpy.ops.import_scene.obj(filepath=str(path))


def set_node_input(node, names: tuple[str, ...], value) -> None:
    for name in names:
        if name in node.inputs:
            node.inputs[name].default_value = value
            return


def add_surface_detail(material: bpy.types.Material, bsdf, material_name: str) -> None:
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    if "Asphalt" in material_name:
        noise = nodes.new(type="ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 62.0
        noise.inputs["Detail"].default_value = 8.0
        noise.inputs["Roughness"].default_value = 0.58
        bump = nodes.new(type="ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.055
        bump.inputs["Distance"].default_value = 0.08
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        if "Normal" in bsdf.inputs:
            links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    elif "Concrete" in material_name or "Curb" in material_name or "Sidewalk" in material_name:
        noise = nodes.new(type="ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 38.0
        noise.inputs["Detail"].default_value = 5.0
        bump = nodes.new(type="ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.035
        bump.inputs["Distance"].default_value = 0.05
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        if "Normal" in bsdf.inputs:
            links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    elif "Tree_Canopy" in material_name:
        noise = nodes.new(type="ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 18.0
        noise.inputs["Detail"].default_value = 6.0
        bump = nodes.new(type="ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.045
        bump.inputs["Distance"].default_value = 0.12
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        if "Normal" in bsdf.inputs:
            links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])


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
        set_node_input(bsdf, ("Roughness",), roughness)
        metal = 0.22 if any(token in name for token in ("Metal", "Guardrail", "Street_Light")) else 0.0
        set_node_input(bsdf, ("Metallic",), metal)
        if "Lamp" in name:
            set_node_input(bsdf, ("Emission Color", "Emission"), color)
            set_node_input(bsdf, ("Emission Strength",), 1.15)
        add_surface_detail(material, bsdf, name)

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


def basemap_texture_path() -> Path | None:
    if WORLD_IMAGERY_TEXTURE_PATH.exists():
        return WORLD_IMAGERY_TEXTURE_PATH
    if GOOGLE_MAP_TEXTURE_PATH.exists():
        return GOOGLE_MAP_TEXTURE_PATH
    return None


def create_basemap_material() -> bpy.types.Material:
    material = create_material("CIM_World_Imagery", (1.0, 1.0, 1.0, 1.0), 0.9)
    texture_path = basemap_texture_path()
    if texture_path is None:
        return material
    nodes = material.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    image_node = nodes.get("Basemap_Texture")
    if image_node is None:
        image_node = nodes.new(type="ShaderNodeTexImage")
        image_node.name = "Basemap_Texture"
    image_node.image = bpy.data.images.load(str(texture_path), check_existing=True)
    if bsdf is not None:
        material.node_tree.links.new(image_node.outputs["Color"], bsdf.inputs["Base Color"])
    return material


def material_for_object(obj_name: str, layer_name: str = "") -> bpy.types.Material:
    if layer_name in MATERIALS:
        return create_material(*MATERIALS[layer_name])
    if obj_name.startswith(("GIS_GoogleMap", "GIS_WorldImagery")):
        return create_basemap_material()
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


def apply_city_materials(obj_path: Path | None = None) -> None:
    attributes_by_name: dict[str, dict] = {}
    if obj_path is not None:
        _, attributes_by_name = load_object_attributes(obj_path)
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        if obj.name.startswith(("GIS_GoogleMap", "GIS_WorldImagery")):
            ensure_planar_uv(obj)
        attributes = attributes_by_name.get(obj.name) or attributes_by_name.get(strip_blender_duplicate_suffix(obj.name)) or {}
        layer_name = str(attributes.get("layer_name") or "")
        obj.data.materials.clear()
        obj.data.materials.append(material_for_object(obj.name, layer_name))


def strip_blender_duplicate_suffix(name: str) -> str:
    if len(name) > 4 and name[-4] == "." and name[-3:].isdigit():
        return name[:-4]
    return name


def attribute_sidecar_for_obj(obj_path: Path) -> Path | None:
    if obj_path.name not in {"cim_city.obj", "cim_city_roads.obj"}:
        return None
    return ROAD_MESH_ATTRIBUTES_PATH if ROAD_MESH_ATTRIBUTES_PATH.exists() else None


def load_object_attributes(obj_path: Path) -> tuple[Path | None, dict[str, dict]]:
    sidecar = attribute_sidecar_for_obj(obj_path)
    if sidecar is None:
        return None, {}
    with sidecar.open("r", encoding="utf-8") as f:
        data = json.load(f)
    objects_by_name = data.get("objects_by_name") or {}
    if not isinstance(objects_by_name, dict):
        return sidecar, {}
    return sidecar, {str(name): record for name, record in objects_by_name.items() if isinstance(record, dict)}


def blender_custom_property_value(value):
    if isinstance(value, (str, int, float, bool)):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def apply_object_attributes(obj_path: Path) -> None:
    sidecar, attributes_by_name = load_object_attributes(obj_path)
    if not attributes_by_name:
        return

    applied_count = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        attributes = attributes_by_name.get(obj.name) or attributes_by_name.get(strip_blender_duplicate_suffix(obj.name))
        if not attributes:
            continue
        for key, value in attributes.items():
            prop_key = str(key) if str(key).startswith("cim_") else f"cim_{key}"
            obj[prop_key] = blender_custom_property_value(value)
        applied_count += 1

    print(f"Applied CIM custom properties to {applied_count} mesh objects from {sidecar}")


def export_fbx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    export_kwargs = {
        "filepath": str(path),
        "use_selection": False,
        "apply_unit_scale": True,
        "bake_space_transform": False,
        "object_types": {"MESH"},
        "mesh_smooth_type": "FACE",
        "add_leaf_bones": False,
        "path_mode": "COPY",
        "embed_textures": False,
        "use_custom_props": True,
    }
    try:
        bpy.ops.export_scene.fbx(**export_kwargs)
    except TypeError:
        export_kwargs.pop("use_custom_props", None)
        bpy.ops.export_scene.fbx(**export_kwargs)


def export_obj_to_fbx(obj_path: Path, fbx_path: Path) -> bool:
    if not obj_path.exists():
        if fbx_path.exists():
            fbx_path.unlink()
        print(f"[SKIP] Missing OBJ input: {obj_path}")
        return False

    clear_scene()
    import_obj(obj_path)
    apply_city_materials(obj_path)
    apply_object_attributes(obj_path)

    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0

    export_fbx(fbx_path)
    print(f"FBX exported: {fbx_path}")
    return True


def export_junction_debug_fbxs() -> int:
    JUNCTION_DEBUG_FBX_DIR.mkdir(parents=True, exist_ok=True)
    obj_paths = sorted(JUNCTION_DEBUG_OBJ_DIR.glob("J*.obj"))
    expected_names = {f"{obj_path.stem}.fbx" for obj_path in obj_paths}
    for old_fbx_path in JUNCTION_DEBUG_FBX_DIR.glob("J*.fbx"):
        if old_fbx_path.name not in expected_names:
            old_fbx_path.unlink()
    if LEGACY_JUNCTION_DEBUG_BUNDLE_OBJ_PATH.exists():
        LEGACY_JUNCTION_DEBUG_BUNDLE_OBJ_PATH.unlink()
    if LEGACY_JUNCTION_DEBUG_BUNDLE_FBX_PATH.exists():
        LEGACY_JUNCTION_DEBUG_BUNDLE_FBX_PATH.unlink()
    for obj_path in obj_paths:
        export_obj_to_fbx(obj_path, JUNCTION_DEBUG_FBX_DIR / f"{obj_path.stem}.fbx")
    update_junction_debug_manifest()
    print(f"Junction debug FBX files exported: {len(obj_paths)}")
    return len(obj_paths)


def update_junction_debug_manifest() -> None:
    if not JUNCTION_DEBUG_MANIFEST_PATH.exists():
        return
    with JUNCTION_DEBUG_MANIFEST_PATH.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["policy"] = "one independently inspectable finalized-geometry OBJ and FBX per junction topology bucket"
    manifest.pop("bundle_obj_path", None)
    manifest["junction_fbx_dir"] = JUNCTION_DEBUG_FBX_DIR.relative_to(ROOT).as_posix()
    summary = manifest.get("summary", {})
    summary.pop("bundle_mesh_count", None)
    summary["mesh_count"] = sum(record.get("mesh_count", 0) for record in manifest.get("objects", []))
    for record in manifest.get("objects", []):
        junction_id = record.get("junction_id")
        if not junction_id:
            continue
        fbx_path = JUNCTION_DEBUG_FBX_DIR / f"{junction_id}.fbx"
        if fbx_path.exists():
            record["fbx_path"] = fbx_path.relative_to(ROOT).as_posix()
    with JUNCTION_DEBUG_MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"Junction debug manifest updated: {JUNCTION_DEBUG_MANIFEST_PATH}")


def main() -> None:
    export_obj_to_fbx(OBJ_PATH, FBX_PATH)

    for _, (obj_path, fbx_path) in MODULE_EXPORTS.items():
        export_obj_to_fbx(obj_path, fbx_path)
    export_junction_debug_fbxs()


if __name__ == "__main__":
    main()
