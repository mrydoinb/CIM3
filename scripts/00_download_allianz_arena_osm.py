from pathlib import Path
import osmnx as ox
import geopandas as gpd

# =========================
# 1. 输出目录
# =========================
out_dir = Path("data/raw")
out_dir.mkdir(parents=True, exist_ok=True)

# =========================
# 2. 测试区域：拜仁慕尼黑主场安联球场附近
# =========================
center_point = (48.218967, 11.623746)  # lat, lon
dist = 1000  # 半径，单位：米


# =========================
# 3. 下载道路中心线
# =========================
G = ox.graph_from_point(
    center_point=center_point,
    dist=dist,
    network_type="drive",
    simplify=True
)

nodes, edges = ox.graph_to_gdfs(G)
road_centerline = edges.reset_index()

road_centerline.to_file(
    out_dir / "road_centerline.geojson",
    driver="GeoJSON"
)


# =========================
# 4. 下载建筑轮廓
# =========================
building_tags = {
    "building": True
}

buildings = ox.features_from_point(
    center_point,
    tags=building_tags,
    dist=dist
).reset_index()

buildings.to_file(
    out_dir / "building_footprint.geojson",
    driver="GeoJSON"
)


# =========================
# 5. 下载公交站、地铁站、公共交通点
# =========================
transport_tags = {
    "highway": "bus_stop",
    "railway": ["station", "subway_entrance"],
    "public_transport": ["platform", "stop_position", "station"]
}

transport = ox.features_from_point(
    center_point,
    tags=transport_tags,
    dist=dist
).reset_index()

transport.to_file(
    out_dir / "transport_points.geojson",
    driver="GeoJSON"
)

print("安联球场附近 OSM 原始数据下载完成：")
print("1. data/raw/road_centerline.geojson")
print("2. data/raw/building_footprint.geojson")
print("3. data/raw/transport_points.geojson")