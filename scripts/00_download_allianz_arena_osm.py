# 导入处理路径的标准库
from pathlib import Path
# 导入 OSMnx 库，用于极其方便地从 OpenStreetMap 下载、建模、分析和可视化街道网络
import osmnx as ox
# 导入 GeoPandas，用于处理地理空间数据（类似于 Pandas，但专门支持空间几何列和坐标系）
import geopandas as gpd

# =========================
# 1. 输出目录
# =========================
# 设定数据输出的基础目录为项目目录下的 data/raw 文件夹
out_dir = Path("data/raw")
# 创建目录。parents=True 表示如果父目录不存在也会一并创建；exist_ok=True 表示如果目录已存在则不报错
out_dir.mkdir(parents=True, exist_ok=True)

# =========================
# 2. 测试区域：拜仁慕尼黑主场安联球场附近
# =========================
# 设定要抓取的地理中心点坐标，这里是德国慕尼黑安联球场 (Allianz Arena) 的纬度和经度
center_point = (48.218967, 11.623746)  # lat, lon
# 设定抓取范围半径为 1000 米，即以中心点为圆心，向外扩展 1 公里的圆形切片区域
dist = 1000  # 半径，单位：米


# =========================
# 3. 下载道路中心线
# =========================
# 使用 OSMnx 根据给定的中心点和半径从 OSM 获取路网路段图 (Graph)
G = ox.graph_from_point(
    center_point=center_point,
    dist=dist,
    network_type="drive",  # 关键过滤条件：仅下载可供汽车行驶的机动车道（剔除纯步行街、自行车道等）
    simplify=True          # 对拓扑进行简化，合并一些非交叉口的冗余节点，使道路变成连续的线段
)

# 将抓取到的图数据（Graph）转换为 GeoDataFrame。nodes 是图节点（路口），edges 是图的边（道路中心线段）
nodes, edges = ox.graph_to_gdfs(G)
# 重置索引：OSMnx 默认生成的边具有 u, v, key 多级索引，这一步将它们转换为普通列，方便我们后续读取
road_centerline = edges.reset_index()

# 将道路线段数据保存为标准的 GeoJSON 文件格式。这是后续 3D 生成脚本的核心输入源
road_centerline.to_file(
    out_dir / "road_centerline.geojson",
    driver="GeoJSON"
)


# =========================
# 4. 下载建筑轮廓
# =========================
# 定义标签过滤器：从 OSM 获取所有带有 "building" 标签的地理元素
building_tags = {
    "building": True
}

# 获取指定区域内符合标签特征的地理空间要素 (Features)，此处通常返回大量多边形 (Polygons)
buildings = ox.features_from_point(
    center_point,
    tags=building_tags,
    dist=dist
).reset_index()

# 保存建筑物轮廓底图数据
buildings.to_file(
    out_dir / "building_footprint.geojson",
    driver="GeoJSON"
)


# =========================
# 5. 下载公交站、地铁站、公共交通点
# =========================
# 通过复杂的标签组合规则，提取公共交通相关设施的点位特征（POI）
transport_tags = {
    "highway": "bus_stop",
    "railway": ["station", "subway_entrance"],
    "public_transport": ["platform", "stop_position", "station"]
}

# 执行抓取交通特征数据（通常返回点 Point 坐标集合）
transport = ox.features_from_point(
    center_point,
    tags=transport_tags,
    dist=dist
).reset_index()

# 保存交通基础设施数据
transport.to_file(
    out_dir / "transport_points.geojson",
    driver="GeoJSON"
)

# 打印执行成功的汇总提示
print("安联球场附近 OSM 原始数据下载完成：")
print("1. data/raw/road_centerline.geojson")
print("2. data/raw/building_footprint.geojson")
print("3. data/raw/transport_points.geojson")