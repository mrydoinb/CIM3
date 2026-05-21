#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Render road cross-section schematic SVGs from city road semantic JSON."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEMANTIC = ROOT / "output" / "semantic" / "cim_city_roads_semantic.json"
DEFAULT_OUT_DIR = ROOT / "output" / "render" / "cross_sections"

TYPE_LABELS = {
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

TYPE_COLORS = {
    "sidewalk": "#d8d4c8",
    "facility_belt": "#f05ad6",
    "green_belt": "#68b957",
    "non_motor_lane": "#6ca7a0",
    "service_lane": "#c9c9c9",
    "side_divider": "#8ed06d",
    "main_carriageway": "#efefef",
    "carriageway": "#efefef",
    "median": "#7fc35f",
    "parking_lane": "#bfc3c0",
}


def fmt_number(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def svg_text(x: float, y: float, text: str, size: int = 12, anchor: str = "middle", color: str = "#222") -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
        f'font-family="Arial, Microsoft YaHei, sans-serif" font-size="{size}" fill="{color}">'
        f"{html.escape(text)}</text>"
    )


def line(x1: float, y1: float, x2: float, y2: float, stroke: str = "#111", width: float = 1.0, dash: str | None = None) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{stroke}" stroke-width="{width}"{dash_attr}/>'


def rect(x: float, y: float, w: float, h: float, fill: str, stroke: str = "#333", width: float = 0.7) -> str:
    return f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'


def tree_symbol(cx: float, ground_y: float, scale: float = 1.0) -> str:
    trunk_w = 3.0 * scale
    trunk_h = 13.0 * scale
    crown_r = 11.0 * scale
    return "\n".join(
        [
            rect(cx - trunk_w / 2, ground_y - trunk_h, trunk_w, trunk_h, "#8b5a2b", "none", 0),
            f'<circle cx="{cx:.2f}" cy="{ground_y - trunk_h - crown_r * 0.45:.2f}" r="{crown_r:.2f}" fill="#20c640" stroke="#0b8b27" stroke-width="1"/>',
            f'<circle cx="{cx - crown_r * 0.55:.2f}" cy="{ground_y - trunk_h - crown_r * 0.1:.2f}" r="{crown_r * 0.72:.2f}" fill="#43d85a" stroke="#0b8b27" stroke-width="1"/>',
            f'<circle cx="{cx + crown_r * 0.55:.2f}" cy="{ground_y - trunk_h - crown_r * 0.1:.2f}" r="{crown_r * 0.72:.2f}" fill="#31cf4c" stroke="#0b8b27" stroke-width="1"/>',
        ]
    )


def lamp_symbol(cx: float, ground_y: float, scale: float = 1.0) -> str:
    h = 58.0 * scale
    arm = 20.0 * scale
    return "\n".join(
        [
            line(cx, ground_y, cx, ground_y - h, "#555", 2.0 * scale),
            line(cx, ground_y - h, cx + arm, ground_y - h - 7.0 * scale, "#555", 1.5 * scale),
            f'<path d="M {cx + arm:.2f} {ground_y - h - 7.0 * scale:.2f} l 10 {-2.0 * scale:.2f} l -7 {5.0 * scale:.2f} z" fill="#555"/>',
        ]
    )


def car_symbol(cx: float, ground_y: float, scale: float = 1.0, color: str = "#376dff") -> str:
    w = 22.0 * scale
    h = 10.0 * scale
    return "\n".join(
        [
            f'<rect x="{cx - w / 2:.2f}" y="{ground_y - h - 4.0 * scale:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{2.0 * scale:.2f}" fill="{color}" stroke="#1536aa" stroke-width="1"/>',
            f'<path d="M {cx - w * 0.28:.2f} {ground_y - h - 4.0 * scale:.2f} l {w * 0.18:.2f} {-6.0 * scale:.2f} h {w * 0.22:.2f} l {w * 0.18:.2f} {6.0 * scale:.2f} z" fill="#8fb1ff" stroke="#1536aa" stroke-width="1"/>',
            f'<circle cx="{cx - w * 0.32:.2f}" cy="{ground_y - 2.5 * scale:.2f}" r="{2.7 * scale:.2f}" fill="#222"/>',
            f'<circle cx="{cx + w * 0.32:.2f}" cy="{ground_y - 2.5 * scale:.2f}" r="{2.7 * scale:.2f}" fill="#222"/>',
        ]
    )


def bus_symbol(cx: float, ground_y: float, scale: float = 1.0) -> str:
    w = 27.0 * scale
    h = 15.0 * scale
    return "\n".join(
        [
            f'<rect x="{cx - w / 2:.2f}" y="{ground_y - h - 4.0 * scale:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{2.0 * scale:.2f}" fill="#376dff" stroke="#1536aa" stroke-width="1"/>',
            f'<rect x="{cx - w * 0.36:.2f}" y="{ground_y - h - 1.0 * scale:.2f}" width="{w * 0.22:.2f}" height="{h * 0.35:.2f}" fill="#cfe0ff"/>',
            f'<rect x="{cx - w * 0.08:.2f}" y="{ground_y - h - 1.0 * scale:.2f}" width="{w * 0.22:.2f}" height="{h * 0.35:.2f}" fill="#cfe0ff"/>',
            f'<rect x="{cx + w * 0.20:.2f}" y="{ground_y - h - 1.0 * scale:.2f}" width="{w * 0.22:.2f}" height="{h * 0.35:.2f}" fill="#cfe0ff"/>',
            f'<circle cx="{cx - w * 0.32:.2f}" cy="{ground_y - 2.5 * scale:.2f}" r="{3.0 * scale:.2f}" fill="#222"/>',
            f'<circle cx="{cx + w * 0.32:.2f}" cy="{ground_y - 2.5 * scale:.2f}" r="{3.0 * scale:.2f}" fill="#222"/>',
        ]
    )


def pedestrian_symbol(cx: float, ground_y: float, scale: float = 1.0) -> str:
    head_r = 3.2 * scale
    body_top = ground_y - 22.0 * scale
    body_bottom = ground_y - 8.0 * scale
    return "\n".join(
        [
            f'<circle cx="{cx:.2f}" cy="{body_top - head_r:.2f}" r="{head_r:.2f}" fill="#333"/>',
            line(cx, body_top, cx, body_bottom, "#333", 1.7 * scale),
            line(cx, body_top + 4.0 * scale, cx - 6.0 * scale, body_top + 10.0 * scale, "#333", 1.5 * scale),
            line(cx, body_top + 4.0 * scale, cx + 6.0 * scale, body_top + 10.0 * scale, "#333", 1.5 * scale),
            line(cx, body_bottom, cx - 5.0 * scale, ground_y, "#333", 1.5 * scale),
            line(cx, body_bottom, cx + 5.0 * scale, ground_y, "#333", 1.5 * scale),
        ]
    )


def bicycle_symbol(cx: float, ground_y: float, scale: float = 1.0) -> str:
    r = 5.0 * scale
    y = ground_y - 5.0 * scale
    return "\n".join(
        [
            f'<circle cx="{cx - 8.0 * scale:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="none" stroke="#224de8" stroke-width="1.4"/>',
            f'<circle cx="{cx + 8.0 * scale:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="none" stroke="#224de8" stroke-width="1.4"/>',
            f'<path d="M {cx - 8.0 * scale:.2f} {y:.2f} L {cx:.2f} {y - 8.0 * scale:.2f} L {cx + 8.0 * scale:.2f} {y:.2f} L {cx - 1.0 * scale:.2f} {y:.2f} Z" fill="none" stroke="#224de8" stroke-width="1.3"/>',
            line(cx, y - 8.0 * scale, cx + 3.0 * scale, y - 16.0 * scale, "#224de8", 1.3 * scale),
        ]
    )


def symbols_for_component(component_type: str, x0: float, x1: float, ground_y: float) -> list[str]:
    width_px = x1 - x0
    cx = (x0 + x1) * 0.5
    symbols: list[str] = []
    if component_type == "sidewalk":
        if width_px > 24:
            symbols.append(pedestrian_symbol(cx - min(14, width_px * 0.18), ground_y, 0.9))
            symbols.append(pedestrian_symbol(cx + min(14, width_px * 0.18), ground_y, 0.75))
    elif component_type in {"facility_belt"}:
        symbols.append(lamp_symbol(cx, ground_y, 0.8))
    elif component_type in {"green_belt", "median", "side_divider"}:
        if width_px > 18:
            symbols.append(tree_symbol(cx, ground_y, 0.75 if component_type != "median" else 0.55))
    elif component_type == "non_motor_lane":
        symbols.append(bicycle_symbol(cx, ground_y, 0.9))
    elif component_type == "service_lane":
        if width_px > 60:
            symbols.append(bus_symbol(cx - width_px * 0.18, ground_y, 0.9))
            symbols.append(car_symbol(cx + width_px * 0.18, ground_y, 0.85))
        else:
            symbols.append(car_symbol(cx, ground_y, 0.85))
    elif component_type in {"main_carriageway", "carriageway"}:
        count = max(1, min(3, int(width_px // 30)))
        if count == 1:
            xs = [cx]
        else:
            step = min(36.0, width_px / (count + 1))
            xs = [cx + (i - (count - 1) / 2) * step for i in range(count)]
        for index, x in enumerate(xs):
            symbols.append(car_symbol(x, ground_y, 0.9, "#376dff" if index % 2 == 0 else "#4e72e6"))
    return symbols


def pick_records(objects: list[dict[str, Any]], basis: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    code_key = f"{basis}_section_code"
    components_key = f"{basis}_cross_section_components"
    for record in objects:
        code = str(record.get(code_key) or "unknown")
        components = record.get(components_key) or []
        if not components or code in seen:
            continue
        seen.add(code)
        records.append(record)
    return sorted(records, key=lambda item: str(item.get(code_key) or "unknown"))


def render_svg(record: dict[str, Any], basis: str) -> str:
    components_key = f"{basis}_cross_section_components"
    code_key = f"{basis}_section_code"
    components = record.get(components_key) or []
    total_width = sum(float(item.get("width", 0.0) or 0.0) for item in components)
    if total_width <= 0:
        raise ValueError("Cross-section width is zero.")

    px_per_m = 11.0 if total_width <= 80 else max(5.5, 850.0 / total_width)
    margin_x = 48.0
    section_w = total_width * px_per_m
    svg_w = section_w + margin_x * 2
    svg_h = 260.0
    title_y = 28.0
    ground_y = 150.0
    band_y = ground_y - 9.0
    band_h = 10.0
    dim_y = 193.0
    total_dim_y = 226.0
    center_x = margin_x + section_w / 2.0

    code = str(record.get(code_key) or "unknown")
    road_name = str(record.get("road_name") or "")
    title = f"{basis.upper()} 横断面 {code}"
    if road_name and road_name != code:
        title += f" / {road_name}"

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w:.0f}" height="{svg_h:.0f}" viewBox="0 0 {svg_w:.0f} {svg_h:.0f}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        svg_text(svg_w / 2, title_y, title, 17, "middle", "#111"),
        svg_text(svg_w / 2, title_y + 22, f"总宽 {fmt_number(total_width)} m", 12, "middle", "#555"),
        line(margin_x, ground_y, margin_x + section_w, ground_y, "#111", 1.3),
        line(center_x, 54, center_x, ground_y + 18, "#999", 1.0, "7 6"),
    ]

    cursor = margin_x
    for component in components:
        ctype = str(component.get("type", "unknown"))
        width_m = float(component.get("width", 0.0) or 0.0)
        w = width_m * px_per_m
        x0 = cursor
        x1 = cursor + w
        fill = TYPE_COLORS.get(ctype, "#eeeeee")
        label = TYPE_LABELS.get(ctype, ctype)

        parts.append(rect(x0, band_y, w, band_h, fill, "#555", 0.5))
        parts.append(line(x0, ground_y - 4, x0, dim_y + 12, "#222", 0.8))
        if w >= 30:
            parts.append(svg_text((x0 + x1) / 2, dim_y, fmt_number(width_m), 11))
        else:
            parts.append(svg_text((x0 + x1) / 2, dim_y, fmt_number(width_m), 9))
        if w >= 46:
            parts.append(svg_text((x0 + x1) / 2, band_y - 8, label, 10, "middle", "#333"))
        parts.extend(symbols_for_component(ctype, x0, x1, ground_y))
        cursor = x1

    parts.append(line(margin_x + section_w, ground_y - 4, margin_x + section_w, dim_y + 12, "#222", 0.8))
    parts.append(line(margin_x, dim_y + 10, margin_x + section_w, dim_y + 10, "#222", 0.8))
    parts.append(line(margin_x, total_dim_y, margin_x + section_w, total_dim_y, "#111", 1.0))
    parts.append(line(margin_x, total_dim_y - 6, margin_x, total_dim_y + 6, "#111", 1.0))
    parts.append(line(margin_x + section_w, total_dim_y - 6, margin_x + section_w, total_dim_y + 6, "#111", 1.0))
    parts.append(svg_text(svg_w / 2, total_dim_y + 18, fmt_number(total_width), 13, "middle", "#111"))
    parts.append("</svg>")
    return "\n".join(parts)


def write_index(out_dir: Path, outputs: list[Path]) -> None:
    links = "\n".join(
        f'<li><a href="{html.escape(path.name)}">{html.escape(path.stem)}</a></li>' for path in outputs
    )
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>CIM road cross sections</title>
  <style>
    body {{ font-family: Arial, "Microsoft YaHei", sans-serif; margin: 24px; color: #222; }}
    li {{ margin: 8px 0; }}
    a {{ color: #1457b8; }}
  </style>
</head>
<body>
  <h1>CIM 道路横断面示意图</h1>
  <p>这些 SVG 由 <code>output/semantic/cim_city_roads_semantic.json</code> 生成。</p>
  <ul>
    {links}
  </ul>
</body>
</html>
"""
    (out_dir / "index.html").write_text(html_doc, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic", type=Path, default=DEFAULT_SEMANTIC, help="Road semantic JSON path.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory.")
    parser.add_argument("--basis", choices=["modeled", "source"], default="modeled", help="Which cross-section components to draw.")
    parser.add_argument("--section", help="Only render one section code, for example B2.")
    args = parser.parse_args()

    data = json.loads(args.semantic.read_text(encoding="utf-8"))
    objects = data.get("objects", [])
    records = pick_records(objects, args.basis)
    if args.section:
        wanted = args.section.upper()
        records = [record for record in records if str(record.get(f"{args.basis}_section_code") or "").upper() == wanted]
    if not records:
        raise SystemExit("No matching cross-section records found.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for record in records:
        code = str(record.get(f"{args.basis}_section_code") or "unknown")
        path = args.out_dir / f"{args.basis}_{code}.svg"
        path.write_text(render_svg(record, args.basis), encoding="utf-8")
        outputs.append(path)
        print(f"Rendered {path}")
    write_index(args.out_dir, outputs)
    print(f"Index: {args.out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
