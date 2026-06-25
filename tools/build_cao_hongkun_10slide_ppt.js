const pptxgen = require("pptxgenjs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const MEDIA = path.join(ROOT, "output", "ppt_unpacked", "ppt", "media");
const OUTPUT = path.join(
  ROOT,
  "曹宏坤_城市基础设施数字化建模_10面汇报审阅版.pptx"
);

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "曹宏坤";
pptx.company = "深圳市智慧城市科技发展集团有限公司";
pptx.subject = "城市基础设施数字化建模与时空数据融合";
pptx.title = "技术路线、成果展示与后续重点工作";
pptx.lang = "zh-CN";
pptx.theme = {
  headFontFace: "Microsoft YaHei",
  bodyFontFace: "Microsoft YaHei",
  lang: "zh-CN",
};
pptx.defineSlideMaster({
  title: "CONTENT",
  background: { color: "F6F9FC" },
  objects: [],
  slideNumber: { x: 12.42, y: 7.10, w: 0.35, h: 0.18, color: "AAB8C8", fontSize: 8 },
});

const C = {
  navy: "0B2E59",
  deep: "07508E",
  blue: "0C6FE8",
  cyan: "16A6CE",
  teal: "14977E",
  green: "298C65",
  orange: "E68A2E",
  red: "EB263F",
  ink: "172B44",
  mid: "5E728A",
  light: "D8E4EF",
  pale: "EAF4FD",
  pale2: "F1F7FC",
  white: "FFFFFF",
  bg: "F6F9FC",
  darkBg: "063E75",
  softRed: "FDECEF",
  softGreen: "E8F6F1",
  softOrange: "FFF3E4",
};

const FONT = "Microsoft YaHei";
const logo = path.join(MEDIA, "image2.png");
const logoLarge = path.join(MEDIA, "image3.png");
const coverBg = path.join(MEDIA, "image1.png");
const roadImage = path.join(MEDIA, "image8.png");
const tunnelImage = path.join(MEDIA, "image7.png");
const networkImage = path.join(MEDIA, "image9.png");
const junctionImage = path.join(MEDIA, "image10.png");
const flowImage = path.join(ROOT, "CIM自动化建模_实施技术流程图.png");

function addText(slide, text, x, y, w, h, options = {}) {
  const base = {
    x, y, w, h,
    fontFace: FONT,
    fontSize: 12,
    color: C.ink,
    margin: 0,
    breakLine: false,
    valign: "mid",
    fit: "shrink",
    paraSpaceAfterPt: 0,
    ...options,
  };
  slide.addText(text, base);
}

function rect(slide, x, y, w, h, fill, line = fill, radius = 0, transparency = 0) {
  slide.addShape(radius ? pptx.ShapeType.roundRect : pptx.ShapeType.rect, {
    x, y, w, h,
    rectRadius: radius,
    fill: { color: fill, transparency },
    line: { color: line, transparency: line === fill ? transparency : 0, width: line === fill ? 0.1 : 0.7 },
    radius,
  });
}

function line(slide, x1, y1, x2, y2, color = C.light, width = 1, endArrowType) {
  slide.addShape(pptx.ShapeType.line, {
    x: x1,
    y: y1,
    w: x2 - x1,
    h: y2 - y1,
    line: { color, width, endArrowType },
  });
}

function circle(slide, x, y, d, fill, lineColor = fill) {
  slide.addShape(pptx.ShapeType.ellipse, {
    x, y, w: d, h: d,
    fill: { color: fill },
    line: { color: lineColor, width: 0.5 },
  });
}

function addHeader(slide, section, title, page) {
  addText(slide, section, 0.58, 0.22, 3.0, 0.22, {
    fontSize: 8,
    bold: true,
    color: C.blue,
    charSpacing: 1.4,
  });
  addText(slide, title, 0.58, 0.49, 11.55, 0.48, {
    fontSize: 21.5,
    bold: true,
    color: C.ink,
  });
  rect(slide, 0.58, 1.08, 0.48, 0.035, C.red);
  rect(slide, 1.07, 1.08, 1.25, 0.035, C.blue);
  slide.addImage({ path: logo, x: 10.72, y: 0.25, w: 2.08, h: 0.20 });
  line(slide, 0.58, 6.98, 12.77, 6.98, C.light, 0.55);
  addText(slide, "城市基础设施数字化建模与时空数据融合", 0.58, 7.04, 4.4, 0.18, {
    fontSize: 7,
    color: "8A9BB0",
  });
  addText(slide, String(page).padStart(2, "0"), 12.40, 7.04, 0.35, 0.18, {
    fontSize: 8,
    color: "AAB8C8",
    align: "right",
  });
}

function addBottomClaim(slide, text, y = 6.28, color = C.deep) {
  rect(slide, 0.70, y, 11.92, 0.48, color, color, 0.06);
  addText(slide, text, 0.92, y + 0.09, 11.48, 0.28, {
    fontSize: 11.5,
    bold: true,
    color: C.white,
    align: "center",
  });
}

function addCard(slide, {
  x, y, w, h, title, body, accent = C.blue, number = null,
  fill = C.white, titleSize = 13.5, bodySize = 9.8,
}) {
  rect(slide, x, y, w, h, fill, C.light, 0.06);
  rect(slide, x, y, 0.075, h, accent);
  let tx = x + 0.25;
  let tw = w - 0.48;
  if (number !== null) {
    addText(slide, number, x + 0.25, y + 0.14, 0.48, 0.28, {
      fontSize: 15,
      bold: true,
      color: accent,
      align: "center",
    });
    tx = x + 0.82;
    tw = w - 1.05;
  }
  addText(slide, title, tx, y + 0.13, tw, 0.30, {
    fontSize: titleSize,
    bold: true,
    color: C.ink,
  });
  addText(slide, body, tx, y + 0.52, tw, h - 0.61, {
    fontSize: bodySize,
    color: C.mid,
    valign: "top",
    breakLine: true,
    lineSpacingMultiple: 1.05,
  });
}

function addTag(slide, text, x, y, w, fill = C.pale, color = C.blue) {
  rect(slide, x, y, w, 0.26, fill, fill, 0.09);
  addText(slide, text, x + 0.05, y + 0.04, w - 0.10, 0.17, {
    fontSize: 8.2,
    bold: true,
    color,
    align: "center",
  });
}

function addImageFrame(slide, imagePath, x, y, w, h, caption) {
  rect(slide, x - 0.03, y - 0.03, w + 0.06, h + 0.42, C.white, C.light, 0.035);
  slide.addImage({ path: imagePath, x, y, w, h });
  rect(slide, x, y + h, w, 0.34, C.deep);
  addText(slide, caption, x + 0.08, y + h + 0.065, w - 0.16, 0.20, {
    fontSize: 8.4,
    bold: true,
    color: C.white,
    align: "center",
  });
}

function addNotes(slide, text) {
  slide.addNotes(text.replace(/\n\s+/g, "\n").trim());
}

// 1. 封面
{
  const s = pptx.addSlide();
  s.background = { color: C.darkBg };
  s.addImage({ path: coverBg, x: 0, y: 0, w: 13.333, h: 7.5, transparency: 18 });
  rect(s, 0, 0, 13.333, 7.5, C.darkBg, C.darkBg, 0, 27);
  rect(s, 0, 5.90, 8.65, 1.60, "064B91", "064B91", 0, 10);
  rect(s, 0.61, 1.02, 0.12, 3.63, C.red);
  addText(s, "城市基础设施数字化建模与时空数据融合", 1.02, 1.12, 9.4, 0.42, {
    fontSize: 18,
    bold: true,
    color: "BFE1FF",
  });
  addText(s, "技术路线、成果展示\n与后续重点工作", 1.00, 1.76, 9.7, 1.25, {
    fontSize: 32,
    bold: true,
    color: C.white,
    breakLine: true,
    valign: "top",
  });
  addText(s, "面向超融合数据库与数字孪生专项的共性能力建设", 1.02, 3.50, 9.2, 0.42, {
    fontSize: 15,
    color: C.white,
  });
  addText(s, "汇报人：曹宏坤", 1.02, 6.18, 3.2, 0.28, {
    fontSize: 11.5,
    bold: true,
    color: C.white,
  });
  addText(s, "2026 年 6 月 23 日", 1.02, 6.59, 2.5, 0.25, {
    fontSize: 9.5,
    color: "C5DFF6",
  });
  rect(s, 10.35, 6.54, 2.47, 0.47, C.white, C.white, 0.05, 4);
  s.addImage({ path: logoLarge, x: 10.50, y: 6.64, w: 2.18, h: 0.22 });
  addNotes(s, `
各位专家好，我今天汇报的题目是“城市基础设施数字化建模与时空数据融合”。
本次汇报不重点展开某一个模型的具体工程实现，而是结合目前已经开展的 CIM 自动化建模工作，汇报总体技术路线、阶段成果与问题发现，并重点说明后续面向超融合数据库研发和数字孪生专项的工作思考。
  `);
}

// 2. 汇报重点
{
  const s = pptx.addSlide("CONTENT");
  addHeader(s, "REPORT FOCUS", "汇报重点：从现有实践出发，形成面向未来专项的技术判断", 2);
  const cards = [
    ["01", "总体路线", "说明目前形成的工作基础，以及多源数据向 CIM 数字对象转换的总体思路。", C.blue],
    ["02", "实施技术路线", "依据真实代码，说明数据、规则、几何、语义和成果转换的实施链路。", C.cyan],
    ["03", "成果与问题", "展示公共道路、区间隧道的输入输出与规则语义，并归纳问题与展示缺陷。", C.orange],
    ["04", "后续重点工作", "围绕共性建模、超融合数据库与数字孪生专项，提出下一阶段工作设想。", C.red],
  ];
  cards.forEach((c, i) => {
    const x = 0.70 + i * 3.08;
    rect(s, x, 1.65, 2.72, 3.92, i % 2 ? C.pale2 : C.white, C.light, 0.08);
    circle(s, x + 0.24, 1.93, 0.66, c[3]);
    addText(s, c[0], x + 0.24, 2.08, 0.66, 0.25, {
      fontSize: 11.5,
      bold: true,
      color: C.white,
      align: "center",
    });
    addText(s, c[1], x + 0.25, 2.88, 2.20, 0.40, {
      fontSize: 17,
      bold: true,
      color: C.ink,
    });
    addText(s, c[2], x + 0.25, 3.55, 2.18, 1.27, {
      fontSize: 10,
      color: C.mid,
      valign: "top",
      breakLine: true,
      lineSpacingMultiple: 1.08,
    });
    rect(s, x + 0.25, 5.15, 2.12, 0.04, c[3]);
  });
  addBottomClaim(s, "从“生成模型”进一步走向“组织对象、表达关系、沉淀数据资产、支撑专题应用”。", 6.22, C.deep);
  addNotes(s, `
本次汇报主要分为四个方面。
第一部分是总体路线，说明目前形成的工作基础；第二部分是实施技术路线，介绍当前数据、规则、几何、语义和成果转换的真实链路；第三部分是成果展示和问题发现，重点围绕公共道路和区间隧道，展示输入输出数据以及各类构件的生成规则和语义，同时说明当前存在的问题和展示缺陷；第四部分是后续重点工作，说明如何将现有探索拓展为共性技术能力，并支撑超融合数据库和数字孪生专项。
  `);
}

// 3. 目前工作基础
{
  const s = pptx.addSlide("CONTENT");
  addHeader(s, "01 / OVERALL ROUTE", "目前工作基础：以典型对象贯通数据到数字对象的基础链路", 3);
  const stages = [
    ["工程数据", "GIS 图层与源属性", C.blue],
    ["空间处理", "坐标本地化与拓扑", C.cyan],
    ["规则匹配", "等级、断面与构件", C.teal],
    ["分级建模", "CIM3 / CIM4 网格", C.green],
    ["成果组织", "OBJ、JSON 与 FBX", C.orange],
  ];
  stages.forEach((st, i) => {
    const x = 0.73 + i * 2.47;
    rect(s, x, 1.47, 2.05, 0.92, C.white, C.light, 0.055);
    rect(s, x, 1.47, 0.07, 0.92, st[2]);
    addText(s, st[0], x + 0.20, 1.62, 1.65, 0.26, {
      fontSize: 12.4, bold: true, color: C.ink, align: "center",
    });
    addText(s, st[1], x + 0.16, 1.98, 1.72, 0.21, {
      fontSize: 8.5, color: C.mid, align: "center",
    });
    if (i < stages.length - 1) {
      line(s, x + 2.07, 1.93, x + 2.35, 1.93, "8FB7DC", 1.5, "triangle");
    }
  });
  const foundations = [
    ["数据基础", "支持道路中心线及轨道、站点、管线等 GIS 图层读取，完成坐标统一和局部原点转换。", C.blue],
    ["规则基础", "形成道路等级、A1–D6 断面、车道与构件参数，并支持依据源属性进行规则匹配。", C.cyan],
    ["建模基础", "形成道路与路口的平面拓扑、偏移扫掠和网格构造方法，支持道路 CIM3/CIM4 与隧道 CIM4。", C.green],
    ["语义基础", "同步组织源属性、对象分类、路口关系和模型属性，并在 FBX 中挂接对象信息。", C.orange],
  ];
  foundations.forEach((f, i) => {
    const x = i % 2 === 0 ? 0.72 : 4.14;
    const y = i < 2 ? 2.78 : 4.26;
    addCard(s, { x, y, w: 3.08, h: 1.18, title: f[0], body: f[1], accent: f[2], bodySize: 8.55, titleSize: 12.5 });
  });
  rect(s, 7.63, 2.78, 4.97, 2.66, C.white, C.light, 0.06);
  addText(s, "对象实施状态", 7.92, 3.00, 2.0, 0.30, { fontSize: 13.5, bold: true, color: C.ink });
  const status = [
    ["主要链路", "公共道路 CIM3/CIM4、区间隧道 CIM4", C.green],
    ["模块基础", "地下管线、公交站、地铁站点", C.cyan],
    ["后续拓展", "多专业对象标准、精细构件、数据库组织", C.orange],
  ];
  status.forEach((row, i) => {
    const y = 3.48 + i * 0.59;
    addTag(s, row[0], 7.92, y, 0.91, row[2] === C.green ? C.softGreen : row[2] === C.orange ? C.softOrange : C.pale, row[2]);
    addText(s, row[1], 9.05, y + 0.01, 3.18, 0.25, { fontSize: 9.3, color: C.mid });
  });
  addBottomClaim(s, "当前以道路和区间隧道形成主要实施链路，并为站点、管线及其他基础设施对象拓展奠定基础。", 6.25, C.deep);
  addNotes(s, `
从真实代码实施情况看，目前已经形成以工程 GIS 数据为输入的规则化自动建模链路。
道路方面，系统能够读取中心线数据，完成坐标本地化、道路属性规范化、断面规则匹配、路口拓扑处理和构件网格生成，并分别输出 CIM3 和 CIM4 成果。区间隧道方面，已经形成面向线路走向和专业构件规则的 CIM4 生成模块。
地下管线、公交站和地铁站点已经具备模块化生成基础，但专业深度和统一对象标准仍需要继续完善。因此，汇报时需要明确区分已经形成的主要能力和后续需要拓展的能力。
  `);
}

// 4. 当前实施技术路线
{
  const s = pptx.addSlide("CONTENT");
  addHeader(s, "02 / IMPLEMENTATION", "当前实施路线：以专业规则和空间拓扑为核心，同步生成几何与语义", 4);
  rect(s, 0.66, 1.34, 12.03, 5.22, C.white, C.light, 0.055);
  s.addImage({ path: flowImage, x: 0.80, y: 1.45, w: 11.74, h: 4.99 });
  addNotes(s, `
当前代码的真实实施路线，是以 Python 规则计算为核心。
首先从预设数据源或指定文件读取工程 GIS 图层，统一坐标系并建立局部建模原点；然后规范化道路名称、等级、车道数、宽度和高程等属性，再匹配道路等级和 A1 到 D6 断面规则。
几何阶段主要通过 Shapely 完成道路偏移、路口识别、聚类、裁剪和面运算，通过 Trimesh 将平面结果转成三维构件网格。道路使用不同生成配置输出 CIM3 和 CIM4，区间隧道目前输出 CIM4。
生成几何的同时，系统同步输出源属性、对象语义、路口关系和模型对象属性。最后 Blender 以后台方式完成 OBJ 到 FBX 的转换，并将语义侧车文件中的属性挂接到模型对象上。因此，Blender 当前承担成果转换和平台适配，而不是核心规则引擎。
  `);
}

// 5. 当前架构与技术演进
{
  const s = pptx.addSlide("CONTENT");
  addHeader(s, "02 / ARCHITECTURE", "架构演进：立足现有规则生成链路，逐步增强精密构件与统一数据底座", 5);
  const colX = [0.70, 4.44, 8.18];
  const headers = [
    ["当前已实施架构", "规则化生成链路", C.blue],
    ["真实对象状态", "按实施成熟度分层", C.cyan],
    ["后续增强方向", "依据技术选型报告", C.green],
  ];
  headers.forEach((h, i) => {
    rect(s, colX[i], 1.43, 3.38, 0.58, h[2], h[2], 0.05);
    addText(s, h[0], colX[i] + 0.18, 1.54, 1.75, 0.25, { fontSize: 13.5, bold: true, color: C.white });
    addText(s, h[1], colX[i] + 1.82, 1.56, 1.38, 0.20, { fontSize: 8.5, color: C.white, align: "right" });
  });
  const left = [
    ["数据与空间层", "图层读取、坐标统一、局部原点、源属性保留"],
    ["规则与拓扑层", "道路等级、断面构件、线路参数、路口关系"],
    ["几何与分级层", "Shapely 平面构造、Trimesh 网格、CIM 分级"],
    ["成果与平台层", "OBJ、语义 JSON、对象属性、Blender/FBX"],
  ];
  left.forEach((r, i) => addCard(s, {
    x: colX[0], y: 2.20 + i * 0.86, w: 3.38, h: 0.70,
    title: r[0], body: r[1], accent: C.blue, titleSize: 10.9, bodySize: 7.9,
  }));
  const mid = [
    ["公共道路", "CIM3/CIM4；断面构件、路口、标线和路缘", C.green],
    ["区间隧道", "CIM4；结构、轨道、疏散、机电和通信构件", C.green],
    ["地下管线", "具备线位、管径、覆土和类型驱动的模块基础", C.cyan],
    ["站点设施", "点位与基础体块生成，专业构件仍需深化", C.orange],
  ];
  mid.forEach((r, i) => addCard(s, {
    x: colX[1], y: 2.20 + i * 0.86, w: 3.38, h: 0.70,
    title: r[0], body: r[1], accent: r[2], titleSize: 10.9, bodySize: 7.9,
  }));
  const right = [
    ["精密实体", "评估 Build123d，增强重点工程构件参数化实体建模"],
    ["复杂布尔", "评估 Manifold，增强复杂布尔与水密网格处理"],
    ["标准道路", "评估 libOpenDRIVE，补充高精道路数据解析"],
    ["数据底座", "通过统一对象模型与接口接入超融合数据库"],
  ];
  right.forEach((r, i) => addCard(s, {
    x: colX[2], y: 2.20 + i * 0.86, w: 3.38, h: 0.70,
    title: r[0], body: r[1], accent: C.green, fill: i % 2 ? "F2FBF7" : C.white, titleSize: 10.9, bodySize: 7.9,
  }));
  addBottomClaim(s, "当前以“Shapely + Trimesh 规则生成”为实施主线，未来以“精密参数化构件 + 统一数据底座”为增强方向。", 6.25, C.deep);
  addNotes(s, `
这一页需要把当前实施与后续技术选型明确区分。
当前已落地的是四层架构：底层完成 GIS 数据读取和坐标本地化；规则与拓扑层负责道路断面、线路参数和空间关系；几何层以 Shapely 和 Trimesh 完成平面构造、网格生成及 CIM 分级；成果层输出 OBJ 和语义 JSON，再由 Blender 后台转换为 FBX。
对象方面，道路和区间隧道已经形成主要生成链路，管线具备规则化管段生成基础，公交站和地铁站点目前以点位和基础体块为主。
技术选型报告中的 Build123d、Manifold 和 libOpenDRIVE 应当作为下一阶段增强，重点补充精密构件、复杂布尔和标准道路数据能力，不能表述为当前已经全面实施。
  `);
}

// 6. 成果展示
{
  const s = pptx.addSlide("CONTENT");
  addHeader(s, "03 / RESULTS", "成果展示：公共道路与区间隧道实现输入、对象与规则语义的同步表达", 6);
  rect(s, 0.68, 1.37, 6.02, 4.75, C.white, C.light, 0.06);
  rect(s, 6.84, 1.37, 5.80, 4.75, C.white, C.light, 0.06);
  addText(s, "公共道路 CIM3 / CIM4", 0.96, 1.56, 2.65, 0.30, { fontSize: 15.5, bold: true, color: C.ink });
  addTag(s, "输入", 4.11, 1.58, 0.56, C.pale, C.blue);
  addText(s, "中心线 · 等级 · 车道 · 宽度 · 高程", 4.75, 1.59, 1.63, 0.23, { fontSize: 7.9, color: C.mid, align: "right" });
  addImageFrame(s, roadImage, 0.96, 1.99, 5.45, 2.16, "真实道路构件成果：车行道、人行道、绿化带、路缘与道路附属对象");
  addText(s, "构件输出", 0.98, 4.64, 0.78, 0.23, { fontSize: 9.5, bold: true, color: C.blue });
  ["路面/车行道", "人行道/绿化带", "设施带/路缘", "标线/路口"].forEach((t, i) => addTag(s, t, 1.78 + i * 1.10, 4.62, 1.00, "F2F7FC", C.deep));
  addText(s, "规则语义", 0.98, 5.13, 0.78, 0.23, { fontSize: 9.5, bold: true, color: C.green });
  ["道路等级", "A1–D6 断面", "构件分类", "路口关系", "源属性"].forEach((t, i) => addTag(s, t, 1.78 + i * 0.91, 5.11, 0.82, C.softGreen, C.green));

  addText(s, "轨道交通区间隧道 CIM4", 7.12, 1.56, 3.15, 0.30, { fontSize: 15.5, bold: true, color: C.ink });
  addTag(s, "输入", 10.59, 1.58, 0.56, C.pale, C.blue);
  addText(s, "线路 · 区间 · 断面 · 专业配置", 11.18, 1.59, 1.12, 0.23, { fontSize: 7.7, color: C.mid, align: "right" });
  addImageFrame(s, tunnelImage, 7.12, 1.99, 5.20, 2.16, "真实区间隧道成果：结构、轨道、疏散、机电与通信专业构件");
  addText(s, "构件输出", 7.14, 4.64, 0.78, 0.23, { fontSize: 9.5, bold: true, color: C.blue });
  ["隧道结构", "轨道系统", "疏散构件", "机电/通信"].forEach((t, i) => addTag(s, t, 7.94 + i * 1.06, 4.62, 0.96, "F2F7FC", C.deep));
  addText(s, "规则语义", 7.14, 5.13, 0.78, 0.23, { fontSize: 9.5, bold: true, color: C.green });
  ["构件规则", "空间位置", "专业属性", "对象语义"].forEach((t, i) => addTag(s, t, 7.94 + i * 1.06, 5.11, 0.96, C.softGreen, C.green));
  addBottomClaim(s, "成果展示不只呈现三维效果，更强调输入数据、输出对象与构件规则语义之间的对应关系。", 6.33, C.deep);
  addNotes(s, `
第三部分首先展示当前已经形成的成果。这里不能只放模型截图，因为单纯看三维效果难以说明技术路线的价值。
公共道路的输入主要是道路中心线、道路等级、车道数、宽度、高程和源属性，输出包括 CIM3/CIM4 道路模型，以及路面、人行道、绿化带、设施带、路缘、标线和路口等构件；同时还需要展示道路等级规则、A1 到 D6 断面规则、构件分类和路口语义。
区间隧道则重点展示轨道线路、区间走向和专业构件配置如何生成隧道结构、轨道、疏散、机电、通信等 CIM4 构件。
这一页需要让专家看到：我们展示的不只是模型结果，而是输入数据、输出对象和规则语义之间的完整对应关系。
  `);
}

// 7. 问题发现
{
  const s = pptx.addSlide("CONTENT");
  addHeader(s, "03 / FINDINGS", "问题发现：链路已贯通，但展示深度与对象组织仍需进一步深化", 7);
  const problems = [
    ["01", "输入数据", "分类、字段完整性、空间精度和业务口径不完全一致。", "建立统一数据选型、字段映射与质量分级机制。", C.blue],
    ["02", "规则表达", "部分构件、断面与语义规则仍分散在配置和程序参数中。", "推动规则配置化、版本化，并建立规则与对象的可解释关联。", C.cyan],
    ["03", "对象关系", "模型、源属性和语义文件可关联，但跨层级、跨专业身份仍需加强。", "建立统一对象编码、构件层级与多专业关系模型。", C.orange],
    ["04", "展示组织", "模型效果较直观，但数据、规则、对象、语义的对应关系不够清楚。", "形成面向数据库与孪生应用的一体化展示口径。", C.red],
  ];
  problems.forEach((p, i) => {
    const x = i % 2 === 0 ? 0.72 : 6.79;
    const y = i < 2 ? 1.48 : 3.49;
    rect(s, x, y, 5.82, 1.68, C.white, C.light, 0.06);
    circle(s, x + 0.22, y + 0.23, 0.53, p[4]);
    addText(s, p[0], x + 0.22, y + 0.35, 0.53, 0.20, { fontSize: 9.5, bold: true, color: C.white, align: "center" });
    addText(s, p[1], x + 0.91, y + 0.18, 1.20, 0.28, { fontSize: 13.2, bold: true, color: C.ink });
    addText(s, "当前表现", x + 0.91, y + 0.62, 0.72, 0.20, { fontSize: 8.3, bold: true, color: p[4] });
    addText(s, p[2], x + 1.70, y + 0.57, 3.77, 0.35, { fontSize: 8.7, color: C.mid, valign: "top" });
    addText(s, "后续认识", x + 0.91, y + 1.13, 0.72, 0.20, { fontSize: 8.3, bold: true, color: C.green });
    addText(s, p[3], x + 1.70, y + 1.07, 3.77, 0.38, { fontSize: 8.7, color: C.mid, valign: "top" });
  });
  addText(s, "需要完成的三个转变", 0.72, 5.50, 1.85, 0.26, { fontSize: 11, bold: true, color: C.ink });
  const shifts = [
    ["模型效果展示", "数据规则展示"],
    ["单一成果展示", "对象关系展示"],
    ["阶段文件展示", "数据资产展示"],
  ];
  shifts.forEach((sh, i) => {
    const x = 2.72 + i * 3.27;
    rect(s, x, 5.43, 2.90, 0.52, i === 2 ? C.softRed : C.pale, i === 2 ? C.softRed : C.pale, 0.06);
    addText(s, sh[0], x + 0.12, 5.54, 1.12, 0.22, { fontSize: 8.7, color: C.mid, align: "center" });
    addText(s, "→", x + 1.27, 5.54, 0.28, 0.22, { fontSize: 12, bold: true, color: i === 2 ? C.red : C.blue, align: "center" });
    addText(s, sh[1], x + 1.55, 5.54, 1.18, 0.22, { fontSize: 8.7, bold: true, color: i === 2 ? C.red : C.deep, align: "center" });
  });
  addBottomClaim(s, "问题的核心不是“某个构件画得不够好”，而是展示和对象组织方式尚未完全支撑数据库与孪生应用。", 6.32, C.deep);
  addNotes(s, `
在成果展示的基础上，需要进一步说明我们发现的问题。这里不能把问题讲成某个局部模型缺陷，而要上升到方法层面。
第一是输入数据问题，不同图层在分类、字段完整性、空间精度和业务口径上存在差异，会直接影响规则匹配和构件生成。
第二是规则表达问题，公共道路和区间隧道已经具备规则生成基础，但部分规则仍分散在配置文件和程序参数中，后续需要形成统一的规则管理方式。
第三是对象关系问题，目前模型、源属性和语义文件已经能够关联，但跨对象、跨层级、跨专业的统一身份还需要加强。
第四是展示组织问题，当前展示容易只看模型效果，而输入数据、生成规则、输出对象和语义属性之间的关系还不够直观。
因此，问题的核心不是某个构件画得不够好，而是当前展示和对象组织还不足以支撑后续超融合数据库和数字孪生应用。
  `);
}

// 8. 超融合数据库
{
  const s = pptx.addSlide("CONTENT");
  addHeader(s, "04 / DATABASE", "超融合数据库研发：构建面向城市基础设施的多模态时空数据底座", 8);
  addText(s, "中心目标", 0.72, 1.43, 0.82, 0.25, { fontSize: 10.5, bold: true, color: C.blue });
  addText(s, "统一组织 GIS、CAD、三维模型、业务表、文档和专题数据，实现对象可管理、关系可计算、变化可追踪、服务可复用。",
    1.58, 1.40, 10.95, 0.34, { fontSize: 10.8, bold: true, color: C.ink });
  circle(s, 5.32, 2.43, 2.65, C.deep);
  addText(s, "统一城市\n基础设施对象", 5.61, 3.08, 2.06, 0.72, {
    fontSize: 17.5, bold: true, color: C.white, align: "center", breakLine: true,
  });
  const modules = [
    [0.72, 2.02, "多源融合接入", "多类型数据接入、映射与基础治理；保留来源与质量信息。", C.blue],
    [0.72, 3.69, "统一对象模型", "建立跨专业对象编码、分类、属性结构和多层级表达。", C.cyan],
    [9.15, 2.02, "时空复合索引", "联合空间、时间、属性、对象关系和语义标签进行检索。", C.green],
    [9.15, 3.69, "关系与版本模型", "表达连接、邻接、上下游、穿越、权属、状态和历史版本。", C.orange],
    [4.20, 5.12, "数据服务体系", "形成面向建模、查询、统计、分析和孪生应用的标准服务接口。", C.red],
  ];
  modules.forEach((m) => addCard(s, {
    x: m[0], y: m[1], w: m[0] === 4.20 ? 4.93 : 3.45, h: m[0] === 4.20 ? 0.82 : 1.12,
    title: m[2], body: m[3], accent: m[4], titleSize: 12.5, bodySize: 8.5,
  }));
  line(s, 4.15, 2.58, 5.33, 3.18, "A9C9E4", 1.1, "triangle");
  line(s, 4.15, 4.12, 5.33, 3.72, "A9C9E4", 1.1, "triangle");
  line(s, 9.15, 2.58, 7.96, 3.18, "A9C9E4", 1.1, "triangle");
  line(s, 9.15, 4.12, 7.96, 3.72, "A9C9E4", 1.1, "triangle");
  line(s, 6.65, 5.12, 6.65, 4.98, "A9C9E4", 1.1, "triangle");
  addText(s, "应用出口", 0.72, 6.23, 0.82, 0.23, { fontSize: 9.8, bold: true, color: C.ink });
  const exits = [
    ["为自动化建模提供标准数据", C.blue],
    ["为数字孪生提供统一对象", C.green],
    ["为专题分析提供关系与时空计算能力", C.orange],
  ];
  exits.forEach((e, i) => {
    const x = 1.72 + i * 3.62;
    rect(s, x, 6.14, 3.27, 0.43, i === 1 ? C.softGreen : i === 2 ? C.softOrange : C.pale, i === 1 ? C.softGreen : i === 2 ? C.softOrange : C.pale, 0.07);
    addText(s, e[0], x + 0.12, 6.24, 3.03, 0.20, { fontSize: 8.8, bold: true, color: e[1], align: "center" });
  });
  addNotes(s, `
超融合数据库是下一阶段需要重点投入的研发方向。
这里所说的“超融合”，不是把 GIS、模型、表格和文档简单放进同一种数据库，也不是某一种数据库产品的选型。核心是围绕统一的城市基础设施对象，建立跨模态数据之间的对应关系。
研发上需要重点推进多源融合接入、统一对象模型、时空复合索引、关系与版本模型以及数据服务体系。
最终希望做到对象可管理、关系可计算、变化可追踪、服务可复用，为自动化建模提供标准数据，为数字孪生提供统一对象，也为跨专业专题分析提供时空和关系计算基础。
  `);
}

// 9. 数字孪生专项
{
  const s = pptx.addSlide("CONTENT");
  addHeader(s, "04 / DIGITAL TWIN", "数字孪生专项：由场景表达走向数据驱动的专题应用", 9);
  rect(s, 0.72, 1.42, 5.74, 4.92, C.white, C.light, 0.06);
  rect(s, 6.72, 1.42, 5.90, 4.92, C.white, C.light, 0.06);
  addText(s, "专项建设内容", 1.02, 1.68, 2.0, 0.32, { fontSize: 15, bold: true, color: C.ink });
  addText(s, "围绕基础设施对象，形成“可展示、可查询、可关联、可分析”的专题能力。",
    1.02, 2.06, 4.98, 0.34, { fontSize: 9.2, color: C.mid });
  const layers = [
    ["01", "场景底座", "道路、轨道、管线、站点与地下空间对象组织", C.blue],
    ["02", "数据关联", "场景对象与数据库对象、业务属性和专题数据关联", C.cyan],
    ["03", "专题图层", "设施分布、空间关系、状态信息与业务约束表达", C.green],
    ["04", "指标分析", "面向管理、研判和汇报的指标口径与分析逻辑", C.orange],
    ["05", "智能辅助", "探索时空数据与多模态模型辅助查询和理解", C.red],
  ];
  layers.forEach((l, i) => {
    const y = 2.56 + i * 0.65;
    circle(s, 1.02, y, 0.38, l[3]);
    addText(s, l[0], 1.02, y + 0.08, 0.38, 0.18, { fontSize: 7.6, bold: true, color: C.white, align: "center" });
    addText(s, l[1], 1.56, y - 0.01, 1.02, 0.25, { fontSize: 10.5, bold: true, color: C.ink });
    addText(s, l[2], 2.64, y - 0.01, 3.30, 0.29, { fontSize: 8.0, color: C.mid });
  });
  addText(s, "专项阶段成果", 7.02, 1.68, 2.0, 0.32, { fontSize: 15, bold: true, color: C.ink });
  addText(s, "在专题场景建设过程中同步沉淀可复用的数据组织、接口和方法成果。",
    7.02, 2.06, 5.05, 0.34, { fontSize: 9.2, color: C.mid });
  const outputs = [
    "典型数字孪生专题场景",
    "专题数据组织与对象关联方案",
    "查询、统计、关系分析和专题展示原型",
    "场景配置、数据接口与指标说明",
    "可复用专题模板与技术研究成果",
  ];
  outputs.forEach((o, i) => {
    const y = 2.55 + i * 0.65;
    rect(s, 7.02, y, 5.05, 0.46, i % 2 ? C.pale2 : C.white, C.light, 0.04);
    rect(s, 7.02, y, 0.08, 0.46, i === 4 ? C.red : C.blue);
    addText(s, String(i + 1).padStart(2, "0"), 7.25, y + 0.10, 0.37, 0.20, {
      fontSize: 8.5, bold: true, color: i === 4 ? C.red : C.blue, align: "center",
    });
    addText(s, o, 7.79, y + 0.09, 4.02, 0.22, { fontSize: 9.3, bold: i === 4, color: C.ink });
  });
  addBottomClaim(s, "数字孪生专项的价值不在于“看见三维场景”，而在于通过统一对象和数据关联理解设施状态及其关系。", 6.42, C.deep);
  addNotes(s, `
数字孪生专项需要形成一条完整的建设路线。
它的目标不只是搭建三维场景，而是围绕基础设施对象形成展示、查询、关联和分析能力。专项建设包括场景底座、数据关联、专题图层、指标分析和智能辅助五个方面。
在阶段成果方面，应形成典型专题场景、对象关联方案、查询分析原型、数据接口和指标说明，并进一步沉淀可复用的专题模板。
也就是说，数字孪生专项要从“看见场景”走向“理解对象、关系和状态”，为实际专题研判提供支撑。
  `);
}

// 10. 下一阶段
{
  const s = pptx.addSlide("CONTENT");
  addHeader(s, "04 / NEXT STEPS", "下一阶段：夯实统一基础，推进数据融合，形成专题应用", 10);
  const phases = [
    ["近期", "夯实统一基础", [
      "梳理五类基础设施对象标准、编码和属性结构",
      "推进道路、路口、隧道和管线规则配置化、版本化",
      "明确 CIM3/CIM4 分级内容与对象对应关系",
      "同步组织模型、属性、关系与来源信息",
    ], C.blue],
    ["中期", "推进数据库与专题协同", [
      "研究精密参数化构件能力与现有网格链路协同",
      "深化超融合数据库对象、索引和关系模型",
      "建立增量更新与历史版本组织机制",
      "形成孪生专题图层、指标体系和应用模板",
    ], C.green],
    ["长期", "形成可持续数字底座", [
      "由单项能力走向跨专业、跨专项的数据资产体系",
      "由静态表达走向状态理解、关系分析和辅助研判",
      "由项目定制走向标准化、模块化和可复用能力",
      "沉淀数据规范、方法体系、知识成果和示范应用",
    ], C.red],
  ];
  phases.forEach((p, i) => {
    const x = 0.72 + i * 4.05;
    rect(s, x, 1.47, 3.61, 3.95, C.white, C.light, 0.07);
    rect(s, x, 1.47, 3.61, 0.61, p[3], p[3], 0.07);
    addText(s, p[0], x + 0.20, 1.62, 0.58, 0.25, { fontSize: 11.5, bold: true, color: C.white });
    addText(s, p[1], x + 0.83, 1.62, 2.50, 0.25, { fontSize: 13.3, bold: true, color: C.white });
    p[2].forEach((item, j) => {
      circle(s, x + 0.25, 2.35 + j * 0.70, 0.22, p[3]);
      addText(s, String(j + 1), x + 0.25, 2.39 + j * 0.70, 0.22, 0.12, {
        fontSize: 6.5, bold: true, color: C.white, align: "center",
      });
      addText(s, item, x + 0.59, 2.29 + j * 0.70, 2.70, 0.35, {
        fontSize: 8.6, color: C.mid, valign: "top",
      });
    });
  });
  addText(s, "三个研究判断", 0.72, 5.66, 1.40, 0.26, { fontSize: 10.5, bold: true, color: C.ink });
  const judgments = [
    "数据标准应先于大规模对象生产",
    "对象与关系应先于复杂应用堆叠",
    "应用场景应反向牵引数据库和建模能力建设",
  ];
  judgments.forEach((j, i) => {
    const x = 2.25 + i * 3.40;
    rect(s, x, 5.58, i === 2 ? 3.67 : 3.02, 0.48, i === 2 ? C.softRed : i === 1 ? C.softGreen : C.pale, i === 2 ? C.softRed : i === 1 ? C.softGreen : C.pale, 0.06);
    addText(s, j, x + 0.10, 5.69, (i === 2 ? 3.47 : 2.82), 0.21, {
      fontSize: i === 2 ? 8.1 : 8.5,
      bold: true,
      color: i === 2 ? C.red : i === 1 ? C.green : C.blue,
      align: "center",
    });
  });
  addBottomClaim(s, "以自动化建模形成数字对象，以超融合数据库沉淀数据资产，以数字孪生专项牵引专题应用。", 6.30, C.deep);
  addText(s, "谢谢，请各位专家批评指正", 9.83, 6.84, 2.75, 0.24, {
    fontSize: 9.2, bold: true, color: C.deep, align: "right",
  });
  addNotes(s, `
下一阶段可以分为三个层次推进。
近期首先夯实对象标准、编码、专业规则和分级表达基础，避免在标准不稳定的情况下大规模生产对象。
中期重点推进超融合数据库与数字孪生专题协同，研究复合索引、关系模型、增量更新和专题模板。
长期希望形成跨专业、跨专项、可持续演进的城市基础设施数字底座。
这里有三个基本判断：数据标准应先于大规模对象生产，对象和关系应先于复杂应用堆叠，具体应用场景还要反向牵引数据库和建模能力建设。
总体上，我们希望以自动化建模形成数字对象，以超融合数据库沉淀数据资产，以数字孪生专项牵引专题应用。我的汇报结束，请各位专家批评指正。
  `);
}

pptx.writeFile({ fileName: OUTPUT, compression: true });

