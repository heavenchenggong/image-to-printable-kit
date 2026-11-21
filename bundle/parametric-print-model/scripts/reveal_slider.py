#!/usr/bin/env python3
"""可切换图案滑块机构的尺寸算尺（reveal-slider）。

给几个主参数，派生出窗口 / 图案 / 滑片 / 腔 / 拨块 / 槽的全套尺寸，
并校验 references/mechanisms.md 第四节的三条等式。

用法：
    python3 reveal_slider.py                      # 默认规格（实测案例复原）
    python3 reveal_slider.py --window 16 --frames 3 --width 24 --length 60
    python3 reveal_slider.py --check-real        # 用 3mf 实测值验三式残差

from reveal_slider import spec
    s = spec(D_w=16, frames=3)
    s['L_s'], s['T'], s['W_c']
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass, asdict

# 实测基准（挂绳版本v3 / 心情涟漪），单位 mm
REAL = dict(D_w=12.00, P=11.77, T_slot=12.51, T_cav=12.65,
            L_s=18.15, L_k=5.64, L_c=42.25, L_p=29.60,
            W_c=15.44, W_p=15.10, W_s=1.80, W_k=1.60,
            W_base=14.20, W_liner=13.90)


@dataclass
class Spec:
    # ---- 主参数（用户改这些）----
    D_w: float = 12.0        # 圆形观察窗直径
    frames: int = 2          # 图案组数（表情数）
    width: float = 20.0      # 外形宽
    length: float = 50.5     # 外形长
    # ---- 间隙 / 余量 ----
    gap_xy: float = 0.17     # 腔宽 - 滑片宽，单边
    gap_slot: float = 0.10   # 槽宽 - 拨块宽，单边
    gap_liner: float = 0.15  # 底壳腔 - 衬片，单边
    wall: float = 2.28       # 外形单边壁厚（20 - 15.44 = 4.56 / 2）
    step_cavity: float = 1.24  # 底壳内腔比上盖内腔窄的量（实测 15.44-14.20）
    margin_x: float = 1.55   # 滑片宽相对窗口的单边余量
    frame_len: float = 6.2   # 单组图案沿滑动方向的高度
    margin_end: float = 5.9  # 滑片两端相对首尾图案的留白
    W_k: float = 1.60        # 拨块宽
    L_k: float = 5.64        # 拨块长（沿滑动方向）
    liner_t: float = 0.80    # 衬片厚
    plate_t: float = 1.40    # 滑片板厚（**阻尼调节量，±0.1**）
    knob_h: float = 2.30     # 拨块凸出板面的高度


def spec(**kw) -> dict:
    """派生全套尺寸。返回值是普通 dict，便于直接塞进建模脚本常量区。"""
    p = Spec(**kw)
    d = asdict(p)

    # 图案间距 = 窗口直径：保证拨到端点时上一组图案完全移出窗口
    d['P'] = p.D_w
    # 行程由图案组数决定
    d['T'] = (p.frames - 1) * d['P']
    # 三条等式正向构造
    d['L_s'] = d['T'] + p.L_k                       # ① 槽长
    d['W_s'] = p.W_k + 2 * p.gap_slot               # 槽宽
    d['W_p'] = p.D_w + 2 * p.margin_x               # 滑片宽
    d['L_p'] = (p.frames - 1) * d['P'] + p.frame_len + 2 * p.margin_end  # 滑片长
    d['L_c'] = d['L_p'] + d['T']                    # ② 腔长
    d['W_c'] = d['W_p'] + 2 * p.gap_xy              # 腔宽
    # 底壳 / 衬片
    d['W_base'] = d['W_c'] - p.step_cavity
    d['W_liner'] = d['W_base'] - 2 * p.gap_liner
    d['L_liner'] = d['L_c'] - 0.35
    # 窗口 / 槽的位置：窗口偏左给右侧长槽让位
    d['win_cx'] = -(d['W_s'] / 2 + p.gap_xy)
    d['win_cy'] = 0.0
    d['slot_cx'] = p.width / 2 - p.wall - d['W_s'] / 2
    d['slot_cy'] = 0.0
    d['frame_centres_y'] = [ (i - (p.frames - 1) / 2) * d['P'] for i in range(p.frames) ]
    d['frame_cx'] = d['win_cx']
    # 外形约束检查用
    d['outer_w'] = p.width
    d['outer_l'] = p.length

    # ---- 校验 ----
    errs = []
    if abs(d['L_s'] - p.L_k - (d['L_c'] - d['L_p'])) > 1e-6:
        errs.append('①≠②：槽行程与腔行程不一致')
    if abs(d['P'] - p.D_w) / p.D_w > 0.05:
        errs.append('③：图案间距偏离窗口直径 >5%，端点会露出半张图案')
    need_w = d['slot_cx'] + d['W_s'] / 2 + p.wall / 2
    if need_w > p.width / 2:
        errs.append(f'外形宽不够：右侧长槽需要 x 到 {need_w:.2f}，外宽半值只有 {p.width/2:.2f}')
    need_l = d['L_c'] / 2 + p.wall
    if need_l > p.length / 2:
        errs.append(f'外形长不够：需 ≥ {2*need_l:.2f}，当前 {p.length:.2f}（每多一组图案腔长 +2P = +{2*d["P"]:.2f}）')
    if d['W_liner'] <= 0 or d['L_liner'] <= 0:
        errs.append('衬片被算成负尺寸')
    d['ok'] = not errs
    d['errors'] = errs
    return d


def _fmt(d):
    rows = [
        ('窗口 Ø', f"{d['D_w']:.2f}", '主参数'),
        ('图案组数 / 间距 P', f"{d['frames']} / {d['P']:.2f}", 'P = D_w'),
        ('行程 T', f"{d['T']:.2f}", 'T = (N−1)·P'),
        ('滑片 W_p × L_p', f"{d['W_p']:.2f} × {d['L_p']:.2f}", '承载图案'),
        ('滑片板厚 / 拨块高', f"{d['plate_t']:.2f} / {d['knob_h']:.2f}", '板厚 = 阻尼调节量'),
        ('拨块 W_k × L_k', f"{d['W_k']:.2f} × {d['L_k']:.2f}", ''),
        ('长槽 W_s × L_s', f"{d['W_s']:.2f} × {d['L_s']:.2f}", 'L_s = T + L_k'),
        ('上盖内腔 W_c × L_c', f"{d['W_c']:.2f} × {d['L_c']:.2f}", 'L_c = L_p + T'),
        ('底壳内腔宽 / 衬片宽', f"{d['W_base']:.2f} / {d['W_liner']:.2f}", '每边 −gap_liner'),
        ('衬片 L / 厚', f"{d['L_liner']:.2f} / {d['liner_t']:.2f}", ''),
        ('窗口中心 / 槽中心 x', f"{d['win_cx']:.2f} / {d['slot_cx']:.2f}", '窗口故意偏左'),
        ('图案组 y 中心', ', '.join(f'{v:.2f}' for v in d['frame_centres_y']), '与窗口同 x'),
        ('外形 W × L', f"{d['outer_w']:.2f} × {d['outer_l']:.2f}", ''),
    ]
    w = max(len(a) for a, _, _ in rows)
    out = [f"{'项目'.ljust(w)}  {'数值'.ljust(22)} 说明", '-' * (w + 52)]
    for a, b, c in rows:
        out.append(f'{a.ljust(w)}  {b.ljust(22)} {c}')
    out.append('')
    out.append('① T = L_s − L_k = %.2f' % (d['L_s'] - d['L_k']))
    out.append('② T = L_c − L_p = %.2f' % (d['L_c'] - d['L_p']))
    out.append('③ P %.2f ≈ D_w %.2f；T %.2f = (N−1)·P' % (d['P'], d['D_w'], d['T']))
    out.append('   注：每多一组图案，腔长 +2P = +%.2f（滑片 +P，行程 +P）' % (2 * d['P']))
    out.append('判定: ' + ('PASS' if d['ok'] else 'FAIL → ' + '; '.join(d['errors'])))
    return '\n'.join(out)


def check_real():
    print('=== 用 3mf 实测值验三式 ===')
    r = REAL
    print('① T = L_s − L_k = %.2f' % (r['L_s'] - r['L_k']))
    print('② T = L_c − L_p = %.2f' % (r['L_c'] - r['L_p']))
    print('   两式残差 = %.2f mm（<0.2 视为同一定位）' % abs((r['L_s'] - r['L_k']) - (r['L_c'] - r['L_p'])))
    print('③ P %.2f ≈ T %.2f ≈ D_w %.2f，最大偏差 %.2f mm' % (
        r['P'], r['T_slot'], r['D_w'], max(r['P'], r['T_slot'], r['D_w']) - min(r['P'], r['T_slot'], r['D_w'])))
    print('XY 间隙：腔/片 单边 %.2f ｜ 槽/拨块 单边 %.2f ｜ 底壳/衬片 单边 %.2f' % (
        (r['W_c'] - r['W_p']) / 2, (r['W_s'] - r['W_k']) / 2, (r['W_base'] - r['W_liner']) / 2))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='可切换图案滑块机构尺寸算尺')
    ap.add_argument('--window', type=float, default=12.0, help='观察窗直径 mm')
    ap.add_argument('--frames', type=int, default=2, help='图案组数')
    ap.add_argument('--width', type=float, default=20.0, help='外形宽 mm')
    ap.add_argument('--length', type=float, default=50.5, help='外形长 mm')
    ap.add_argument('--gap-xy', type=float, default=0.17, help='腔/片单边间隙 mm')
    ap.add_argument('--gap-slot', type=float, default=0.10, help='槽/拨块单边间隙 mm')
    ap.add_argument('--check-real', action='store_true', help='用实测值验三式')
    a = ap.parse_args()
    if a.check_real:
        check_real()
    else:
        print(_fmt(spec(D_w=a.window, frames=a.frames, width=a.width,
                        length=a.length, gap_xy=a.gap_xy, gap_slot=a.gap_slot)))
