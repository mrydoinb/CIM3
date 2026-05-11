#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Apply CityEngine-style rule-driven PBR/procedural materials to the generated
road OBJ and export a GLB.

Run:
blender --background --python scripts/03_apply_materials_blender.py
"""

from __future__ import annotations

import json
import os

import bpy


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJ_PATH = os.path.join(ROOT, "output", "obj", "road_test.obj")
GLB_OUT = os.path.join(ROOT, "output", "gltf", "road_test_realistic.glb")
BLEND_OUT = os.path.join(ROOT, "output", "road_test_realistic.blend")
RULE_PATH = os.path.join(ROOT, "data", "rules", "road_rules.json")
TEXTURE_ROOT = os.path.join(ROOT, "assets", "textures")

# CityEngine/CGA-like material flow:
# 1. Road rules choose semantic material ids for generated components.
# 2. A material library resolves each id to texture assets and fallback shader settings.
# 3. Meshes receive deterministic projected UVs in model meters, similar to setupProjection/projectUV.
COMPONENT_RULE_FIELDS = {
    "road_surface": "material",
    "sidewalk": "sidewalk_material",
    "curb": "curb_material",
    "lane_marking": "marking_material",
}

MATERIAL_LIBRARY = {
    "asphalt": {
        "name": "MAT_Asphalt",
        "texture_type": "asphalt",
        "texture_dir": "asphalt",
        "projection_size_m": 8.0,
        "viewport_color": (0.05, 0.05, 0.045, 1.0),
        "fallback": {
            "scale": 50.0,
            "detail": 15.0,
            "color_a": (0.08, 0.08, 0.08, 1.0),
            "color_b": (0.15, 0.15, 0.15, 1.0),
            "roughness": 0.85,
            "base": (0.12, 0.12, 0.12, 1.0),
            "bump_strength": 0.2,
            "bump_distance": 0.01,
        },
    },
    "concrete": {
        "name": "MAT_Concrete",
        "texture_type": "concrete",
        "texture_dir": "concrete",
        "projection_size_m": 4.0,
        "viewport_color": (0.58, 0.56, 0.52, 1.0),
        "fallback": {
            "scale": 30.0,
            "detail": 10.0,
            "color_a": (0.35, 0.35, 0.35, 1.0),
            "color_b": (0.45, 0.45, 0.45, 1.0),
            "roughness": 0.8,
            "base": (0.4, 0.4, 0.4, 1.0),
            "bump_strength": 0.15,
            "bump_distance": 0.02,
        },
    },
    "curb_concrete": {
        "name": "MAT_Curb",
        "texture_type": "curb",
        "texture_dir": "curb_concrete",
        "projection_size_m": 3.0,
        "viewport_color": (0.78, 0.76, 0.7, 1.0),
        "fallback": {
            "scale": 20.0,
            "detail": 10.0,
            "color_a": (0.5, 0.5, 0.5, 1.0),
            "color_b": (0.6, 0.6, 0.6, 1.0),
            "roughness": 0.75,
            "base": (0.55, 0.55, 0.55, 1.0),
            "bump_strength": 0.15,
            "bump_distance": 0.02,
        },
    },
    "white_marking": {
        "name": "MAT_White_Marking",
        "texture_type": "marking",
        "texture_dir": "road_marking",
        "projection_size_m": 2.0,
        "viewport_color": (1.0, 0.96, 0.72, 1.0),
        "fallback": {
            "scale": 1.0,
            "detail": 2.0,
            "color_a": (0.96, 0.96, 0.9, 1.0),
            "color_b": (1.0, 1.0, 0.96, 1.0),
            "roughness": 0.48,
            "base": (0.98, 0.98, 0.92, 1.0),
            "bump_strength": 0.0,
            "bump_distance": 0.0,
        },
    },
}

TEXTURE_HINTS = {
    "asphalt": ["asphalt_track"],
    "concrete": ["patterned_concrete_pavers"],
    "curb": ["gravel_concrete_04"],
    "marking": ["marking", "paint", "line", "white"],
}

TEXTURE_ROLE_KEYWORDS = {
    "basecolor": ["basecolor", "base_color", "diffuse", "diff", "color", "albedo"],
    "roughness": ["roughness", "rough"],
    "normal": ["normal_gl", "nor_gl", "normal", "nor"],
    "ao": ["ao", "ambient_occlusion", "ambientocclusion"],
}

TEXTURE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff"]


def ensure_dirs() -> None:
    os.makedirs(os.path.dirname(GLB_OUT), exist_ok=True)
    for mat_dir in ["asphalt", "concrete", "curb_concrete", "road_marking"]:
        os.makedirs(os.path.join(TEXTURE_ROOT, mat_dir), exist_ok=True)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_obj(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(f"OBJ file not found: {path}")

    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=path)
    else:
        bpy.ops.import_scene.obj(filepath=path)


def load_default_rule() -> dict:
    if not os.path.exists(RULE_PATH):
        raise FileNotFoundError(f"Rule file not found: {RULE_PATH}")

    with open(RULE_PATH, "r", encoding="utf-8") as f:
        rules = json.load(f)

    default_rule = rules.get("default_road")
    if not isinstance(default_rule, dict):
        raise ValueError("data/rules/road_rules.json must contain a default_road object")
    return default_rule


def find_texture_file(tex_dir: str, mat_type: str, role: str) -> str | None:
    candidates = []
    search_roots = [tex_dir, TEXTURE_ROOT]
    role_keywords = TEXTURE_ROLE_KEYWORDS[role]
    mat_hints = TEXTURE_HINTS.get(mat_type, [])

    for root in search_roots:
        if not os.path.isdir(root):
            continue

        for current_dir, _, files in os.walk(root):
            for filename in files:
                stem, ext = os.path.splitext(filename)
                if ext.lower() not in TEXTURE_EXTENSIONS:
                    continue

                stem_lower = stem.lower()
                path_lower = os.path.join(current_dir, filename).lower()
                if not any(keyword in stem_lower for keyword in role_keywords):
                    continue
                if mat_hints and not any(hint in path_lower for hint in mat_hints):
                    continue

                score = 0
                if os.path.abspath(current_dir).lower().startswith(os.path.abspath(tex_dir).lower()):
                    score += 20
                if any(hint in stem_lower for hint in mat_hints):
                    score += 10
                if role == "normal" and ("nor_gl" in stem_lower or "normal_gl" in stem_lower):
                    score += 5
                if ext.lower() in [".jpg", ".jpeg", ".png"]:
                    score += 2
                candidates.append((score, os.path.join(current_dir, filename)))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][1]


def set_principled_input(bsdf, name: str, value) -> None:
    if name in bsdf.inputs:
        bsdf.inputs[name].default_value = value


def create_cityengine_material(material_id: str, definition: dict):
    mat = bpy.data.materials.new(name=definition["name"])
    mat["cityengine_material_id"] = material_id
    mat["projection_size_m"] = definition["projection_size_m"]
    mat.diffuse_color = definition["viewport_color"]
    mat.use_nodes = True

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new(type="ShaderNodeOutputMaterial")
    output.location = (600, 0)

    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    set_principled_input(bsdf, "Metallic", 0.0)

    tex_dir = os.path.join(TEXTURE_ROOT, definition["texture_dir"])
    mat_type = definition["texture_type"]

    def load_tex(role: str, colorspace: str = "sRGB"):
        path = find_texture_file(tex_dir, mat_type, role)
        if not path or not os.path.exists(path):
            return None

        img = bpy.data.images.load(path)
        img.colorspace_settings.name = colorspace
        node = nodes.new(type="ShaderNodeTexImage")
        node.image = img
        print(f"{definition['name']}: loaded {role} texture -> {path}")
        return node

    base = load_tex("basecolor", "sRGB")
    rough = load_tex("roughness", "Non-Color")
    normal = load_tex("normal", "Non-Color")
    ao = load_tex("ao", "Non-Color")

    if base or rough or normal or ao:
        print(f"[{definition['name']}] PBR textures found; applying texture material.")
        tex_coord = nodes.new(type="ShaderNodeTexCoord")
        tex_coord.location = (-1000, 0)
        mapping = nodes.new(type="ShaderNodeMapping")
        mapping.location = (-800, 0)
        mapping.inputs["Scale"].default_value = (1.0, 1.0, 1.0)
        links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

        if base:
            base.location = (-500, 250)
            links.new(mapping.outputs["Vector"], base.inputs["Vector"])

            if ao:
                ao.location = (-500, 450)
                links.new(mapping.outputs["Vector"], ao.inputs["Vector"])
                mix = nodes.new(type="ShaderNodeMix")
                mix.data_type = "RGBA"
                mix.blend_type = "MULTIPLY"
                mix.inputs["Factor"].default_value = 1.0
                mix.location = (-100, 300)
                links.new(base.outputs["Color"], mix.inputs["A"])
                links.new(ao.outputs["Color"], mix.inputs["B"])
                links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
            else:
                links.new(base.outputs["Color"], bsdf.inputs["Base Color"])

        if rough:
            rough.location = (-500, 0)
            links.new(mapping.outputs["Vector"], rough.inputs["Vector"])
            links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])

        if normal:
            normal.location = (-500, -250)
            links.new(mapping.outputs["Vector"], normal.inputs["Vector"])
            normal_map = nodes.new(type="ShaderNodeNormalMap")
            normal_map.location = (-100, -250)
            links.new(normal.outputs["Color"], normal_map.inputs["Color"])
            links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])
    else:
        fallback = definition["fallback"]
        print(f"[{definition['name']}] No matching texture found; using procedural fallback.")
        noise = nodes.new(type="ShaderNodeTexNoise")
        noise.location = (-600, 0)
        color_ramp = nodes.new(type="ShaderNodeValToRGB")
        color_ramp.location = (-300, 150)
        bump = nodes.new(type="ShaderNodeBump")
        bump.location = (-300, -150)

        links.new(noise.outputs["Fac"], color_ramp.inputs["Fac"])
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        links.new(color_ramp.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

        noise.inputs["Scale"].default_value = fallback["scale"]
        noise.inputs["Detail"].default_value = fallback["detail"]
        color_ramp.color_ramp.elements[0].position = 0.35
        color_ramp.color_ramp.elements[0].color = fallback["color_a"]
        color_ramp.color_ramp.elements[1].position = 0.75
        color_ramp.color_ramp.elements[1].color = fallback["color_b"]
        set_principled_input(bsdf, "Roughness", fallback["roughness"])
        set_principled_input(bsdf, "Base Color", fallback["base"])
        bump.inputs["Strength"].default_value = fallback["bump_strength"]
        bump.inputs["Distance"].default_value = fallback["bump_distance"]

    return mat


def material_id_for_object(obj_name: str, rule: dict) -> str | None:
    name_lower = obj_name.lower()
    for component_key, rule_field in COMPONENT_RULE_FIELDS.items():
        if component_key in name_lower:
            return rule.get(rule_field)
    return None


def build_materials(rule: dict) -> dict:
    material_ids = {
        rule.get(rule_field)
        for rule_field in COMPONENT_RULE_FIELDS.values()
        if rule.get(rule_field)
    }

    materials = {}
    for material_id in sorted(material_ids):
        definition = MATERIAL_LIBRARY.get(material_id)
        if definition is None:
            print(f"[WARN] Material '{material_id}' is not in MATERIAL_LIBRARY; using asphalt fallback.")
            definition = MATERIAL_LIBRARY["asphalt"]
        materials[material_id] = create_cityengine_material(material_id, definition)
    return materials


def projected_uv_for_point(point, normal, size: float) -> tuple[float, float]:
    ax = abs(normal.x)
    ay = abs(normal.y)
    az = abs(normal.z)

    if az >= ax and az >= ay:
        u, v = point.x, point.y
    elif ax >= ay:
        u, v = point.y, point.z
    else:
        u, v = point.x, point.z
    return (u / size, v / size)


def assign_projected_uv(obj, projection_size_m: float) -> None:
    mesh = obj.data
    uv_layer = mesh.uv_layers.get("CE_ProjectUV") or mesh.uv_layers.new(name="CE_ProjectUV")
    world_matrix = obj.matrix_world
    normal_matrix = world_matrix.to_3x3()

    for poly in mesh.polygons:
        world_normal = (normal_matrix @ poly.normal).normalized()
        for loop_index in poly.loop_indices:
            vertex = mesh.vertices[mesh.loops[loop_index].vertex_index]
            world_point = world_matrix @ vertex.co
            uv_layer.data[loop_index].uv = projected_uv_for_point(
                world_point,
                world_normal,
                projection_size_m,
            )


def clear_imported_vertex_colors(obj) -> None:
    color_attributes = getattr(obj.data, "color_attributes", None)
    if color_attributes is None:
        return

    while color_attributes:
        color_attributes.remove(color_attributes[0])


def configure_scene_for_material_review() -> None:
    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.world.color = (0.78, 0.82, 0.86)

    if not any(obj.type == "LIGHT" for obj in bpy.data.objects):
        bpy.ops.object.light_add(type="SUN", location=(0.0, 0.0, 900.0))
        sun = bpy.context.object
        sun.name = "Material_Review_Sun"
        sun.data.energy = 2.5
        sun.rotation_euler = (0.9, 0.0, -0.6)


def apply_materials_to_scene() -> None:
    rule = load_default_rule()
    materials = build_materials(rule)

    bpy.ops.object.select_all(action="DESELECT")

    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue

        material_id = material_id_for_object(obj.name, rule)
        if material_id is None:
            print(f"[WARN] No road component rule matched object: {obj.name}")
            continue

        mat = materials.get(material_id)
        if mat is None:
            print(f"[WARN] Material '{material_id}' was not created for object: {obj.name}")
            continue

        definition = MATERIAL_LIBRARY.get(material_id, MATERIAL_LIBRARY["asphalt"])
        assign_projected_uv(obj, definition["projection_size_m"])
        clear_imported_vertex_colors(obj)
        obj.color = definition["viewport_color"]
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        obj["cityengine_component_material"] = material_id


def main() -> None:
    ensure_dirs()
    clear_scene()
    import_obj(OBJ_PATH)
    apply_materials_to_scene()

    bpy.ops.export_scene.gltf(
        filepath=GLB_OUT,
        export_format="GLB",
        use_selection=False,
    )
    print(f"PBR GLB export complete: {GLB_OUT}")

    configure_scene_for_material_review()
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_OUT)
    print(f"Blend file saved: {BLEND_OUT}")


if __name__ == "__main__":
    main()
