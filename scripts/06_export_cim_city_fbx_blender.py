#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Thin wrapper for :mod:`blender.fbx_export`."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from blender.fbx_export import main


if __name__ == "__main__":
    main()
