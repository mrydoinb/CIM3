from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "tmp" / "pptx_replacements"
ROAD_SEMANTIC = ROOT / "output" / "semantic" / "cim4" / "city_roads_semantic.json"
JUNCTION_SEMANTIC = ROOT / "output" / "semantic" / "cim4" / "city_junctions_semantic.json"

COMPONENT_LABELS = {
    "sidewalk": "人行道",
    "facility_belt": "设施带",
    "green_belt": "绿化带",
    "non_motor_lane": "非机动车道",
    "service_lane": "辅道",
    "side_divider": "侧分带",
    "main_carriageway": "主车行道",
    "carriageway": "车行道",
    "median": "中央分隔带",
    "parking_lane": "停车带",
}

JUNCTION_TYPE_LABELS = {
    "CROSS_JUNCTION": "十字路口",
    "T_JUNCTION": "T 型路口",
    "RAMP_MERGE": "匝道并入",
    "MULTI_ARM_JUNCTION": "多臂路口",
    "SKEWED_CROSS_OR_MULTI_ARM": "斜交/多臂",
    "Y_JUNCTION": "Y 型路口",
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def load_data() -> tuple[list[dict], list[dict]]:
    roads = json.loads(ROAD_SEMANTIC.read_text(encoding="utf-8")).get("objects", [])
    junction_doc = json.loads(JUNCTION_SEMANTIC.read_text(encoding="utf-8"))
    junctions = junction_doc.get("objects", junction_doc.get("junctions", []))
    return roads, junctions


def round_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 1) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def draw_text(draw: ImageDraw.ImageDraw, xy, text: str, size: int, color, bold: bool = False, anchor: str | None = None) -> None:
    draw.text(xy, text, font=font(size, bold), fill=color, anchor=anchor)


def draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color=(210, 214, 220), width: int = 3) -> None:
    draw.line([start, end], fill=color, width=width)
    x1, y1 = start
    x2, y2 = end
    angle = 0 if x2 >= x1 else 3.14159
    size = 12
    points = [(x2, y2), (x2 - size, y2 - 7), (x2 - size, y2 + 7)] if angle == 0 else [(x2, y2), (x2 + size, y2 - 7), (x2 + size, y2 + 7)]
    draw.polygon(points, fill=color)


def draw_flow(draw: ImageDraw.ImageDraw, w: int, y: int, box_h: int, scale: float) -> None:
    margin = int(118 * scale)
    gap = int(28 * scale)
    box_w = int((w - 2 * margin - 3 * gap) / 4)
    labels = [
        ("源数据", "道路中心线 / 属性字段"),
        ("分类规则", "等级、断面、宽度归一"),
        ("构件语义", "车行道 / 绿带 / 人行道"),
        ("交付成果", "OBJ / FBX / semantic JSON"),
    ]
    for i, (title, desc) in enumerate(labels):
        x = margin + i * (box_w + gap)
        fill = (24, 28, 36) if i % 2 == 0 else (32, 36, 45)
        round_rect(draw, (x, y, x + box_w, y + box_h), int(14 * scale), fill, (83, 91, 108), 2)
        draw_text(draw, (x + int(22 * scale), y + int(28 * scale)), title, int(25 * scale), (255, 255, 255), True)
        draw_text(draw, (x + int(22 * scale), y + int(68 * scale)), desc, int(17 * scale), (189, 198, 212))
        if i < 3:
            draw_arrow(draw, (x + box_w + int(6 * scale), y + box_h // 2), (x + box_w + gap - int(6 * scale), y + box_h // 2), (229, 54, 54), max(2, int(3 * scale)))


def draw_counter_panel(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, title: str, items: Iterable[tuple[str, int]], scale: float) -> None:
    round_rect(draw, (x, y, x + w, y + h), int(12 * scale), (17, 20, 27), (80, 86, 100), 2)
    draw_text(draw, (x + int(18 * scale), y + int(28 * scale)), title, int(21 * scale), (255, 255, 255), True)
    max_value = max((value for _, value in items), default=1)
    bar_x = x + int(20 * scale)
    bar_y = y + int(62 * scale)
    bar_w = w - int(40 * scale)
    row_h = int(34 * scale)
    for index, (name, value) in enumerate(items):
        yy = bar_y + index * row_h
        draw_text(draw, (bar_x, yy), str(name), int(15 * scale), (212, 218, 230))
        filled = int((bar_w - int(98 * scale)) * value / max_value)
        bx = bar_x + int(95 * scale)
        by = yy + int(4 * scale)
        round_rect(draw, (bx, by, bx + bar_w - int(98 * scale), by + int(12 * scale)), int(6 * scale), (35, 39, 49))
        round_rect(draw, (bx, by, bx + filled, by + int(12 * scale)), int(6 * scale), (232, 49, 49))
        draw_text(draw, (x + w - int(20 * scale), yy), str(value), int(15 * scale), (236, 240, 247), False, "ra")


def draw_component_strip(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, components: list[dict], scale: float) -> None:
    total_width = sum(float(item.get("width", 0) or 0) for item in components) or 1.0
    colors = [(211, 208, 199), (224, 76, 185), (92, 174, 91), (96, 160, 154), (190, 194, 199), (116, 192, 88), (232, 232, 232), (117, 186, 90)]
    cursor = x
    strip_h = int(46 * scale)
    for index, component in enumerate(components):
        ctype = str(component.get("type", ""))
        width_m = float(component.get("width", 0) or 0)
        seg_w = max(int(w * width_m / total_width), int(18 * scale))
        fill = colors[index % len(colors)]
        draw.rectangle((cursor, y, min(x + w, cursor + seg_w), y + strip_h), fill=fill)
        cursor += seg_w
        if cursor >= x + w:
            break
    draw.rectangle((x, y, x + w, y + strip_h), outline=(238, 240, 245), width=2)
    cursor = x
    label_y = y + strip_h + int(12 * scale)
    for index, component in enumerate(components[:8]):
        ctype = str(component.get("type", ""))
        width_m = float(component.get("width", 0) or 0)
        label = COMPONENT_LABELS.get(ctype, ctype)
        draw_text(draw, (cursor, label_y + index * int(25 * scale)), f"{label} {width_m:g}m", int(14 * scale), (211, 218, 230))
        if index == 3:
            cursor += int(210 * scale)
            label_y = y + strip_h + int(12 * scale) - 4 * int(25 * scale)


def make_image(path: Path, size: tuple[int, int]) -> None:
    roads, junctions = load_data()
    w, h = size
    scale = min(w / 2000.0, h / 1125.0)
    img = Image.new("RGB", size, (9, 11, 16))
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, int(74 * scale), h), fill=(181, 24, 31))
    for i, word in enumerate(["规则", "语义", "结果"]):
        draw_text(draw, (int(37 * scale), int((110 + i * 130) * scale)), word, int(25 * scale), (255, 255, 255), True, "mm")

    draw_text(draw, (int(118 * scale), int(70 * scale)), "CIM 道路规则语义映射", int(42 * scale), (255, 255, 255), True)
    draw_text(draw, (int(120 * scale), int(120 * scale)), "由当前 cim4 结果生成：道路构件、路口拓扑、交付语义保持同源", int(21 * scale), (178, 188, 205))

    draw_flow(draw, w, int(170 * scale), int(120 * scale), scale)

    road_class = Counter(str(item.get("road_class", "unknown")) for item in roads).most_common(5)
    sections = Counter(str(item.get("modeled_section_code", "unknown")) for item in roads).most_common(7)
    junction_types = Counter(
        JUNCTION_TYPE_LABELS.get(str(item.get("junction_type", "unknown")), str(item.get("junction_type", "unknown")))
        for item in junctions
    ).most_common(5)

    panel_y = int(350 * scale)
    panel_w = int(420 * scale)
    panel_h = int(250 * scale)
    draw_counter_panel(draw, int(118 * scale), panel_y, panel_w, panel_h, "道路等级统计", road_class, scale)
    draw_counter_panel(draw, int(570 * scale), panel_y, panel_w, panel_h, "断面模板统计", sections, scale)
    draw_counter_panel(draw, int(1022 * scale), panel_y, panel_w, panel_h, "路口类型统计", junction_types, scale)

    summary_x = int(1475 * scale)
    summary_y = panel_y
    summary_w = w - summary_x - int(80 * scale)
    round_rect(draw, (summary_x, summary_y, summary_x + summary_w, summary_y + panel_h), int(12 * scale), (17, 20, 27), (80, 86, 100), 2)
    stats = [("道路对象", len(roads)), ("路口对象", len(junctions)), ("断面模板", len(set(item.get("modeled_section_code") for item in roads))), ("语义层级", 4)]
    for index, (label, value) in enumerate(stats):
        sx = summary_x + int(30 * scale) + (index % 2) * int(170 * scale)
        sy = summary_y + int(48 * scale) + (index // 2) * int(94 * scale)
        draw_text(draw, (sx, sy), str(value), int(36 * scale), (255, 255, 255), True)
        draw_text(draw, (sx, sy + int(44 * scale)), label, int(17 * scale), (186, 196, 210))

    sample = next((item for item in roads if item.get("modeled_cross_section_components")), roads[0])
    components = sample.get("modeled_cross_section_components", [])
    bottom_y = int(660 * scale)
    round_rect(draw, (int(118 * scale), bottom_y, w - int(80 * scale), h - int(70 * scale)), int(16 * scale), (17, 20, 27), (80, 86, 100), 2)
    draw_text(draw, (int(145 * scale), bottom_y + int(44 * scale)), "断面构件语义示例", int(25 * scale), (255, 255, 255), True)
    draw_text(draw, (int(145 * scale), bottom_y + int(82 * scale)), f"模板 {sample.get('modeled_section_code')}，总宽 {sample.get('modeled_width_m')} m", int(18 * scale), (184, 194, 208))
    draw_component_strip(draw, int(145 * scale), bottom_y + int(118 * scale), int(720 * scale), components, scale)

    steps = [
        "属性字段标准化",
        "道路等级识别",
        "断面模板匹配",
        "路口拓扑归并",
        "构件 Mesh 赋语义",
        "OBJ / FBX / JSON 输出",
    ]
    chip_x = int(930 * scale)
    chip_y = bottom_y + int(50 * scale)
    chip_w = int(250 * scale)
    chip_h = int(42 * scale)
    for index, step in enumerate(steps):
        x = chip_x + (index % 2) * int(300 * scale)
        y = chip_y + (index // 2) * int(72 * scale)
        round_rect(draw, (x, y, x + chip_w, y + chip_h), int(20 * scale), (35, 39, 49), (229, 54, 54), 2)
        draw_text(draw, (x + chip_w // 2, y + int(27 * scale)), step, int(17 * scale), (236, 240, 247), False, "mm")

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=95)
    print(path)


OUT_DIR.mkdir(parents=True, exist_ok=True)
make_image(OUT_DIR / "rule_semantics_16x9.png", (2000, 1125))
make_image(OUT_DIR / "rule_semantics_wide.png", (1892, 820))
