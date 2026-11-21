#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
伸缩锥管（print-in-place telescoping taper tubes）尺寸算尺。

复刻「攀过那座山」登山杖挂件的机构：N 级同锥度薄壁锥管同心嵌套，
装配态即打印态，滑动靠统一放大的单边间隙，防脱靠线-to-line 微干涉。

输入：级数、管长、最外级底端外半径、间隙、干涉、壁厚。
输出：每级锥管上下端内外半径、行程、验收结论。--check-real 对照实测件。

实测锚点（登山杖挂件，单位 mm）：
  管长 65.0；comp3 底 r 5.00/4.20，顶 5.75/4.95
  comp1 底 3.50/2.70，顶 4.25/3.45；comp2(内杆) 底外壁 2.01，顶 2.75/1.95
  手柄出口内缘 5.70–5.80；滑动单边间隙 0.70；防脱干涉 ~0.05
"""
import argparse
import sys

# 实测件参数（--check-real 用）。锥度是独立参数：全程半径差 0.75/65mm
REAL = dict(levels=3, tube_len=65.0, r_outer_bottom=5.00, gap=0.70,
            detent=0.05, wall=0.80, seg=126, taper=0.75)


def derive(p):
    """由底端最外管外半径逐级向内推导。返回 dict。"""
    d = {}
    n = p.levels
    # 第 i 级（0=最外）底端外半径：下一级 = 上一级底端内半径 - 滑动间隙
    r = [p.r_outer_bottom]
    for i in range(1, n):
        r.append(r[-1] - p.wall - p.gap)
    d['r_out_bot'] = r
    d['r_in_bot'] = [x - p.wall for x in r]
    # 全程锥度（顶端外半径 - 底端外半径），原设计 0.75，与间隙/干涉无耦合
    d['taper_total'] = p.taper
    d['r_out_top'] = [x + d['taper_total'] for x in r]
    d['r_in_top'] = [x - p.wall for x in d['r_out_top']]
    # 手柄出口内缘：卡住最外管顶端 = 顶端外半径 + [0, +0.05] 区间中心
    d['exit_r'] = d['r_out_top'][0]  # 线-to-line 中心值
    # 行程：管顶从手柄内移动到出口即停
    d['stroke'] = p.tube_len - d['taper_total'] * 0  # 顶端初始在 tube_len 顶端
    d['stroke'] = p.tube_len * (n) / n  # 全行程 = 管长（顶端可达出口）
    d['stroke'] = p.tube_len - 3.0      # 减收合位余量（原设计管顶距出口 3mm 时开始挡）
    # 实际行程：顶端移动到出口 = (收合顶端z - 出口z)。原设计 = 65 - 3 = 62
    return d


def check(p, d):
    errs, warns = [], []
    if p.wall * 2 < 0.8:
        warns.append('壁厚 %.2f 太薄，打印易渗漏/断裂（原设计 0.80=2×0.42）' % p.wall)
    if p.gap < 0.6:
        warns.append('滑动间隙 %.2f 偏小：65mm 级长管建议 ≥0.6，短管(≤25mm)可 0.4' % p.gap)
    if p.gap > 1.0:
        warns.append('滑动间隙 %.2f 偏大：可能晃动过于自由' % p.gap)
    if p.detent < 0.05:
        warns.append('防脱干涉 %.2f 太小：复刻建议 0.15–0.20' % p.detent)
    if p.detent > 0.30:
        errs.append('防脱干涉 %.2f 过大：收合态会被卡住拉不开' % p.detent)
    # 防脱方向校验：每级顶端外半径必须 ≥ 下一级底端开口（外管底内半径）
    for i in range(len(d['r_out_top']) - 1):
        outer_hole_bot = d['r_in_bot'][i]
        inner_top = d['r_out_top'][i + 1]
        if inner_top < outer_hole_bot - 1e-9:
            errs.append('第 %d 级顶端 r%.3f < 外管底孔 r%.3f：会脱出！'
                        % (i + 1, inner_top, outer_hole_bot))
        if inner_top - outer_hole_bot > 0.5:
            warns.append('第 %d 级防脱干涉 %.2f 过大，收合打印后可能拉不开'
                         % (i + 1, inner_top - outer_hole_bot))
    # 内杆球头
    d['ball_r'] = max(d['r_out_bot'][-1] - p.wall, 0.4)
    return errs, warns


def report(p, d, errs, warns):
    out = []
    out.append('=== 伸缩锥管尺寸链 ===')
    out.append('级数 %d | 管长 %.1f | 滑动单边间隙 %.2f | 防脱干涉 %.2f | 壁厚 %.2f | %d 边'
               % (p.levels, p.tube_len, p.gap, p.detent, p.wall, p.seg))
    out.append('')
    out.append('%-4s %-18s %-18s' % ('级', '底端 外/内 r', '顶端 外/内 r'))
    names = ['外管', '中管', '内杆']
    for i in range(p.levels):
        nm = names[i] if i < 3 else '第%d级' % (i + 1)
        out.append('%-4s %.2f / %.2f        %.2f / %.2f'
                   % (nm, d['r_out_bot'][i], d['r_in_bot'][i],
                      d['r_out_top'][i], d['r_in_top'][i]))
    out.append('')
    out.append('半锥角      = %.2f°  (半径差 %.2f / 长 %.1f)'
               % (__import__('math').degrees(__import__('math').atan2(
                   d['taper_total'], p.tube_len)), d['taper_total'], p.tube_len))
    out.append('手柄出口内缘 r = %.2f（线-to-line 卡最外管顶端）' % d['exit_r'])
    out.append('行程上限    ≈ %.0f mm（管顶抵达出口，收合余量 3）' % d['stroke'])
    out.append('内杆球头半径 ≈ %.2f' % d['ball_r'])
    if warns:
        out.append('')
        out.append('⚠ 注意：')
        for w in warns:
            out.append('  - ' + w)
    if errs:
        out.append('')
        out.append('✗ 不通过：')
        for e in errs:
            out.append('  - ' + e)
        print('\n'.join(out))
        return 1
    out.append('')
    out.append('✓ 通过')
    print('\n'.join(out))
    return 0


def main():
    ap = argparse.ArgumentParser(description='伸缩锥管尺寸算尺')
    ap.add_argument('--levels', type=int, default=3, help='滑动管级数（不含手柄）')
    ap.add_argument('--tube-len', type=float, default=65.0, help='管长 mm')
    ap.add_argument('--r-outer-bottom', type=float, default=5.00,
                    help='最外级底端外半径 mm')
    ap.add_argument('--gap', type=float, default=0.70, help='滑动单边间隙 mm')
    ap.add_argument('--detent', type=float, default=0.05,
                    help='防脱干涉 mm（复刻建议 0.15-0.20）')
    ap.add_argument('--taper', type=float, default=0.75,
                    help='全程锥度：顶底半径差 mm')
    ap.add_argument('--wall', type=float, default=0.80, help='管壁厚 mm')
    ap.add_argument('--seg', type=int, default=126, help='多边形边数')
    ap.add_argument('--check-real', action='store_true',
                    help='对照实测件参数自检')
    p = ap.parse_args()
    if p.check_real:
        p = argparse.Namespace(**REAL)
        d = derive(p)
        print('对照实测（攀过那座山登山杖挂件）：')
        errs, warns = check(p, d)
        # 实测对照表
        real_r_out_bot = [5.00, 3.50, 2.01]
        real_r_in_bot = [4.20, 2.70, None]
        real_r_out_top = [5.75, 4.25, 2.75]
        ok = True
        for i in range(3):
            for tag, got, exp in [('底外', d['r_out_bot'][i], real_r_out_bot[i]),
                                  ('顶外', d['r_out_top'][i], real_r_out_top[i])]:
                if exp is None:
                    continue
                flag = '✓' if abs(got - exp) < 0.02 else '✗'
                if flag == '✗':
                    ok = False
                print('  级%d %s 推导 %.3f vs 实测 %.3f %s' % (i + 1, tag, got, exp, flag))
        if abs(d['r_out_bot'][1] - d['r_out_bot'][0] + p.wall + p.gap) > 1e-9:
            ok = False
        print('  滑动间隙逐级 = %s' % ['%.2f' % (d['r_in_bot'][i] - d['r_out_bot'][i + 1])
                                        for i in range(2)])
        sys.exit(0 if ok else 1)
    d = derive(p)
    errs, warns = check(p, d)
    sys.exit(report(p, d, errs, warns))


if __name__ == '__main__':
    main()
