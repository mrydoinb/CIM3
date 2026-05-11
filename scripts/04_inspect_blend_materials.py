#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import os

import bpy


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLEND_PATH = os.path.join(ROOT, "output", "road_test_realistic.blend")


def main() -> None:
    bpy.ops.wm.open_mainfile(filepath=BLEND_PATH)

    counts = {}
    missing = []
    no_uv = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue

        mat_name = obj.data.materials[0].name if obj.data.materials else "<none>"
        counts[mat_name] = counts.get(mat_name, 0) + 1
        if not obj.data.materials:
            missing.append(obj.name)
        if not obj.data.uv_layers:
            no_uv.append(obj.name)

    print("material_counts:", counts)
    print("missing_material_objects:", missing[:20], "count=", len(missing))
    print("objects_without_uv:", no_uv[:20], "count=", len(no_uv))

    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        images = []
        for node in mat.node_tree.nodes:
            if node.bl_idname == "ShaderNodeTexImage" and node.image:
                images.append(bpy.path.abspath(node.image.filepath))
        print("material:", mat.name, "images:", images)


if __name__ == "__main__":
    main()
