from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile
import sys


sys.stdout.reconfigure(encoding="utf-8")
paths = [
    path
    for path in (Path.home() / "Downloads").glob("*.pptx")
    if "CIM" in path.name and "截图替换" in path.stem and not path.name.startswith("~$")
]
for path in sorted(paths, key=lambda item: item.name):
    print(f"PPT={path.name} size={path.stat().st_size}")
    with ZipFile(path) as zf:
        for slide_number in (5, 8, 12):
            xml = zf.read(f"ppt/slides/slide{slide_number}.xml").decode("utf-8")
            print(
                f"  slide{slide_number}: "
                f"pic_tags={xml.count('<p:pic')} blip_tags={xml.count('<a:blip')}"
            )
