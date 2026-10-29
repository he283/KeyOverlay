# -*- coding: utf-8 -*-
"""make_extended_svg.py —— 生成扩展布局的设计稿 keyboard-mouse-extended.svg。

为什么要有这个脚本：
    手写 SVG 很难保证「字母和原版同一套字体」。原版 keyboard-mouse.svg 的字母是
    从某个窄体粗字转出来的轮廓路径。这里改用 fontTools 把 Windows 自带的
    「Arial Narrow Bold Italic」逐字转成路径，所以每个键的字母都是真字体轮廓，
    字重、字宽、斜度完全一致，不会出现混排。

    键帽形状沿用原版的画法：平行四边形 + 圆角，上边比下边右移 SKEW。

注意：
    正式稿 keyboard-mouse-extended.svg 是**手工绘制的**，本脚本生成的只是备选方案，
    默认输出到 keyboard-mouse-extended-generated.svg，不会覆盖正式稿。

用法：
    python make_extended_svg.py            # 输出 keyboard-mouse-extended-generated.svg
    python make_extended_svg.py out.svg

想换字体：改 FONT_PATH / FONT_ITALIC，重跑即可。
"""
import os
import sys

from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = r"C:\Windows\Fonts\arialnbi.ttf"     # Arial Narrow Bold Italic
BASE_SVG = os.path.join(HERE, "keyboard-mouse.svg")
OUT_SVG = os.path.join(HERE, "keyboard-mouse-extended-generated.svg")

# ---------------- 版面参数（沿用原版的键距手感） ----------------
KEY_W, KEY_H = 168, 172        # 键帽宽高
SKEW = 38                      # 上边比下边右移多少（平行四边形的斜度）
RADIUS = 20                    # 圆角
PITCH_X, PITCH_Y = 188, 185    # 键距
COLS = [250, 438, 626, 814]    # Q W E R 四列
LABEL_X = 60                   # TAB/SHIFT/ALT/CTRL 那一列
LABEL_W = 150
ROWS = [60, 245, 430, 615, 800]
HEAD_ROOM = 40                 # 键帽顶部留白，防止圆角被裁

STROKE = 4.5                   # 键帽描边
LETTER_STROKE_RATIO = 0.14     # 字母描边粗细 = 大写高度 × 这个比例（空心字）
LETTER_FILL = "none"

# 布局：(文字, 列, 行, 宽度)   列 0 = 左边那一列
ROWS_DEF = [
    [("1", 0, 0, KEY_W), ("2", 1, 0, KEY_W), ("3", 2, 0, KEY_W), ("4", 3, 0, KEY_W)],
    [("TAB", -1, 1, LABEL_W), ("Q", 0, 1, KEY_W), ("W", 1, 1, KEY_W),
     ("E", 2, 1, KEY_W), ("R", 3, 1, KEY_W)],
    [("SHIFT", -1, 2, LABEL_W), ("A", 0, 2, KEY_W), ("S", 1, 2, KEY_W),
     ("D", 2, 2, KEY_W), ("F", 3, 2, KEY_W)],
    [("ALT", -1, 3, LABEL_W), ("Z", 0, 3, KEY_W), ("X", 1, 3, KEY_W),
     ("C", 2, 3, KEY_W), ("V", 3, 3, KEY_W)],
    [("CTRL", -1, 4, LABEL_W), ("", 0, 4, 3 * PITCH_X - (PITCH_X - KEY_W))],
]

# 元素 id -> 程序里的按键名
ID_OF = {
    "1": "key-1", "2": "key-2", "3": "key-3", "4": "key-4",
    "TAB": "key-tab", "Q": "key-q", "W": "key-w", "E": "key-e", "R": "key-r",
    "SHIFT": "key-shift", "A": "key-a", "S": "key-s", "D": "key-d", "F": "key-f",
    "ALT": "key-alt", "Z": "key-z", "X": "key-x", "C": "key-c", "V": "key-v",
    "CTRL": "key-ctrl", "": "key-space",
}


def key_path(x, y, w, h):
    """平行四边形 + 圆角，画法与 keyboard-mouse.svg 里的键帽一致（上边右移 SKEW）。"""
    x0, y0, x1, y1 = float(x), float(y), float(x + w), float(y + h)
    s, r = float(SKEW), float(RADIUS)
    k = r * 0.35                       # 圆角收口，接近原版 Q 的写法
    return (
        "M%g %g H%g Q%g %g %g %g L%g %g Q%g %g %g %g H%g Q%g %g %g %g L%g %g Q%g %g %g %g Z" % (
            x0 + s + r, y0,
            x1 + s - r,
            x1 + s, y0, x1 + s - k, y0 + r,
            x1 - k, y1 - r,
            x1, y1, x1 - r, y1,
            x0 + r,
            x0, y1, x0 + k, y1 - r,
            x0 + s + k, y0 + r,
            x0 + s, y0, x0 + s + r, y0,
        ))


class GlyphText(object):
    """把一个字符串转成 SVG path（真字体轮廓）。"""

    def __init__(self, font_path):
        self.font = TTFont(font_path)
        self.gs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upm = self.font["head"].unitsPerEm
        self.cap = getattr(self.font["OS/2"], "sCapHeight", None) or int(self.upm * 0.72)
        self.hmtx = self.font["hmtx"]

    def build(self, text, cap_px):
        """返回 (path_d, 宽度, 大写高度) ；坐标以「文字基线左端」为原点。"""
        scale = cap_px / float(self.cap)
        pen_paths = []
        x = 0.0
        for ch in text:
            gname = self.cmap.get(ord(ch))
            if not gname:
                continue
            pen = SVGPathPen(self.gs, ntos=lambda v: ("%.2f" % v).rstrip("0").rstrip("."))
            # y 轴翻转：字体坐标 y 向上，SVG y 向下
            self.gs[gname].draw(TransformPen(pen, Transform(scale, 0, 0, -scale, x, 0)))
            d = pen.getCommands()
            if d:
                pen_paths.append(d)
            x += self.hmtx[gname][0] * scale
        return " ".join(pen_paths), x


def build_svg(text_engine):
    keys, labels = [], []
    for row in ROWS_DEF:
        for text, col, ri, w in row:
            if col == -1:
                x = LABEL_X
            else:
                x = COLS[col]
            y = ROWS[ri]
            keys.append('      <path id="%s" d="%s"/>'
                        % (ID_OF[text], key_path(x, y, w, KEY_H)))
            if not text:
                continue
            cap = 104 if len(text) == 1 else 48
            d, tw = text_engine.build(text, cap)
            if tw > w - 26:                      # 放不下就按宽度缩，别越出键帽
                cap *= (w - 26) / tw
                d, tw = text_engine.build(text, cap)
            cx = x + SKEW / 2.0 + w / 2.0       # 平行四边形视觉中心略偏右
            cy = y + KEY_H / 2.0
            tx = cx - tw / 2.0
            ty = cy + cap / 2.0                  # 基线
            labels.append(
                '      <path aria-label="%s" fill="%s" stroke="#f5f5ef" '
                'stroke-width="%.2f" stroke-linejoin="round" transform="translate(%.2f %.2f)" d="%s"/>'
                % (text, LETTER_FILL, cap * LETTER_STROKE_RATIO, tx, ty, d))
    return keys, labels


def mouse_group():
    """把原版 SVG 里的鼠标原样搬过来，整体放大并居中到键盘右边。"""
    import re
    src = open(BASE_SVG, encoding="utf-8").read()
    m = re.search(r'<g id="mouse".*?</g>', src, re.S)
    if not m:
        raise SystemExit("原版 SVG 里没找到鼠标组")
    body = m.group(0)
    body = body.replace('<g id="mouse"', '<g id="mouse" transform="translate(1040 166) scale(1.29) translate(-843 -184)"')
    return body


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = args[0] if args else OUT_SVG
    font = FONT_PATH
    for a in sys.argv[1:]:
        if a.startswith("--font="):
            font = a.split("=", 1)[1]
    eng = GlyphText(font)
    keys, labels = build_svg(eng)
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1560" height="1060" viewBox="0 0 1560 1060" role="img" aria-labelledby="title desc">
  <title id="title">游戏键鼠显示——扩展布局</title>
  <desc id="desc">五排键位：数字 1-4、Tab、Q W E R、Shift A S D F、Alt Z X C V、Ctrl 与空格，右侧为鼠标。字母为 Arial Narrow Bold Italic 的真实轮廓，空心描边。</desc>
  <defs>
    <linearGradient id="yellow" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#fff06a"/>
      <stop offset="0.52" stop-color="#ffdf18"/>
      <stop offset="1" stop-color="#ffe653"/>
    </linearGradient>
  </defs>
  <g id="outlines" stroke="#f5f5ef" stroke-width="%g" stroke-linejoin="round" stroke-linecap="round" fill="none">
%s
    %s
  </g>
  <g id="labels">
%s
  </g>
</svg>
""" % (STROKE, "\n".join(keys), mouse_group(), "\n".join(labels))
    open(out, "w", encoding="utf-8", newline="\n").write(svg)
    print("已生成", out, "（%d 个键帽，%d 个标签）" % (len(keys), len(labels)))


if __name__ == "__main__":
    raise SystemExit(main())
