#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
用 Blender 将 OBJ 转为 FBX。

运行方式：
blender --background --python scripts/02_export_fbx_blender.py

输入：
- output/obj/road_test.obj

输出：
- output/fbx/road_test.fbx
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import bpy


ROOT = Path(__file__).resolve().parents[1]
OBJ_PATH = ROOT / "output" / "obj" / "road_test.obj"
FBX_PATH = ROOT / "output" / "fbx" / "road_test.fbx"
MATERIAL_SCRIPT = ROOT / "scripts" / "03_apply_materials_blender.py"


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def import_obj(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"未找到 OBJ 文件：{path}")

    # Blender 4.x 推荐 wm.obj_import；老版本使用 import_scene.obj
    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        bpy.ops.import_scene.obj(filepath=str(path))


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
    )


def apply_materials() -> None:
    spec = importlib.util.spec_from_file_location("road_materials", MATERIAL_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load material script: {MATERIAL_SCRIPT}")

    material_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(material_module)
    material_module.ensure_dirs()
    material_module.apply_materials_to_scene()


def main() -> None:
    clear_scene()
    import_obj(OBJ_PATH)
    apply_materials()

    # 设置单位为米
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0

    export_fbx(FBX_PATH)
    print(f"FBX 导出完成：{FBX_PATH}")


if __name__ == "__main__":
    main()
