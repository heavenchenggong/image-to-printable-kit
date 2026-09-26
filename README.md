# Image → Printable Kit

端到端流水线 skill：**从一张图片到一套打开即打的 3MF 打印文件**——参数化建模、分色拆件（AMS 单件 / 胶接 / 免胶快拆三路）、逐件打印朝向、切片烘焙、五道质量门禁、交付文档与验证图。

> Claude Code / WorkBuddy skill。踩过一遍完整的坑才成型，不是拼出来的模板。

## 为什么要做成 skill

把一个吉祥物从图片做成能打印的东西，真正的难点不在建模，而在**报废方式超出直觉**：

- 切片器报「有浮空部件」——只是其中一类
- 大平面翘边——`warp.py` 抓
- 件中部**单层水平台阶**：截面一层内跳 +406 mm²，切片器不报警（上下都连料，不是岛）、不在首层翘边门禁的视野里，实物表现是台阶处一圈细丝乱团

第三类是本流水线最贵的一条教训，也只能靠逐层悬空扫描抓。整套 skill 的组织方式就是围绕「四类报废 × 五道门禁」的覆盖矩阵。

## 流水线

| # | 阶段 | 出口判据 |
|---|---|---|
| 0 | 分路冻结（参数化 vs AI 图生 3D；AMS vs 胶接 vs 免胶） | 路线定并已告知 |
| 1 | 图纸化：件清单 / 接口清单 / 颜色表 | 成文冻结 |
| 2 | 参数化建模 + 多视图渲染核对 | 全件水密连通 |
| 3 | 分色分盘 | 每件归属唯一盘 |
| 4 | 逐件朝向（切片器黑箱判决 + 翘边门禁） | 每件有解或已上支撑 |
| 5 | 切片烘焙 → 3MF（内嵌配置 + gcode + 缩略图） | 判决文件落盘 |
| 6 | 五道门禁 | 全部 PASS |
| 7 | 文档同步（克重/板占/时间实测回填） | 无残留旧数字 |
| 8 | 交付 + 打印提示 | present_files |

## 安装

### ClawHub

```bash
clawhub install image-to-printable-kit
```

### 手动

```bash
# 主 skill（流水线编排）
git clone https://github.com/heavenchenggong/image-to-printable-kit ~/.claude/skills/image-to-printable-kit

# 依赖 skill（建模 / 朝向 / 烘焙的全部脚本），本机没有就一起装
cp -R ~/.claude/skills/image-to-printable-kit/bundle/parametric-print-model ~/.claude/skills/
```

主 skill 是**编排层**：它按路径 `~/.claude/skills/parametric-print-model/` 调用工具脚本；`bundle/` 里放的是同一份依赖的副本，避免装完跑不起来。两边内容一致时用你的那份即可。

WorkBuddy 用户的 skills 目录通常是 `~/.claude/skills/` 的软链，装一次两边都能用。

### 环境

```bash
pip install numpy trimesh shapely scipy matplotlib pillow   # python ≥ 3.10
```

切片器需要 Bambu Studio CLI（macOS 上指向 GUI 二进制：
`/Applications/BambuStudio.app/Contents/MacOS/BambuStudio`）。

## 目录

```
SKILL.md                  # 八阶段 runbook + 依赖说明
references/gates.md       # 五道门禁：判据、命令、纪律
references/case-winston.md# 实战案例志： Winston 吉祥物套件踩过的坑
bundle/parametric-print-model/   # 依赖 skill（脚本 + 深层文档）
```

## 实战：Winston 吉祥物套件

X2D 双喷嘴 / PETG / 免胶快拆 10 件 / 三盘（P1 绿 · P2 深色 · P3 点缀），三盘全部实物打印成功。过程中的两次报废与修法都写在 `references/case-winston.md`：

- `05 犄角`（弯管中段悬空）：240 个候选姿态只有 1 个放行且要竖成 45.6 mm → 保留矮稳姿态 + **只给这一件**开对象级树形支撑
- `01 底座+下身罩`（颈部水平台阶）：45° 肩台锥台，两端各埋 3 mm 进相邻实体，406 mm² → 11.4 mm²

## 易拆支撑

树形支撑想徒手撕：B 嘴装 Bambu **Support for PLA/PETG** 断离式耗材，切片器里「支撑/筏接口」选它；树身仍用本体耗材（官方提示不要把支撑耗材用于 base）。交付的 3mf 里 `enable_support` 已写好，改接口耗材重切即可，不用重烘。

## License

MIT
