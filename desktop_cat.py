"""desktop-cat —— 会溜达、会讨食、会玩逗猫棒的 Windows 桌面宠物
纯 Win32 分层窗口实现（ctypes，无 GUI 框架依赖），逐像素透明、置顶、不抢焦点。

功能：
  · 自己在屏幕底部溜达：走、跑、跳、打盹、发呆，还会自言自语
  · 右键菜单 → 投喂小鱼干：鱼干掉落，猫走过去吃掉（饱食度系统）
  · 右键菜单 → 逗猫棒：逗猫棒跟鼠标 12 秒，猫追着扑；举高了会跳起来够
  · 饱食度低会讨食，饿的时候不睡觉；睡觉被投喂/逗猫棒会立刻醒来
  · 左键点它 = 开心蹦 + 爱心；按住拖动 = 拎着走

用法：
  python desktop_cat.py [--size 120] [--sprite <精灵图>] [--sleep-sheet <睡觉精灵图>]

精灵图规格见 assets/sprites/ 与 README.md —— 给自己家的猫做一个只需换一张图。

依赖：Python 3.9+，Pillow（pip install pillow）
"""

import argparse
import ctypes, ctypes.wintypes as wt, math, random, sys, time, os, traceback
from ctypes import byref, sizeof, c_void_p, c_int, c_uint
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------- 素材与日志路径（相对仓库/脚本目录，不再写死绝对路径） ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE_DIR, "assets", "sprites")
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "cat_states.log")
ERR_PATH = os.path.join(LOG_DIR, "cat_err.log")

_parser = argparse.ArgumentParser(description="Windows 桌面宠物猫")
_parser.add_argument("--size", type=int, default=120, help="猫的像素高度（默认 120）")
_parser.add_argument("--sprite", default=os.path.join(ASSETS, "walk_cat.png"),
                     help="行走/动作精灵图（8列x9行，单元格192x208）")
_parser.add_argument("--sleep-sheet", default=os.path.join(ASSETS, "sleep_cat.png"),
                     help="睡觉精灵图（8列，第0行 8 帧）")
_parser.add_argument("--demo", action="store_true", help="自测模式：自动投喂/逗猫后退出")
_args, _unknown = _parser.parse_known_args()

SPRITE_SRC = _args.sprite
CELL_W, CELL_H = 192, 208
SHEET = Image.open(SPRITE_SRC).convert("RGBA")
ROW_OF = {"idle": 0, "walk": 1, "run": 1, "jump": 3, "happy": 6, "eat": 6, "sleep": 5}
# 可选：睡觉时换成单独的精灵图
SLEEP_SHEET = Image.open(_args.sleep_sheet).convert("RGBA") if os.path.exists(_args.sleep_sheet) else None

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 2
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
WM_DESTROY, WM_TIMER, WM_COMMAND = 0x0002, 0x0113, 0x0111
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, WM_RBUTTONUP = 0x0201, 0x0202, 0x0200, 0x0205
CS_DBLCLKS = 0x0008


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wt.LONG), ("cy", wt.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", POINT)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", c_int),
                ("cbWndExtra", c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


# --- 关键：声明 64 位参数类型，避免 LPARAM 溢出（Windows x64 下必须）---
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, c_int, c_int,
                                   c_int, c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
                                       wt.HDC, ctypes.POINTER(POINT), wt.COLORREF,
                                       ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), ctypes.c_uint,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, ctypes.c_uint]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]


# ---------------- 帧生成 ----------------
FLOOR_MARGIN = 70          # 距屏幕底部
_sprite_cache = {}


def make_cat(height, rot=0.0, sx=1.0, sy=1.0):
    first = SHEET.crop((0, 0, CELL_W, CELL_H))
    cat = first.crop(first.getchannel("A").getbbox())
    s = height / cat.height
    img = cat.resize((max(1, int(cat.width * s * sx)), max(1, int(cat.height * s * sy))), Image.LANCZOS)
    if abs(rot) > 0.01:
        img = img.rotate(rot, expand=True, resample=Image.BICUBIC)
    return img


def heart_img(size=11):
    im = Image.new("RGBA", (size * 3, size * 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = (238, 108, 130, 255)
    r = size
    d.ellipse([size - r, size - r, size, size], fill=c)
    d.ellipse([size, size - r, size + r, size], fill=c)
    d.polygon([(size - r, size), (size + r, size), (size, size + r * 1.5)], fill=c)
    return im.crop(im.getbbox())


HEART = heart_img(9)


def frames_for(state, height, n=6):
    """整表模式：切对应行的帧（空帧用前一帧补，避免闪烁）；否则从单张图生成形变帧"""
    key = (state, height, n)
    if key in _sprite_cache:
        return _sprite_cache[key]
    if state == "sleep" and SLEEP_SHEET is not None:
        frames = []
        for c in range(8):
            cell = SLEEP_SHEET.crop((c * CELL_W, 0, (c + 1) * CELL_W, CELL_H))
            if height != CELL_H:
                k = height / CELL_H
                cell = cell.resize((int(CELL_W * k), int(height)), Image.LANCZOS)
            frames.append(cell)
        _sprite_cache[key] = frames
        return frames

    if SHEET.height >= CELL_H * 9:
        row = ROW_OF.get(state, 0)
        frames = []
        prev = None
        for c in range(8):
            cell = SHEET.crop((c * CELL_W, row * CELL_H, (c + 1) * CELL_W, (row + 1) * CELL_H))
            if cell.getchannel("A").getbbox() is None and prev is not None:
                cell = prev                                  # 空格补帧，防闪烁
            if height != CELL_H:
                k = height / CELL_H
                cell = cell.resize((int(CELL_W * k), int(height)), Image.LANCZOS)
            frames.append(cell)
            prev = cell
        _sprite_cache[key] = frames
        return frames

    # 单张图模式（--sprite 传单格 PNG 时）
    CAT0 = SHEET.crop(SHEET.getchannel("A").getbbox())
    out = []
    for c in range(n):
        t = c / n
        rot = 0.0
        sx = sy = 1.0
        extra = None
        if state == "idle":
            b = math.sin(t * 2 * math.pi)
            sy = 1 + 0.02 * b
        elif state == "walk":
            b = abs(math.sin(t * 2 * math.pi))
            sy = 1 + 0.035 * b
            rot = 5 * math.sin(t * 4 * math.pi)
        elif state == "run":
            b = abs(math.sin(t * 4 * math.pi))
            sy = 1 + 0.06 * b
            rot = 9 * math.sin(t * 8 * math.pi)
            sx = 1 - 0.02 * b
        elif state == "jump":
            k = t
            if k < 0.28:
                sy, sx = 1 - 0.15 * (k / 0.28), 1 + 0.10 * (k / 0.28)
            else:
                j = math.sin((k - 0.28) / 0.72 * math.pi)
                sy, sx = 1 + 0.10 * j, 1 - 0.07 * j
                rot = -7 * math.sin(j * math.pi)
        elif state == "happy":
            b = abs(math.sin(t * 2 * math.pi))
            sy = 1 + 0.06 * b
            rot = 6 * math.sin(t * 4 * math.pi)
            extra = "hearts"
        elif state == "sleep":
            sy = 1 - 0.03
            rot = 3 * math.sin(t * 2 * math.pi)
            extra = "zzz"
        img = CAT0.resize((int(CAT0.width * height / CAT0.height * sx),
                           int(height * sy)), Image.LANCZOS)
        if abs(rot) > 0.01:
            img = img.rotate(rot, expand=True, resample=Image.BICUBIC)
        canvas = Image.new("RGBA", (int(img.width * 1.35) + 30, int(img.height * 1.45) + 24), (0, 0, 0, 0))
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(sh)
        w = int(img.width * 0.72)
        cx = canvas.width // 2
        y = canvas.height - 10
        d.ellipse([cx - w // 2, y - 6, cx + w // 2, y + 6], fill=(0, 0, 0, 70))
        canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(3)))
        canvas.alpha_composite(img, ((canvas.width - img.width) // 2, canvas.height - 12 - img.height))
        if extra == "hearts":
            for i, (dx, dy) in enumerate([(0.86, 0.30), (0.92, 0.20), (0.80, 0.16)]):
                canvas.alpha_composite(HEART, (int(canvas.width * dx), int(canvas.height * dy) - i * 3))
        elif extra == "zzz":
            d = ImageDraw.Draw(canvas)
            for i, (dx, dy, zs) in enumerate([(0.82, 0.26, 7), (0.86, 0.16, 5), (0.90, 0.08, 4)]):
                x0, y0 = canvas.width * dx, canvas.height * dy
                d.line([(x0, y0), (x0 + zs, y0)], fill=(130, 180, 230, 230), width=2)
                d.line([(x0 + zs, y0), (x0, y0 + zs)], fill=(130, 180, 230, 230), width=2)
                d.line([(x0, y0 + zs), (x0 + zs, y0 + zs)], fill=(130, 180, 230, 230), width=2)
        out.append(canvas)
    _sprite_cache[key] = out
    return out


# ---------------- 道具图：小鱼干 / 逗猫棒 ----------------
def make_fish():
    """小鱼干：细长灰蓝色鱼干"""
    im = Image.new("RGBA", (68, 34), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    body = (176, 190, 200, 255)
    dark = (138, 152, 164, 255)
    d.ellipse([4, 8, 46, 26], fill=body)                       # 鱼身
    d.polygon([(40, 10), (62, 4), (62, 30), (40, 24)], fill=dark)   # 尾鳍
    d.ellipse([12, 13, 16, 17], fill=(70, 74, 84, 255))        # 眼
    d.arc([22, 4, 32, 30], 60, 300, fill=dark, width=2)        # 鱼干纹理
    d.arc([34, 4, 44, 30], 60, 300, fill=dark, width=2)
    return im


def make_wand():
    """逗猫棒：棒身斜向右下，棒尖=毛球+羽毛，TIP 对准鼠标"""
    W, H = 170, 170
    tip = (52, 52)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.line([(66, 66), (W - 12, H - 12)], fill=(198, 152, 92, 255), width=7)   # 棒
    d.line([(tip[0], tip[1]), (66, 66)], fill=(235, 235, 240, 255), width=2)  # 线
    c = (233, 88, 96, 255)
    d.ellipse([tip[0] - 8, tip[1] - 8, tip[0] + 8, tip[1] + 8], fill=c)       # 毛球
    for ang, ln in ((-150, 16), (-110, 18), (-70, 15)):                        # 羽毛
        a = math.radians(ang)
        d.line([(tip[0], tip[1]), (tip[0] + ln * math.cos(a), tip[1] + ln * math.sin(a))],
               fill=(250, 200, 90, 255), width=3)
    return im, tip


FISH_IMG = make_fish()
WAND_IMG, WAND_TIP = make_wand()

try:
    _font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
except Exception:
    _font = ImageFont.load_default()
_bubble_cache = {}

# ---- v3 台词语料 ----
CHATTER = {
    "idle": ["喵~",             "今天也在努力溜达", "（盯着你的屏幕）", "尾巴摇摇~", "肚子有点饿了…",
             "屏幕底下好暖和", "（舔了舔爪子）", "要摸摸吗", "（假装没在看你）"],
    "night": ["这么晚还不睡？", "（打了个哈欠）", "深夜的屏幕格外亮…", "喵呜…我也困了", "再玩一会儿嘛"],
    "wand": ["逗猫棒！我的最爱！", "别想跑！", "看我的必杀·飞扑！", "今天手气不错喵", "（瞳孔放大）"],
    "wake": ["（伸懒腰）喵啊~", "谁叫醒我…哦，是你啊", "睡觉被打扰了喵！", "嗯？有好玩的？"],
    "pet": ["呼噜呼噜~", "再摸摸嘛", "喵~ 好开心", "（蹭蹭你的光标）"],
    "fed": "喵呜~ 好吃！",
    "no_fish": "小鱼干没有啦…",
}


def bubble_img(text):
    if text in _bubble_cache:
        return _bubble_cache[text]
    tmp = Image.new("RGBA", (10, 10))
    tb = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=_font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = 10
    w, h = tw + pad * 2, th + pad * 2 - 4
    im = Image.new("RGBA", (w, h + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=8, fill=(255, 255, 255, 238),
                        outline=(150, 150, 158, 255), width=1)
    d.polygon([(w // 2 - 6, h - 1), (w // 2 + 6, h - 1), (w // 2, h + 7)],
              fill=(255, 255, 255, 238), outline=(150, 150, 158, 255))
    d.text((pad - tb[0], pad // 2 - tb[1]), text, font=_font, fill=(80, 82, 92, 255))
    _bubble_cache[text] = im
    return im


# ---------------- 窗口 ----------------
class DesktopCat:
    def __init__(self, height=120, x=None, y=None):
        self.height = height
        self.state = "idle"
        self.frame_i = 0
        self.frame_t = time.time()
        self.fps = 8.0
        self.dir = 1                     # 1 右, -1 左
        self.x = x if x is not None else 600
        self.y = y
        self.target_x = self.x
        self.speed = 0.0
        self.paused = False
        self.next_decision = time.time() + 2
        self.dragging = False
        self.drag_off = (0, 0)
        self.moved = False
        self.hold_until = 0
        # ---- 互动状态 ----
        self.satiety = 70.0              # 饱食度 0~100
        self.stock = 3                   # 小鱼干库存
        self.last_regen = time.time()
        self.fish = None                 # {"x","drop","eating","eat_start"}
        self.wand_until = 0.0            # 逗猫棒结束时刻
        self.pounce_cd = 0.0
        self.y_off = 0.0                 # 跳跃垂直位移
        self.leap = False
        self.leap_dur = 0.8
        self.leap_apex = 0.0
        self.leap_tx = 0.0
        self.bubble = None               # (text, until)
        self.next_beg = time.time() + 20
        self.next_chatter = time.time() + random.uniform(25, 60)
        self._last_tick = time.time()
        self._prop_x = 0
        self._prop_y = 0
        self.prop_visible = False
        self.demo = None
        self._last_state = None
        self._register()
        self._create_window()
        self._layout()

    # --- 窗口基础 ---
    def _register(self):
        self.hinst = kernel32.GetModuleHandleW(None)
        self.wc = WNDCLASS()
        self.wc.style = CS_DBLCLKS
        self.wc.lpfnWndProc = WNDPROC(self._wndproc)
        self.wc.hInstance = self.hinst
        self.wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        self.wc.lpszClassName = "HermesDesktopCat"
        user32.RegisterClassW(byref(self.wc))
        self.wc2 = WNDCLASS()
        self.wc2.lpfnWndProc = WNDPROC(self._wndproc)
        self.wc2.hInstance = self.hinst
        self.wc2.hCursor = user32.LoadCursorW(None, 32512)
        self.wc2.lpszClassName = "HermesDesktopCatProp"
        user32.RegisterClassW(byref(self.wc2))

    def _create_window(self):
        ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        self.hwnd = user32.CreateWindowExW(ex, "HermesDesktopCat", "desktop-cat", WS_POPUP,
                                           0, 0, 200, 200, None, None, self.hinst, None)
        user32.ShowWindow(self.hwnd, 8)   # SW_SHOWNA：不抢焦点显示
        exp = ex | WS_EX_TRANSPARENT      # 道具窗：点击穿透
        self.prop_hwnd = user32.CreateWindowExW(exp, "HermesDesktopCatProp", "cat-prop", WS_POPUP,
                                                -500, -500, 200, 200, None, None, self.hinst, None)

    def _layout(self):
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        self.screen_w, self.screen_h = sw, sh
        if self.y is None:
            self.y = sh - FLOOR_MARGIN
        self.x = max(40, min(sw - 40, self.x))

    # --- 绘制 ---
    def _blit(self, img, hwnd=None):
        hwnd = hwnd or self.hwnd
        w, h = img.size
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = c_void_p()
        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateDIBSection(hdc_mem, byref(bmi), 0, byref(bits), None, 0)
        if not hbmp or not bits:
            if hbmp:
                gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
            return
        gdi32.SelectObject(hdc_mem, hbmp)
        data = img.tobytes("raw", "BGRA")
        ctypes.memmove(bits, data, len(data))
        if hwnd == self.hwnd:
            pt_dst = POINT(int(self.x - w / 2), int(self.y + self.y_off - h + 14))
        else:
            pt_dst = POINT(int(self._prop_x), int(self._prop_y))
        sz = SIZE(w, h)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(hwnd, hdc_screen, byref(pt_dst), byref(sz),
                                   hdc_mem, byref(pt_src), 0, byref(blend), ULW_ALPHA)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

    def _current_frames(self):
        return frames_for(self.state, self.height)

    def _cursor(self):
        pt = POINT()
        user32.GetCursorPos(byref(pt))
        return pt.x, pt.y

    def tick(self):
        now = time.time()
        dt = min(0.5, now - self._last_tick)
        self._last_tick = now

        if self._last_state != self.state:
            self._last_state = self.state
            self._log("state -> %-6s x=%4.0f" % (self.state, self.x))
            try:
                user32.SetWindowTextW(self.hwnd, "cat:" + self.state + ("|P" if self.paused else ""))
            except Exception:
                pass

        # ---- demo 脚本（测试用）：到点触发事件 ----
        if self.demo:
            while self.demo and self.demo[0][0] <= now:
                _, fn = self.demo.pop(0)
                fn()

        # ---- 生理：饱食度衰减 + 小鱼干补充 ----
        self.satiety = max(0.0, self.satiety - dt / 25.0)
        if self.stock < 5 and now - self.last_regen > 90:
            self.stock += 1
            self.last_regen = now
        if self.satiety < 35 and now > self.next_beg and not self.fish:
            self._say("喵~ 好饿，想吃小鱼干")
            self.next_beg = now + 30

        # ---- v3 闲聊：到点随机冒一句（深夜说夜话）----
        if self.state not in ("sleep",) and now > self.next_chatter:
            hour = time.localtime().tm_hour
            self._meow("night" if (hour >= 23 or hour < 6) else "idle")
            self.next_chatter = now + random.uniform(70, 160)

        wand_on = now < self.wand_until

        # ---- 睡觉：完全冻结；但被投喂/逗猫棒会醒来 ----
        if self.state == "sleep":
            if self.fish or wand_on:
                self.state = "idle"
                self.next_decision = now
                self._meow("wake")                # 被叫醒要嘟囔一句
            else:
                if now >= self.next_decision:
                    self._decide(now)
                self._paint(now)
                self._paint_prop(now)
                return

        # ---- 拖拽中：跟手 ----
        if self.dragging:
            self.state = "idle"
            self.y_off = 0.0
            self.leap = False
            self._paint(now)
            self._paint_prop(now)
            return

        if self.paused:
            if self.state != "idle":
                self.state = "idle"
            self.y_off = 0.0
            self.leap = False
            self._paint(now)
            self._paint_prop(now)
            return

        # ---- 互动优先级：吃鱼 > 逗猫棒 > 自主溜达 ----
        if self.fish and not self.fish["eating"]:
            d = self.fish["x"] - self.x
            if abs(d) < 18:
                self.state = "eat"
                self.frame_i = 0
                self.frame_t = now
                self.fish["eating"] = True
                self.fish["eat_start"] = now
                self.hold_until = now + 2.4
            else:
                self.state = "walk"
                self.speed = 80
                self.target_x = self.fish["x"]
                self.dir = 1 if d > 0 else -1
                self.next_decision = now + 30
        elif self.fish and self.fish["eating"]:
            self.state = "eat"
            if now >= self.fish["eat_start"] + 2.4:
                self.fish = None
                self.satiety = min(100.0, self.satiety + 45)
                self.state = "happy"
                self.hold_until = now + 1.0
                self.next_decision = now + 1.2
                self._say("喵呜~ 好吃！")
        elif wand_on:
            cx, cy = self._cursor()
            d = cx - self.x
            if self.state == "jump" and now < self.hold_until:
                pass                                  # 跳跃进行中，不要打断
            elif abs(d) > 60:
                self.state = "run"
                self.speed = 215
            elif abs(d) > 20:
                self.state = "walk"
                self.speed = 120
            else:
                self.state = "idle"
                tip_h = (self.y + 14) - cy            # 棒尖离地高度
                if now > self.pounce_cd:
                    self.state = "jump"
                    self.hold_until = now + 0.8
                    self.pounce_cd = now + 1.0
                    if tip_h > 60:                     # 棒举高了 → 真跳起来够
                        self.leap = True
                        self.leap_dur = 0.8
                        self.leap_apex = min(190.0, tip_h * 0.75)
                        self.leap_tx = cx
                        self._log("leap apex=%.0f tip_h=%.0f" % (self.leap_apex, tip_h))
                    else:
                        self.leap = False
            self.target_x = cx
            if abs(d) > 4:
                self.dir = 1 if d > 0 else -1
            self.next_decision = self.wand_until + 0.5

        # ---- 动作帧推进（jump/happy/eat 期间不位移）----
        if self.state in ("jump", "happy", "eat"):
            dur = {"jump": 0.10, "happy": 0.10, "eat": 0.12}[self.state]
            if now - self.frame_t > dur:
                self.frame_t = now
                self.frame_i = (self.frame_i + 1) % len(self._current_frames())
            if now < self.hold_until:
                if getattr(self, "leap", False):          # 跳起来够棒尖：真实垂直位移
                    p = 1.0 - (self.hold_until - now) / max(0.01, self.leap_dur)
                    self.y_off = -self.leap_apex * math.sin(min(1.0, p) * math.pi)
                    dx = self.leap_tx - self.x            # 空中蹭向棒尖
                    self.x += max(-7, min(7, dx * 0.25))
                self._paint(now)
                self._paint_prop(now)
                return
            self.y_off = 0.0
            self.leap = False
            self.state = "idle"
            self.next_decision = now

        dur = {"idle": 0.14, "walk": 0.12, "run": 0.08}[self.state]
        if now - self.frame_t > dur:
            self.frame_t = now
            self.frame_i = (self.frame_i + 1) % len(self._current_frames())

        self._ai(now)

        if self.state in ("walk", "run"):
            step = self.speed / 30.0
            d = self.target_x - self.x
            if abs(d) <= step:
                self.x = self.target_x
                if not (self.fish and not self.fish["eating"]):
                    self.state = "idle"              # 到站即停
                    self.next_decision = now + random.uniform(0.8, 2.5)
            else:
                self.x += step if d > 0 else -step
        self._paint(now)
        self._paint_prop(now)

    def _paint(self, now):
        frames = self._current_frames()
        img = frames[self.frame_i % len(frames)]
        half = img.width / 2.0
        lo, hi = half + 2, self.screen_w - half - 2
        if not self.dragging:
            self.x = max(lo, min(hi, self.x))
            self.target_x = max(lo, min(hi, self.target_x))
        if self.dir < 0:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        # 互动叠加：气泡 / 爱心
        if (self.bubble and now < self.bubble[1]) or self.state in ("happy", "eat"):
            img = img.copy()
            if self.state in ("happy", "eat"):
                fi = self.frame_i % 4
                if fi in (0, 2):
                    img.alpha_composite(HEART, (int(img.width * 0.78), int(img.height * 0.18) - fi * 2))
            if self.bubble and now < self.bubble[1]:
                b = bubble_img(self.bubble[0])
                if b.width > img.width - 8:           # 气泡比猫窗宽 → 扩画布防裁切
                    pad = b.width - img.width + 16
                    canvas = Image.new("RGBA", (img.width + pad, img.height), (0, 0, 0, 0))
                    canvas.alpha_composite(img, (pad // 2, 0))
                    img = canvas
                img.alpha_composite(b, (img.width // 2 - b.width // 2, 2))
        self._blit(img)

    def _paint_prop(self, now):
        """道具窗：逗猫棒跟鼠标 / 小鱼干落地上"""
        img = None
        if now < self.wand_until:
            cx, cy = self._cursor()
            wig = math.sin(now * 9) * 5                       # 棒尖左右晃
            self._prop_x = cx - WAND_TIP[0] + wig
            self._prop_y = cy - WAND_TIP[1] + math.cos(now * 7) * 3
            img = WAND_IMG
        elif self.fish:
            t = now - self.fish["drop"]
            fy = self.y + 14                                   # 与猫脚同一地面线
            if t < 0.45:                                       # 下落
                fy -= 220 * (1 - t / 0.45) ** 2
            img = FISH_IMG
            if self.fish["eating"]:                            # 被吃：逐渐变小
                k = max(0.12, 1.0 - (now - self.fish["eat_start"]) / 2.4 * 0.85)
                img = img.resize((max(2, int(FISH_IMG.width * k)), max(2, int(FISH_IMG.height * k))), Image.LANCZOS)
            self._prop_x = self.fish["x"] - img.width / 2
            self._prop_y = fy - img.height
        if img is not None:
            if not self.prop_visible:
                user32.ShowWindow(self.prop_hwnd, 8)
                self.prop_visible = True
            self._blit(img, hwnd=self.prop_hwnd)
        elif self.prop_visible:
            user32.ShowWindow(self.prop_hwnd, 0)
            self.prop_visible = False

    def _log(self, msg):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write("%s %s" % (time.strftime("%H:%M:%S"), msg) + chr(10))
        except Exception:
            pass

    def _say(self, text, sec=3.0):
        self.bubble = (text, time.time() + sec)

    def _meow(self, kind, sec=3.2):
        """挑一句台词（夜里优先夜话），冒气泡"""
        pool = CHATTER.get(kind, [])
        if not pool:
            return
        line = random.choice(pool)
        self._say(line, sec)

    # --- 互动动作 ---
    def feed(self):
        now = time.time()
        if self.fish:
            self._say("先吃完这条~")
            return
        if self.stock <= 0:
            self._say("小鱼干没有啦…")
            return
        self.stock -= 1
        side = random.choice((-1, 1))
        fx = max(60, min(self.screen_w - 60, self.x + side * random.uniform(100, 170)))
        self.fish = {"x": fx, "drop": now, "eating": False, "eat_start": 0.0}
        self._log("feed fish x=%.0f stock=%d" % (fx, self.stock))

    def play_wand(self):
        self.wand_until = time.time() + 12.0
        self.pounce_cd = 0.0
        self._meow("wand")
        self._log("wand on until=%.0f" % self.wand_until)

    def _ai(self, now):
        # 跳跃/开心是"动作"，做完回 idle
        if self.state in ("jump", "happy"):
            if now < self.hold_until:
                return
            self.state = "idle"
            self.next_decision = now
        if now < self.next_decision:
            return
        self._decide(now)

    def _decide(self, now):
        r = random.random()
        margin = 70
        if r < 0.40:                      # 走
            self.state = "walk"
            self.speed = random.uniform(45, 75)
            self.target_x = random.uniform(margin, self.screen_w - margin)
            self.dir = 1 if self.target_x > self.x else -1
            self.next_decision = now + 30.0          # 上限；到站会提前结束
        elif r < 0.58:                    # 跑
            self.state = "run"
            self.speed = random.uniform(140, 200)
            self.target_x = random.uniform(margin, self.screen_w - margin)
            self.dir = 1 if self.target_x > self.x else -1
            self.next_decision = now + 30.0
        elif r < 0.72:                    # 跳一下
            self.state = "jump"
            self.hold_until = now + 0.75
            self.next_decision = now + 1.0
        elif r < 0.86 or self.satiety < 35:   # 发呆（饿了不睡）
            self.state = "idle"
            self.next_decision = now + random.uniform(1.5, 4.0)
        else:                             # 趴下睡（冻结 8~14 秒）
            self.state = "sleep"
            self.next_decision = now + random.uniform(8, 14)

    # --- 消息 ---
    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_LBUTTONDOWN and hwnd == self.hwnd:
            self.moved = False
            pt = POINT(); user32.GetCursorPos(byref(pt))
            self.drag_off = (pt.x - int(self.x), pt.y - int(self.y))
            self.dragging = True
            user32.SetCapture(hwnd)
            return 0
        if msg == WM_MOUSEMOVE and self.dragging:
            pt = POINT(); user32.GetCursorPos(byref(pt))
            nx = pt.x - self.drag_off[0]
            ny = pt.y - self.drag_off[1]
            if abs(nx - self.x) > 2 or abs(ny - self.y) > 2:
                self.moved = True
            self.x = max(30, min(self.screen_w - 30, nx))
            self.y = max(90, min(self.screen_h - 10, ny))
            self.state = "idle"
            return 0
        if msg == WM_LBUTTONUP and hwnd == self.hwnd:
            user32.ReleaseCapture()
            was_drag = self.dragging
            self.dragging = False
            if was_drag and not self.moved:      # 单击 = 开心
                self.state = "happy"
                self.hold_until = time.time() + 1.1
                self.next_decision = time.time() + 1.2
                if random.random() < 0.6:
                    self._meow("pet")
            if was_drag and self.moved:          # 拖完贴回地面
                self.y = self.screen_h - FLOOR_MARGIN
            return 0
        if msg == WM_RBUTTONUP and hwnd == self.hwnd:
            self._menu()
            return 0
        if msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == 1: self.paused = not self.paused
            elif cmd == 2: self.height = 90
            elif cmd == 3: self.height = 120
            elif cmd == 4: self.height = 160
            elif cmd == 5: user32.DestroyWindow(hwnd)
            elif cmd == 6: self.feed()
            elif cmd == 7: self.play_wand()
            elif cmd == 8:
                hour = time.localtime().tm_hour
                self._meow("night" if (hour >= 23 or hour < 6) else "idle")
                self.next_chatter = time.time() + random.uniform(70, 160)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self):
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, 0x0000, 6, "投喂小鱼干（剩 %d 条）" % self.stock)
        user32.AppendMenuW(hmenu, 0x0000, 7, "拿出逗猫棒（玩 12 秒）")
        user32.AppendMenuW(hmenu, 0x0000, 8, "听小猫说一句")
        user32.AppendMenuW(hmenu, 0x0800, 0, "")     # 分隔线
        user32.AppendMenuW(hmenu, 0x0000, 1, "暂停/继续溜达")
        user32.AppendMenuW(hmenu, 0x0000, 2, "小")
        user32.AppendMenuW(hmenu, 0x0000, 3, "中")
        user32.AppendMenuW(hmenu, 0x0000, 4, "大")
        user32.AppendMenuW(hmenu, 0x0800, 0, "")
        user32.AppendMenuW(hmenu, 0x0000, 5, "退出")
        pt = POINT(); user32.GetCursorPos(byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        user32.TrackPopupMenu(hmenu, 0x0000, pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(hmenu)

    def run(self):
        user32.SetTimer(self.hwnd, 1, 33, None)
        msg = MSG()
        while user32.GetMessageW(byref(msg), None, 0, 0):
            if msg.message == WM_TIMER:
                self.tick()
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))


if __name__ == "__main__":
    # 崩溃留痕：pythonw 会吞 traceback
    def _excepthook(t, v, tb):
        try:
            with open(ERR_PATH, "a", encoding="utf-8") as fh:
                traceback.print_exception(t, v, tb, file=fh)
        except Exception:
            pass
    sys.excepthook = _excepthook

    # 单实例锁：已有猫在跑就直接退出，避免出现两只
    kernel32.CreateMutexW.restype = wt.HANDLE
    kernel32.CreateMutexW(None, False, "HermesDesktopCat_SingleInstance")
    if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        sys.exit(0)
    size = 120
    if "--size" in sys.argv:
        size = int(sys.argv[sys.argv.index("--size") + 1])
    cat = DesktopCat(height=size)
    if "--demo" in sys.argv:
        t0 = time.time()

        def _wand_high():     # 把鼠标移到猫正上方高处（模拟举高逗猫棒）
            r = wt.RECT()
            user32.GetWindowRect(cat.hwnd, byref(r))
            user32.SetCursorPos((r.left + r.right) // 2, r.bottom - 240)

        cat.demo = [
            (t0 + 3, cat.play_wand),
            (t0 + 3.5, _wand_high),
            (t0 + 5.2, _wand_high),
            (t0 + 6.9, _wand_high),
            (t0 + 9, lambda: user32.PostQuitMessage(0)),
        ]
    cat.run()
