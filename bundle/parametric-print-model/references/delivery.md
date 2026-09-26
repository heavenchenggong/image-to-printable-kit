# 交付：让文件打开就能打（烘焙）

`threemf.write_3mf()` 产出的是**纯几何** 3MF（`3D/3dmodel.model` + `Metadata/model_settings.config`）。
Bambu Studio 打开它会先弹一句：

> **3mf 文件配置无效，仅加载几何数据**
> （官方英文 "The 3mf is not from Bambu Lab, load geometry data only"）

这句是**身份提示不是错误**——论坛管理员原话 "Consider it as 'information'"，Bambu 对任何
**不是自己保存的** 3MF 都弹。但对一个"交付给用户去打印"的文件来说，它是纯噪音：用户看到
红字会来问，然后你只能回"点掉就行"。**能一次做掉的事不要留给用户。**

helper：`scripts/bake_project.py`。

---

## 一、根因与两条布局

3MF 有两种布局，**交付前必须分清**：

| | 几何式（自己写的） | 工程式（切片器 `--export-3mf` 的产物） |
|---|---|---|
| 几何在哪 | 内联在 `3D/3dmodel.model` 的 `<mesh>` | 分件文件 `3D/Objects/object_N.model`，主模型里只有 `<component p:path="..."/>` |
| 摆位 | 顶点即世界坐标 | 分件存**局部坐标**，靠 `<build><item transform="...">` 落到板上 |
| `Metadata/project_settings.config` | **没有** ← 弹窗的原因 | 有（569 键） |
| 成员数 | 4 左右 | 17–20（多出 gcode、盘缩略图、slice_info） |

⚠️ **这两个差异会让校验脚本静默假阴性。** `audit_3mf.py` / `warp.py` 早期都只读
`3D/3dmodel.model`，遇到工程式文件时：

- 组件引用被跳过 → 一个对象都读不到 → 门禁打印 "all clear"，其实**一件都没检查**
- `<item transform>` 没应用 → 分件报 z 为负 → 误报 "verts below the bed"

**假阴性比假阳性危险得多**：它让你以为验过了。两端都已修成"两种布局都读 + 应用 build transform"。

---

## 二、烘焙：让切片器自己写配置

手工拼 `project_settings.config` 是自找麻烦（版本串、预设 id、569 个键、继承链）。
正解是**让切片器写它自己的文件**——它写出来的东西它自己必然认：

```bash
BambuStudio --load-settings "<machine>.json;<process>.json" \
            --load-filaments "<filament>.json" \
            --slice 1 --export-3mf out.3mf --outputdir <dir> in.3mf
```

一次调用同时产出：`project_settings.config`、`model_settings.config`（含对象名与槽位）、
盘 gcode、盘缩略图、`slice_info.config`。文件打开不再弹窗，**而且是已经切好的**——
用户拖进去直接看见预览和耗材用量。

`bake_project.py` 把这件事包成一条命令，并做三件额外的事：

1. **纯几何源快照**（`geo_src/`）——交付文件本身就是工程式了，不另存一份纯几何原版，
   第二次烘焙就会**把上一次的输出喂给切片器**；
2. 切片**前**按门禁结论注入**按对象**的 brim（`warp.py` 说 `brim` 的件才注）；
3. 切片**后**回读 `project_settings.config` 校验：`config` 在不在、gcode 有没有、
   `close_fan_the_first_x_layers`、`curr_bed_type`、`filament_type` / `filament_density` 对不对。

### 该烘焙什么进去

| 参数 | 为什么必须写死进文件 |
|---|---|
| `close_fan_the_first_x_layers = 3` | **默认是 1**：Bambu 第 2 层就开风扇（实测 `M106 S104`／`S255`），底面还没定型就被吹冷收缩。这是"底部打烂"的头号原因，而它在 GUI 里藏在"冷却 → 前 N 层不吹风扇" |
| `curr_bed_type` | 预设默认是 Cool Plate（35 ℃）。换料不改板型就前功尽弃——PETG 在 35 ℃ 上粘不住 |
| `filament_type` / `filament_density` | CLI 不解析 `inherits`（见下节），不显式写进去工程会显示 **0 g** |
| 按对象 brim | 切片器的 `auto_brim` 判据是"会不会被喷嘴带飞"，门禁 `warp.py` 的判据是翘边（`lift`）。**两套判据不重合**：薄片件经常拿不到自动 brim，必须显式注入 |

> **不要把自动 brim 当成门禁的替代。** 实测同一盘里 `06 耳鳍`/`05 犄角`/`07 手臂` 拿到了
> 自动 brim，而门禁判成 `brim` 档的 `04 瞳孔` **没有**。两边都要跑，谁判 brim 谁加。

#### 按对象树形支撑（`SUPPORT`）

`SUPPORT = { "<盘文件名>": ["<对象名子串>", ...] }` → 给这些对象注入
`enable_support=1` + `support_type=tree(auto)` + `support_threshold_angle`。

**什么时候加：切片器自己说了算。** 它的原话就是判据，抄进代码注释里备查：

```
It seems object 07 手臂R has floating regions.
Please re-orient the object or enable support generation.
```

它是**唯一**能算"这条挤出路径下面到底有没有东西托着"的权威——几何启发式（包括
`overhang.py`）都只是代理，实测两边**不重合**：`07 手臂` 被切片器判浮空区，
而 `overhang.py` 只给出 4 mm 跨度 / 2 mm²（面积太小、不构成结构问题），反之亦然。
所以分工是：`warp.py` 管翘边、`overhang.py` 管中段台阶、**切片器管浮空区**。

两条纪律：

- **先试改姿势，再上支撑。** 姿势是 200–300 方向搜出来的（`pose_brute.py`）就说明搜过了，
  这时才轮到支撑。手臂是 240 方向里的赢家（首层接触 132 mm²），没得再摆 → 开支撑。
- **同名的左右镜像件要一起命中**（needle 写 `"手臂"` 就同时命中 `07 手臂L` / `07 手臂R`），
  否则只支撑一半，另一半照样报浮空区。

⚠️ `inject_support` 和 `inject_brim` 是同一条 splice：**必须接在完整标签之后**，
只替换匹配到的那截子串会写出 `<metadata key="name" value="x" <metadata .../>`，
XML 不再合法 → 切片器**丢掉所有对象名**，交付件里全变成 `Object_3`、`Object_4`。

### 换机器 / 换料 / 换板

`--printer` / `--process` / `--filament` / `--bed` / `--fan-layers` / `--brim` / `--brim-width`。
`--filament-type` 和 `--density` **不用手填**——脚本沿 `inherits` 链自己解析（见下）。

---

## 三、七条坑（每条都花了一次运行）

### 1. `--slice` 不带 `--load-settings` 会**永久卡死**

停在 `Initializing StaticPrintConfigs`：不报错、不产 gcode、**退出码 0**、进程也不退。
**它看起来极像"GUI 占着单实例锁"——第一版就是这么误诊的，错的**：GUI 早关干净了照样卡
（`lsof -c Bambu` 空、配置 mtime 停在关闭那刻），非沙箱下也卡。真因是缺打印机预设，
补上后同一条命令 **~2 秒出 gcode**。

> 附带：**沙箱可能禁掉 `ps`**（`operation not permitted`），只靠 `ps` 的预检会静默返回
> "没开"。改用 `lsof`，并且只当提示、不当门禁。

⚠️ **那一行日志不是诊断依据。** CLI 的 stdout/stderr **永远只有一行**
（`Initializing StaticPrintConfigs`，实测两个场景都是 92 字节），因为我们在它继续输出之前
就 `SIGKILL` 了进程组——**成功的那次日志和失败的那次一字不差**。拿日志去猜原因是一条死路，
只能回去查输入。实测排除掉的几个嫌疑人（**都不是**真因）：

| 假设 | 实测 |
|---|---|
| 内嵌 `filament_density: 0` 导致死循环 | 切成 0 照样出 gcode（只是克重读 `0.00 g`） |
| 工程文件缺 `plate_*.gcode` | 删掉 gcode 照样切得出来 |
| 上面两条同时成立 | 照样切得出来 |
| 归档里的 `winston_fit_coupon_v1_bambu_DO_NOT_PRINT.3mf` | **真凶后来找到了**：它内嵌 `curr_bed_type = Cool Plate`，PETG 被切片器**拒绝** → 见第 7 条 |

**结论：留着超时、失败就报错并列出要检查的输入，别指望日志。** 遇到说不清的挂起，
重新烘焙一遍（`bake_project.py`）通常就好了。

### 2. `--export-3mf` 收**文件名**，不接路径

CLI 会把它拼到 `--outputdir` 后面。传绝对路径 → 变成 `<outputdir>/<abs path>` →
`Unable to open the file ...tmp`。

### 3. 不能 `rmtree` 脚本自己的 cwd

子进程 `cwd=` 指向已删 inode，切片器报
`Could not determine canonical path to application directory`（或 `setup params error`）。
工作目录用 `/tmp/<something>`，别用项目目录。

### 4. CLI **不解析** filament 预设的 `inherits`

只加载 `Bambu PETG Basic @BBL X2D 0.4 nozzle.json` 时：

```
; filament_density: 0
; total filament weight [g]: 0.00
```

工程显示 **0 g**。而**真正驱动刀路的字段会正常透传**：风扇 60%、喷嘴温度、flow ratio…
**你顺手会去看的那些都对，所以这个洞很难发现。** 切片前必须把 `filament_type` /
`filament_density` 显式写进预设副本。

### 5. 密度**不要手写**——最近定义赢，而它不是类目值

实测（`profiles/BBL/filament/`）：

```
Bambu PETG Basic @BBL X2D 0.4 nozzle   -- (无)
  Bambu PETG Basic @base                1.25   ← 生效
    fdm_filament_pet                    1.27   ← 类目值，不生效
      fdm_filament_common               0      ← 占位，跳过
Bambu PLA Basic @base                   1.26
  fdm_filament_pla                      1.24
```

按类目值手写 1.27，交付文件里读出来的克重就比切片器高 **1.6%**——用户是拿着这个数
去判断"料够不够"的。`bake_project.py` / `weigh_3mf.py` 现在都有 `resolve_filament()` /
`resolve_density()` 沿链解析（跳过 0 占位）。

### 6. 按对象 metadata 必须**接在完整标签之后**

只替换正则命中的片段会得到
`<metadata key="name" value="x" <metadata .../>` —— **非法 XML**。

切片器的反应是**静默丢掉所有对象名**，交付文件里变成 `Object_3`、`Object_4`…，
和你精心起的 `04 瞳孔` 全无关系。`inject_brim()` 改成替换**完整标签**，并加了
`ET.fromstring(ms)` 合法性守卫。

> 顺带：注入必须**幂等**（`key="brim_type"` 已存在就跳过）。交付文件本身是工程式，
> "再烘焙一次"是常规操作，不幂等会往同一个对象上叠第二对 `brim_type`/`brim_width`。

### 7. 切片器**拒绝**一个作业时不失败，而是伪装成挂起

这就是上面那个"跑满超时、原因没查"的归档件的真凶，也是**最容易和第 1 条混为一谈**的一种。

板型与耗材不匹配时（PETG 配 Cool Plate），切片器打印

```
got error when validate: Plate 1: Cool Plate does not support filament 1
run found error, exit
```

把原因写进工作目录的 **`result.json`**：

```json
{"error_string": "Filaments are not compatible with the plate type.",
 "return_code": -61}
```

**然后进程不退出。** 从外面看，"拒绝"和"挂起"一字不差——所以第 1 条的修复（补预设）
对它完全无效，会让人一直在预设上打转。

正解是**每轮轮询 `result.json`**（`presets.read_result_error()`；`weigh_3mf.py` 与
`bake_project.py` 都已接入）：不支持的板从 **180 s 超时降到 1.2 s 报错**。

> ⚠️ **判据是 `return_code`，不是 `error_string` 的真值性。** 成功的那次 `error_string`
> 也是 `"Success."`——第一版写成 `if d.get("error_string"): return it`，于是每个成功切片
> 都报 `refused: Success.`。
>
> ⚠️ `slice.log` 在这件事上**完全无用**：它永远只有一行（`Initializing StaticPrintConfigs`，
> 92 字节），**成功与失败一字不差**。`result.json` 才是这个场景的唯一真日志。

**所以 `--bed` / `curr_bed_type` 不是可选项。** 预设默认是 Cool Plate，PETG 会被直接拒绝；
而热床温度只在**耗材**链里（`textured_plate_temp`：PETG Basic 70 / PLA Basic 55），
CLI 不解析 `inherits` 时会落到硬编码的 **45 ℃**——差 25 ℃，PETG 粘不牢，
而喷嘴温度/风扇这些"顺手会看"的字段全是正常的。三条链必须**全部扁平化**后再喂切片器。

---

## 四、交付前验收清单

```bash
python scripts/warp.py  out.3mf      # 翘边 + 站姿，redesign 退 2 → 不许交付
python scripts/audit_3mf.py out.3mf  # 每件水密 + 1 个连通实体（两种布局都能读）
BambuStudio --info out.3mf           # 切片器自己的内核：manifold / number_of_parts
```

烘焙产物再补三项（`bake_project.py` 已自动做）：

| 检查 | 期望 |
|---|---|
| `Metadata/project_settings.config` 存在 | 是 ← 弹窗没了 |
| 盘内 `.gcode` 存在 | 1 个 |
| `close_fan_the_first_x_layers` | 你要的值（不是 `1`） |
| `curr_bed_type` | 你实际的板 |
| `M190` 热床温度 | **板 × 耗材的组合值**（PETG + 纹理 PEI = `70`；出现 `45` 就说明预设链没扁平化） |
| `filament_type` / `filament_density` | 与预设链一致（不是 `PLA` / `0`） |
| 前 3 层 `M106 S0` | 是（拿 gcode 直接 grep） |
| `enable_support` + gcode 的 `FEATURE:` 列表 | 标称"免支撑"的盘必须 `enable_support = 0` **且** `FEATURE:` 里**不出现** `Support` —— `grep -c '; FEATURE: Support'` 应为 0 |
| 每对象 `extruder` | 单色盘必须是 `{1}` |
| 对象名 | 中文名保留，没退化成 `Object_N` |

> **"免支撑"要用 gcode 验，不能靠看形状。** `enable_support = 0` 是充分条件，但它太容易被自己看漏
> （`support_threshold_angle = 30` 还写在配置里、看着像开着）。真正的硬证据是 gcode 的
> `FEATURE:` 类型集合里**没有 `Support`**。注意别拿 `grep -ci support` 去数——gcode 尾部的配置注释
> 块会把 `support_*` 五十多个键全抄一遍，我第一版就是这么数出"52 行 support"的假阳性。
>
> 免支撑件的悬垂是靠**桥接 + 降速 + 悬垂处加风**过去的，不是靠运气：实测 P2 盘
> `enable_overhang_bridge_fan = 1`、`overhang_4_4_speed = 10`（最陡那档慢到 10 mm/s）、
> `overhang_fan_speed = 50%`、`bridge_speed = 50`，gcode 里相应出现 `Bridge` 与 `Overhang wall`
> 特征。**别在切片器里手动打开支撑**——Ø10.6 球头周围会长出一圈树，纯属后处理负担。

回环再验一遍（**槽位与名字是否真的活下来**）：

```bash
BambuStudio --export-3mf rt.3mf --outputdir /tmp/rt out.3mf
```

CLI 是 GUI 程序**永不退出**，macOS 又没有 `timeout` → 后台起 + `sleep` + `kill`。

---

## 五、一句话

**能一次做掉的不要留给用户。** 交付给用户"打开即可打"的文件，代价只是多跑一次切片器；
省下的是一次"为什么又弹这个"的往返，和一次因为默认参数（第 2 层就吹风扇）打废的料。
