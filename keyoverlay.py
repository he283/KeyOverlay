# -*- coding: utf-8 -*-
"""
keyoverlay.py —— 直接按 SVG 设计稿渲染的键鼠覆盖层。

思路：不再手写几何，而是**解析 SVG 里的路径**（含二次/三次贝塞尔、圆角、字形路径、
transform 矩阵），再用 Pillow 逐元素栅格化。因此形状、圆角、字重、鼠标轮廓和
参考图完全一致，只是把"按下高亮"做成了可交互的状态。

用法：
    python keyoverlay.py                 # 默认放屏幕左下角、1.2 倍、点击穿透
    python keyoverlay.py --scale 1.0
    python keyoverlay.py --pos 40,900
    python keyoverlay.py --bg            # 连 SVG 的黑色底板一起画
    python keyoverlay.py --mouse-yellow  # 鼠标左右键常亮黄色（不带按下反馈）
    python keyoverlay.py --solid         # 取消点击穿透
    python keyoverlay.py --preview out.png [--pressed W,LMB]
    python keyoverlay.py --toggle-vk 0x78 --quit-vk 0x79

运行中：F9 隐藏/显示，F10 退出；控制面板可切换经典/扩展/五列布局。
"""
import argparse
import ctypes
import json
import math
import os
import re
import struct
import sys
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# 打包成 exe（PyInstaller）后：__file__ 指向临时解包目录，exe 实际在 sys.executable 旁边。
# HERE 用来放日志等"要留得住"的文件；_RES 用来找打包进去的只读资源。
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
    _RES = getattr(sys, "_MEIPASS", HERE)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    _RES = HERE


def _resource(name):
    """先找 exe/脚本旁边的同名文件（换设计稿只需丢一个 svg 过去），否则用打包进去的那份。"""
    local = os.path.join(HERE, name)
    if os.path.exists(local):
        return local
    return os.path.join(_RES, name)


SVG_FILES = {
    "classic": "keyboard-mouse.svg",
    "extended": "keyboard-mouse-extended.svg",
    "wide": "keyboard-mouse-wide.svg",
}
LAYOUT_NAMES = {"classic": "经典布局", "extended": "扩展布局", "wide": "五列布局"}
CURRENT_LAYOUT = "classic"
SCALE_MIN, SCALE_MAX, SCALE_STEP = 0.7, 2.4, 0.1
CURRENT_SCALE = 1.2

# 当前生效的热键（虚拟键码，0 = 禁用）。可在控制面板里改，存 overlay.json。
_HK = {"toggle": 0x78, "quit": 0x79, "move": 0x77}
SVG_FILE = _resource(SVG_FILES[CURRENT_LAYOUT])
SVG_W, SVG_H = 1280, 736          # 当前设计稿的画布尺寸，load_svg 会更新

# ============================ 外观参数 ============================
SS = 4                     # 超采样倍数
ART_SCALE = 0.34           # SVG 单位 -> scale=1 时的像素
HILITE = (233, 223, 104, 255)      # 按下填充（取自 SVG 黄色渐变中间色）
# 按下时字母保持 SVG 原本的白色，不改成深色（想反过来就把下面这行接进 _tile 的 label 分支）
# DARK_TEXT = (26, 29, 35, 255)
GLOW = True                # 白线外发光（对应 SVG 里那层 19% 的模糊副本）
GLOW_ALPHA = 0.19
SHADOW = True              # 深色投影，压到亮画面上也能看清
SHADOW_BLUR = 1.4
SHADOW_OFFSET = (1, 2)
SHADOW_ALPHA = 0.55
SHADOW_MARGIN = 6

# 元素 id / 字形 aria-label -> 虚拟键码
KEYMAP = {
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35,
    "TAB": 0x09, "Q": 0x51, "W": 0x57, "E": 0x45, "R": 0x52, "T": 0x54,
    "SHIFT": 0xA0, "A": 0x41, "S": 0x53, "D": 0x44, "F": 0x46, "G": 0x47,
    "ALT": 0xA4, "Z": 0x5A, "X": 0x58, "C": 0x43, "V": 0x56, "B": 0x42,
    "CTRL": 0xA2, "SPACE": 0x20,
}
MOUSE_BTNS = [("LMB", 0x01), ("RMB", 0x02), ("MMB", 0x04), ("X1", 0x05), ("X2", 0x06)]
# 右侧修饰键也映射到同一个显示名，否则按右边那颗不会亮。
ALT_VK = {"SHIFT": (0xA1,), "CTRL": (0xA3,), "ALT": (0xA5,)}
POLL_KEYS = list(KEYMAP.items()) + [(n, v) for n, vs in ALT_VK.items() for v in vs]
ACTIVE_VKS = POLL_KEYS          # 实际轮询的键，随布局收窄（见 _rebuild_active_vks）


def _rebuild_active_vks():
    """只轮询当前设计稿里画了的键，布局没用到的不白读。"""
    global ACTIVE_VKS
    names = {e["press"] for e in ART if e["press"]}
    out = []
    for n in sorted(names):
        for v in ((KEYMAP.get(n),) + ALT_VK.get(n, ())):
            if v:
                out.append((n, v))
    ACTIVE_VKS = out

PRESS_BY_ID = {
    "key-1": "1", "key-2": "2", "key-3": "3", "key-4": "4", "key-5": "5",
    "key-tab": "TAB", "key-q": "Q", "key-w": "W", "key-e": "E", "key-r": "R", "key-t": "T",
    "key-shift": "SHIFT", "key-a": "A", "key-s": "S", "key-d": "D", "key-f": "F", "key-g": "G",
    "key-alt": "ALT", "key-z": "Z", "key-x": "X", "key-c": "C", "key-v": "V", "key-b": "B",
    "key-ctrl": "CTRL", "key-space": "SPACE",
    "mouse-left": "LMB", "mouse-right": "RMB", "mouse-wheel": "MMB",
    "mouse-side-front": "X1", "mouse-side-back": "X2",
}


# ============================ SVG 解析 ============================
_NUM = r"-?\d*\.?\d+(?:[eE][-+]?\d+)?"
_TOK = re.compile(r"[MmLlHhVvCcQqSsTtZz]|" + _NUM)


def _mul(a, b):
    """矩阵相乘：先 b 后 a。"""
    return [a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]]


def _parse_transform(s):
    m = [1, 0, 0, 1, 0, 0]
    for name, arg in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = [float(x) for x in re.findall(_NUM, arg)]
        if not v:
            continue
        if name == "translate":
            t = [1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0]
        elif name == "scale":
            t = [v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0]
        elif name == "matrix" and len(v) >= 6:
            t = v[:6]
        elif name == "skewX":
            t = [1, 0, math.tan(math.radians(v[0])), 1, 0, 0]
        elif name == "skewY":
            t = [1, math.tan(math.radians(v[0])), 0, 1, 0, 0]
        else:
            continue
        m = _mul(m, t)
    return m


def _apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _path_subpaths(d, seg=8):
    """把 path 的 d 解析成若干子路径：[(points, closed), ...]"""
    tok = _TOK.findall(d)
    subs = []
    i, cmd = 0, None
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    pts = []

    def flush(closed):
        if len(pts) >= 2:
            subs.append((list(pts), closed))

    while i < len(tok):
        if tok[i].isalpha():
            cmd = tok[i]
            i += 1
        if cmd in ("Z", "z"):
            flush(True)
            pts = []
            cur = start
            cmd = None
            continue
        try:
            if cmd in ("M", "m"):
                x, y = float(tok[i]), float(tok[i + 1]); i += 2
                if cmd == "m":
                    x, y = cur[0] + x, cur[1] + y
                flush(False)
                pts = [(x, y)]
                cur = start = (x, y)
                cmd = "L" if cmd == "M" else "l"
            elif cmd in ("L", "l"):
                x, y = float(tok[i]), float(tok[i + 1]); i += 2
                if cmd == "l":
                    x, y = cur[0] + x, cur[1] + y
                pts.append((x, y)); cur = (x, y)
            elif cmd in ("H", "h"):
                x = float(tok[i]); i += 1
                x = cur[0] + x if cmd == "h" else x
                pts.append((x, cur[1])); cur = (x, cur[1])
            elif cmd in ("V", "v"):
                y = float(tok[i]); i += 1
                y = cur[1] + y if cmd == "v" else y
                pts.append((cur[0], y)); cur = (cur[0], y)
            elif cmd in ("Q", "q"):
                x1, y1, x, y = [float(t) for t in tok[i:i + 4]]; i += 4
                if cmd == "q":
                    x1, y1 = cur[0] + x1, cur[1] + y1
                    x, y = cur[0] + x, cur[1] + y
                for s in range(1, seg + 1):
                    t = s / seg
                    a = (1 - t) ** 2; b = 2 * (1 - t) * t; c = t * t
                    pts.append((a * cur[0] + b * x1 + c * x, a * cur[1] + b * y1 + c * y))
                cur = (x, y)
            elif cmd in ("C", "c"):
                x1, y1, x2, y2, x, y = [float(t) for t in tok[i:i + 6]]; i += 6
                if cmd == "c":
                    x1, y1 = cur[0] + x1, cur[1] + y1
                    x2, y2 = cur[0] + x2, cur[1] + y2
                    x, y = cur[0] + x, cur[1] + y
                for s in range(1, seg + 1):
                    t = s / seg
                    a = (1 - t) ** 3; b = 3 * (1 - t) ** 2 * t
                    c = 3 * (1 - t) * t * t; e = t ** 3
                    pts.append((a * cur[0] + b * x1 + c * x2 + e * x,
                                a * cur[1] + b * y1 + c * y2 + e * y))
                cur = (x, y)
            else:
                i += 1
        except (IndexError, ValueError):
            break
    flush(False)
    return subs


def _rect_subpaths(x, y, w, h, rx, ry, seg=8):
    if rx <= 0 and ry <= 0:
        return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)]
    pts = []
    for cx, cy, a0 in ((x + w - rx, y + ry, -90), (x + w - rx, y + h - ry, 0),
                       (x + rx, y + h - ry, 90), (x + rx, y + ry, 180)):
        for s in range(seg + 1):
            a = math.radians(a0 + 90 * s / seg)
            pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    return [(pts, True)]


def _color(txt, default=None):
    if not txt or txt in ("none", "transparent"):
        return None
    if txt.startswith("url("):
        return None
    if txt.startswith("#"):
        h = txt[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    return default


def load_svg(path):
    """解析成元素列表，每个元素带：子路径、填充、描边、线宽、包围盒、按下映射。"""
    global SVG_W, SVG_H
    root = ET.parse(path).getroot()
    try:
        SVG_W = float(root.attrib.get("width") or SVG_W)
        SVG_H = float(root.attrib.get("height") or SVG_H)
    except (TypeError, ValueError):
        pass
    out = []

    def walk(node, attrs, mat):
        tag = node.tag.split("}")[-1]
        if tag in ("defs", "title", "desc", "filter", "linearGradient", "stop", "use"):
            return
        a = dict(attrs)
        for k in ("fill", "stroke", "stroke-width"):
            if k in node.attrib:
                a[k] = node.attrib[k]
        m = _mul(mat, _parse_transform(node.attrib.get("transform")))
        if tag in ("g", "svg"):
            for ch in node:
                walk(ch, a, m)
            return
        subs = []
        if tag == "path" and node.attrib.get("d"):
            subs = _path_subpaths(node.attrib["d"])
        elif tag == "rect":
            try:
                rx = float(node.attrib.get("rx") or 0)
                ry = float(node.attrib.get("ry") or rx)
                subs = _rect_subpaths(float(node.attrib["x"]), float(node.attrib["y"]),
                                      float(node.attrib["width"]), float(node.attrib["height"]),
                                      rx, ry)
            except (KeyError, ValueError):
                return
        else:
            return
        subs = [([_apply(m, x, y) for x, y in p], c) for p, c in subs]
        if not subs:
            return
        xs = [x for p, _ in subs for x, _ in p]
        ys = [y for p, _ in subs for _, y in p]
        det = abs(m[0] * m[3] - m[1] * m[2]) ** 0.5 or 1.0
        try:
            sw = float(a.get("stroke-width", 1)) * det
        except ValueError:
            sw = 1.0 * det
        eid = node.attrib.get("id")
        label = (node.attrib.get("aria-label") or "").upper()
        out.append(dict(
            id=eid,
            press=PRESS_BY_ID.get(eid) or (label if label in KEYMAP else None),
            label=bool(label),
            subs=subs,
            fill=_color(a.get("fill"), (0, 0, 0, 255)),
            yellow=str(a.get("fill") or "").startswith("url("),
            stroke=_color(a.get("stroke")),
            sw=sw,
            bbox=(min(xs), min(ys), max(xs), max(ys)),
        ))

    walk(root, {"fill": None, "stroke": None, "stroke-width": 1}, [1, 0, 0, 1, 0, 0])
    return out


ART = load_svg(SVG_FILE)
_BODY = next((e for e in ART if e["id"] == "background"), None)
_CONTENT = [e for e in ART if e["id"] != "background"]
BX0 = min(e["bbox"][0] for e in _CONTENT)
BY0 = min(e["bbox"][1] for e in _CONTENT)
BX1 = max(e["bbox"][2] for e in _CONTENT)
BY1 = max(e["bbox"][3] for e in _CONTENT)

_rebuild_active_vks()          # 启动时按当前设计稿收窄轮询范围

_TILES = {}


# ============================ 栅格化 ============================
def _tile(index, k, mode, mouse_yellow=False):
    """把单个元素渲染成小图（带缓存）。返回 (图, 左上角坐标)。

    mode: None=不亮；"full"=整个亮；"up"/"down"=只亮滚轮上半/下半。
    """
    hit = bool(mode)
    key = (index, round(k, 4), mode, mouse_yellow)
    if key in _TILES:
        return _TILES[key]

    e = ART[index]
    s = ART_SCALE * k
    x0, y0, x1, y1 = e["bbox"]
    sw = max(1, int(round(e["sw"] * s)))
    pad = sw + 3
    W = max(1, int(round((x1 - x0) * s)) + 2 * pad + 2)
    H = max(1, int(round((y1 - y0) * s)) + 2 * pad + 2)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    fill, stroke = e["fill"], e["stroke"]
    if e["yellow"]:
        fill = HILITE if (hit or mouse_yellow) else None
    elif hit and not e["label"]:
        # 只有键帽（轮廓）变黄，字母保持原本的白色
        fill = HILITE

    paths = [([((x - x0) * s + pad, (y - y0) * s + pad) for x, y in pts], closed)
             for pts, closed in e["subs"]]
    half = mode in ("up", "down")
    if fill and half:
        # 滚轮上下半亮：先整块填黄，再按高度切一半
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        for p_, closed_ in paths:
            if closed_ and len(p_) > 2:
                dl.polygon(p_, fill=fill)
        mid = int(round(pad + (y1 - y0) * s / 2.0))
        mask = Image.new("L", (W, H), 255)
        dm = ImageDraw.Draw(mask)
        if mode == "up":
            dm.rectangle([0, mid, W, H], fill=0)
        else:
            dm.rectangle([0, 0, W, mid], fill=0)
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
        im.alpha_composite(layer)
        fill = None
    if fill:
        # TrueType/SVG 字形的内孔用相反绕向表示。先画外轮廓，再清掉反向
        # 子路径，避免 Q/A/D 等字母被填成实心块。
        closed = []
        for p, is_closed in paths:
            if not is_closed:
                continue
            area = sum(p[i][0] * p[(i + 1) % len(p)][1] -
                       p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))
            closed.append((p, area))
        if closed:
            outer_sign = 1 if max(closed, key=lambda item: abs(item[1]))[1] > 0 else -1
            for p, area in closed:
                d.polygon(p, fill=fill if (1 if area > 0 else -1) == outer_sign else 0)
    for p, closed_path in paths:
        if stroke:
            d.line(p + [p[0]] if closed_path else p, fill=stroke, width=sw, joint="curve")

    pos = (int(round(x0 * s)) - pad, int(round(y0 * s)) - pad)
    _TILES[key] = (im, pos)
    return im, pos


_FRAMES = OrderedDict()
FRAME_CACHE_MAX = 24


def render(pressed=(), scale=1.0, bg=False, mouse_yellow=False):
    key = (frozenset(pressed), round(scale, 3), bg, mouse_yellow)
    if key in _FRAMES:
        _FRAMES.move_to_end(key)
        return _FRAMES[key]
    k = scale * SS
    pressed = key[0]
    if bg and _BODY is not None:
        box = (0, 0, SVG_W, SVG_H)
    else:
        box = (BX0, BY0, BX1, BY1)

    pad = int(round(ART_SCALE * k * 4))
    W = int(round((box[2] - box[0]) * ART_SCALE * k)) + 2 * pad
    H = int(round((box[3] - box[1]) * ART_SCALE * k)) + 2 * pad
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ox, oy = pad - int(round(box[0] * ART_SCALE * k)), pad - int(round(box[1] * ART_SCALE * k))

    order = ([ART.index(_BODY)] if (bg and _BODY) else []) + \
            [i for i, e in enumerate(ART) if e["id"] != "background"]
    for i in order:
        e = ART[i]
        mode = None
        if e["press"] == "MMB":
            # 滚轮：按下=整块亮，上滚=上半亮，下滚=下半亮（按下优先）
            if "MMB" in pressed:
                mode = "full"
            elif "WHEEL_UP" in pressed:
                mode = "up"
            elif "WHEEL_DOWN" in pressed:
                mode = "down"
        elif e["press"] in pressed:
            mode = "full"
        tile, pos = _tile(i, k, mode, mouse_yellow)
        im.alpha_composite(tile, (pos[0] + ox, pos[1] + oy))

    im = im.resize((max(1, round(im.width / SS)), max(1, round(im.height / SS))),
                   Image.LANCZOS)

    if GLOW:
        mask = im.getchannel("A").point(lambda v: int(v * GLOW_ALPHA))
        glow = Image.new("RGBA", im.size, (255, 255, 255, 0))
        glow.putalpha(mask)
        glow = glow.filter(ImageFilter.GaussianBlur(max(0.8, 1.7 * ART_SCALE * scale * 1.6)))
        glow.alpha_composite(im)
        im = glow

    if SHADOW:
        im = _with_shadow(im)
    _FRAMES[key] = im
    while len(_FRAMES) > FRAME_CACHE_MAX:
        _FRAMES.popitem(last=False)
    return im


def _with_shadow(im):
    m = SHADOW_MARGIN
    canvas = Image.new("RGBA", (im.width + 2 * m, im.height + 2 * m), (0, 0, 0, 0))
    alpha = im.getchannel("A").point(lambda v: int(v * SHADOW_ALPHA))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.putalpha(alpha)
    sh = sh.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    canvas.alpha_composite(sh, (m + SHADOW_OFFSET[0], m + SHADOW_OFFSET[1]))
    canvas.alpha_composite(im, (m, m))
    return canvas


# ============================ Win32 ============================
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t)

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
TOPMOST_REFRESH = 1.0      # 每隔多久重新声明一次置顶（秒）
CLASS_NAME = "KeyMouseOverlayWnd"
QUIT_EVENT = "KeyMouseOverlayQuitEvent"
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint),
                ("biSizeImage", ctypes.c_uint), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint),
                ("biClrImportant", ctypes.c_uint)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint * 3)]


kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(SIZE), wintypes.HDC, ctypes.POINTER(POINT),
                                       wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION),
                                       ctypes.c_uint]
# 64 位下句柄是 8 字节，不声明 argtypes 会被当成 int 而溢出
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
user32.DefWindowProcW.restype = LRESULT
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, ctypes.c_uint]

_wndproc_ref = []

# ---------------- 移动模式 ----------------
# 浮窗平时是点击穿透的（WS_EX_TRANSPARENT），鼠标点不进去，自然拖不动。
# 按「移动键」（默认 F8）进入移动模式：去掉穿透、画一圈虚线框、可以按住左键拖，
# 松手写进 overlay.json，下次启动自动回到这个位置。
GWL_EXSTYLE = -20
WM_SETCURSOR = 0x0020
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
IDC_SIZEALL = 32646
SWP_FRAMECHANGED = 0x0020
CFG_FILE = os.path.join(HERE, "overlay.json")

_MOVE = {"on": False, "dragging": False, "grab": (0, 0), "hwnd": None,
         "save": None, "decor": {}}

user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetCapture.argtypes = [wintypes.HWND]
user32.ReleaseCapture.argtypes = []
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]


def _move_decor(w, h):
    """移动模式的提示层：半透明白底 + 虚线框，让整块区域都能按住拖动。"""
    got = _MOVE["decor"].get((w, h))
    if got:
        return got
    ov = Image.new("RGBA", (w, h), (255, 255, 255, 18))
    d = ImageDraw.Draw(ov)
    step = 9
    for x in range(0, w, step * 2):
        d.line([(x, 0), (min(x + step, w - 1), 0)], fill=(255, 255, 255, 230), width=2)
        d.line([(x, h - 1), (min(x + step, w - 1), h - 1)], fill=(255, 255, 255, 230), width=2)
    for y in range(0, h, step * 2):
        d.line([(0, y), (0, min(y + step, h - 1))], fill=(255, 255, 255, 230), width=2)
        d.line([(w - 1, y), (w - 1, min(y + step, h - 1))], fill=(255, 255, 255, 230), width=2)
    _MOVE["decor"][(w, h)] = ov
    return ov


def save_config(**kv):
    """把位置/布局写进 overlay.json（读写失败都不影响运行）。"""
    data = {}
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data.update(kv)
    try:
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        _log_exc()


def load_config():
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def set_move_mode(on, img=None):
    """开关移动模式：去/加点击穿透，并重画一帧（带虚线框）。"""
    hwnd = _MOVE["hwnd"]
    if not hwnd or on == _MOVE["on"]:
        return
    _MOVE["on"] = on
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    if on:
        ex &= ~WS_EX_TRANSPARENT
    else:
        ex |= WS_EX_TRANSPARENT
        _MOVE["dragging"] = False
        user32.ReleaseCapture()
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    if img is not None:
        paint(hwnd, img)
    tray_set_tip("键鼠显示（移动模式）· 拖到想要的位置 · 再按一次 F8 完成")
    panel_update()
    print("移动模式：" + ("开（拖完再按一次）" if on else "关"))


def paint(hwnd, img):
    """按当前模式把一帧贴到窗口上（移动模式叠一层虚线提示）。"""
    if _MOVE["on"]:
        dec = _move_decor(*img.size)
        img = Image.alpha_composite(img, dec)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    blit(hwnd, img, r.left, r.top)


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_INPUT:
        _RAW_PENDING[0] = True      # 只做个记号，真正的解析在主循环里批量做
        return 0
    if msg == WM_SETCURSOR and _MOVE["on"]:
        user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(IDC_SIZEALL)))
        return 1
    if msg == WM_LBUTTONDOWN and _MOVE["on"]:
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        _MOVE["grab"] = (pt.x - r.left, pt.y - r.top)
        _MOVE["dragging"] = True
        user32.SetCapture(hwnd)
        return 0
    if msg == WM_MOUSEMOVE and _MOVE["dragging"]:
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        gx, gy = _MOVE["grab"]
        user32.SetWindowPos(hwnd, None, pt.x - gx, pt.y - gy, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        return 0
    if msg == WM_LBUTTONUP and _MOVE["dragging"]:
        _MOVE["dragging"] = False
        user32.ReleaseCapture()
        if _MOVE["save"]:
            _MOVE["save"]()
        return 0
    if msg == 0x0010:          # WM_CLOSE：别人（stop.py / 任务栏）要求关闭
        user32.DestroyWindow(hwnd)
        return 0
    if msg == 0x0002:          # WM_DESTROY
        user32.PostQuitMessage(0)
        return 0
    if msg == 0x0012:          # WM_QUIT
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def create_window(w, h, x, y, clickthrough=True):
    hinst = kernel32.GetModuleHandleW(None)
    class_name = CLASS_NAME
    proc = WNDPROC(_wndproc)
    _wndproc_ref.append(proc)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.style = 0x0003  # CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.hCursor = user32.LoadCursorW(None, 32512)
    wc.lpszClassName = class_name
    user32.RegisterClassExW(ctypes.byref(wc))
    ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if clickthrough:
        ex |= WS_EX_TRANSPARENT
    hwnd = user32.CreateWindowExW(ex, class_name, "KeyMouse Overlay", WS_POPUP,
                                  x, y, w, h, None, None, hinst, None)
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    return hwnd


_DIB = {}          # (w, h) -> (hdc_mem, hbmp, bits)  复用，避免每帧 CreateDIBSection


def _get_dib(w, h):
    got = _DIB.get((w, h))
    if got:
        return got
    for key in list(_DIB):                 # 尺寸变了就丢掉旧的
        hdc_mem, hbmp, _ = _DIB.pop(key)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负值 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0      # BI_RGB
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    gdi32.SelectObject(hdc_mem, hbmp)
    user32.ReleaseDC(None, hdc_screen)
    _DIB[(w, h)] = (hdc_mem, hbmp, bits)
    return _DIB[(w, h)]


def blit(hwnd, im, x, y):
    w, h = im.size
    r, g, b, a = im.split()
    prem = Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(b, a), a))
    data = prem.tobytes("raw", "BGRA")

    hdc_mem, _hbmp, bits = _get_dib(w, h)
    ctypes.memmove(bits, data, len(data))

    hdc_screen = user32.GetDC(None)
    pt_dst = POINT(x, y)
    pt_src = POINT(0, 0)
    size = SIZE(w, h)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    user32.UpdateLayeredWindow(hwnd, hdc_screen, ctypes.byref(pt_dst), ctypes.byref(size),
                               hdc_mem, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA)
    user32.ReleaseDC(None, hdc_screen)


def pump():
    """处理消息队列；收到 WM_QUIT 返回 False。"""
    msg = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        if msg.message == 0x0012:      # WM_QUIT
            return False
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    return True


def is_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# 修饰键：按住任意一个时，单键热键不触发。
# 起因是 issue #1：NVIDIA 录屏用 Alt+F9，我们只认 F9，一录屏就把浮层隐藏了。
MOD_VKS = (0x11, 0xA2, 0xA3,      # Ctrl
           0x10, 0xA0, 0xA1,      # Shift
           0x12, 0xA4, 0xA5,      # Alt
           0x5B, 0x5C)            # Win


def hotkey_pressed(vk):
    """只有"光按这一个键"才算，组合键（Alt+F9 之类）不触发。vk 为 0 表示已禁用。"""
    if not vk or not is_down(vk):
        return False
    return not any(is_down(m) for m in MOD_VKS)


# ============================ 滚轮方向（Raw Input） ============================
# 滚轮没有"按住"这种状态，GetAsyncKeyState 读不到；低层鼠标钩子又会给整机输入加延迟。
# Raw Input 是"旁路复制"：系统把鼠标事件复制一份给我们，不拦截、不影响其它程序。
RI_MOUSE_WHEEL = 0x0400
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
WM_INPUT = 0x00FF
WHEEL_HOLD = 0.07          # 一次滚格亮多久（秒）

_WHEEL = {"up": 0.0, "down": 0.0}


class _RAWMOUSE_BTN(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _RAWMOUSE_U(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("btn", _RAWMOUSE_BTN)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT), ("u", _RAWMOUSE_U),
                ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


user32.GetRawInputData.restype = wintypes.UINT
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]


def register_wheel(hwnd):
    dev = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)
    ok = user32.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(dev))
    return bool(ok)


_RAW_PENDING = [False]
_RAW_BUF = ctypes.create_string_buffer(65536)
_RAWHDR_SIZE = ctypes.sizeof(RAWINPUTHEADER)
_RAWMOUSE_OFF = _RAWHDR_SIZE


def drain_raw_input():
    """把这一轮积压的 Raw Input 一次性读完（鼠标移动事件非常多，逐条处理很费 CPU）。

    只关心滚轮：读出来的都是内存里的结构体，不做系统调用。
    """
    cb = wintypes.UINT(len(_RAW_BUF))
    n = user32.GetRawInputBuffer(_RAW_BUF, ctypes.byref(cb), _RAWHDR_SIZE)
    if n == 0xFFFFFFFF or not n:
        return
    off = 0
    now = time.perf_counter()
    buf = _RAW_BUF
    for _ in range(n):
        # 直接按字节读，别为每条事件创建 ctypes 对象（8kHz 游戏鼠标一秒能发上万条）
        dw_type, dw_size = struct.unpack_from("<II", buf, off)
        if dw_type == RIM_TYPEMOUSE:
            flags, data = struct.unpack_from("<HH", buf, off + _RAWMOUSE_OFF + 4)
            if flags & RI_MOUSE_WHEEL:
                delta = ctypes.c_short(data).value
                if delta > 0:
                    _WHEEL["up"] = now
                elif delta < 0:
                    _WHEEL["down"] = now
        step = (dw_size + 7) & ~7
        if step <= 0:
            break
        off += step
        if off >= len(buf):
            break


def _on_raw_input(lparam):
    size = wintypes.UINT(ctypes.sizeof(RAWINPUT))
    buf = RAWINPUT()
    got = user32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(buf),
                                 ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
    if got == 0xFFFFFFFF or buf.header.dwType != RIM_TYPEMOUSE:
        return
    if buf.mouse.u.btn.usButtonFlags & RI_MOUSE_WHEEL:
        delta = ctypes.c_short(buf.mouse.u.btn.usButtonData).value
        now = time.perf_counter()
        if delta > 0:
            _WHEEL["up"] = now
        elif delta < 0:
            _WHEEL["down"] = now


def _log_exc():
    """渲染/贴图出错时记一笔日志——pythonw 没有控制台，不记就等于静默死掉。"""
    try:
        import traceback
        with open(os.path.join(HERE, "keyoverlay.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + traceback.format_exc() + "\n")
    except Exception:
        pass



# ============================ 系统托盘 ============================
# 用 Shell_NotifyIcon 做托盘图标 + 右键菜单：显示/隐藏、重新加载设计稿、退出。
# 托盘消息需要一个能收消息的窗口；这里单独建一个"隐藏的普通窗口"，
# 而不是复用浮层窗口——浮层是 WS_EX_NOACTIVATE|TOOLWINDOW，用它做菜单宿主时
# SetForegroundWindow 会失败，导致"点菜单外面不会关闭"的老毛病。
TRAY_CLASS = "KeyMouseOverlayTray"
TRAY_MSG = 0x8001          # WM_APP + 1
WM_COMMAND = 0x0111
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK, WM_CONTEXTMENU = 0x0202, 0x0205, 0x0203, 0x007B
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0000, 0x0800, 0x0008
TPM_RETURNCMD, TPM_RIGHTBUTTON, TPM_NONOTIFY = 0x0100, 0x0002, 0x0080
ID_TOGGLE, ID_RELOAD, ID_QUIT, ID_PANEL = 1001, 1002, 1003, 1004
ID_CLASSIC, ID_EXTENDED, ID_WIDE = 1005, 1006, 1013
ID_SCALE_DOWN, ID_SCALE_UP, ID_SCALE_RESET, ID_MOVE = 1007, 1008, 1009, 1010
ID_HK_TOGGLE, ID_HK_QUIT = 1011, 1012
CBS_DROPDOWNLIST = 0x0003
WS_VSCROLL = 0x00200000
CB_ADDSTRING, CB_SETCURSEL, CB_GETCURSEL = 0x0143, 0x014E, 0x0147
CBN_SELCHANGE = 1
# 可选热键（0 = 禁用）。F8 留给"移动浮窗"，不放进来避免打架。
HOTKEY_CHOICES = [
    ("禁用", 0), ("F6", 0x75), ("F7", 0x76), ("F9", 0x78), ("F10", 0x79),
    ("F11", 0x7A), ("F12", 0x7B), ("ScrollLock", 0x91), ("Pause", 0x13),
    ("Insert", 0x2D), ("Home", 0x24), ("End", 0x23), ("PageUp", 0x21),
    ("PageDown", 0x22), ("`", 0xC0), ("[", 0xDB), ("]", 0xDD),
]

CMD = {"toggle": False, "reload": False, "quit": False, "layout": None,
       "panel": False, "scale": None, "move": False}
_TRAY = {"hwnd": None, "hicon": None, "icon": None, "on": False, "visible": True}


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, ctypes.c_uint, ctypes.c_size_t, wintypes.LPCWSTR]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_uint,
                              ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINTER)] if False else [ctypes.c_void_p]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

_tray_proc_ref = []


def _load_tray_icon():
    """优先用 exe/脚本旁边的 app.ico，没有就用 exe 自带图标，再没有就系统默认。"""
    cx = user32.GetSystemMetrics(49)      # SM_CXSMICON
    cy = user32.GetSystemMetrics(50)      # SM_CYSMICON
    for name in ("app.ico",):
        path = _resource(name)
        if os.path.exists(path):
            h = user32.LoadImageW(None, path, 1, cx, cy, 0x0010)   # IMAGE_ICON | LR_LOADFROMFILE
            if h:
                return h, True
    if getattr(sys, "frozen", False):
        h = user32.LoadImageW(None, sys.executable, 1, cx, cy, 0x0000)
        if h:
            return h, True
    return user32.LoadIconW(None, ctypes.c_void_p(32512)), False   # IDI_APPLICATION


def tray_set_tip(text):
    d = _TRAY
    if not d["on"]:
        return
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = d["hwnd"]
    nid.uID = 1
    nid.uFlags = NIF_TIP
    nid.szTip = text[:127]
    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))


def tray_add():
    d = _TRAY
    if d["on"] or not d["hwnd"]:
        return
    hicon, owned = _load_tray_icon()
    d["hicon"], d["icon"] = hicon, owned
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = d["hwnd"]
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = TRAY_MSG
    nid.hIcon = hicon
    nid.szTip = "键鼠显示  ·  左键单击显示/隐藏  ·  右键菜单"
    ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
    d["on"] = ok
    print("托盘图标：%s" % ("已添加" if ok else "添加失败，错误码 %d" % ctypes.get_last_error()))


def tray_remove():
    d = _TRAY
    if not d["on"]:
        return
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = d["hwnd"]
    nid.uID = 1
    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    if d["icon"] and d["hicon"]:
        user32.DestroyIcon(d["hicon"])
    d["on"] = False


def show_tray_menu(hwnd):
    menu = user32.CreatePopupMenu()
    flags = MF_STRING | (0 if _TRAY["visible"] else MF_CHECKED)
    user32.AppendMenuW(menu, MF_STRING, ID_TOGGLE, "隐藏浮层" if _TRAY["visible"] else "显示浮层")
    if _PANEL["hwnd"]:
        user32.AppendMenuW(menu, MF_STRING, ID_PANEL, "打开控制面板")
    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if CURRENT_LAYOUT == "classic" else 0),
                       ID_CLASSIC, "经典布局")
    user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if CURRENT_LAYOUT == "extended" else 0),
                       ID_EXTENDED, "扩展布局")
    user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if CURRENT_LAYOUT == "wide" else 0),
                       ID_WIDE, "五列布局")
    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(menu, MF_STRING, ID_RELOAD, "重新加载当前设计稿 (svg)")
    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出")
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    user32.SetForegroundWindow(hwnd)      # 否则点菜单外面不会自动关
    cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
                                pt.x, pt.y, 0, hwnd, None)
    user32.PostMessageW(hwnd, 0x0000, 0, 0)
    user32.DestroyMenu(menu)
    if cmd == ID_TOGGLE:
        CMD["toggle"] = True
    elif cmd == ID_PANEL:
        panel_show()
    elif cmd == ID_CLASSIC:
        CMD["layout"] = "classic"
    elif cmd == ID_EXTENDED:
        CMD["layout"] = "extended"
    elif cmd == ID_WIDE:
        CMD["layout"] = "wide"
    elif cmd == ID_RELOAD:
        CMD["reload"] = True
    elif cmd == ID_QUIT:
        CMD["quit"] = True


def _tray_wndproc(hwnd, msg, wparam, lparam):
    if msg == TRAY_MSG:
        if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
            show_tray_menu(hwnd)
        elif lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            CMD["toggle"] = True
        return 0
    if msg == WM_COMMAND:
        rid = wparam & 0xFFFF
        if rid == ID_TOGGLE:
            CMD["toggle"] = True
        elif rid == ID_PANEL:
            panel_show()
        elif rid == ID_CLASSIC:
            CMD["layout"] = "classic"
        elif rid == ID_EXTENDED:
            CMD["layout"] = "extended"
        elif rid == ID_WIDE:
            CMD["layout"] = "wide"
        elif rid == ID_RELOAD:
            CMD["reload"] = True
        elif rid == ID_QUIT:
            CMD["quit"] = True
        return 0
    if msg in (0x0010, 0x0002):        # WM_CLOSE / WM_DESTROY
        return 0                        # 托盘窗口不许被关掉
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def create_tray_window():
    hinst = kernel32.GetModuleHandleW(None)
    proc = WNDPROC(_tray_wndproc)
    _tray_proc_ref.append(proc)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.lpszClassName = TRAY_CLASS
    user32.RegisterClassExW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, TRAY_CLASS, "KeyMouseOverlayTray", WS_POPUP,
                                  0, 0, 0, 0, None, None, hinst, None)
    _TRAY["hwnd"] = hwnd
    return hwnd


# ============================ 原生控制面板 ============================
PANEL_CLASS = "KeyMouseOverlayPanel"
PANEL_CLASSIC_DESC, PANEL_EXTENDED_DESC, PANEL_WIDE_DESC = 2001, 2002, 2004
PANEL_STATUS = 2003
WM_SETFONT = 0x0030
BM_SETCHECK = 0x00F1
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_GROUP = 0x00020000
WS_OVERLAPPEDWINDOW = 0x00CF0000
BS_AUTORADIOBUTTON = 0x00000009
BS_GROUPBOX = 0x00000007
BS_PUSHBUTTON = 0x00000000
_PANEL = {"hwnd": None, "visible": False, "font": None, "title_font": None,
          "classic": None, "extended": None, "wide": None, "status": None,
          "toggle": None, "scale_label": None, "move": None,
          "hk_toggle": None, "hk_quit": None}
_panel_proc_ref = []

user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
gdi32.CreateFontW.restype = wintypes.HFONT


def _panel_wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_COMMAND:
        rid = wparam & 0xFFFF
        code = (wparam >> 16) & 0xFFFF
        if rid in (ID_HK_TOGGLE, ID_HK_QUIT):
            if code == CBN_SELCHANGE:
                which = "toggle" if rid == ID_HK_TOGGLE else "quit"
                ctrl = _PANEL["hk_toggle" if which == "toggle" else "hk_quit"]
                idx = int(user32.SendMessageW(ctrl, CB_GETCURSEL, 0, 0))
                if 0 <= idx < len(HOTKEY_CHOICES):
                    vk = HOTKEY_CHOICES[idx][1]
                    other = "quit" if which == "toggle" else "toggle"
                    if vk and vk == _HK[other]:
                        panel_update()          # 把下拉框退回原值
                        user32.SetWindowTextW(
                            _PANEL["status"],
                            "显示/隐藏与退出程序不能使用同一个按键")
                        user32.MessageBeep(0)
                        return 0
                    _HK[which] = vk
                    save_config(hotkeys=dict(_HK))
                    panel_update()
            return 0
        if rid == ID_CLASSIC:
            CMD["layout"] = "classic"
        elif rid == ID_EXTENDED:
            CMD["layout"] = "extended"
        elif rid == ID_WIDE:
            CMD["layout"] = "wide"
        elif rid == ID_TOGGLE:
            CMD["toggle"] = True
        elif rid == ID_SCALE_DOWN:
            CMD["scale"] = "down"
        elif rid == ID_SCALE_UP:
            CMD["scale"] = "up"
        elif rid == ID_SCALE_RESET:
            CMD["scale"] = "reset"
        elif rid == ID_MOVE:
            CMD["move"] = True
        elif rid == ID_QUIT:
            CMD["quit"] = True
        return 0
    if msg == 0x0010:              # WM_CLOSE：只收起面板，不退出浮层
        user32.ShowWindow(hwnd, 0)
        _PANEL["visible"] = False
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def panel_update():
    """同步面板里的单选状态、浮层状态和按钮文字。"""
    if not _PANEL["hwnd"]:
        return
    user32.SendMessageW(_PANEL["classic"], BM_SETCHECK,
                        1 if CURRENT_LAYOUT == "classic" else 0, 0)
    user32.SendMessageW(_PANEL["extended"], BM_SETCHECK,
                        1 if CURRENT_LAYOUT == "extended" else 0, 0)
    user32.SendMessageW(_PANEL["wide"], BM_SETCHECK,
                        1 if CURRENT_LAYOUT == "wide" else 0, 0)
    user32.SetWindowTextW(_PANEL["status"],
                          "当前：%s  ·  浮层%s" % (LAYOUT_NAMES[CURRENT_LAYOUT],
                                               "显示中" if _TRAY["visible"] else "已隐藏"))
    user32.SetWindowTextW(_PANEL["toggle"], "隐藏浮层" if _TRAY["visible"] else "显示浮层")
    if _PANEL["scale_label"]:
        user32.SetWindowTextW(_PANEL["scale_label"], "%d%%" % round(CURRENT_SCALE * 100))
    if _PANEL["move"]:
        user32.SetWindowTextW(_PANEL["move"],
                              "完成移动 (F8)" if _MOVE["on"] else "移动浮窗 (F8)")
    for key, ctl in (("toggle", "hk_toggle"), ("quit", "hk_quit")):
        h = _PANEL.get(ctl)
        if h:
            idx = next((i for i, (_l, vk) in enumerate(HOTKEY_CHOICES) if vk == _HK[key]), 0)
            user32.SendMessageW(h, CB_SETCURSEL, idx, 0)


def panel_show():
    if not _PANEL["hwnd"]:
        return
    panel_update()
    user32.ShowWindow(_PANEL["hwnd"], 5)       # SW_SHOW
    user32.SetForegroundWindow(_PANEL["hwnd"])
    _PANEL["visible"] = True


def create_panel(show=True):
    """创建轻量 Win32 控制面板；消息由主循环统一的 pump() 处理。"""
    hinst = kernel32.GetModuleHandleW(None)
    proc = WNDPROC(_panel_wndproc)
    _panel_proc_ref.append(proc)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.hCursor = user32.LoadCursorW(None, 32512)
    wc.hbrBackground = 16                       # COLOR_BTNFACE + 1
    wc.lpszClassName = PANEL_CLASS
    user32.RegisterClassExW(ctypes.byref(wc))

    sw = user32.GetSystemMetrics(0)
    hwnd = user32.CreateWindowExW(0, PANEL_CLASS, "KeyOverlay 控制面板",
                                  WS_OVERLAPPEDWINDOW, max(20, sw - 470), 60,
                                  430, 628, None, None, hinst, None)
    if not hwnd:
        return None
    _PANEL["hwnd"] = hwnd
    font = gdi32.CreateFontW(-18, 0, 0, 0, 400, 0, 0, 0, 1,
                             0, 0, 0, 0, "Microsoft YaHei UI")
    title_font = gdi32.CreateFontW(-25, 0, 0, 0, 600, 0, 0, 0, 1,
                                   0, 0, 0, 0, "Microsoft YaHei UI")
    _PANEL["font"], _PANEL["title_font"] = font, title_font

    def control(kind, text, x, y, w, h, cid=None, style=0, title=False):
        child = user32.CreateWindowExW(0, kind, text, WS_CHILD | WS_VISIBLE | style,
                                       x, y, w, h, hwnd, cid, hinst, None)
        chosen = title_font if title else font
        if chosen:
            user32.SendMessageW(child, WM_SETFONT, chosen, 1)
        return child

    def combo(choices, x, y, w, cur_vk, cid=None):
        child = user32.CreateWindowExW(
            0, "COMBOBOX", "", WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST | WS_VSCROLL,
            x, y, w, 200, hwnd, cid, hinst, None)
        for label, _vk in choices:
            # SendMessageW 的 argtypes 是整数，字符串要塞成指针地址
            buf = ctypes.c_wchar_p(label)
            user32.SendMessageW(child, CB_ADDSTRING, 0,
                                ctypes.cast(buf, ctypes.c_void_p).value)
        idx = next((i for i, (_l, vk) in enumerate(choices) if vk == cur_vk), 0)
        user32.SendMessageW(child, CB_SETCURSEL, idx, 0)
        if font:
            user32.SendMessageW(child, WM_SETFONT, font, 1)
        return child

    control("STATIC", "KeyOverlay 控制面板", 24, 18, 360, 34, title=True)
    control("STATIC", "切换显示方案；按 F8 可拖动浮窗位置", 24, 56, 370, 24)
    control("BUTTON", "按键显示布局", 20, 84, 370, 196, style=BS_GROUPBOX)
    _PANEL["classic"] = control("BUTTON", "经典布局", 42, 112, 180, 25,
                                ID_CLASSIC, BS_AUTORADIOBUTTON | WS_GROUP)
    control("STATIC", "Q W E / A S D / Ctrl / 空格", 61, 138, 290, 22,
            PANEL_CLASSIC_DESC)
    _PANEL["extended"] = control("BUTTON", "扩展布局", 42, 165, 180, 25,
                                 ID_EXTENDED, BS_AUTORADIOBUTTON)
    control("STATIC", "1–4 / Tab / Q–R / A–F / Z–V / Alt", 61, 191, 310, 22,
            PANEL_EXTENDED_DESC)
    _PANEL["wide"] = control("BUTTON", "五列布局", 42, 218, 180, 25,
                             ID_WIDE, BS_AUTORADIOBUTTON)
    control("STATIC", "1–5 / Tab / Q–T / A–G / Z–B / Alt", 61, 244, 310, 22,
            PANEL_WIDE_DESC)
    _PANEL["status"] = control("STATIC", "", 24, 288, 370, 24, PANEL_STATUS)

    control("BUTTON", "快捷键", 20, 318, 370, 116, style=BS_GROUPBOX)
    control("STATIC", "显示/隐藏", 36, 344, 100, 22)
    _PANEL["hk_toggle"] = combo(HOTKEY_CHOICES, 150, 340, 150, _HK["toggle"], ID_HK_TOGGLE)
    control("STATIC", "退出程序", 36, 382, 100, 22)
    _PANEL["hk_quit"] = combo(HOTKEY_CHOICES, 150, 378, 150, _HK["quit"], ID_HK_QUIT)

    control("STATIC", "缩放", 24, 452, 60, 24)
    control("BUTTON", "\u2212", 88, 448, 46, 32, ID_SCALE_DOWN, BS_PUSHBUTTON)
    _PANEL["scale_label"] = control("STATIC", "120%", 138, 452, 90, 24, PANEL_STATUS + 10)
    control("BUTTON", "+", 232, 448, 46, 32, ID_SCALE_UP, BS_PUSHBUTTON)
    control("BUTTON", "重置", 286, 448, 98, 32, ID_SCALE_RESET, BS_PUSHBUTTON)

    _PANEL["move"] = control("BUTTON", "移动浮窗 (F8)", 24, 494, 170, 36,
                             ID_MOVE, BS_PUSHBUTTON)
    _PANEL["toggle"] = control("BUTTON", "隐藏浮层", 216, 494, 170, 36,
                               ID_TOGGLE, BS_PUSHBUTTON)
    control("BUTTON", "退出程序", 24, 540, 362, 36, ID_QUIT, BS_PUSHBUTTON)
    _TRAY["visible"] = True
    if show:
        panel_show()
    return hwnd


def reload_art():
    """重新解析 SVG 并清空贴片/帧缓存（托盘菜单里的"重新加载设计稿"）。"""
    global ART, _BODY, _CONTENT, BX0, BY0, BX1, BY1
    ART = load_svg(SVG_FILE)
    _BODY = next((e for e in ART if e["id"] == "background"), None)
    _CONTENT = [e for e in ART if e["id"] != "background"]
    BX0 = min(e["bbox"][0] for e in _CONTENT)
    BY0 = min(e["bbox"][1] for e in _CONTENT)
    BX1 = max(e["bbox"][2] for e in _CONTENT)
    BY1 = max(e["bbox"][3] for e in _CONTENT)
    _TILES.clear()
    _FRAMES.clear()
    _rebuild_active_vks()


def set_layout(name):
    """切换设计稿并清空缓存；name 为 classic、extended 或 wide。"""
    global CURRENT_LAYOUT, SVG_FILE
    if name not in SVG_FILES:
        raise ValueError("未知布局: %s" % name)
    if name == CURRENT_LAYOUT:
        return
    CURRENT_LAYOUT = name
    SVG_FILE = _resource(SVG_FILES[name])
    reload_art()


# ============================ 主流程 ============================
def main():
    global CURRENT_SCALE
    ap = argparse.ArgumentParser()
    ap.add_argument("--pos", default=None, help="左上角坐标 x,y（默认左下角）")
    ap.add_argument("--margin", type=int, default=40)
    ap.add_argument("--move-vk", default=None,
                    help="移动浮窗的按键（默认 0x77 = F8）：按一下进入移动模式，拖完再按一次")
    ap.add_argument("--scale", type=float, default=None,
                    help="初始缩放倍率（不给就用上次的，默认 1.2）")
    ap.add_argument("--layout", choices=tuple(SVG_FILES), default=None,
                    help="初始按键布局：classic 经典，extended 扩展，wide 五列（不给就用上次的）")
    ap.add_argument("--no-wheel", action="store_true",
                    help="不监听滚轮方向（省一点 CPU，上滚/下滚就不亮了）")
    ap.add_argument("--panel", dest="panel", action="store_true", default=None,
                    help="启动时打开控制面板（exe 默认不开，脚本模式默认打开）")
    ap.add_argument("--no-panel", dest="panel", action="store_false",
                    help="启动时不打开控制面板")
    ap.add_argument("--bg", action="store_true", help="连 SVG 的黑色底板一起画")
    ap.add_argument("--mouse-yellow", action="store_true", help="鼠标左右键常亮黄色")
    ap.add_argument("--solid", action="store_true", help="取消点击穿透")
    ap.add_argument("--preview", default=None, help="只渲染一张静态图到指定路径")
    ap.add_argument("--pressed", default="W,LMB", help="预览时高亮的键，逗号分隔")
    ap.add_argument("--hold", type=float, default=0.03,
                    help="按下后至少点亮多久（秒）。调小=连点更容易分成两下，调大=更快的手点也能看清")
    ap.add_argument("--no-tray", action="store_true", help="不创建系统托盘图标")
    ap.add_argument("--debug", action="store_true", help="把状态变化写进 keyoverlay.log，排查连点用")
    ap.add_argument("--poll", type=float, default=0.006,
                    help="轮询间隔（秒），默认 0.006")
    ap.add_argument("--toggle-vk", default=None,
                    help="显示/隐藏热键的虚拟键码（默认 0x78 = F9；写 0 或 none 表示禁用）")
    ap.add_argument("--quit-vk", default=None,
                    help="退出热键（默认 0x79 = F10；写 0 或 none 表示禁用）")
    args = ap.parse_args()
    def _vk(text, default):
        """把 "0x78" / "0" / "none" / "off" 解析成虚拟键码。"""
        if text is None:
            return default
        t = str(text).strip().lower()
        if t in ("", "0", "none", "off", "disable"):
            return 0
        return int(t, 0)

    saved_hk = load_config().get("hotkeys") or {}
    _HK["toggle"] = _vk(args.toggle_vk, int(saved_hk.get("toggle", 0x78)))
    _HK["quit"] = _vk(args.quit_vk, int(saved_hk.get("quit", 0x79)))
    _HK["move"] = _vk(args.move_vk, int(saved_hk.get("move", 0x77)))
    if _HK["toggle"] and _HK["toggle"] == _HK["quit"]:
        if args.toggle_vk is not None or args.quit_vk is not None:
            ap.error("--toggle-vk 和 --quit-vk 不能使用同一个按键")
        _HK["quit"] = 0                     # 旧配置冲突：先禁用退出热键
        save_config(hotkeys=dict(_HK))
        print("快捷键冲突：退出热键已禁用，请在控制面板里重新设置。")
    set_layout(args.layout or load_config().get("layout") or "classic")
    CURRENT_SCALE = args.scale or float(load_config().get("scale") or 1.2)
    CURRENT_SCALE = max(SCALE_MIN, min(SCALE_MAX, round(CURRENT_SCALE, 2)))

    if args.preview:
        hit = tuple(v.strip().upper() for v in args.pressed.split(",") if v.strip())
        im = render(pressed=hit, scale=CURRENT_SCALE, bg=args.bg, mouse_yellow=args.mouse_yellow)
        bg = Image.new("RGBA", im.size, (32, 38, 52, 255))
        bg.alpha_composite(im)
        bg.convert("RGB").save(args.preview)
        print("已生成预览:", args.preview, im.size)
        return 0

    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

    # 单实例：已经有实例在跑就让旧的安静退出，自己接管（避免叠出多个浮层，F9 只关掉一个）
    h_event = kernel32.CreateEventW(None, False, False, QUIT_EVENT)
    old = user32.FindWindowW(CLASS_NAME, None)
    if old:
        kernel32.SetEvent(h_event)
        for _ in range(40):
            time.sleep(0.05)
            if not user32.FindWindowW(CLASS_NAME, None):
                break

    img = render(scale=CURRENT_SCALE, bg=args.bg, mouse_yellow=args.mouse_yellow)
    w, h = img.size
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    cfg = load_config()
    restored = False
    if args.pos:
        x, y = [int(v) for v in args.pos.split(",")]
    elif cfg.get("pos"):
        # 存的是窗口真实左上角，不要再扣投影边距，否则每重启一次就偏移一点
        x, y = int(cfg["pos"][0]), int(cfg["pos"][1])
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        restored = True
    else:
        x, y = args.margin, sh - h - args.margin
    if SHADOW and not restored:
        x -= SHADOW_MARGIN
        y -= SHADOW_MARGIN

    hwnd = create_window(w, h, x, y, clickthrough=not args.solid)
    if not hwnd:
        print("创建窗口失败，错误码:", ctypes.get_last_error())
        return 1

    _MOVE["hwnd"] = hwnd
    if not args.no_wheel:
        register_wheel(hwnd)

    def _save_pos():
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        save_config(pos=[r.left, r.top])

    _MOVE["save"] = _save_pos

    def _place_keeping_bottom(img):
        """换布局/换缩放时保持窗口左下角不动；超出屏幕时贴边。"""
        nonlocal x, y, w, h
        r = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(r)):
            left, bottom = r.left, r.bottom
        else:
            left, bottom = x, y + h
        w, h = img.size
        # 用虚拟屏幕范围，避免把副屏上的浮窗夹回主屏
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        if vw <= 0 or vh <= 0:
            vx, vy, vw, vh = 0, 0, sw, sh
        x = max(vx, min(left, vx + vw - w))
        y = max(vy, min(bottom - h, vy + vh - h))
        user32.SetWindowPos(hwnd, None, x, y, w, h,
                            SWP_NOZORDER | SWP_NOACTIVATE)

    paint(hwnd, img)

    # 托盘图标：单击显示/隐藏，右键出菜单
    if not args.no_tray:
        try:
            create_tray_window()
            tray_add()
        except Exception:
            _log_exc()

    # 面板总是创建（托盘里随时能打开），但 exe 双击启动时不弹出来
    want_panel = args.panel if args.panel is not None else (not getattr(sys, "frozen", False))
    try:
        create_panel(show=want_panel)
    except Exception:
        _log_exc()

    prev_state = frozenset()
    last_top = 0.0
    last_seen = {}          # 名称 -> 最后按下的时刻，实现最短显示时长
    MIN_HOLD = max(0.02, args.hold)
    POLL = max(0.002, args.poll)
    dbg = args.debug
    dbg_f = open(os.path.join(HERE, "keyoverlay.log"), "w", encoding="utf-8") if dbg else None
    prev_move = False
    prev_f9 = prev_f10 = False
    visible = True
    print("已启动：%dx%d @ (%d,%d)   F9 隐藏/显示，F10 退出" % (w, h, x, y))

    try:
        while True:
            # 必须先读 Raw Input 再泵消息：pump() 派发完 WM_INPUT 后，队列里的原始数据就没了
            drain_raw_input()
            if not pump():
                break
            if h_event and kernel32.WaitForSingleObject(h_event, 0) == 0:
                break
            now = time.time()
            down = {n for n, vk in ACTIVE_VKS if is_down(vk)}
            down |= {b[0] for b in MOUSE_BTNS if is_down(b[1])}
            for n in down:
                last_seen[n] = now
            state = frozenset(n for n, t in last_seen.items() if now - t < MIN_HOLD)
            # 滚轮方向：只有"刚刚滚过"这一小段时间内才亮
            nowp = time.perf_counter()
            wheel_names = set()
            if nowp - _WHEEL["up"] < WHEEL_HOLD:
                wheel_names.add("WHEEL_UP")
            if nowp - _WHEEL["down"] < WHEEL_HOLD:
                wheel_names.add("WHEEL_DOWN")
            if wheel_names:
                state = state | frozenset(wheel_names)
            if state != prev_state:
                if dbg_f:
                    dbg_f.write("%.3f %s\n" % (now, ",".join(sorted(state)) or "-"))
                    dbg_f.flush()
                if visible:
                    try:
                        img = render(pressed=state, scale=CURRENT_SCALE,
                                     bg=args.bg, mouse_yellow=args.mouse_yellow)
                    except Exception:
                        _log_exc()
                        img = None
                    if img is not None:
                        if img.size != (w, h):
                            w, h = img.size
                            user32.SetWindowPos(hwnd, None, x, y, w, h,
                                                SWP_NOZORDER | SWP_NOACTIVATE)
                        paint(hwnd, img)
                prev_state = state

            requested_layout = CMD["layout"]
            CMD["layout"] = None
            if requested_layout and requested_layout != CURRENT_LAYOUT:
                try:
                    set_layout(requested_layout)
                    img = render(pressed=prev_state, scale=CURRENT_SCALE,
                                 bg=args.bg, mouse_yellow=args.mouse_yellow)
                    _place_keeping_bottom(img)
                    paint(hwnd, img)
                    save_config(layout=CURRENT_LAYOUT)
                    _save_pos()
                    panel_update()
                    print("布局：%s" % LAYOUT_NAMES[CURRENT_LAYOUT])
                except Exception:
                    _log_exc()

            if CMD["move"]:
                CMD["move"] = False
                set_move_mode(not _MOVE["on"], img)

            req_scale = CMD["scale"]
            CMD["scale"] = None
            if req_scale:
                try:
                    if req_scale == "reset":
                        new_scale = 1.2
                    elif req_scale == "up":
                        new_scale = round(CURRENT_SCALE + SCALE_STEP, 2)
                    else:
                        new_scale = round(CURRENT_SCALE - SCALE_STEP, 2)
                    new_scale = max(SCALE_MIN, min(SCALE_MAX, new_scale))
                    if new_scale != CURRENT_SCALE:
                        CURRENT_SCALE = new_scale
                        _TILES.clear()          # 旧倍率的贴片全扔掉，别越攒越多
                        _FRAMES.clear()
                        img = render(pressed=prev_state, scale=CURRENT_SCALE,
                                     bg=args.bg, mouse_yellow=args.mouse_yellow)
                        _place_keeping_bottom(img)
                        paint(hwnd, img)
                        save_config(scale=CURRENT_SCALE)
                        _save_pos()
                        panel_update()
                        print("缩放：%d%%" % round(CURRENT_SCALE * 100))
                except Exception:
                    _log_exc()

            if CMD["quit"]:
                CMD["quit"] = False
                break
            if CMD["reload"]:
                CMD["reload"] = False
                try:
                    reload_art()
                except Exception:
                    _log_exc()
                img = render(pressed=prev_state, scale=CURRENT_SCALE,
                             bg=args.bg, mouse_yellow=args.mouse_yellow)
                if img.size != (w, h):
                    _place_keeping_bottom(img)
                    _save_pos()
                paint(hwnd, img)
                panel_update()

            toggle_req = CMD["toggle"]
            CMD["toggle"] = False
            f9 = hotkey_pressed(_HK["toggle"])
            f10 = hotkey_pressed(_HK["quit"])
            if (f9 and not prev_f9) or toggle_req:
                visible = not visible
                _TRAY["visible"] = visible
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE if visible else 0)
                if visible:
                    paint(hwnd, img)
                tray_set_tip("键鼠显示（显示中）· 左键单击隐藏 · 右键菜单" if visible
                             else "键鼠显示（已隐藏）· 左键单击显示 · 右键菜单")
                panel_update()
                print("显示" if visible else "隐藏")
            if f10 and not prev_f10:
                print("退出")
                break
            mv = hotkey_pressed(_HK["move"])
            if mv and not prev_move:
                set_move_mode(not _MOVE["on"], img)
            prev_move = mv

            prev_f9, prev_f10 = f9, f10

            # 有些游戏会抢置顶，把浮层压到下面；隔一秒重申一次
            if now - last_top > TOPMOST_REFRESH:
                user32.SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                last_top = now
            time.sleep(POLL)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            tray_remove()
        except Exception:
            pass
        user32.DestroyWindow(hwnd)
        if _PANEL["hwnd"]:
            user32.DestroyWindow(_PANEL["hwnd"])
            _PANEL["hwnd"] = None
        for key in ("font", "title_font"):
            if _PANEL[key]:
                gdi32.DeleteObject(_PANEL[key])
                _PANEL[key] = None
        if dbg_f:
            dbg_f.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
