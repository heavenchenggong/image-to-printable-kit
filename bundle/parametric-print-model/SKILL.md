---
name: parametric-print-model
description: 把参考图/品牌素材做成可直接 FDM 打印的多色 3D 模型——用 trimesh 参数化基元建模（球/胶囊/圆柱/圆锥 + 布尔），按 AMS 颜色分壳，导出带材质的 3MF，并渲染多视图预览。当用户说「照这张图做个能打印的模型」/「把 X 机器人做成 3D 打印件」/「分色拆件」/「AMS 多色模型」/「图片转可打印 3D」，或给出一组角色/吉祥物参考图要求实体化时触发。也用于判断该走参数化建模还是 AI 图生 3D。
metadata:
  type: skill
  scope: global
  agent_created: true
---

# 参数化可打印模型（多色 / AMS）

## 先做这个判断：参数化建模 还是 AI 图生 3D

**别默认上 AI 图生 3D。** 判断标准只看一件事：造型能不能用参数描述。

| 主体 | 走哪条 | 原因 |
|---|---|---|
| 球体/胶囊/圆柱堆出来的机器人、吉祥物、雪人、字母、logo、几何摆件 | **参数化建模（本 skill）** | AI 对平面矢量图的背面全靠编，球体会畸变、对称性保不住、部件位置飘。参数化更快更准，且天然水密 |
| 毛发、褶皱、有机曲面、人脸、手办级细节 | AI 图生 3D（Meshy / Hi3D / MakerLab） | 这些说不清参数，AI 的重建才有价值 |

用户如果已经说「想要能打印」，先按上表分路，**别先问需求**——直接按判断给结论并说明理由。

## 先做这个判断：AMS 单件 / 胶接拆件 / 免胶快拆

这是**三条独立路线**，不是一件事的三个说法。用户说「分色拆件」= 中间那列；
说「免胶 / 卡扣 / 能拆装 / 不要胶水」= 右边那列。用户没指定时，
**默认按「要不要 AMS」分路**，并在交付里说明另外两版的存在。

| | AMS 单件 | 胶接拆件 | 免胶快拆 |
|---|---|---|---|
| 形态 | 一件打完，层内换色 | N 个独立件，打后上胶 | N 个独立件，压合即装 |
| 需要 AMS | 是 | 否 | 否 |
| 调料 | 自重 + purge | 自重 + 接口料 | 自重 + 接口料 |
| 可反复拆装 | — | 不可 | **可，且球关节装配后仍可调角度** |
| 打印朝向 | 一个整体朝向 | 各件独立最优 | 各件独立最优 |

> ⚠️ **不能"按颜色拆成 N 件"**：某个颜色的壳往往是十几个互不相连的碎片，
> 拆出来是含大量悬空岛的废件。**拆件的单位是「单个连通的可打印实体」**，
> 颜色是它的属性。分块后再分配颜色，必要时允许某小块改色以合并件数。
>
> ⚠️ **拆件版的交付单位是「盘」，不是一个文件**：拆完必须按颜色**分盘导出**，一盘一个
> 颜色、每盘对象全部锁 1 号槽（`splitter.write_colour_plates()`）。把几个颜色排在一张
> 床上等于把分色拆件的收益又还回去——擦料塔回来了、每次换色都要 purge（小件的换色
> 废料常比零件本身还重），双喷嘴机器还会因为两卷料温度档不同**直接拒绝切片**
> （「同时打印高温和低温材料可能导致喷嘴堵塞或打印机损坏」）。混色单盘版只留作排布
> 参考，并在交付说明里写明「别拿去打」。

拆件的四条红线、接合件尺寸表、验收清单见 `references/split-to-print.md`
（helper `scripts/splitter.py`）；免胶接口（球关节的四个数字、压配的 0.10 mm、
四条几何红线、插拔扫掠验证）见 `references/snap-fit.md`（helper `scripts/joints.py`）。

## 工作流

1. **读参考图，定基元分解**。在图上标注：哪块是球、哪块是胶囊、哪块是圆锥、各自相对比例。只给正面图时，背面按最合理的方式补（并在交付说明里写清哪部分是推测）。
2. **取真实色值**，别目测。用 PIL `quantize(colors=8, method=MEDIANCUT)` 提主色板，取出现率最高的几档。
3. **写建模脚本**（用 `scripts/ptools.py` 的 helper）。所有尺寸走顶部常量，姿态走明确坐标——用户改一个数字就能出新版。
4. **按颜色分壳**，不是按部件分文件。AMS 打印的真实单位是「颜色」：一个颜色一个壳 = 一个对象 = 一个 AMS 槽位。
5. **导出 3MF**（`scripts/threemf.py`），内嵌 basematerials 色值。同时导 STL 作备份。
6. **渲染四视图预览**（`scripts/preview.py`）自检轮廓，对照参考图改比例，重跑。**必须看图，不能只看 bbox 数字。**
7. **跑可打印性检查**：`references/printability-checklist.md`。**外加切片前门禁**：`warp.py out.3mf`。它问两件事——**会不会翘**（`lift = span²/depth`）和**站不站得住**（`COM` 重心到接地轮廓的余量）。任何一件判成 `redesign` 就**回去改模型或换姿态**（拆掉不承力的底板 / 打断长边 / 加厚 / 倒角；重心在外就用 `splitter.rest_flat` 重摆），不要去调切片参数——见 `references/warp.md`。
8. **拆件路线**：每件查连通性（`n_components`，按坐标焊接）与水密；每件转到各自最优朝向；跑 `footprint` 确认重心落在接地凸包内。
9. **分盘导出**（拆件路线必做）：`splitter.write_colour_plates(plate, out_dir, stem=..., names={hex: slug}, merge={丢: 留})` —— 按颜色拆成 N 个**单槽**文件，每盘重新排板，画面上先看板占再定 `merge`。`merge` 用来把「只有一件的那种颜色」并进另一盘：别为一片眼片单开一整盘。
10. **免胶路线**：先 `joints.check()` 核算应变/过盈，再出**配合校验件**（5 个收口 + 2 个球头），让用户打印确认过盈量，**再出全套**。同时跑一次插拔扫掠（见 `references/snap-fit.md` "验证"）。校验件一律**单色**——形状已足够区分座与销，单色还能拿废料打。
11. **称重**：报任何克重之前跑 `weigh_3mf.py`（带预设后**几秒**/盘）。分盘表里的数字必须是**切片实测**，并写清口径（**机器 / 工艺 / 耗材三个预设名** + 层高 / 墙数 / 填充 / **密度**）。**密度从预设链解析，不要手写**——`--material PETG` 会顺带切到真正的 PETG 预设；PETG Basic 是 **1.25**，不是类目值 1.27。脚本自检里的网格估算只允许标成 `est.`，不能进交付表。
12. **烘焙（交付前必做，否则用户会来问"为什么弹窗"）**：`bake_project.py` 一次调用把纯几何 3MF 变成**切片器自己保存的工程文件**——补上 `Metadata/project_settings.config`、gcode、缩略图，并把 `close_fan_the_first_x_layers=3`（默认是 **1**，第 2 层就吹风扇）、`curr_bed_type`、`filament_type`/`filament_density` 写死。见 `references/delivery.md`。
13. **交付 README**：文件清单、**分盘表（哪盘哪些件 / 板占 / 切片实测克重）**、配色表、切片参数、缩放下限、装配顺序、已知取舍、版权。


## 关键工程约定

### 布尔是朋友，重叠不是
允许基元互相穿透，但**同一颜色壳内**必须布尔合并（`union`）成水密实体。两个不同颜色壳之间可以重叠——但**绝不能是「0.7mm 薄膜套在另一个实体外面」**：那在切片器里就是一堵独立薄墙，打出来一碰就掉。

正确做法是让两个壳**在同一位置对切**：给球体做 `intersection(sphere, halfspace(z_lo=seam-0.3))` 和 `intersection(sphere, halfspace(z_hi=seam))`，让接缝处只重叠 0.3mm。

### 分色接缝尽量落在水平面
按对象分色时，换色发生在整层边界 → purge 最少。斜接缝或逐层穿插会让换色次数爆炸。

### 姿势为打印服务
免支撑的姿势：手臂下垂、外张 <30°、没有朝下的平面。要动感姿势就得开支撑——说明白用辅助喷嘴 + Support for PLA 撕干净，别为了免支撑把造型做丑。

### 悬浮造型要落地
原设悬浮（脚下涟漪、无腿）的吉祥物必须给底座或拆件，否则打不出来也站不住。

## 脚本

- `scripts/ptools.py` — `vec` / `frame` / `place(oriented)` / `union` / `chain_capsules(沿折线做粗细渐变的管，做犄角手指)` / `bezier` / `halfspace` / `cn(圆锥，已居中)` / `frustum(锥台，做 45° 台阶与倒角)` / `chamfered_base(带 45° 底倒角的圆柱)`
- `scripts/threemf.py` — `write_3mf(shells, out, filaments=[(label, hex), ...])`。自建 3MF：`<basematerials>` + 每色一个 `<object>`，**外加 `Metadata/model_settings.config`**（对象名 + 耗材槽位号）—— `basematerials` 在 Bambu Studio 里不生效，那份 config 才是让切片器显示正确名字和槽位的唯一途径。`filaments` 是**交付契约**：它定槽位顺序，用户必须按这个顺序上料。
- `scripts/preview.py` — 多视图正交预览。`views=[(name, azim[, elev])]`，扁件用 `elev≈74` 看俯视。
- `scripts/splitter.py` — 拆件专用：`slab`（切装配平面）/ `cyl_d`（按"沿某方向 20→32 mm"指定销与孔）/ `n_components`（按坐标焊接的连通性）/ `footprint`（接地面积 + 重心是否在接地凸包内）/ `footing_margin`（**重心到接地轮廓的带符号余量**，负 = 会翻）/ `overhang_area`（向下的悬垂面积 ≈ 支撑量）/ `lay_flat`（摆平，站不稳 / 接地面 `MIN_CONTACT`=5 mm² 以下时**自动回退**到 `rest_flat`）/ `rest_flat`（**在凸包面里搜真实落座姿态**；判"有没有面贴板"用 `CONTACT_TOL`=0.10 mm，判"重心偏不偏"用 0.6 mm——**两把尺子别混**）/ `upright` / **`orient(spec="face", dirvec)`**（朝向由切片器实测选定时的入口；`dirvec` = **要贴床的那个方向，是叠在 `lay_flat` 之上的增量**——候选是在**已导出的打印姿态 STL** 上判的，当成机架绝对方向会**转两次**）/ `mirror_twin` / `write_kit`（排板 + 导出）/ `write_colour_plates`（**按颜色拆成 N 个单槽 3MF**：`merge={丢: 留}` 并颜色，`names={hex: slug}` 定文件名，返回每盘的件数/板占/估重）
- `scripts/joints.py` — 免胶接口：`ball_pin`（**开槽弹性夹头**，`bury` 埋入宿主件）/ `ball_socket`（刚性球腔，孔口自动外切 1 mm）/ `press_peg` / `press_hole`（过盈可负）/ `snap_skirt` / `boss_groove` / `taper_pin` / `check`（应变与过盈核算）/ 常数 `JOINT_S` `JOINT_XS` `JOINT_XXS`
- `scripts/test_joints.py` — 改过 `joints.py` 之后必跑：算应变、查水密、**切一片看夹头是不是真的 4 片叶子**、量压配的实际过盈量。`ALL PASS` 才算过。
- `scripts/warp.py` — **切片前必跑**：量接地面的三个数（`contact` 面积 / `span` 最长连续跨度 / `depth` = 件体积÷接地面积），算 **`lift = span²/depth`**、分档 `ok | brim | redesign`；**同时报 `COM`**（重心到接地轮廓的余量，负 = 会自己翻倒）。退出码可当门禁（ok 0 / brim 1 / redesign 2）。**翘边九成是几何问题，先看这个再动切片参数。**
- `scripts/overhang.py` — **切片前必跑（跟 warp.py 配一对）**：查**件中部一圈水平台阶**。`warp.py` 只管首层、切片器只管整块脱离的岛，**这两类之间的第三种报废没人管**：台阶上下都连着料、切片器不报警、也不在首层，但那一圈之下是空的，头几层就是往空气里挤丝。实测 winston P2 `01 底座+下身罩` 在 z=21.0 由 Ø28 一步跳到 Ø36（**单层 +4.035 mm 半径 / 406 mm²**），切片器判「无警告」，实物颈口一团丝。判据：watertight 用真实截面的 `area`（新出现悬空面积）+ `span`（离下层最近材料多远 = 真实悬挑跨度）；不封闭退回**只用面积**的 `Δr`（等效半径单层增长）/`ΔA`。退出码 0/2 可当门禁。⚠️ 它**不能**用凸包——凸包会把弯折件的两条肢桥起来（手臂实测凸包报 101.8 mm²/7.04 mm，真实只有 0.01 mm/层）；也**必须**给 `Path3D.to_2D()` 显式传 `plane_transform`，否则每层坐标系都不一样。**修法**：把台阶改成 `ptools.frustum()` 做的 **45° 肩台**（锥台两端都要埋进相邻实体，别让端面落在表面上）。
- `scripts/audit_3mf.py` — **交付前必跑**：逐对象判断"是不是一个水密的可打印实体"。自己解析 `3D/3dmodel.model` 的顶点/索引再建 `Trimesh(process=False)`（**不能用 `trimesh.load()`**，载入路径会做修复，好件也会被判成散件）。退出码可当门禁。
- `scripts/pose_brute.py` — **朝向拿不定时跑**：离线给 200–300 个候选姿态排序（真实首层截面 + "连续 ≥2 层"的浮空体积），再**逐个丢给切片器判**（`result.json → sliced_plates[0].warning_message`，空串 = 干净）。一个 45 mm 件切一次只要 **1.4 s**，8 进程并行一分多钟出结果。判决通常**双峰**：找到放行姿态 → 用 `orient("face", dir)` 写进件定义；**一个都不放行** → 别继续找，改成给**这一件**开对象级树形支撑。⚠️ **`footprint()` 是代理、不是判决**：凹底面上它**两个方向都会错**（实测犄角 153→18.4 mm² 虚高 8 倍、耳鳍 1→4.35 mm² 虚低 4 倍）。**虚高最危险**——它会把"刀尖上的姿态"判成"站得挺好"，`MIN_CONTACT` 那道门形同虚设。详见 `references/split-to-print.md`。
- `scripts/presets.py` — **预设解析的唯一一份实现**，`weigh_3mf.py` 与 `bake_project.py` 共用（否则"烘焙时的口径"和"称重时的口径"会漂，实测漂过 5%）：`find_preset(kind, stem, prefer=...)`（**打分挑最近的子串命中**，否则会落到 `Bambu PETG Basic @BBL A1 0.2 nozzle` 这种错喷嘴的文件）/ `flatten_preset`（**祖先→子逐层合并、子覆盖父**——这是让 CLI 拿到正确热床温度的关键）/ `resolve_filament`（沿链取 `filament_type` 与密度）/ `write_presets`（把 machine / process / filament 三条链写成扁平化副本）/ `preset_args` / `read_result_error`（读 `result.json`，**判 `return_code` 而不是 `error_string`**）。
- `scripts/weigh_3mf.py` — **报克重前必跑**：调 Bambu 内核切片，累加 gcode 的 E 值算出**真实**挤出体积与克重。⚠️ **`--slice` 必须带 `--load-settings "<machine>;<process>"` + `--load-filaments "<filament>"`**，否则它会停在 `Initializing StaticPrintConfigs` **永久等待**：不报错、不产 gcode、退出码 0、进程也不退。**那条日志极易被误诊成"GUI 占着单实例锁"——GUI 早关干净了照样卡，真凶就是缺预设**（我们为此白等了两轮 5 分钟）。密度**沿预设 `inherits` 链解析**（PETG Basic 1.25 / PLA Basic 1.26，都不是类目值），`--material` 会顺带选对预设，`--density` / `--filament` / `--printer` / `--process` 可覆盖。**别用网格体积乘系数报克重**：同一套件实测，校验件估 7 g / 实测 17 g，P2 盘估 43 g / 实测 34 g——Ø16 小柱的墙吃掉截面 ~67%、大空心球 ~20%、2.5 mm 薄板 ~100%，**一个系数不可能同时对上**。
- `scripts/bake_project.py` — **交付前必跑**：把纯几何 3MF 烘焙成"切片器自己保存的"工程文件（补 `Metadata/project_settings.config` + gcode + 缩略图），顺手把 `close_fan_the_first_x_layers` / `curr_bed_type` / 按对象 brim 写进去，回读校验后才覆盖原文件。坑与验收清单见 `references/delivery.md`。

## 踩过的坑

渲染与导出陷阱见 `references/printability-checklist.md` 末尾：matplotlib 3D 有两个必踩的坑（多 collection 无深度排序、预投影后再 view_init 会二次投影），以及 trimesh `capsule()` 的参数名不是 `sections`。

拆件陷阱见 `references/split-to-print.md`（销孔比肢体粗会切断、销孔埋在内部是空腔、附录件不能穿进主体、镜像件要复用朝向）。

免胶接口陷阱见 `references/snap-fit.md`（让母头让开 = 让它永久屈服；收口只开一半会让球腔侧壁变成隐形收口；球赤道必须落在收口后面；销根不埋 = 布尔静默丢连接）。

翘边与站姿陷阱见 `references/warp.md`（按"坏在哪"分诊；`lift` 判据与 COM 站姿判据；四种几何改法；倒角与埋入段两个静默坑；Bambu 参数表；X2D 腔体开/关方向相反；**`--slice` 不带预设会永久卡住、且伪装成"GUI 占锁"**）。

交付与烘焙陷阱见 `references/delivery.md`（两种 3MF 布局与假阴性；烘焙三步；七条参数级坑，含"拒绝伪装成挂起"；验收清单）。

另外这些通用坑：

- **为了"连成一个对象"而加的实心底板，是把翘边风险打包进来**。Winston v1 校验件把 5 个缩口座摆在一块 102 × 28 × 2.5 mm 的底板上，实测打废：底面长边翘起，喷嘴每趟刮过，沿边一整排料被压糊拉断，两个 Ø20 销圆盘的边缘也翘了。量出来 `span 102 mm / depth 6.0 mm → ratio 17`（门禁阈值 12）。底板**根本没在承力**——它只是让 5 个 Ø16×21 的柱子（高宽比 1.3:1，推不倒）连成一个对象。删掉后：接触面积 3484 → 1206 mm²，span 102 → 14.8 mm，全部转 `ok`。**动手之前先问：这个底板真的在承力吗？**
- **量翘边要看 `lift = span²/depth`，不是比值，也不是只看尺寸**。翘起量 h ∝ ε·span²/depth，随跨度的**平方**增长。判据第一版写成 `span/depth`，把 Ø35×2.8 的瞳孔薄片判成 `redesign`，而真正打废的 102 mm 底板也只是"超线"——**比值型判据分不出"小一号"和"小一个量级"**（瞳孔的实际风险是底板的 (35/102)² = 1/11）。虚警会让人白白返工好件。另外补一条下限：**`depth ≤ 4.5 mm` 的薄片一律 brim**，它自己那点刚度不解决问题。**不要把 `depth` 用顶点包围盒去估**：trimesh 圆柱只有上下端盖有顶点，Ø16×18.5 的实心柱会被估成 0.6 mm 薄片而误判为「软」。用体积÷面积，薄板得到板厚、实心柱得到柱高，两种极端都对。
- **还要问一句"它站得住吗"**：`warp.py` 的 `COM` = 重心到接地轮廓边缘的余量，**负 = 会自己翻倒**，切片器里完全看不出来（brim 是为旧姿态打的，翻过去就废）。**别用接地面积判断稳不稳**：圆柱躺下是线接触、面积≈0 但稳得很；一个桶可以有一大片接触面却只压在棱上。Winston 免胶版被抓出两件（犄角 COM −2.1 / 耳鳍 −2.4），胶接版又抓出一件（犄角底面只有 **3 个顶点**碰板），改姿态后 +2.8 / +1.9 / +4.5。修法一律是 `splitter.rest_flat()`（在**凸包面**里搜哪个面朝下能让重心落在轮廓内），不要手调角度。
- **两把尺子，混用就会把坏件当好件（这条最贵）**：判断"姿态到底有没有面贴板"必须用 **`CONTACT_TOL = 0.10 mm`**，判断"重心偏不偏"才轮到 0.6–0.8 mm。胶接版犄角那 0.55 mm 高、根本够不着板的一圈顶点，在 `footprint()` 默认的 0.8 mm 容差下被算成 **80 mm² 的接地面**——于是摆位器认为它"站得挺好"，`rest_flat` 不触发，**第一轮完全没抓到**。真实数字：0.10 mm 容差下它的接地面是 **0.07 mm²**。同一把尺子也要贯穿门禁与摆位器，否则会出现"门禁说会翻、摆位器说没问题"然后卡死（耳鳍第一次重建纹丝不动就是这个）。详见 `references/warp.md`。
- **不要把求解器复制进项目脚本**。Winston 的 `snapkit_winston.py` 当初把 `lay_flat` 抄了一份，修库里那份对脚本完全不生效（重建后两件姿态一字未变，排查了一轮）。项目脚本里的 `footprint / sit / lay_flat / upright` 一律改成调用 `splitter`。
- **倒角必须切出来，不能并上去**。在圆柱底下并一个锥环，圆柱自己那圈完整底面还在，等于什么都没做（接触面积一个字节都不会变）。正确做法是 `outer_ring - inner_taper` 得到一把刀，再从圆柱里减掉。`ptools.chamfered_base()` 已经封装好。
- **克重必须来自切片器，不能用网格体积乘系数**。同一套件实测：校验件网格估 7 g / 切片实测 **17 g**（差 2.4 倍，用户当场发现的）；P2 暗色盘估 43 g / 实测 34 g。原因：Ø16 小柱的 2–3 圈墙 + 顶底实心吃掉 ~67% 截面，大空心球 ~20%，2.5 mm 薄板 ~100%。报数前跑 `weigh_3mf.py`（带预设几秒/盘 + 累加 gcode 的 E 值），并注明**预设口径**与密度；`splitter.plastic_g` 只能进脚本自检并标 `est.`。
- **弹窗「3mf 文件配置无效，仅加载几何数据」必须烘焙掉，别教用户"点掉就行"**：官方英文原文 "The 3mf is not from Bambu Lab, load geometry data only"，论坛管理员原话 "Consider it as 'information'"——它确实是**身份提示不是错误**。但那是对"自己写着玩的文件"成立；对**交付给用户去打印**的文件，它只是噪音：用户看到红字就会来问，而你只能回"点掉就行"。根因是文件里没有 `Metadata/project_settings.config`，**手工拼那个文件是自找麻烦**（版本串、预设 id、569 个键、继承链），正解是让切片器写自己的文件（`--slice --export-3mf` 一次调用，包成员 4 → 17–20）。**能一次做掉的不要留给用户**——多跑一次切片器，换掉一次"为什么又弹这个"的往返。完整流程、六条坑与验收清单见 `references/delivery.md`。
- **工程式 3MF 会让校验脚本静默假阴性（这条最隐蔽）**：切片器 `--export-3mf` 的产物是**工程式**布局——几何在 `3D/Objects/object_N.model`，主模型里只剩 `<component p:path="..."/>`，摆位在 `<build><item transform="...">` 里（分件存**局部坐标**，z 可以是负的）。只读 `3D/3dmodel.model` 的脚本会：**一个对象都读不到 → 门禁打印 "all clear" 却一件没查**；勉强读到分件又没应用 transform → 误报 "verts below the bed"。**假阴性比假阳性危险，它让你以为验过了。** `audit_3mf.py` / `warp.py` 现在两种布局都读，并按 `v @ M[:3,:] + M[3,:]` 应用 build transform。
- **CLI 不解析 filament 预设的 `inherits`，而且只漏掉你不看的字段**：只加载 `Bambu PETG Basic @BBL X2D 0.4 nozzle.json` 时 gcode 里是 `filament_density: 0` / `total filament weight [g]: 0.00`——工程显示 **0 g**。而**真正驱动刀路的字段正常透传**（风扇 60%、喷嘴 250 ℃、flow 0.95），**顺手会去看的那些都对，所以洞很难发现**。切片前必须把 `filament_type` / `filament_density` 显式写进预设副本。
- **密度不要手写，最近定义赢且它不是类目值**：`Bambu PETG Basic @base` = **1.25**，覆盖 `fdm_filament_pet` 的 1.27；`Bambu PLA Basic @base` = **1.26**，覆盖 `fdm_filament_pla` 的 1.24；`fdm_filament_common` 的 0 是占位要跳过。按类目值手写 1.27，交付文件读出的克重就比切片器高 **1.6%**——用户拿这个数判断料够不够。`bake_project.py` / `weigh_3mf.py` 都有 `resolve_filament()` / `resolve_density()` 沿链解析。
- **`close_fan_the_first_x_layers` 默认是 1，这是"底部打烂"的头号原因**：Bambu 第 2 层就开风扇（实测 `M106 S104`），底面还没定型就被吹冷收缩。设 3 之后第 1/2/3 层 `S0`、第 4 层才 `S255`。它在 GUI 里藏在"冷却 → 前 N 层不吹风扇"，**默认值不会自己是对的**——烘焙时写死进文件。
- **切片器的 `auto_brim` 与翘边门禁是两套判据，不重合**：`auto_brim` 问的是"会不会被喷嘴带飞"，`warp.py` 问的是翘边（`lift = span²/depth`）。实测同一盘里 `06 耳鳍`/`05 犄角`/`07 手臂` 拿到了自动 brim，而门禁判成 `brim` 档的 `04 瞳孔` **没有**。**两边都跑，谁判 brim 谁加**，别把自动 brim 当门禁的替代。
- **`--export-3mf` 收的是文件名，不是路径**：CLI 会把它拼到 `--outputdir` 后面。传绝对路径 → 变成 `<outputdir>/<abs path>` → `Unable to open the file ...tmp`。
- **不能 `rmtree` 脚本自己的 cwd**：子进程 `cwd=` 指向已删 inode，切片器报 `Could not determine canonical path to application directory` / `setup params error`，而且**脚本文件本身可能一起被删掉**。工作目录用 `/tmp/<something>`。
- **往 XML 里插 metadata 必须接在完整标签之后**：只替换正则命中的片段会得到 `<metadata key="name" value="x" <metadata .../>`——非法 XML。切片器的反应是**静默丢掉所有对象名**，交付文件里变成 `Object_3`/`Object_4`，和你起的 `04 瞳孔` 全无关系。替换**完整标签** + 加 `ET.fromstring()` 守卫；注入还必须**幂等**（交付文件本身是工程式，"再烘焙一次"是常规操作）。
- **热床档位是枚举序号，不是名字**：二进制字符串表顺序（0 起）为 `Default → Cool Plate → Engineering Plate → High Temp Plate → Textured PEI Plate → Supertack Plate`，配置里存的是这个序号。要确认某个档，最硬的证据是**改一份机器预设副本 → 看 gcode 的 `M190`**（实测 Textured PEI + PETG Basic → `M190 S70`）。⚠️ **热床温度不在机器预设链里，在耗材预设链里**（`textured_plate_temp`，PETG Basic 70 / PLA Basic 55）：CLI 不解析 `inherits` 时会落到硬编码的 **45 ℃**——我们第一版交付文件就是 `M190 S45`，**差 25 ℃、PETG 必然粘不牢**，而"真正驱动刀路"的喷嘴温度 250 / 风扇 60% 却全部正常透传，所以极易漏看。`bake_project.py` 现在把 machine / process / filament **三条链全部扁平化**再交给切片器。预设默认是 Cool Plate 35 ℃，**换料不改板型就前功尽弃**。
- **切片器"拒绝"一个作业时不失败，而是伪装成挂起**：板型与耗材不匹配（如 PETG 配 Cool Plate）时，它把原因写进工作目录的 **`result.json`**（`error_string` + `return_code: -61`），**然后进程不退出**。从外面看，"拒绝"和"卡住"一模一样，所以很容易当成超时去查别的地方。判据必须是 **`return_code`，不能用 `error_string` 的真值性**——**成功时那个字段也是 `"Success."`**（第一版这么写，成功也被判成 `refused: Success.`）。而 `slice.log` 永远是**一行**（`Initializing StaticPrintConfigs`，92 字节），**成功与失败一字不差**，根本不能用于诊断。`presets.read_result_error()` 读 `result.json`，`weigh_3mf.py` / `bake_project.py` 每轮轮询它：不支持的板从 180 s 超时降到 **1.2 s 报错**。
- **校验件 / 测试件一律单色**，别为了"好看"给它两个槽位。实测：把配合校验件写成「红=座 / 蓝=销」，X2D 直接报 **「同时打印高温和低温材料可能导致喷嘴堵塞或打印机损坏」** 拒绝切片——两个槽位会触发双喷嘴的高低温材料检查，颜色本身完全没必要（形状已经把座和销分开了），单色还顺带可以用废料打。**混色单盘也不等于分色拆件**：分色拆件的收益是零 purge / 零擦料塔 / 各件最优朝向，三色排一张床会丢掉前两个。回环验证 `slots_used = {1}` 才算过。
- **`<basematerials>` 在 Bambu Studio 里根本不生效**，而且**伪装成成功**：3MF 合法、几何正常载入，只是颜色全丢、对象名退化成 `Object_N`。唯一生效的是 `Metadata/model_settings.config` 里的 `extruder`（= 耗材槽位号）。**别内嵌 `project_settings.config`** —— Bambu 会丢掉它、保留自己的预设。
- **交付前用切片器自己的内核验收，别只做 zip/XML 校验**：
  `BambuStudio --info x.3mf`（看 `manifold` 和 `number_of_parts`，应是每对象 1 个连通体），
  `BambuStudio --export-3mf rt.3mf --outputdir /tmp/out x.3mf` 再回读 config 核对名字与槽位。
  CLI 是 GUI 程序永不退出，macOS 没有 `timeout` → 后台起 + `sleep` + `kill`。
  这两个子命令**不需要预设**；**`--slice` 需要**（缺 `--load-settings` / `--load-filaments` 会停在 `Initializing StaticPrintConfigs` 永久等待，别误诊成 GUI 占锁）。
- **件上的档位标记做通孔，不做凸点**：同色凸点与底板法向相同 = 同亮度，俯视和手里都读不出来。
- **"件是不是一个连通实体"不能用 `trimesh.load()` 判断**：STL / 3MF 的载入路径会合并顶点、删退化面，**修复本身会把好件的边配对搞坏**——`is_watertight` 变 False、`split()` 编出几十个零体积"碎片"。同一套件实测：载入路径说 10 件里 8 件坏，按文件自带索引重建后 10 件全水密、各 1 个实体，切片器内核也判 `manifold = yes`。要判断就自己解析 XML 建 `Trimesh(process=False)`（`audit_3mf.py`），并以 `BambuStudio --info` 的 `number_of_parts` 为准。
- **销与宿主的端面精确共面 = 静默出碎片**。`.stl` 层面看不出，进程内的连通性检查也看不出，**只有切片器的 `number_of_parts` 会报**（犄角报 5）。凡是"销插进某个凸台/项圈"的场合，销必须多伸进宿主 **2–3 mm 造成真实重叠**。
- **trimesh 的 `cone()` 底面在 z=0、尖端在 +h**（cylinder / capsule 却是居中的）。直接用会把圆锥按"从 `at` 再长 h"放置，**静默地和本该连接的件脱开**。`ptools.cn()` 已经补了 `-h/2`，别绕过它直接用 `trimesh.creation.cone`。
- **座标重合的两个实体，并集是"两个实体共享一个面"**，布尔会静默丢掉这条连接。
  凡是"公头从宿主件表面长出来"的场合（销、立柱、凸台），公头的根部都要**多埋 2 mm**。
- **检查件是否连通不能按共享顶点索引做**：布尔接缝处的近重复顶点会误报悬空岛。
  按坐标 round 后焊接再数（`splitter.n_components` / 脚本里的 `n_components`），或装 scipy 用 trimesh 的图算法。
- **matplotlib 默认字体没有中文**，中文标签会变成豆腐块。`preview.py` 已在 `rcParams["font.family"]` 里挂了 PingFang SC / Hiragino Sans GB 等候选；自己画标注图时同样要挂。**标注图别用 `Poly3DCollection` 挂在 2D `Axes` 上**（`Axes` 没有 `get_proj`）——投影到 (x, z) 后用 2D `PolyCollection`，按深度排序，远的面先画。

