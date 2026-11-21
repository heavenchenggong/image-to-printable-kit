#!/usr/bin/env python3
"""按打印朝向逐层查「中段悬空」——现有门禁漏掉的那一类。

为什么需要这个脚本
------------------
`warp.py` 只看**首层接地**（翘边）；切片器只看 **floating regions**（整块脱离的岛）。
两者都抓不到最常见的一类报废：**件中部一圈水平台阶**。
它上下都连着料，所以切片器不报警；它不在首层，所以 warp.py 也不报警；
但那一圈之下是空的，头几层就是往空气里挤丝 → 垂丝/拉丝/接不上 → 一掰就断。

实测案例（winston 免胶版 P2 `01 底座+下身罩`，2026-09-26 实物打废）：
截面半径停在 13.995 mm（= 颈 Ø28）一直到 z=20.8，**z=21.0 一步跳到 18.030 mm**，
单层凭空多出 **406 mm²** 的悬空环（615.3 → 1021.3 mm²）。切片器判「无警告」，
实物在颈→裙交界处一团丝。

算法
----
两种量，按网格能不能封闭自动选：

1. **watertight → 真实截面轮廓**（主判据）
   `mesh.section()` + `Polygon2D.polygons_full`，量两个数：
   - `area` 本层新出现的悬空面积（mm²）
   - `span` 新出现处离下层最近材料多远（mm）= **真实悬挑跨度**
   必须同时看：只看 `area` 会被「大件薄薄一圈」骗，只看 `span` 会被
   「小件一个尖角」骗。

2. **不封闭 → 只用面积**（兜底）
   截面面积 `A(z)` → 等效半径 `r = sqrt(A/π)`，逐层差 `dr = r(z) − r(z−h)`。
   `dr > 层高` 就是比 45° 更陡的壁。面积与坐标系无关，所以这一路永远稳。

⚠️ 三个踩过的坑，写在这里免得再走一遍
--------------------------------------
1. **`Path3D.to_2D()` 不传 `to_2D` 时会按「本层自己的包围盒」重新定位平面**，
   每层坐标系都不一样 → 层间 `difference()` 算出来全是假的。
   必须显式传 `plane_transform(origin, normal)`。
2. **凸包会把弯折件的两条肢桥起来**：手臂实测凸包报 101.8 mm² / 7.04 mm，
   真实截面只有 0.015 mm/层（= 完全没问题）。弯折件用凸包**全是假阳性**。
   —— 之前这版就是凸包版，误报过整盘。
3. **3mf 对象命名有三套编号**：
   - 自己刚出的盘（`splitter.write_colour_plates`）：trimesh 的 node 名 == 对象 id；
   - 切片器回写过的盘：node 名被重编号（实测偏移 −1），但 `<object>` 上多了
     `face_count`；
   - `face_count` **不是 3mf 规范字段**，新盘上根本没有。
   所以：先按 id 查名，再退回 `face_count`，最后退回 node 名。

用法
----
    python overhang.py plate.3mf                  # 扫整盘
    python overhang.py plate.3mf --json out.json  # 存 JSON，可当 CI 门禁
    python overhang.py part.stl                   # 单件（无变换，按自身 z 轴）

退出码：0 = 全部放行；2 = 有件报警。

边界说明：本脚本只判**几何**上的悬空。真正的支撑是切片器侧的
`bake_project.py` 里 `SUPPORT` 按对象注入的（`enable_support=1` + `tree(auto)`）。
本脚本负责告诉你**哪一件、哪一层**需要它，以及改完设计后验证那一圈是否真的消失。
"""

import argparse
import json
import os
import re
import sys
import zipfile

import numpy as np
import trimesh
from shapely.geometry import Point
from shapely.ops import unary_union
from trimesh.geometry import plane_transform

# One fixed 2-D frame for EVERY layer.  See trap 1 in the module docstring.
PLANE = plane_transform(origin=[0.0, 0.0, 0.0], normal=[0.0, 0.0, 1.0])


def weld(mesh):
    """合并重复顶点。STL 天生不共享顶点，不 weld 什么都测不准。"""
    m = mesh.copy()
    m.merge_vertices()
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    return m


def section(mesh, z):
    """→ (截面面积 mm², 轮廓 shapely 几何或 None)。

    面积对**任何**网格都成立（多闭环求和，与坐标系无关）；
    轮廓只在 watertight 时才可信，所以不封闭时返回 None 走兜底判据。
    """
    s = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if s is None:
        return None, None
    if isinstance(s, tuple):
        s = s[0]
    p2, _ = s.to_2D(to_2D=PLANE)
    polys = [p for p in p2.polygons_full if p.area > 1e-9]
    if not polys:
        return 0.0, None
    union = unary_union(polys)
    return float(union.area), (union if mesh.is_watertight else None)


def scan(mesh, label, layer=0.2, tol=0.25, skip_bottom=1.5,
         area_warn=150.0, span_warn=3.0, area_min=30.0,
         dr_warn=0.35, darea_warn=25.0):
    """tol = 下层外扩量；层高 0.2 时 tol=0.25 ≈ 放行与竖直方向夹角 ≤51° 的壁。"""
    z0, z1 = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    prev, prev_a = None, None
    rows, z = [], z0
    while z <= z1 + 1e-9:
        a, cur = section(mesh, z)
        if a is not None and a > 0 and z - z0 > skip_bottom:
            rec = {"h": round(z - z0, 2), "section_area": round(a, 1),
                   "r_eff": round(float(np.sqrt(a / np.pi)), 2),
                   "area": 0.0, "span": 0.0, "dr": None, "darea": None}
            if cur is not None and prev is not None:
                new = cur.difference(prev.buffer(tol, join_style=2))
                if not new.is_empty and new.area > 1e-6:
                    span = 0.0
                    for g in (list(new.geoms) if hasattr(new, "geoms") else [new]):
                        for xy in g.exterior.coords:
                            span = max(span, prev.distance(Point(xy)))
                    rec["area"] = round(new.area, 1)
                    rec["span"] = round(span, 2)
            if prev_a is not None:
                rec["dr"] = round(rec["r_eff"] - float(np.sqrt(prev_a / np.pi)), 3)
                rec["darea"] = round(a - prev_a, 1)
            rows.append(rec)
        if a is not None:
            prev_a = a
            prev = cur if cur is not None else None   # lost outline -> a diff
        z += layer                                  # against it would be fake

    # 判据只有一个，按网格能不能封闭二选一 —— 两个混用会互相污染：
    #   watertight → span/area：真实几何，能定位「离下层多远」
    #   不封闭     → dr/darea ：只吃面积，任何网格都成立
    # 混用的代价是实测过的：`08 手掌` 的截面细长，横向错动就让 r_eff 涨 0.77，
    # 而真实悬挑只有 0.25 mm —— dr 判据在细长截面上会误报。
    sealed = mesh.is_watertight

    def bad(r):
        if sealed:
            return ((r["span"] >= span_warn and r["area"] >= area_min)
                    or r["area"] >= area_warn)
        return (r["dr"] is not None and r["dr"] >= dr_warn
                and (r["darea"] or 0.0) >= darea_warn)

    def severity(r):
        """没报警时也要报出「最接近报警」的那一层，否则 ok 件看着像没测。"""
        if sealed:
            return max(r["span"] / span_warn if span_warn else 0.0,
                       r["area"] / area_warn if area_warn else 0.0)
        return (r["dr"] / dr_warn if (dr_warn and r["dr"] is not None) else 0.0,
                (r["darea"] or 0.0) / darea_warn if darea_warn else 0.0)

    worst = max(rows, key=lambda r: (bad(r), severity(r)), default=None)
    verdict = "warn" if (worst and bad(worst)) else "ok"
    return {"part": label, "height": round(z1 - z0, 1), "verdict": verdict,
            "worst": worst,
            "top": sorted(rows, key=lambda r: (bad(r), severity(r)),
                          reverse=True)[:3],
            "method": "poly+area" if sealed else "area-only"}


def object_names(path):
    """3mf 里『对象 id → 盘上显示名』。

    三套编号的坑见模块 docstring 第 3 条。这里返回两个字典，调用方按优先级取。
    """
    try:
        with zipfile.ZipFile(path) as z:
            ms = z.read("Metadata/model_settings.config").decode("utf-8", "ignore")
    except Exception:
        return {}, {}
    by_id, by_faces = {}, {}
    for oid, block in re.findall(r'<object id="([^"]+)"[^>]*>(.*?)</object>',
                                 ms, re.S):
        n = re.search(r'key="name"\s+value="([^"]+)"', block)
        f = re.search(r'face_count="(\d+)"', block)
        if n:
            by_id[oid] = n.group(1)
            if f:
                by_faces[int(f.group(1))] = n.group(1)
    return by_id, by_faces


def load_plate(path):
    """→ [(名字, world 坐标下的 mesh)]。3mf 走 scene graph，stl 直接读。"""
    obj = trimesh.load(path)
    if hasattr(obj, "graph") and hasattr(obj, "geometry"):
        by_id, by_faces = object_names(path)
        out = []
        for node in obj.graph.nodes_geometry:
            T, gname = obj.graph[node]
            raw = obj.geometry[gname]
            # 先按**原始**面数取名再 weld —— weld 会删掉退化面，面数一变
            # `face_count` 就对不上了（切片器回写过的盘只有这条路可走）。
            lab = (by_id.get(str(node)) or by_faces.get(len(raw.faces))
                   or str(node))
            m = raw.copy()
            m.apply_transform(T)
            out.append((lab, weld(m)))
        return out
    return [(os.path.basename(path), weld(obj))]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plates", nargs="+")
    ap.add_argument("--layer", type=float, default=0.2)
    ap.add_argument("--tol", type=float, default=0.25,
                    help="下层外扩量，吸收斜坡（0.2 层高时 0.25 ≈ 放行 51° 以内的壁）")
    ap.add_argument("--skip-bottom", type=float, default=1.5,
                    help="忽略距底面这么高的层（斜底归 warp.py 管）")
    ap.add_argument("--area-warn", type=float, default=150.0,
                    help="单层新出现悬空面积报警线")
    ap.add_argument("--span-warn", type=float, default=3.0,
                    help="悬挑跨度报警线（要与 --area-min 同时满足）")
    ap.add_argument("--area-min", type=float, default=30.0,
                    help="走 span 判据时的面积下限，挡掉针尖噪声")
    ap.add_argument("--dr-warn", type=float, default=0.35,
                    help="等效半径单层增长报警线（0.35 mm/0.2 mm ≈ 60°）")
    ap.add_argument("--darea-warn", type=float, default=25.0,
                    help="dr 判据的面积增量下限")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    report, bad = [], 0
    for plate in a.plates:
        print(f"===== {plate}")
        for label, mesh in load_plate(plate):
            s = scan(mesh, label, layer=a.layer, tol=a.tol,
                     skip_bottom=a.skip_bottom, area_warn=a.area_warn,
                     span_warn=a.span_warn, area_min=a.area_min,
                     dr_warn=a.dr_warn, darea_warn=a.darea_warn)
            report.append({"plate": plate, **s})
            w = s["worst"]
            if w is None:
                print(f"   {label:16s} ok    中段无悬空（件高 {s['height']} mm）")
                continue
            tag = "!! WARN" if s["verdict"] == "warn" else "ok    "
            if s["method"] == "area-only":
                detail = (f"Δr {w['dr']:+6.3f} mm/层  "
                          f"ΔA {w['darea']:+8.1f} mm²")
            else:
                detail = (f"悬空 {w['area']:7.1f} mm²  "
                          f"悬挑 {w['span']:5.2f} mm")
            print(f"   {label:16s} {tag} [{s['method']:9s}] 最差层 离底 "
                  f"{w['h']:5.1f} mm  {detail}")
            if s["verdict"] == "warn":
                bad += 1
                print("       → 这一件要对象级支撑，或把那一圈改成 45° 倒角")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\nJSON → {a.json}")
    print(f"\n合计报警 {bad} 件")
    sys.exit(2 if bad else 0)


if __name__ == "__main__":
    main()
