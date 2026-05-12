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

# 导入 Blender 的 Python API 核心库。只有在 Blender 环境下运行才有效。
import bpy


# ==========================================
# 1. 路径与全局常量配置区
# ==========================================
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJ_PATH = os.path.join(ROOT, "output", "obj", "road_test.obj")
GLB_OUT = os.path.join(ROOT, "output", "gltf", "road_test_realistic.glb")
BLEND_OUT = os.path.join(ROOT, "output", "road_test_realistic.blend")
RULE_PATH = os.path.join(ROOT, "data", "rules", "road_rules.json")
TEXTURE_ROOT = os.path.join(ROOT, "assets", "textures")

# CityEngine/CGA-like material low:
# 1. Road rules choose semantic mafterial ids for generated components.
# 2. A material library resolves each id to texture assets and fallback shader settings.
# 3. Meshes receive deterministic projected UVs in model meters, similar to setupProjection/projectUV.
# 业务映射字典：
# 将生成的 3D Mesh 的名称特征（如 "road_surface"），映射到 road_rules.json 中的具体字段（如 "material"）。
# 这样程序看到名为 "Road_Surface_0" 的模型，就知道要去查规则里的 "material" 字段来决定材质。
COMPONENT_RULE_FIELDS = {
    "road_surface": "material",
    "sidewalk": "sidewalk_material",
    "curb": "curb_material",
    "lane_marking": "marking_material",
}

# 材质物理属性库（核心）：
# 定义了各种材质的贴图搜索目录、UV 缩放比例（米）、视图颜色，以及找不到贴图时的程序化（Procedural）噪波生成参数。
MATERIAL_LIBRARY = {
    "asphalt": {
        "name": "MAT_Asphalt",            # 在 Blender 里生成的材质球名字
        "texture_type": "asphalt",        # 贴图类别（与下文 TEXTURE_HINTS 配合搜索用）
        "texture_dir": "asphalt",         # 存放贴图的相对文件夹名字（assets/textures/asphalt/）
        "projection_size_m": 8.0,         # 物理缩放比例！意思是这张贴图在现实中代表 8米 x 8米 的面积。这能保证路面贴图在模型上具有正确的真实比例，不会被拉伸或压缩。
        "viewport_color": (0.05, 0.05, 0.045, 1.0), # 在没有渲染（Solid 视图）时的默认颜色 (深灰/黑色)。
        
        # 【程序化兜底参数】
        "fallback": {
            "scale": 50.0,                # 噪波的缩放大小
            "detail": 15.0,               # 噪波的细节层级
            "color_a": (0.08, 0.08, 0.08, 1.0), # 噪波暗部颜色
            "color_b": (0.15, 0.15, 0.15, 1.0), # 噪波亮部颜色
            "roughness": 0.85,            # 表面的粗糙度（沥青反光少，数值偏大）
            "base": (0.12, 0.12, 0.12, 1.0),    # 基础色底色
            "bump_strength": 0.2,         # 凹凸强度
            "bump_distance": 0.01,        # 凹凸距离（模拟沥青表面的颗粒感）
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

# 贴图名称特征词（模糊匹配用）：
# 用于在文件夹里自动猜测哪张图片是彩色图、哪张是粗糙度图、哪张是法线图。
TEXTURE_HINTS = {
    "asphalt": ["asphalt_track", "road012a"],
    "concrete": ["patterned_concrete_pavers", "pavingstones006"],
    "curb": ["gravel_concrete_04", "concrete048"],
    "marking": ["marking", "paint", "line", "white"],
}

# PBR 标准贴图后缀匹配规则
TEXTURE_ROLE_KEYWORDS = {
    "basecolor": ["basecolor", "base_color", "diffuse", "diff", "color", "albedo"],
    "roughness": ["roughness", "rough"],
    "normal": ["normal_gl", "nor_gl", "normal", "nor"],
    "ao": ["ao", "ambient_occlusion", "ambientocclusion"],
}

# 支持读取的图片格式
TEXTURE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff"]


# ==========================================
# 2. 基础文件与场景操作函数
# ==========================================
def ensure_dirs() -> None:
    """确保贴图库的目录结构存在，方便用户后续往里面丢图片。"""
    os.makedirs(os.path.dirname(GLB_OUT), exist_ok=True)
    for mat_dir in ["asphalt", "concrete", "curb_concrete", "road_marking"]:
        os.makedirs(os.path.join(TEXTURE_ROOT, mat_dir), exist_ok=True)


def clear_scene() -> None:
    """清空 Blender 默认场景里的那个经典的“正方体”、摄像机和灯光。"""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def import_obj(path: str) -> None:
    """将上一阶段 Python 脚本生成的 OBJ 白模导入进 Blender 场景中。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"OBJ file not found: {path}")

    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=path)
    else:
        bpy.ops.import_scene.obj(filepath=path)


def load_default_rule() -> dict:
    """读取道路生成的 JSON 规则配置，拿到各种默认材质名称。"""
    if not os.path.exists(RULE_PATH):
        raise FileNotFoundError(f"Rule file not found: {RULE_PATH}")

    with open(RULE_PATH, "r", encoding="utf-8") as f:
        rules = json.load(f)

    default_rule = rules.get("default_road")
    if not isinstance(default_rule, dict):
        raise ValueError("data/rules/road_rules.json must contain a default_road object")
    return default_rule


# ==========================================
# 3. 智能贴图加载与材质构建核心
# ==========================================
def find_texture_file(tex_dir: str, mat_type: str, role: str) -> str | None:
    """
    核心的贴图搜索算法：利用“打分制”从目录里找出最合适的贴图。
    例如，需要找 asphalt（沥青）的 normal（法线）贴图。
    """
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
                
                # 必须包含角色特征词（比如图片名里必须有 normal 或 nor 等）
                if not any(keyword in stem_lower for keyword in role_keywords):
                    continue
                if mat_hints and not any(hint in path_lower for hint in mat_hints):
                    continue

                # ---------------- 打分机制 ----------------
                score = 0
                # 如果正好在指定的文件夹（如 textures/asphalt）里，加 20 分（高优）
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

    # 按分数从高到低排序，返回最匹配的一张图片的绝对路径
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][1]


def set_principled_input(bsdf, name: str, value) -> None:
    """安全地为 Blender 的 Principled BSDF（原理化 BSDF）节点设置参数，兼容不同版本 Blender。"""
    if name in bsdf.inputs:
        bsdf.inputs[name].default_value = value


def create_cityengine_material(material_id: str, definition: dict):
    """
    程序化材质生成器：针对特定的 material_id 生成 Blender 节点材质。
    如果磁盘上有贴图，就生成【贴图节点流】；如果没贴图，就生成【程序化噪波节点流】。
    """
    mat = bpy.data.materials.new(name=definition["name"])
    mat["cityengine_material_id"] = material_id
    # 将 UV 映射所需的缩放比例以自定义属性存在材质上
    mat["projection_size_m"] = definition["projection_size_m"]
    mat.diffuse_color = definition["viewport_color"]
    mat.use_nodes = True

    # 清空默认节点，手动重新连线
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new(type="ShaderNodeOutputMaterial")
    output.location = (600, 0)

    # 创建标准的 PBR 核心节点
    bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    set_principled_input(bsdf, "Metallic", 0.0)

    tex_dir = os.path.join(TEXTURE_ROOT, definition["texture_dir"])
    mat_type = definition["texture_type"]

    # 尝试加载当前材质对应的四张标准贴图
    def load_tex(role: str, colorspace: str = "sRGB"):
        path = find_texture_file(tex_dir, mat_type, role)
        if not path or not os.path.exists(path):
            return None

        # 将图片挂载进 Blender，并根据角色设定色彩空间（法线和粗糙度必须是 Non-Color）
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

    # ================= 策略 A：采用物理贴图 =================
    if base or rough or normal or ao:
        print(f"[{definition['name']}] PBR textures found; applying texture material.")
        # 建立 UV 纹理坐标输入节点
        tex_coord = nodes.new(type="ShaderNodeTexCoord")
        tex_coord.location = (-1000, 0)
        
        # 建立映射节点，用于后续调节贴图重复度
        mapping = nodes.new(type="ShaderNodeMapping")
        mapping.location = (-800, 0)
        mapping.inputs["Scale"].default_value = (1.0, 1.0, 1.0)
        links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

        if base:
            base.location = (-500, 250)
            links.new(mapping.outputs["Vector"], base.inputs["Vector"])

            # 如果有环境光遮蔽(AO)图，就用正片叠底(Multiply)把它混合到 Base Color 上增加阴影细节
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
            
    # ================= 策略 B：无贴图兜底生成 =================
    else:
        fallback = definition["fallback"]
        print(f"[{definition['name']}] No matching texture found; using procedural fallback.")
        # 使用纯数学算法（Noise Texture + ColorRamp + Bump）实时渲染出柏油路或混凝土的粗糙质感
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


# ==========================================
# 4. 几何 UV 处理与装配分配
# ==========================================
def material_id_for_object(obj_name: str, rule: dict) -> str | None:
    """通过截取模型名称（如 Curb_0）来匹配判断它属于什么构件，应该用什么材质。"""
    name_lower = obj_name.lower()
    for component_key, rule_field in COMPONENT_RULE_FIELDS.items():
        if component_key in name_lower:
            return rule.get(rule_field)
    return None


def build_materials(rule: dict) -> dict:
    """遍历配置，批量调用材质生成器，缓存到内存中备用。"""
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
    """
    CityEngine 经典的 Triplanar（三向/盒状）贴图投影算法。
    根据平面的法线朝向，自动决定使用真实世界坐标的 XY、YZ 还是 XZ 进行贴图平铺。
    """
    ax = abs(normal.x)
    ay = abs(normal.y)
    az = abs(normal.z)

    if az >= ax and az >= ay:
        u, v = point.x, point.y
    # 侧面投影处理
    elif ax >= ay:
        u, v = point.y, point.z
    else:
        u, v = point.x, point.z
    # 除以 size (米) 使得 UV 完全按物理真实尺寸映射（不再拉伸）
    return (u / size, v / size)


def assign_projected_uv(obj, projection_size_m: float) -> None:
    """
    遍历模型的每一个三角面和顶点，把刚刚算好的世界坐标投影 UV 写入底层数据。
    这是极其关键的一步，它解决了曲折道路的贴图扭曲问题。
    """
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
    """清理 OBJ 导入时产生的“垃圾”顶点色属性，防止在引擎中与纹理冲突发黑。"""
    color_attributes = getattr(obj.data, "color_attributes", None)
    if color_attributes is None:
        return

    while color_attributes:
        color_attributes.remove(color_attributes[0])


def configure_scene_for_material_review() -> None:
    """
    设置保存出的 .blend 文件的预览环境。
    切换到光线追踪引擎(Cycles)并打上一盏太阳光，确保你一打开文件就能看到非常棒的效果。
    """
    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.world.color = (0.78, 0.82, 0.86)

    if not any(obj.type == "LIGHT" for obj in bpy.data.objects):
        bpy.ops.object.light_add(type="SUN", location=(0.0, 0.0, 900.0))
        sun = bpy.context.object
        sun.name = "Material_Review_Sun"
        sun.data.energy = 2.5
        sun.rotation_euler = (0.9, 0.0, -0.6)


def apply_materials_to_scene() -> None:
    """主材质装配循环：为场景里的每个部件应用材质和计算独立 UV。"""
    rule = load_default_rule()
    materials = build_materials(rule)

    bpy.ops.object.select_all(action="DESELECT")

    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue

        # 查字典，比如 Curb_0 应该用 curb_concrete 材质
        material_id = material_id_for_object(obj.name, rule)
        if material_id is None:
            print(f"[WARN] No road component rule matched object: {obj.name}")
            continue

        mat = materials.get(material_id)
        if mat is None:
            print(f"[WARN] Material '{material_id}' was not created for object: {obj.name}")
            continue

        definition = MATERIAL_LIBRARY.get(material_id, MATERIAL_LIBRARY["asphalt"])
        
        # 重新生成 UV 并清理顶点色
        assign_projected_uv(obj, definition["projection_size_m"])
        clear_imported_vertex_colors(obj)
        obj.color = definition["viewport_color"]
        
        # 正式绑定 Blender 材质球
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        obj["cityengine_component_material"] = material_id


def main() -> None:
    """主函数执行入口。"""
    ensure_dirs()
    clear_scene()
    import_obj(OBJ_PATH)
    apply_materials_to_scene()

    # 导出包含完整材质和 UV 信息的 Khronos glTF 标准格式（被大多数前端库如 Three.js, Cesium 完美支持）
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
