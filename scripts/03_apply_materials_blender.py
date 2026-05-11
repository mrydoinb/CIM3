#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Apply PBR/procedural materials to the generated road OBJ and export a GLB.

Run:
blender --background --python scripts/03_apply_materials_blender.py
"""

import os

import bpy


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJ_PATH = os.path.join(ROOT, "output", "obj", "road_test.obj")
GLB_OUT = os.path.join(ROOT, "output", "gltf", "road_test_realistic.glb")
BLEND_OUT = os.path.join(ROOT, "output", "road_test_realistic.blend")
TEXTURE_ROOT = os.path.join(ROOT, "assets", "textures")

TEXTURE_HINTS = {
    "asphalt": ["asphalt_track"],
    "concrete": ["patterned_concrete_pavers"],
    "curb": ["gravel_concrete_04"],
    "marking": [],
}

TEXTURE_ROLE_KEYWORDS = {
    "basecolor": ["basecolor", "base_color", "diffuse", "diff", "color", "albedo"],
    "roughness": ["roughness", "rough"],
    "normal": ["normal_gl", "nor_gl", "normal", "nor"],
    "ao": ["ao", "ambient_occlusion", "ambientocclusion"],
}

TEXTURE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff"]


def ensure_dirs():
    os.makedirs(os.path.dirname(GLB_OUT), exist_ok=True)
    for mat_dir in ["asphalt", "concrete", "curb_concrete", "road_marking"]:
        os.makedirs(os.path.join(TEXTURE_ROOT, mat_dir), exist_ok=True)


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_obj(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"OBJ file not found: {path}")

    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=path)
    else:
        bpy.ops.import_scene.obj(filepath=path)


def find_texture_file(tex_dir, mat_type, role):
    if mat_type == "marking":
        return None

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


def set_principled_input(bsdf, name, value):
    if name in bsdf.inputs:
        bsdf.inputs[name].default_value = value


def create_pbr_or_procedural_material(name, tex_dir, mat_type, scale=10.0):
    mat = bpy.data.materials.new(name=name)
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

    def load_tex(role, colorspace="sRGB"):
        path = find_texture_file(tex_dir, mat_type, role)
        if not path or not os.path.exists(path):
            return None

        img = bpy.data.images.load(path)
        img.colorspace_settings.name = colorspace
        node = nodes.new(type="ShaderNodeTexImage")
        node.image = img
        print(f"{name}: loaded {role} texture -> {path}")
        return node

    base = load_tex("basecolor", "sRGB")
    rough = load_tex("roughness", "Non-Color")
    normal = load_tex("normal", "Non-Color")
    ao = load_tex("ao", "Non-Color")

    if base or rough or normal or ao:
        print(f"[{name}] PBR textures found; applying texture material.")
        tex_coord = nodes.new(type="ShaderNodeTexCoord")
        tex_coord.location = (-1000, 0)
        mapping = nodes.new(type="ShaderNodeMapping")
        mapping.location = (-800, 0)
        mapping.inputs["Scale"].default_value = (scale, scale, scale)
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
        print(f"[{name}] No matching texture found; using procedural fallback.")
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

        presets = {
            "asphalt": {
                "scale": 50.0,
                "detail": 15.0,
                "color_a": (0.08, 0.08, 0.08, 1.0),
                "color_b": (0.15, 0.15, 0.15, 1.0),
                "roughness": 0.85,
                "base": (0.12, 0.12, 0.12, 1.0),
                "bump_strength": 0.2,
                "bump_distance": 0.01,
            },
            "concrete": {
                "scale": 30.0,
                "detail": 10.0,
                "color_a": (0.35, 0.35, 0.35, 1.0),
                "color_b": (0.45, 0.45, 0.45, 1.0),
                "roughness": 0.8,
                "base": (0.4, 0.4, 0.4, 1.0),
                "bump_strength": 0.15,
                "bump_distance": 0.02,
            },
            "curb": {
                "scale": 20.0,
                "detail": 10.0,
                "color_a": (0.5, 0.5, 0.5, 1.0),
                "color_b": (0.6, 0.6, 0.6, 1.0),
                "roughness": 0.75,
                "base": (0.55, 0.55, 0.55, 1.0),
                "bump_strength": 0.15,
                "bump_distance": 0.02,
            },
            "marking": {
                "scale": 100.0,
                "detail": 5.0,
                "color_a": (0.88, 0.88, 0.84, 1.0),
                "color_b": (1.0, 1.0, 0.95, 1.0),
                "roughness": 0.55,
                "base": (0.95, 0.95, 0.9, 1.0),
                "bump_strength": 0.05,
                "bump_distance": 0.005,
            },
        }
        preset = presets[mat_type]
        noise.inputs["Scale"].default_value = preset["scale"]
        noise.inputs["Detail"].default_value = preset["detail"]
        color_ramp.color_ramp.elements[0].position = 0.35
        color_ramp.color_ramp.elements[0].color = preset["color_a"]
        color_ramp.color_ramp.elements[1].position = 0.75
        color_ramp.color_ramp.elements[1].color = preset["color_b"]
        set_principled_input(bsdf, "Roughness", preset["roughness"])
        set_principled_input(bsdf, "Base Color", preset["base"])
        bump.inputs["Strength"].default_value = preset["bump_strength"]
        bump.inputs["Distance"].default_value = preset["bump_distance"]

    return mat


def apply_materials_to_scene():
    mat_asphalt = create_pbr_or_procedural_material(
        "MAT_Asphalt", os.path.join(TEXTURE_ROOT, "asphalt"), "asphalt", scale=6.0
    )
    mat_concrete = create_pbr_or_procedural_material(
        "MAT_Concrete", os.path.join(TEXTURE_ROOT, "concrete"), "concrete", scale=4.0
    )
    mat_curb = create_pbr_or_procedural_material(
        "MAT_Curb", os.path.join(TEXTURE_ROOT, "curb_concrete"), "curb", scale=4.0
    )
    mat_marking = create_pbr_or_procedural_material(
        "MAT_Marking", os.path.join(TEXTURE_ROOT, "road_marking"), "marking", scale=8.0
    )

    # 在循环处理对象之前，清空当前的选中状态，防止误操作其他网格
    bpy.ops.object.select_all(action="DESELECT")

    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue

        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(island_margin=0.01)
        bpy.ops.object.mode_set(mode="OBJECT")
        obj.select_set(False)

        obj.data.materials.clear()
        name_lower = obj.name.lower()
        if "road_surface" in name_lower:
            obj.data.materials.append(mat_asphalt)
        elif "sidewalk" in name_lower:
            obj.data.materials.append(mat_concrete)
        elif "curb" in name_lower:
            obj.data.materials.append(mat_curb)
        elif "lane_marking" in name_lower:
            obj.data.materials.append(mat_marking)


def main():
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

    # 额外保存一份 .blend 工程，方便直接在 Blender 中打开查看完整节点与材质效果
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_OUT)
    print(f"Blend file saved: {BLEND_OUT}")


if __name__ == "__main__":
    main()
