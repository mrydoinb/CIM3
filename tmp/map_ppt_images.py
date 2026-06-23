from pathlib import Path
from zipfile import ZipFile
import sys
import xml.etree.ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def find_target() -> Path:
    downloads = Path.home() / "Downloads"
    candidates = [
        path
        for path in downloads.glob("*.pptx")
        if not path.name.startswith("~$")
        and "CIM" in path.name
        and path.name.endswith("11.pptx")
    ]
    if not candidates:
        raise SystemExit("No matching PPTX found in Downloads.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def rel_targets(zip_file: ZipFile, slide_number: int) -> dict[str, str]:
    rel_name = f"ppt/slides/_rels/slide{slide_number}.xml.rels"
    root = ET.fromstring(zip_file.read(rel_name))
    out = {}
    for rel in root.findall("rel:Relationship", NS):
        out[rel.attrib["Id"]] = rel.attrib["Target"]
    return out


def pic_names(zip_file: ZipFile, slide_number: int) -> list[tuple[str, str]]:
    slide_name = f"ppt/slides/slide{slide_number}.xml"
    root = ET.fromstring(zip_file.read(slide_name))
    out = []
    for pic in root.findall(".//p:pic", NS):
        name = ""
        c_nv_pr = pic.find(".//p:cNvPr", NS)
        if c_nv_pr is not None:
            name = c_nv_pr.attrib.get("name", "")
        blip = pic.find(".//a:blip", NS)
        rid = ""
        if blip is not None:
            rid = blip.attrib.get(f"{{{NS['r']}}}embed", "")
        out.append((name, rid))
    return out


sys.stdout.reconfigure(encoding="utf-8")
path = find_target()
print(f"target={path}")
with ZipFile(path) as zf:
    for slide_number in (5, 8):
        rels = rel_targets(zf, slide_number)
        print(f"slide {slide_number}")
        for index, (name, rid) in enumerate(pic_names(zf, slide_number), 1):
            target = rels.get(rid, "")
            media = str((Path(f"ppt/slides/slide{slide_number}.xml").parent / target).resolve())
            if target.startswith("../"):
                media = "ppt/" + target[3:]
            print(f"  pic{index}: {name} rid={rid} target={media}")
