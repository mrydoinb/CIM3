from __future__ import annotations

from pathlib import Path
import statistics

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OBJ_PATH = ROOT / "output" / "obj" / "modules" / "cim4" / "city_roads.obj"
OUT_DIR = ROOT / "tmp" / "pptx_replacements"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * pct))))
    return values[index]


def parse_obj() -> tuple[list[tuple[float, float, tuple[int, int, int]]], list[list[int]]]:
    vertices: list[tuple[float, float, tuple[int, int, int]]] = []
    faces: list[list[int]] = []
    for line in OBJ_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("v "):
            parts = line.split()
            x = float(parts[1])
            y = float(parts[2])
            if len(parts) >= 7:
                rgb = tuple(max(0, min(255, int(float(value) * 255))) for value in parts[4:7])
            else:
                rgb = (56, 62, 66)
            vertices.append((x, y, rgb))
        elif line.startswith("f "):
            idxs: list[int] = []
            for token in line.split()[1:]:
                raw = token.split("/")[0]
                if raw:
                    idx = int(raw)
                    idxs.append(idx - 1 if idx > 0 else len(vertices) + idx)
            if len(idxs) >= 3:
                faces.append(idxs)
    return vertices, faces


def make_image(path: Path, size: tuple[int, int], crop_pct: tuple[float, float] = (0.004, 0.996)) -> None:
    vertices, faces = parse_obj()
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    min_x, max_x = percentile(xs, crop_pct[0]), percentile(xs, crop_pct[1])
    min_y, max_y = percentile(ys, crop_pct[0]), percentile(ys, crop_pct[1])
    width, height = size
    margin = 58
    scale = min((width - margin * 2) / max(1.0, max_x - min_x), (height - margin * 2) / max(1.0, max_y - min_y))
    canvas = Image.new("RGB", size, (12, 17, 22))
    draw = ImageDraw.Draw(canvas)

    def project(index: int) -> tuple[int, int]:
        x, y, _ = vertices[index]
        px = margin + (x - min_x) * scale
        py = height - margin - (y - min_y) * scale
        return int(px), int(py)

    for face in faces:
        pts = [project(index) for index in face]
        if all((p[0] < -200 or p[0] > width + 200 or p[1] < -200 or p[1] > height + 200) for p in pts):
            continue
        colors = [vertices[index][2] for index in face]
        avg = tuple(int(statistics.fmean(channel)) for channel in zip(*colors))
        fill = tuple(min(235, int(value * 1.35 + 35)) for value in avg)
        draw.polygon(pts, fill=fill)

    for face in faces[::2]:
        pts = [project(index) for index in face]
        if len(pts) >= 2:
            draw.line(pts + [pts[0]], fill=(115, 142, 153), width=1)

    draw.rectangle((margin, margin, width - margin, height - margin), outline=(205, 217, 226), width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, quality=94)
    print(path)


make_image(OUT_DIR / "road_obj_plan.png", (2000, 1125))
make_image(OUT_DIR / "road_obj_plan_wide.png", (1892, 820), (0.01, 0.99))
