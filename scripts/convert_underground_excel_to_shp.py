from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd


SOURCE_DIR = Path(r"C:\Users\22838\Desktop\chk\地下管线")

WORKBOOKS = [
    {
        "path": SOURCE_DIR / "污水管线.xlsx",
        "prefix": "ws",
        "sheets": {
            "污水井信息": ("point", "ws_wells"),
            "污水圆管信息": ("line", "ws_pipes"),
        },
    },
    {
        "path": SOURCE_DIR / "污水管线-02.xlsx",
        "prefix": "sys02",
        "sheets": {
            "污水管井信息": ("point", "sys02_sw_wells"),
            "污水管圆管信息": ("line", "sys02_sw_pipes"),
            "给水管圆管信息": ("line", "sys02_gs_pipes"),
            "雨水管井信息": ("point", "sys02_ys_wells"),
            "雨水管圆管信息": ("line", "sys02_ys_pipes"),
            "雨水管方涵信息": ("line", "sys02_ys_box"),
        },
    },
]


FIELD_MAP = {
    "图元ID": "elem_id",
    "管道类别": "sys_type",
    "节点类别": "node_cat",
    "节点类型": "node_type",
    "节点规格": "node_spec",
    "节点角度(°)": "angle",
    "节点自然标高(m)": "nat_z",
    "节点设计标高(m)": "design_z",
    "深度(m)": "depth",
    "长度(mm)": "len_mm",
    "宽度(mm)": "wid_mm",
    "特征标高类型": "z_type",
    "起点特征标高(m)": "start_z",
    "终点特征标高(m)": "end_z",
    "公称直径(mm)": "diam_mm",
    "壁厚(mm)": "wall_mm",
    "管道长度(m)": "pipe_len",
    "坡度(%)": "slope",
    "材质要求": "material",
    "规格型号": "spec",
    "接口方式": "joint",
    "敷设方式": "laying",
    "沟类型": "ditch",
    "管渠类型": "chan_type",
    "公称压力(MPa)": "press_mpa",
    "流量(m³/h)": "flow",
    "模型元素名称": "model_nm",
    "分类编码": "class_cd",
    "编号": "code",
}


def parse_coord(value: Any) -> tuple[float, float, float] | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", str(value))
    if len(nums) < 2:
        return None
    x = float(nums[0])
    y = float(nums[1])
    z = float(nums[2]) if len(nums) > 2 else math.nan
    return x, y, z


def clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def build_properties(row: pd.Series, source_file: str, sheet_name: str) -> dict[str, Any]:
    props: dict[str, Any] = {
        "src_file": source_file,
        "src_sheet": sheet_name,
    }
    for chinese_name, shp_name in FIELD_MAP.items():
        if chinese_name in row.index:
            props[shp_name] = clean_value(row[chinese_name])
    return props


def row_to_feature(
    row: pd.Series,
    geom_type: str,
    source_file: str,
    sheet_name: str,
) -> dict[str, Any] | None:
    props = build_properties(row, source_file, sheet_name)

    if geom_type == "point":
        coord = parse_coord(row.get("节点定位坐标"))
        if coord is None:
            return None
        x, y, z = coord
        props["coord_z"] = z if not math.isnan(z) else None
        geometry = {"type": "Point", "coordinates": [x, y]}
        return {"type": "Feature", "properties": props, "geometry": geometry}

    start = parse_coord(row.get("起点坐标"))
    end = parse_coord(row.get("终点坐标"))
    if start is None or end is None:
        return None
    sx, sy, sz = start
    ex, ey, ez = end
    props["sx"] = sx
    props["sy"] = sy
    props["sz"] = sz if not math.isnan(sz) else None
    props["ex"] = ex
    props["ey"] = ey
    props["ez"] = ez if not math.isnan(ez) else None
    geometry = {"type": "LineString", "coordinates": [[sx, sy], [ex, ey]]}
    return {"type": "Feature", "properties": props, "geometry": geometry}


def extract_geojson(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    manifest: list[dict[str, Any]] = []

    for workbook in WORKBOOKS:
        workbook_path = workbook["path"]
        for sheet_name, (geom_type, layer_name) in workbook["sheets"].items():
            df = pd.read_excel(workbook_path, sheet_name=sheet_name, header=2).dropna(how="all")
            features = []
            for _, row in df.iterrows():
                feature = row_to_feature(row, geom_type, workbook_path.name, sheet_name)
                if feature is not None:
                    features.append(feature)

            geojson = {
                "type": "FeatureCollection",
                "name": layer_name,
                "features": features,
            }
            geojson_path = output_dir / f"{layer_name}.geojson"
            geojson_path.write_text(json.dumps(geojson, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(geojson_path)
            manifest.append(
                {
                    "layer": layer_name,
                    "geometry": geom_type,
                    "source": str(workbook_path),
                    "sheet": sheet_name,
                    "features": len(features),
                    "geojson": str(geojson_path),
                }
            )

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return written


def write_shapefiles(geojson_dir: Path, shp_dir: Path, crs: str | None = None) -> None:
    import geopandas as gpd

    shp_dir.mkdir(parents=True, exist_ok=True)
    for geojson_path in sorted(geojson_dir.glob("*.geojson")):
        gdf = gpd.read_file(geojson_path)
        if gdf.empty:
            continue
        if crs:
            gdf = gdf.set_crs(crs, allow_override=True)
        else:
            # The source spreadsheets do not declare a CRS. Avoid writing a
            # misleading WGS84 .prj for projected/local engineering coordinates.
            gdf.crs = None
        target = shp_dir / f"{geojson_path.stem}.shp"
        for sidecar in target.parent.glob(f"{target.stem}.*"):
            sidecar.unlink()
        gdf.to_file(target, driver="ESRI Shapefile", encoding="UTF-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert underground pipeline Excel tables to Shapefiles.")
    parser.add_argument("--mode", choices=["extract", "write-shp"], required=True)
    parser.add_argument("--geojson-dir", type=Path, default=Path("output/shp/underground_pipelines/geojson"))
    parser.add_argument("--shp-dir", type=Path, default=Path("output/shp/underground_pipelines/shp"))
    parser.add_argument(
        "--crs",
        default="EPSG:4547",
        help="CRS for output Shapefiles. Defaults to EPSG:4547, matching existing pipeline data.",
    )
    args = parser.parse_args()

    if args.mode == "extract":
        written = extract_geojson(args.geojson_dir)
        print(f"wrote {len(written)} geojson layers to {args.geojson_dir}")
    else:
        write_shapefiles(args.geojson_dir, args.shp_dir, args.crs)
        print(f"wrote shapefiles to {args.shp_dir}")


if __name__ == "__main__":
    main()
