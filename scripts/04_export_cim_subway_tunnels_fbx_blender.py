#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""将规则生成的 CIM 地铁隧道 OBJ 转换为 FBX，并挂接语义属性。"""

from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from blender.fbx_export import MODULE_FBX_DIR, MODULE_OBJ_DIR, export_obj_to_fbx


def subway_level() -> str:
    level = str(os.environ.get("CIM_SUBWAY_LEVEL", "cim4")).strip().lower()
    return level if level == "cim4" else "cim4"


def main() -> None:
    level = subway_level()
    obj_path = MODULE_OBJ_DIR / level / "subway_tunnels.obj"
    fbx_path = MODULE_FBX_DIR / level / "subway_tunnels.fbx"
    export_obj_to_fbx(obj_path, fbx_path)


if __name__ == "__main__":
    main()
