---
name: image-to-printable-kit
description: 端到端流水线：从原始图片/参考图出发，经参数化建模、分色拆件（AMS 单件 / 胶接 / 免胶快拆三路）、逐件打印朝向、切片烘焙，产出一盘一个颜色、打开即切、带 gcode 和缩略图、通过六道质量门禁的 .3mf 打印文件套件，并同步交付 README / 件表 / 验证图。当用户说「从这张图做到能打印」「做成一整套可打印的」「图片→模型→分色→打印文件」「把 X 做成免胶快拆套件」等要求覆盖全流程时触发。单步需求（只建模、只拆件、只烘焙）直接用 parametric-print-model skill。
metadata:
  type: skill
  scope: global
  agent_created: true
---

# 图片 → 可打印套件：端到端流水线

## 定位与依赖

本 skill 是**编排层 runbook**：把「一张图」变成「一盘一个颜色、打开即打」的 3MF 套件。全部工具与深层细节在兄弟 skill **`parametric-print-model`**（路径 `~/.workbuddy/skills/parametric-print-model/`，与 `~/.claude/skills/` 是同一份软链）：

| 工具/文档 | 用途 |
|---|---|
| `scripts/splitter.py` | trimesh 基元建模、布尔、`lay_flat`/`orient("face")`、球头关节 |
| `scripts/joints.py` | 免胶快拆接头（弹性夹头公头、**卡珠销 = 滑配身 + 公头卡珠**、旧式压配柱只作兜底） |
| `scripts/mate_profile.py` | **装配门禁**：体间隙 / 珠握持 / 轴向余量 / 叶片应变，逐层从实体上量 |
| `scripts/pose_brute.py` | 逐件朝向：离线排序 + 切片器黑箱判决（240 姿态约 65 s） |
| `scripts/bake_project.py` | 切片烘焙 → 3MF（内嵌配置 + gcode + 缩略图），brim / 对象级支撑、判决门禁 |
| `scripts/overhang.py` | 逐层悬空扫描（抓切片器不报警的水平台阶） |
| `scripts/warp.py` | 大平面翘边门禁（`lift = span²/depth` 平方律） |
| `scripts/audit_3mf.py` / `weigh_3mf.py` | 连通性审计 / 克重复核 |
| `references/snap-fit.md` | 免胶快拆设计规则、四条红线、配合校验件 |
| `references/split-to-print.md` | 朝向判决、`warning_message` 判据、凸包陷阱 |
| `references/warp.md` | 翘边判据与 redesign 线 |
| `references/printability-checklist.md` / `delivery.md` | 打印性清单 / 交付口径 |

环境：python 用 `~/.workbuddy/binaries/python/envs/default/bin/python`（numpy/trimesh/shapely/scipy/matplotlib/PIL 都在这里）。切片器 = Bambu Studio CLI（GUI 二进制，**永不退出**，macOS 无 `timeout`，轮询产物后 `killpg`）。

## 流水线总览（八阶段，每阶段有出口判据）

| # | 阶段 | 出口判据 | 失败时的动作 |
|---|---|---|---|
| 0 | 分路冻结 | 建模路线 + 拆件路线已定并告知用户 | — |
| 1 | 图纸化 | 件清单 + 接口清单 + 颜色表成文 | 与用户对齐后冻结 |
| 2 | 参数化建模 | 全件水密、布尔有效、多视图渲染核对通过 | 修几何，不修切片参数 |
| 3 | 分色分盘 | 每件归属唯一盘；brim/支撑目标表写入烘焙脚本 | — |
| 4 | 逐件朝向 | 每件过切片器黑箱判决或 warp 门禁 | 换姿态 / 开对象级支撑 / redesign |
| 5 | 烘焙 | 每盘 `baked.3mf` 落盘且 `result.json` 判决落盘 | 修输入，不修门禁 |
| 6 | 六道门禁 | 全部 PASS（详见 `references/gates.md`） | 逐门禁处置，禁止跳过 |
| 7 | 文档同步 | README / 件表数字与实测一致 | 重扫全部 md 的旧数字 |
| 8 | 交付 | present_files + 打印提示（支撑耗材、拆件手法） | — |

**节奏规则**：一次只推进一个阶段；每阶段产出物先自己核对，再给用户看。建模脚本是唯一几何真源（`snapkit_<name>.py`），所有交付文件从它重跑生成——改几何永远改脚本，不改 STL。

## 阶段 0：分路冻结（不要默认，要判断）

两个判断都在 `parametric-print-model/SKILL.md` 的决策表里，先做完再动手：

1. **参数化建模 vs AI 图生 3D**：球/胶囊/圆柱堆出来的造型走参数化；毛发/人脸/有机曲面才走 AI 图生 3D。用户说「想要能打印」时直接给结论并说明理由，别先问需求。
2. **AMS 单件 / 胶接拆件 / 免胶快拆**：三条独立路线。用户说「分色拆件」= 胶接；说「免胶/卡扣/能拆装」= 快拆；没指定按「有没有 AMS」分路并告知三版存在。

分路结论写进项目 README 的头部，后续所有阶段引用它。

## 阶段 1：图纸化（把图变成数）

从图片提取：三视图比例、件清单（每件一个连通实体）、颜色表、接口清单（谁插谁、压配还是球头）。产出一段成文的「件表」贴进建模脚本头部注释。规则：

- **拆件的单位是「单个连通的可打印实体」，颜色只是属性**。绝不能按颜色壳拆——某个颜色的壳常是十几个悬空碎片。
- 互相插接的件同盘或考虑耗材换色成本；每盘一个颜色是默认布局。
- 接口尺寸走 `snap-fit.md` 的表（球头规格、收口过盈 0.30 mm；小件用**卡珠销**：销身滑配 +0.10~0.15/边、珠 +0.10/边——**不要用负 clearance 的过盈压配**，实物上会变成直径约 0.45 mm 的干涉）；**所有销/柱留 2–2.5 mm 埋入段**（布尔并集对「座位落在配合面上」会静默丢连接）。

## 阶段 2：参数化建模

- 用 `splitter.py` 的基元（球/胶囊/圆柱/圆锥/圆环）+ `P.union/cut` 布尔组合；每件一个 `parts["NN_name"] = dict(name=中文名, colour=..., orient=..., mesh=...)`。
- 布尔后逐件检查水密与连通（解析顶点索引自建 `Trimesh(process=False)` 判连通；**不要用 `trimesh.load()` 判断**，它会静默修复把好件判成散件，见 `audit_3mf.py`）。
- 用 `preview.py` 渲染多视图预览，**给用户人工核对**再进入下一阶段。
- 免胶路线：接头用 `joints.py`；公头开槽弹性夹头（弹性做在公头上，不是母头孔）；先打**配合校验件**定过盈再出全套——校验件档位用通孔标记，不用凸点。

## 阶段 3：分色分盘

- 盘 = 颜色；`bake_project.py` 的 `PLATES` 表定义每盘文件名、brim 目标件；`SUPPORT` 表定义对象级支撑目标件。
- brim 只给首层接触 < ~30 mm² 的件显式写；支撑只给「黑箱搜索无姿态解」的件开对象级（process 全局开关保持 0）。

## 阶段 4：逐件打印朝向

- 默认 `lay_flat`；可疑件（弯管、尖点、大悬挑）跑 `pose_brute.py`——**离线指标只用来排序，判决一律以切片器 `warning_message` 为准**（黑箱判决通常双峰：有解 → 换 `orient="face"`；无解 → 对象级支撑）。
- `orient="face"` 的 `dirvec` 是**叠在 lay_flat 之上的增量**，不是机架绝对方向——喂错会转两次（详见 `references/gates.md` §G2）。
- 每件再过 `warp.py`：`lift = span²/depth`，>1200 → redesign 别调参。
- **切片器不报警 ≠ 能打**：件中部单层水平台阶（截面积一步跳变）切片器和首层门禁都看不见，必须 `overhang.py` 抓（见 `references/gates.md` §G3，第④类报废）。

## 阶段 5：烘焙

`bake_project.py` 一次烘一盘：`--load-settings` 必带（否则挂死）、`--export-3mf` 只收名字、轮询 `baked.3mf` 后**继续等 `result.json` 判决落盘**再 `killpg`。对象级 brim/support 的 XML 拼接必须贴在整个 `<metadata key="name" .../>` 标签**之后**（贴错 → XML 不合法 → 切片器丢全部对象名）。其余坑见脚本 docstring（耗材 `inherits` 链、密度手填 1.6% 谎报、单实例锁假象等）。

## 阶段 6：六道门禁

逐盘执行，判据与命令见 `references/gates.md`。G1 切片器判决（三态，`None` ≠ 通过）→ G2 朝向/黑箱 → G3 逐层悬空扫描 → G4 翘边 → G5 连通性与克重 → **G6 装配配合**（`mate_profile.py`：体间隙 / 珠握持 / 轴向余量 / 叶片应变——顶死的接口再松也合不拢，且 CAD 里看不出来）。门禁的纪律：

- **独立复切 + 独立扫描**，不采信流水线中间留下的结果文件。
- 门禁代码「没读到判决」必须算失败（三态处理），否则失败的切片伪装成干净通过并覆盖交付件。
- 门禁是代码不是 eyeball；每道门禁可脚本化、有退出码。

## 阶段 7：文档同步

交付三件套：`README.md`（给人看的使用说明）、设计决策文档（件表、翻车记录）、验证图（门禁前后对比）。**所有实测数字（克重、板占、时间、支撑标注）以切片实测为准回填**；改完一件，全文 `grep` 旧数字扫残留。

## 阶段 8：交付与打印提示

`present_files` 交付全部 3mf + 验证图。打印提示固定包含：拆支撑从根部剪；树形支撑想徒手撕 → B 嘴装 Bambu **Support for PLA/PETG** 断离式耗材、切片器里「支撑/筏接口」选它（树身仍用本体耗材，**不要把支撑耗材用于 base**）；免胶件装配按 `snap-fit.md` 的手法。
