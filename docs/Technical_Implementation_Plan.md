# CIM3 公共道路自动化建模与材质增强实施方案技术文档

## 1. 项目背景与总体目标

本项目为 **CIM3 公共道路自动化建模 POC**，旨在通过自动化的计算流程，将二维或伪三维的道路矢量网络（如 OSM 中心线 GeoJSON）转化为包含拓扑结构、高度信息、多级组件（路面、人行道、路缘石、车道线）且具备高质量 PBR（Physically Based Rendering）/ 程序化材质的 3D 模型。

项目的核心设计理念是 **“业务逻辑与渲染表现解耦”**：
- 主控建模与业务语义生成完全在轻量级的 Python 环境中完成。
- 高级的 UV 展开与物理材质赋填由 Blender 作为后台（Headless）服务介入，确保核心系统不与臃肿的 3D 软件生态直接耦合。

---

## 2. 系统架构设计

本系统由两个核心子引擎构成，通过标准化文件（OBJ/JSON）进行上下游数据交换：

### 2.1 引擎 A：宏观场景与几何引擎（Python / CIM3 级）
- **技术栈**：Python, GeoPandas, Shapely, Trimesh, Numpy, Pandas。
- **核心职责**：
  1. 读取原始路网 `road_centerline.geojson` 及配置文件 `road_rules.json`。
  2. 基于数字高程模型（DGM / TIF）或默认标高对节点进行高程采样。
  3. 结合 OSM 属性（`lanes`、`bridge`、`layer`）计算桥梁净空补偿并处理多层立体交通。
  4. 使用 Shapely 进行 2D 缓冲外扩，并完成交叉路口的布尔运算（Boolean Union & Difference）。
  5. 利用受限三角剖分（Triangulate）与垂直拉伸（Extrude）生成无厚度路面与带厚度路缘石的 3D Mesh 网格。
  6. 输出基底 3D 网格（`road_test.obj`）、基础 glTF、语义字典（`semantic.json`）与质量检查报告（`qc_report.json`）。

### 2.2 引擎 B：材质后处理引擎（Blender Headless）
- **技术栈**：Blender 4.x, Python (`bpy`)。
- **核心职责**：
  1. 在后台静默运行（`--background`），无缝对接引擎 A 输出的 OBJ 几何底模。
  2. 通过脚本自动清理场景、导入网格、并基于阈值进行智能 UV 展开（Smart UV Project）。
  3. 基于命名匹配规则（如包含 `Road_Surface`、`Sidewalk` 等）自动分配对应的材质槽。
  4. **PBR 优先与程序化兜底**：若材质目录存在物理贴图，则构建贴图流节点（含 BaseColor, Roughness, Normal, AO）；否则，自动注入数学程序化噪波节点（Noise Texture + ColorRamp + Bump）以模拟真实的沥青颗粒与混凝土质感。
  5. 导出经过材质增强的最终成品 `road_test_realistic.glb`。

---

## 3. 核心实施流程

整个实施管线被封装为自动化脚本（如 `run_workflow.bat` / `.sh`），依次执行以下三大步骤：

### 步骤一：基础模型网格与拓扑生成
- **执行指令**：`python scripts/01_generate_cim3_road.py`
- **输入流**：
  - 几何源：`data/raw/road_centerline.geojson`
  - 规则源：`data/rules/road_rules.json`
  - 高程源（可选）：`data/raw/road_centerline/*.tif`
- **处理细节**：
  1. 将输入坐标重投影至局部投影坐标系（模型 CRS `EPSG:32632`）。
  2. 计算 DGM 高程与 OSM 桥梁标识带来的高程纠正偏移。
  3. 按标高聚类，在同高程簇内通过 `line_buffer` 构建路面、标线，相减构建出人行道与路缘石 2D 多边形。
  4. 基于 `trimesh.creation.triangulate_polygon` 和法线方向校正生成最终面片。
- **输出成果**：`output/obj/road_test.obj`、局部坐标 GeoJSON 和各类记录报表。

### 步骤二：PBR / 程序化材质增强
- **执行指令**：`blender --background --python scripts/03_apply_materials_blender.py`
- **输入流**：`output/obj/road_test.obj`
- **处理细节**：
  1. **UV 自动生成**：进入编辑模式对合并模型采用智能 UV 投射。
  2. **对象名称匹配**：
     - `Road_Surface_*` -> `MAT_Asphalt`
     - `Sidewalk_*` -> `MAT_Concrete`
     - `Curb_*` -> `MAT_Curb`
     - `Lane_Marking_*` -> `MAT_Marking`
  3. **材质决策逻辑**：优先读取 `assets/textures/` 对应子目录下的贴图集，缺失则退回到节点混合生成的物理噪波程序化材质（保证输出永不失效，且具备基本粗糙度和凹凸感）。
- **输出成果**：`output/gltf/road_test_realistic.glb`。

### 步骤三：格式拓展导出（可选）
- **执行指令**：`blender --background --python scripts/02_export_fbx_blender.py`
- **功能说明**：将模型转换为特定引擎（如 UE5 / Unity）常用的 FBX 格式进行资产交付。

---

## 4. 关键技术难点与解决方案

### 4.1 立体交通防穿模（Z-fighting / Collision Avoidance）
- **问题**：多重道路交叉（高架桥与底层道路相交）时，若将所有道路统一 2D Buffer 并集，会导致立交桥塌陷到地面并发生拓扑错误。
- **解决方案**：引入 `generate_planar_geometries` 中的高程聚类（`groupby("elevation")`）。道路仅与处于同一高度的路线进行布尔操作，高低架路完全剥离成多个独立的 Mesh 实体层。

### 4.2 高质量侧面厚度处理（Extrusion）
- **问题**：路缘石需要展现明显的高差质感（Curb Height）。
- **解决方案**：由 `polygon_to_extruded_mesh` 函数进行立体拉伸。算法获取顶部与底部的三角化平面，并扫描多边形外部边界与内部孔洞（Interiors），生成封闭的带厚度四边形连桥网格。

### 4.3 渲染引擎解耦下的物理材质补偿
- **问题**：传统的 Trimesh `face_colors` 生成的底模呈单调的塑料色块状，无法反映实际城市场景质感；而强制绑定 UE 或 WebGL 引擎的材质分配机制又不便流转。
- **解决方案**：引入基于 Blender Principled BSDF 的自包含程序化节点。通过 `Noise Texture -> ColorRamp -> Bump -> Normal`，使得导出的 GLTF 在被任意下游 PBR 兼容浏览器加载时，即使无外部贴图资源，自带逼真的水泥/沥青质感。

---

## 5. 输入输出规约与目录结构

```text
cim_road_poc/
├── assets/
│   └── textures/                # PBR材质库目录（可为空）
│       ├── asphalt/             # 沥青路面贴图
│       ├── concrete/            # 混凝土人行道贴图
│       ├── curb_concrete/       # 混凝土路缘石贴图
│       └── road_marking/        # 车道标线贴图
├── data/
│   ├── raw/
│   │   ├── road_centerline.geojson    # 输入：OSM原始地理路网矢量
│   │   └── road_centerline/           # 输入：可选的DGM切片包
│   ├── processed/
│   │   └── road_centerline_local.geojson # 输出：重投影并注入三维高程的路网
│   └── rules/
│       └── road_rules.json            # 输入：道路逻辑尺寸预设参数
├── scripts/
│   ├── 01_generate_cim3_road.py       # A引擎：生成几何、高度、语义的Python核心
│   ├── 02_export_fbx_blender.py       # B引擎扩展：导出FBX
│   └── 03_apply_materials_blender.py  # B引擎：后处理、UV生成、PBR材质渲染分配
├── output/
│   ├── obj/
│   │   └── road_test.obj              # 输出：Python生成的无材质集合底模
│   ├── gltf/
│   │   ├── road_test.glb              # 输出：底模版glTF
│   │   └── road_test_realistic.glb    # 输出：材质增强版的最终三维模型
│   ├── semantic/
│   │   └── road_test_semantic.json    # 输出：包含模型级别CIM语义信息的数据库
│   └── qc_report/
│       └── road_test_qc_report.json   # 输出：过程统计与几何拓扑合规性报告
├── run_workflow.bat                   # Win：一键执行工作流脚本
└── run_workflow.sh                    # Linux/Mac：一键执行工作流脚本
```

---

## 6. 后续迭代建议

1. **复杂平滑转角**：当前的布尔并集产生的交叉口为硬角。后续可在二维运算引入更复杂的曲线相切生成算法。
2. **地表地形贴合**：实现道路面网格动态细分（Tessellation），以紧密贴合波浪起伏不平的真实倾斜地形，而非纯平面连接。
3. **多类型标记支持**：针对 `road_rules.json` 规则加入斑马线、导向箭头等矢量对象的生成功能，利用贴花（Decal）逻辑投射到 `Road_Surface` 之上。






材质目录
