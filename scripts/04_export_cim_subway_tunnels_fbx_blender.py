#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Blender-side exporter for the generated CIM subway tunnel module.

Default mode exports the current hybrid subway model: large structures from the
generated railway-centerline OBJ plus sample-derived small components from
subway01.blend. Set CIM_SUBWAY_PROCEDURAL_ONLY=1 for the old direct OBJ-to-FBX
path.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys

import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from blender.fbx_export import MODULE_FBX_DIR, MODULE_OBJ_DIR, export_obj_to_fbx


DEFAULT_TEMPLATE = Path.home() / "Desktop" / "chk" / "\u5730\u94c1" / "subway01.blend"
PATH_JSON = ROOT / "output" / "semantic" / "cim4" / "subway_tunnel_template_paths.json"
PROCEDURAL_OBJ = Path(
    os.environ.get("CIM_SUBWAY_PROCEDURAL_OBJ")
    or ROOT / "output" / "obj" / "modules" / "cim4" / "subway_tunnels.obj"
)
PROCEDURAL_MESH_ATTRIBUTES_JSON = Path(
    os.environ.get("CIM_SUBWAY_PROCEDURAL_MESH_ATTRIBUTES_JSON")
    or ROOT / "output" / "semantic" / "cim4" / "subway_tunnels_mesh_attributes.json"
)
OUTPUT_OBJ = Path(
    os.environ.get("CIM_SUBWAY_HYBRID_OUTPUT_OBJ")
    or ROOT / "output" / "obj" / "modules" / "cim4" / "subway_tunnels.obj"
)
OUTPUT_BLEND = Path(
    os.environ.get("CIM_SUBWAY_HYBRID_OUTPUT_BLEND")
    or ROOT / "output" / "blend" / "modules" / "cim4" / "subway_tunnels.blend"
)
OUTPUT_FBX = Path(
    os.environ.get("CIM_SUBWAY_HYBRID_OUTPUT_FBX")
    or ROOT / "output" / "fbx" / "modules" / "cim4" / "subway_tunnels.fbx"
)
OUTPUT_MESH_ATTRIBUTES_JSON = Path(
    os.environ.get("CIM_SUBWAY_HYBRID_MESH_ATTRIBUTES_JSON")
    or ROOT / "output" / "semantic" / "cim4" / "subway_tunnels_mesh_attributes.json"
)


LARGE_REFERENCE_COMPONENTS = {
    "Ref02_Aggregate_Base",
    "Ref03_Rubber_Isolation",
    "Ref04_Concrete_Segment",
    "Ref08_Platform_Main",
    "Ref09_Platform_Support",
    "Ref10_Platform_Edge_Strip",
    "Ref11_Platform_Steel_Frame",
    "Ref12_Platform_Concrete_Panel",
    "Ref13_Platform_Bracket",
    "Ref37_Rail_Bed_Surface",
    "Ref38_Rail_Aluminum_Part",
    "Ref39_Rail_Cast_Iron_Part",
    "Ref40_Rail_Chrome_Part",
}


SMALL_TEMPLATE_MESHES = {
    "Mesh.001",
    "Mesh.005",
    "Mesh.006",
    "Mesh.007",
    "Mesh.014",
    "Mesh.015",
    "Mesh.016",
    "Mesh.017",
    "Mesh.018",
    "Mesh.019",
    "Mesh.020",
    "Mesh.021",
    "Mesh.022",
    "Mesh.023",
    "Mesh.024",
    "Mesh.025",
    "Mesh.026",
    "Mesh.027",
    "Mesh.028",
    "Mesh.029",
    "Mesh.030",
    "Mesh.031",
    "Mesh.032",
    "Mesh.033",
    "Mesh.034",
    "Mesh.035",
    "Mesh.036",
    "Mesh.041",
}

SINGLE_TUNNEL_SECTION_SIDE = "right"
SINGLE_TUNNEL_SECTION_SPLIT_X_M = 135.573803
SINGLE_TUNNEL_SECTION_CENTER_XZ_M = (144.079249, -22.147192)
SINGLE_TUNNEL_SECTION_RADIUS_M = 3.010074
SINGLE_TUNNEL_SECTION_CLIP_TOLERANCE_M = 0.75


def env_flag(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def subway_level() -> str:
    level = str(os.environ.get("CIM_SUBWAY_LEVEL", "cim4")).strip().lower()
    return level if level in {"cim4"} else "cim4"


def token(text: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(text), flags=re.UNICODE).strip("_")
    return cleaned or fallback


def strip_blender_duplicate_suffix(name: str) -> str:
    return re.sub(r"\.\d{3}$", "", str(name))


def json_safe_value(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    return str(value)


def load_objects_by_name(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    objects_by_name = data.get("objects_by_name") if isinstance(data, dict) else {}
    if not isinstance(objects_by_name, dict):
        return {}
    return {str(name): record for name, record in objects_by_name.items() if isinstance(record, dict)}


def local_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    points = [Vector(corner) for corner in obj.bound_box]
    return (
        Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))),
        Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))),
    )


def bounds_center(minimum: Vector, maximum: Vector) -> Vector:
    return (minimum + maximum) * 0.5


def single_tunnel_anchor_center(reference: bpy.types.Object) -> Vector:
    reference_min, reference_max = local_bounds(reference)
    center = bounds_center(reference_min, reference_max)
    center.x = SINGLE_TUNNEL_SECTION_CENTER_XZ_M[0]
    center.z = SINGLE_TUNNEL_SECTION_CENTER_XZ_M[1]
    return center


def point_in_single_tunnel_half(point: Vector) -> bool:
    dx = float(point.x) - SINGLE_TUNNEL_SECTION_CENTER_XZ_M[0]
    dz = float(point.z) - SINGLE_TUNNEL_SECTION_CENTER_XZ_M[1]
    return math.hypot(dx, dz) <= SINGLE_TUNNEL_SECTION_RADIUS_M + SINGLE_TUNNEL_SECTION_CLIP_TOLERANCE_M


def clipped_small_component_mesh(
    template_obj: bpy.types.Object,
    reference_inverse: Matrix,
) -> bpy.types.Mesh | None:
    relative_matrix = reference_inverse @ template_obj.matrix_world.copy()
    vertices = [vertex.co.copy() for vertex in template_obj.data.vertices]
    faces: list[list[int]] = []
    material_indices: list[int] = []
    for polygon in template_obj.data.polygons:
        centroid = Vector((0.0, 0.0, 0.0))
        for vertex_idx in polygon.vertices:
            centroid += relative_matrix @ template_obj.data.vertices[vertex_idx].co
        centroid /= max(len(polygon.vertices), 1)
        if not point_in_single_tunnel_half(centroid):
            continue
        faces.append(list(polygon.vertices))
        material_indices.append(int(polygon.material_index))
    if not faces:
        return None

    mesh = bpy.data.meshes.new(f"{template_obj.data.name}_SingleTunnelHalf")
    mesh.from_pydata([tuple(vertex) for vertex in vertices], [], faces)
    for material in template_obj.data.materials:
        mesh.materials.append(material)
    mesh.update()
    for polygon, material_index in zip(mesh.polygons, material_indices):
        polygon.material_index = min(material_index, max(len(mesh.materials) - 1, 0))
    mesh["single_tunnel_half"] = SINGLE_TUNNEL_SECTION_SIDE
    mesh["single_tunnel_radius_m"] = SINGLE_TUNNEL_SECTION_RADIUS_M
    mesh["single_tunnel_clip_policy"] = "right_circular_bore_face_centroid"
    mesh["source_template_mesh"] = template_obj.name
    return mesh


def polyline_length(points: list[Vector]) -> float:
    return sum((b - a).length for a, b in zip(points, points[1:]))


def interpolate_polyline(points: list[Vector], distance: float) -> Vector:
    if distance <= 0.0:
        return points[0].copy()
    remaining = float(distance)
    for a, b in zip(points, points[1:]):
        segment = b - a
        length = segment.length
        if length <= 1e-6:
            continue
        if remaining <= length:
            return a + segment * (remaining / length)
        remaining -= length
    return points[-1].copy()


def tangent_at_polyline_distance(points: list[Vector], distance: float, sample_m: float) -> Vector:
    total = polyline_length(points)
    if total <= 1e-6:
        return Vector((0.0, 1.0, 0.0))
    half_window = max(float(sample_m) * 0.5, 0.5)
    before = interpolate_polyline(points, max(0.0, float(distance) - half_window))
    after = interpolate_polyline(points, min(total, float(distance) + half_window))
    tangent = after - before
    tangent.z = 0.0
    if tangent.length <= 1e-6:
        for a, b in zip(points, points[1:]):
            tangent = b - a
            tangent.z = 0.0
            if tangent.length > 1e-6:
                break
    return tangent.normalized() if tangent.length > 1e-6 else Vector((0.0, 1.0, 0.0))


def chunk_polyline(points: list[Vector], chunk_length: float) -> list[tuple[Vector, Vector, float]]:
    total = polyline_length(points)
    if total <= 1e-6:
        return []
    if chunk_length <= 0.0:
        chunk_length = total
    chunks: list[tuple[Vector, Vector, float]] = []
    distance = 0.0
    tangent_sample_m = max(8.0, min(float(chunk_length) * 0.5, 45.0))
    while distance < total - 1e-6:
        next_distance = min(total, distance + chunk_length)
        center_distance = (distance + next_distance) * 0.5
        length = next_distance - distance
        center = interpolate_polyline(points, center_distance)
        tangent = tangent_at_polyline_distance(points, center_distance, tangent_sample_m)
        chunks.append((center - tangent * (length * 0.5), center + tangent * (length * 0.5), length))
        distance = next_distance
    return chunks


def placement_matrix(start: Vector, end: Vector, depth_m: float) -> Matrix | None:
    direction = end - start
    direction.z = 0.0
    if direction.length <= 1e-6:
        return None
    tangent = direction.normalized()
    normal = Vector((-tangent.y, tangent.x, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    center = (start + end) * 0.5
    center.z = depth_m
    return Matrix(
        (
            (normal.x, tangent.x, up.x, center.x),
            (normal.y, tangent.y, up.y, center.y),
            (normal.z, tangent.z, up.z, center.z),
            (0.0, 0.0, 0.0, 1.0),
        )
    )


def load_paths() -> dict:
    with PATH_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def obj_import(path: Path) -> list[bpy.types.Object]:
    before = set(bpy.data.objects)
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
    return [obj for obj in bpy.data.objects if obj not in before]


def export_selected_obj(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(bpy.ops.wm, "obj_export"):
        try:
            bpy.ops.wm.obj_export(
                filepath=str(path),
                export_selected_objects=True,
                forward_axis="Y",
                up_axis="Z",
            )
            return
        except TypeError:
            bpy.ops.wm.obj_export(filepath=str(path), export_selected_objects=True)
            return
    bpy.ops.export_scene.obj(filepath=str(path), use_selection=True, axis_forward="Y", axis_up="Z")


def apply_custom_properties(obj: bpy.types.Object, attributes: dict) -> None:
    for key, value in attributes.items():
        try:
            obj[str(key)] = json_safe_value(value)
        except TypeError:
            obj[str(key)] = str(value)


def ensure_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def move_to_collection(obj: bpy.types.Object, collection: bpy.types.Collection) -> None:
    if obj.name not in collection.objects:
        collection.objects.link(obj)
    for existing in list(obj.users_collection):
        if existing != collection:
            existing.objects.unlink(obj)


def keep_large_object(obj: bpy.types.Object) -> bool:
    return any(component in obj.name for component in LARGE_REFERENCE_COMPONENTS)


def import_procedural_large_structures() -> int:
    if not PROCEDURAL_OBJ.exists():
        raise FileNotFoundError(f"Procedural subway OBJ not found: {PROCEDURAL_OBJ}")
    procedural_attributes = load_objects_by_name(PROCEDURAL_MESH_ATTRIBUTES_JSON)
    collection = ensure_collection("CIM_Subway_Procedural_Large_From_Railway")
    imported = obj_import(PROCEDURAL_OBJ)
    kept = 0
    for obj in imported:
        if obj.type != "MESH":
            continue
        if not keep_large_object(obj):
            bpy.data.objects.remove(obj, do_unlink=True)
            continue
        attributes = (
            procedural_attributes.get(obj.name)
            or procedural_attributes.get(strip_blender_duplicate_suffix(obj.name))
            or {}
        )
        apply_custom_properties(obj, attributes)
        obj["hybrid_role"] = "procedural_large_from_railway_centerline"
        obj["large_component_policy"] = "generated_by_pipeline_3d_rules"
        move_to_collection(obj, collection)
        kept += 1
    return kept


def hide_source_template_objects(template_objects: list[bpy.types.Object]) -> None:
    for obj in template_objects:
        obj.hide_set(True)
        obj.hide_viewport = True
        obj.hide_render = True
        obj["template_role"] = "hidden_source_small_component_asset"


def create_small_component_instances(path_data: dict) -> tuple[int, int]:
    reference = bpy.data.objects["Mesh.004"]
    reference_min, reference_max = local_bounds(reference)
    reference_axis_length = max(float((reference_max - reference_min).y), 0.001)
    template_anchor_center = single_tunnel_anchor_center(reference)
    reference_inverse = reference.matrix_world.inverted()
    template_objects = [
        bpy.data.objects[name]
        for name in sorted(SMALL_TEMPLATE_MESHES)
        if name in bpy.data.objects and bpy.data.objects[name].type == "MESH"
    ]
    if len(template_objects) != len(SMALL_TEMPLATE_MESHES):
        found = {obj.name for obj in template_objects}
        missing = sorted(SMALL_TEMPLATE_MESHES - found)
        raise RuntimeError(f"Missing small template meshes: {missing}")

    relative_matrices = {obj.name: reference_inverse @ obj.matrix_world.copy() for obj in template_objects}
    clipped_meshes: dict[str, bpy.types.Mesh] = {}
    for obj in template_objects:
        clipped_mesh = clipped_small_component_mesh(obj, reference_inverse)
        if clipped_mesh is not None:
            clipped_meshes[obj.name] = clipped_mesh
    collection = ensure_collection("CIM_Subway_Template_Small_Components")
    generated_objects: list[bpy.types.Object] = []
    chunk_total = 0
    for path in path_data.get("paths", []):
        line_name = token(path.get("line_name") or "Subway")
        source_id = token(path.get("source_subway_id") or path.get("source_index") or "Source")
        depth_m = float(path.get("tunnel_depth_m") or 0.0)
        for line_idx, line in enumerate(path.get("lines", [])):
            points = [Vector((float(x), float(y), 0.0)) for x, y in line.get("coords_xy", [])]
            if len(points) < 2:
                continue
            for chunk_idx, (start, end, length_m) in enumerate(chunk_polyline(points, reference_axis_length)):
                matrix = placement_matrix(start, end, depth_m)
                if matrix is None:
                    continue
                base_matrix = matrix @ Matrix.Translation(-template_anchor_center)
                chunk_total += 1
                for template_obj in template_objects:
                    clipped_mesh = clipped_meshes.get(template_obj.name)
                    if clipped_mesh is None:
                        continue
                    instance = template_obj.copy()
                    instance.data = clipped_mesh
                    instance.animation_data_clear()
                    instance.name = f"HybridSmall_{line_name}_{source_id}_G{line_idx:03d}_C{chunk_idx:04d}_{template_obj.name}"
                    instance.matrix_world = base_matrix @ relative_matrices[template_obj.name]
                    instance["hybrid_role"] = "sample_small_component_rule_placed"
                    instance["template_source_blend"] = str(Path(os.environ.get("CIM_SUBWAY_TEMPLATE_BLEND") or DEFAULT_TEMPLATE))
                    instance["template_mesh"] = template_obj.name
                    instance["small_component_policy"] = "copied_from_sample_mesh_no_geometry_rebuild"
                    instance["placement_policy"] = "path_chunk_rigid_rule"
                    instance["source_subway_id"] = source_id
                    instance["line_name"] = line_name
                    instance["base_line_name"] = str(path.get("base_line_name") or line_name)
                    instance["tunnel_side"] = str(path.get("tunnel_side") or "")
                    instance["template_chunk_index"] = chunk_idx
                    instance["template_line_index"] = line_idx
                    instance["template_chunk_length_m"] = round(float(length_m), 3)
                    instance["template_reference_axis_length_m"] = round(reference_axis_length, 3)
                    instance["single_tunnel_half"] = SINGLE_TUNNEL_SECTION_SIDE
                    instance["single_tunnel_split_x_m"] = round(SINGLE_TUNNEL_SECTION_SPLIT_X_M, 6)
                    instance["single_tunnel_radius_m"] = round(SINGLE_TUNNEL_SECTION_RADIUS_M, 6)
                    instance["single_tunnel_clip_tolerance_m"] = round(SINGLE_TUNNEL_SECTION_CLIP_TOLERANCE_M, 6)
                    instance["single_tunnel_clip_policy"] = "right_circular_bore_face_centroid"
                    instance["single_tunnel_anchor_x_m"] = round(float(template_anchor_center.x), 6)
                    instance["single_tunnel_anchor_z_m"] = round(float(template_anchor_center.z), 6)
                    instance["template_lateral_scale"] = 1.0
                    instance["template_longitudinal_scale"] = 1.0
                    instance["template_vertical_scale"] = 1.0
                    collection.objects.link(instance)
                    generated_objects.append(instance)

    hide_source_template_objects([obj for obj in bpy.data.objects if obj.name.startswith("Mesh.")])
    for obj in bpy.data.objects:
        obj.select_set(False)
    for obj in generated_objects:
        obj.select_set(True)
    return len(generated_objects), chunk_total


def select_hybrid_output_objects() -> int:
    selected = 0
    for obj in bpy.data.objects:
        obj.select_set(False)
        if obj.type == "MESH" and str(obj.get("hybrid_role") or ""):
            obj.hide_set(False)
            obj.hide_viewport = False
            obj.hide_render = False
            obj.select_set(True)
            selected += 1
    return selected


def selected_hybrid_objects() -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH" and obj.select_get() and str(obj.get("hybrid_role") or "")
    ]


def hybrid_layer_name(obj: bpy.types.Object) -> str:
    layer_name = str(obj.get("layer_name") or "")
    if layer_name:
        return layer_name
    if obj.get("hybrid_role") == "sample_small_component_rule_placed":
        template_mesh = str(obj.get("template_mesh") or "Unknown")
        return f"Subway_TemplateSmall_{template_mesh}"
    return "Subway_Hybrid"


def hybrid_entity_type(obj: bpy.types.Object) -> str:
    entity_type = str(obj.get("cim_entity_type") or "")
    if entity_type:
        return entity_type
    if obj.get("hybrid_role") == "sample_small_component_rule_placed":
        return "subway_interval_tunnel_template_component"
    return "subway_interval_tunnel_component"


def hybrid_mesh_attribute_record(obj: bpy.types.Object) -> dict:
    mesh = obj.data
    record = {
        "object_name": obj.name,
        "layer_name": hybrid_layer_name(obj),
        "mesh_vertex_count": int(len(mesh.vertices)),
        "mesh_face_count": int(len(mesh.polygons)),
        "mesh_area_m2": round(float(sum(polygon.area for polygon in mesh.polygons)), 3),
        "cim_domain": str(obj.get("cim_domain") or "subway"),
        "cim_entity_type": hybrid_entity_type(obj),
    }
    for key in obj.keys():
        if key == "_RNA_UI":
            continue
        record[str(key)] = json_safe_value(obj[key])
    return record


def write_hybrid_mesh_attributes(
    objects: list[bpy.types.Object],
    path_data: dict,
    large_count: int,
    small_count: int,
    chunk_count: int,
) -> None:
    OUTPUT_MESH_ATTRIBUTES_JSON.parent.mkdir(parents=True, exist_ok=True)
    records = [hybrid_mesh_attribute_record(obj) for obj in sorted(objects, key=lambda item: item.name)]
    layer_counts: dict[str, int] = {}
    for record in records:
        layer_name = str(record.get("layer_name") or "unknown")
        layer_counts[layer_name] = layer_counts.get(layer_name, 0) + 1
    data = {
        "project": "cim_road_poc",
        "model": "subway_tunnels_mesh_attributes",
        "generation_profile": {
            "name": "cim4",
            "semantic_level": "hybrid_interval_tunnel_with_sample_components",
        },
        "policy": (
            "hybrid subway tunnels are exported in the same module sidecar shape as roads; "
            "large structures are generated from railway centerlines and small components are "
            "copied from the sample blend"
        ),
        "object_count": len(records),
        "source_line_count": len({str(path.get("source_subway_id") or "") for path in path_data.get("paths", [])}),
        "large_procedural_object_count": large_count,
        "small_template_instance_count": small_count,
        "template_chunk_count": chunk_count,
        "layer_object_counts": dict(sorted(layer_counts.items())),
        "objects": records,
        "objects_by_name": {str(record["object_name"]): record for record in records},
    }
    with OUTPUT_MESH_ATTRIBUTES_JSON.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def export_procedural_only() -> None:
    level = subway_level()
    obj_path = MODULE_OBJ_DIR / level / "subway_tunnels.obj"
    fbx_path = MODULE_FBX_DIR / level / "subway_tunnels.fbx"
    export_obj_to_fbx(obj_path, fbx_path)


def export_hybrid() -> None:
    template_path = Path(os.environ.get("CIM_SUBWAY_TEMPLATE_BLEND") or DEFAULT_TEMPLATE)
    export_obj = env_flag("CIM_SUBWAY_HYBRID_EXPORT_OBJ")
    export_fbx = env_flag("CIM_SUBWAY_HYBRID_EXPORT_FBX")
    save_blend = env_flag("CIM_SUBWAY_HYBRID_SAVE_BLEND")

    bpy.ops.wm.open_mainfile(filepath=str(template_path))
    path_data = load_paths()
    large_count = import_procedural_large_structures()
    small_count, chunk_count = create_small_component_instances(path_data)
    export_object_count = select_hybrid_output_objects()
    export_objects = selected_hybrid_objects()

    write_hybrid_mesh_attributes(export_objects, path_data, large_count, small_count, chunk_count)

    if export_obj:
        export_selected_obj(OUTPUT_OBJ)

    if save_blend:
        OUTPUT_BLEND.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT_BLEND))

    if export_fbx:
        OUTPUT_FBX.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.export_scene.fbx(
            filepath=str(OUTPUT_FBX),
            use_selection=True,
            object_types={"MESH"},
            add_leaf_bones=False,
            bake_anim=False,
            apply_unit_scale=True,
            use_space_transform=True,
        )

    print(
        json.dumps(
            {
                "template": str(template_path),
                "procedural_obj": str(PROCEDURAL_OBJ),
                "paths": len(path_data.get("paths", [])),
                "large_procedural_objects": large_count,
                "small_template_meshes": len(SMALL_TEMPLATE_MESHES),
                "small_instances": small_count,
                "chunks": chunk_count,
                "single_tunnel_half": SINGLE_TUNNEL_SECTION_SIDE,
                "single_tunnel_split_x_m": SINGLE_TUNNEL_SECTION_SPLIT_X_M,
                "single_tunnel_radius_m": SINGLE_TUNNEL_SECTION_RADIUS_M,
                "single_tunnel_clip_tolerance_m": SINGLE_TUNNEL_SECTION_CLIP_TOLERANCE_M,
                "single_tunnel_clip_policy": "right_circular_bore_face_centroid",
                "single_tunnel_anchor_xz_m": list(SINGLE_TUNNEL_SECTION_CENTER_XZ_M),
                "export_objects": export_object_count,
                "large_component_policy": "generated_by_pipeline_3d_rules_from_railway",
                "small_component_policy": "copied_from_sample_mesh_no_geometry_rebuild",
                "obj": str(OUTPUT_OBJ) if export_obj else None,
                "mesh_attributes": str(OUTPUT_MESH_ATTRIBUTES_JSON),
                "blend": str(OUTPUT_BLEND) if save_blend else None,
                "fbx": str(OUTPUT_FBX) if export_fbx else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    if env_flag("CIM_SUBWAY_PROCEDURAL_ONLY"):
        export_procedural_only()
    else:
        export_hybrid()


if __name__ == "__main__":
    main()
