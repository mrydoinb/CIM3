from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import sys


ROOT = Path(__file__).resolve().parents[1]
REPL = ROOT / "tmp" / "pptx_replacements"


def find_target() -> Path:
    downloads = Path.home() / "Downloads"
    candidates = [
        path
        for path in downloads.glob("*.pptx")
        if not path.name.startswith("~$")
        and "CIM" in path.name
        and path.name.endswith("11.pptx")
        and "截图替换" not in path.stem
    ]
    if not candidates:
        raise SystemExit("No matching PPTX found in Downloads.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


MAPPING = {
    "ppt/media/image7.png": REPL / "subway_perspective.png",
    "ppt/media/image8.png": REPL / "road_obj_plan.png",
    "ppt/media/image9.png": REPL / "junction_j0038.png",
    "ppt/media/image10.png": REPL / "junction_j0006.png",
    "ppt/media/image11.png": REPL / "rule_semantics_16x9.png",
    "ppt/media/image12.png": REPL / "junction_j0006.png",
    "ppt/media/image13.png": REPL / "subway_perspective.png",
    "ppt/media/image14.png": REPL / "road_obj_plan.png",
    "ppt/media/image15.png": REPL / "rule_semantics_wide.png",
    "ppt/media/image16.png": REPL / "subway_perspective.png",
}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    src = find_target()
    out = src.with_name(f"{src.stem}_截图替换.pptx")
    replacements = {name: path.read_bytes() for name, path in MAPPING.items()}

    with ZipFile(src, "r") as zin, ZipFile(out, "w", ZIP_DEFLATED) as zout:
        existing = set(zin.namelist())
        missing = sorted(set(replacements) - existing)
        if missing:
            raise SystemExit(f"Missing PPT media entries: {missing}")
        for info in zin.infolist():
            data = replacements.get(info.filename)
            if data is None:
                data = zin.read(info.filename)
            zout.writestr(info, data)

    print(f"source={src}")
    print(f"output={out}")
    for media_name, repl_path in MAPPING.items():
        print(f"{media_name} <= {repl_path.name}")


if __name__ == "__main__":
    main()
