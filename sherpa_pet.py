# -*- coding: utf-8 -*-
"""
Десктоп-маскот Чиби-пёс Sherpa для Windows (живёт над панелью задач).
- Защищён от дубликатов через именованный мьютекс Windows.
- Гуляет вдоль панели задач (лицом по направлению движения), виляет хвостом и моргает.
- Засыпает (пускает z-z), если не трогать мышь и клавиатуру 90 секунд.
- При клике левой кнопкой мыши радостно прыгает («Ура!») или машет лапой.
- Перетаскивается мышью по экранным координатам (x_root, y_root) без дёрганья.
- Слушает файл sherpa-state.txt для интеграции со скриптами или Claude Code.
- Правый клик мыши открывает меню (выбор режима, размер 2x..5x, пауза ходьбы, выход).
"""
import ctypes
from ctypes import wintypes
from pathlib import Path
import random
import sys
import time
import tkinter as tk

from build_sherpa_pack import H, W, draw_chibi_frame

TRANSPARENT_HEX = "#010203"
STATE_FILE = Path(__file__).resolve().parent / "sherpa-state.txt"
MUTEX_NAME = "Local\\SherpaChibiDesktopPetSingletonMutex"
ERROR_ALREADY_EXISTS = 183


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


# Задаём корректные беззнаковые типы WinAPI, чтобы избежать переполнения знакового c_int
try:
    ctypes.windll.kernel32.GetTickCount.restype = wintypes.DWORD
    ctypes.windll.user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    ctypes.windll.user32.GetLastInputInfo.restype = wintypes.BOOL
except Exception:
    pass


def acquire_single_instance_mutex():
    """Создаёт именованный мьютекс Windows. Возвращает дескриптор или None, если копия уже запущена."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.restype = wintypes.DWORD
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if handle and kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        return True


def get_idle_seconds() -> float:
    """Возвращает, сколько секунд пользователь не трогал мышь и клавиатуру в Windows."""
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            now_tick = int(ctypes.windll.kernel32.GetTickCount()) & 0xFFFFFFFF
            last_tick = int(lii.dwTime) & 0xFFFFFFFF
            millis = (now_tick - last_tick) & 0xFFFFFFFF
            return max(0.0, millis / 1000.0)
    except Exception:
        pass
    return 0.0


def get_work_area():
    """Возвращает рабочую область экрана без панели задач Windows: (left, top, right, bottom)."""
    try:
        rect = wintypes.RECT()
        SPI_GETWORKAREA = 0x0030
        ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
        if rect.right > rect.left and rect.bottom > rect.top:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return 0, 0, 1920, 1040


def pil_to_tk_photo(pil_img, scale: int) -> tk.PhotoImage:
    """Конвертирует RGBA PIL Image в tk.PhotoImage с чёткими пикселями без внешних библиотек."""
    w, h = pil_img.size
    photo = tk.PhotoImage(width=w * scale, height=h * scale)
    px = pil_img.load()
    rows_str = []
    for y in range(h):
        row_colors = []
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 128:
                hexc = TRANSPARENT_HEX
            else:
                hexc = f"#{r:02x}{g:02x}{b:02x}"
            row_colors.extend([hexc] * scale)
        line = "{" + " ".join(row_colors) + "}"
        for _ in range(scale):
            rows_str.append(line)
    photo.put(" ".join(rows_str))
    return photo


class SherpaDesktopPet:
    def __init__(self, scale: int = 4):
        self.root = tk.Tk()
        self.root.title("Sherpa Pet")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_HEX)
        self.root.wm_attributes("-transparentcolor", TRANSPARENT_HEX)

        self.scale = scale
        self.states = ["idle", "walk", "wave", "think", "jump", "sleep", "look", "drag"]
        self.sprites = {}
        self._build_sprites()

        self.canvas = tk.Canvas(
            self.root,
            width=W * self.scale,
            height=H * self.scale,
            bg=TRANSPARENT_HEX,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()
        self.img_id = self.canvas.create_image(0, 0, anchor="nw")

        left, top, right, bottom = get_work_area()
        self.work_left = left
        self.work_top = top
        self.work_right = right
        self.ground_y = bottom - (H * self.scale) + (2 * self.scale)

        self.x = float(right - (W * self.scale) - 140)
        self.y = float(self.ground_y)
        self.vx = -2.0
        self.vy = 0.0
        # В базовом спрайте (flip_x=False) хвост слева, нос смотрит вправо.
        # Поэтому при движении влево (vx < 0) включаем отражение facing_left = True.
        self.facing_left = True

        self.state = "idle"
        self.manual_state = None
        self.roaming = True
        self.frame = 0
        self.last_frame_time = time.monotonic()
        self.state_until = time.monotonic() + 4.0
        self.temp_override_until = 0.0

        self.dragging = False
        self.drag_moved = False
        self.press_root_x = 0
        self.press_root_y = 0
        self.start_win_x = 0.0
        self.start_win_y = 0.0

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right_click)

        self._build_menu()
        self._clamp_position()
        self._update_geometry()
        self._draw()

    def _build_sprites(self):
        self.sprites.clear()
        for st in self.states:
            for flip in (False, True):
                frames = [
                    pil_to_tk_photo(draw_chibi_frame(st, f, flip_x=flip), self.scale)
                    for f in range(4)
                ]
                self.sprites[(st, flip)] = frames

    def _build_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Авто-жизнь (гуляет сам)", command=lambda: self.set_manual(None))
        self.menu.add_separator()
        self.menu.add_command(label="Поза: Рад (сидит)", command=lambda: self.set_manual("idle"))
        self.menu.add_command(label="Поза: Привет (машет)", command=lambda: self.set_manual("wave"))
        self.menu.add_command(label="Поза: Смотри (экран чата)", command=lambda: self.set_manual("look"))
        self.menu.add_command(label="Поза: Думает", command=lambda: self.set_manual("think"))
        self.menu.add_command(label="Поза: Ура! (прыгает)", command=lambda: self.set_manual("jump"))
        self.menu.add_command(label="Поза: Спит (z-z-z)", command=lambda: self.set_manual("sleep"))
        self.menu.add_separator()
        self.menu.add_command(label="Вкл/Выкл прогулку по панели", command=self.toggle_roaming)
        size_menu = tk.Menu(self.menu, tearoff=0)
        for s, label in ((2, "Мелкий (2x)"), (3, "Средний (3x)"), (4, "Крупный (4x)"), (5, "Очень крупный (5x)")):
            size_menu.add_command(label=label, command=lambda sc=s: self.change_scale(sc))
        self.menu.add_cascade(label="Размер", menu=size_menu)
        self.menu.add_separator()
        self.menu.add_command(label="Закрыть Шерпу", command=self.root.destroy)

    def set_manual(self, st):
        self.manual_state = st
        self.temp_override_until = 0.0
        if st is not None:
            self.state = st
            self._draw()

    def toggle_roaming(self):
        self.roaming = not self.roaming
        if not self.roaming and self.state == "walk":
            self.state = "idle"
            self._draw()

    def _clamp_position(self):
        min_x = float(self.work_left + 8)
        max_x = float(max(min_x, self.work_right - (W * self.scale) - 8))
        min_y = float(self.work_top + 8)
        max_y = float(self.ground_y)
        self.x = max(min_x, min(max_x, self.x))
        self.y = max(min_y, min(max_y, self.y))

    def change_scale(self, new_scale: int):
        self.scale = new_scale
        self._build_sprites()
        self.canvas.config(width=W * self.scale, height=H * self.scale)
        left, top, right, bottom = get_work_area()
        self.work_left = left
        self.work_top = top
        self.work_right = right
        self.ground_y = bottom - (H * self.scale) + (2 * self.scale)
        self.y = float(self.ground_y)
        self._clamp_position()
        self._update_geometry()
        self._draw()

    def on_press(self, event):
        self.dragging = True
        self.drag_moved = False
        self.press_root_x = event.x_root
        self.press_root_y = event.y_root
        self.start_win_x = self.x
        self.start_win_y = self.y

    def on_drag(self, event):
        if not self.dragging:
            return
        dx = event.x_root - self.press_root_x
        dy = event.y_root - self.press_root_y
        if not self.drag_moved and (abs(dx) > 4 or abs(dy) > 4):
            self.drag_moved = True
        if not self.drag_moved:
            return
        self.x = self.start_win_x + dx
        self.y = self.start_win_y + dy
        self._clamp_position()
        self.state = "drag"
        self._update_geometry()

    def on_release(self, event):
        if not self.dragging:
            return
        self.dragging = False
        now = time.monotonic()
        if not self.drag_moved:
            # Короткий клик — радуется или машет лапой (даже если выбрана ручная поза)
            self.state = random.choice(["jump", "wave", "look"])
            self.state_until = now + 3.0
            self.temp_override_until = now + 3.0
            self._draw()
        else:
            if self.y >= self.ground_y:
                self.y = float(self.ground_y)
                self.state = self.manual_state or "idle"
                self.state_until = now + 3.0
                self._draw()

    def on_right_click(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _read_external_state(self):
        if not STATE_FILE.exists():
            return None
        try:
            txt = STATE_FILE.read_text(encoding="utf-8").strip().lower()
            if txt in self.states:
                return txt
        except Exception:
            pass
        return None

    def _update_geometry(self):
        self.root.geometry(f"{W * self.scale}x{H * self.scale}+{int(self.x)}+{int(self.y)}")

    def _draw(self):
        flip = self.facing_left and self.state in ("walk", "idle")
        frames = self.sprites.get((self.state, flip)) or self.sprites[("idle", False)]
        photo = frames[self.frame % len(frames)]
        self.canvas.itemconfig(self.img_id, image=photo)

    def tick(self):
        now = time.monotonic()

        left, top, right, bottom = get_work_area()
        self.work_left = left
        self.work_top = top
        self.work_right = right
        self.ground_y = bottom - (H * self.scale) + (2 * self.scale)

        if not self.dragging:
            if self.y < self.ground_y:
                self.vy = min(14.0, self.vy + 1.2)
                self.y += self.vy
                if self.y >= self.ground_y:
                    self.y = float(self.ground_y)
                    self.vy = 0.0
                    self.state = "jump"
                    self.state_until = now + 1.2
                    self.temp_override_until = now + 1.2
                self._clamp_position()
                self._update_geometry()
            else:
                self.y = float(self.ground_y)
                ext = self._read_external_state()
                idle_sec = get_idle_seconds()

                if now < self.temp_override_until:
                    pass
                elif ext:
                    self.state = ext
                elif self.manual_state:
                    self.state = self.manual_state
                elif idle_sec >= 90.0:
                    self.state = "sleep"
                else:
                    if self.state == "sleep":
                        self.state = "wave"
                        self.state_until = now + 2.2
                    elif now >= self.state_until:
                        if self.roaming:
                            self.state = random.choices(
                                ["idle", "walk", "wave", "think"],
                                weights=[40, 38, 12, 10],
                                k=1,
                            )[0]
                        else:
                            self.state = random.choices(["idle", "wave", "think"], weights=[75, 15, 10], k=1)[0]
                        self.state_until = now + random.uniform(3.0, 6.5)
                        if self.state == "walk":
                            self.vx = random.choice([-1.8, 1.8])
                            # При движении влево (vx < 0) отражаем спрайт (facing_left = True)
                            self.facing_left = self.vx < 0

                if self.state == "walk" and self.roaming:
                    self.x += self.vx
                    min_x = self.work_left + 16
                    max_x = self.work_right - (W * self.scale) - 16
                    if self.x <= min_x:
                        self.x = float(min_x)
                        self.vx = abs(self.vx)
                        self.facing_left = False
                    elif self.x >= max_x:
                        self.x = float(max_x)
                        self.vx = -abs(self.vx)
                        self.facing_left = True
                    self._update_geometry()

        if now - self.last_frame_time >= 0.22:
            self.frame = (self.frame + 1) % 4
            self.last_frame_time = now
            self._draw()

        self.root.after(30, self.tick)

    def run(self):
        self.root.after(30, self.tick)
        self.root.mainloop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sherpa Chibi Desktop Pet")
    parser.add_argument("--scale", type=int, default=4, help="Sprite scale (2..5)")
    parser.add_argument("--screenshot-test", action="store_true", help="Exit after 0.8s for testing")
    args = parser.parse_args()

    mutex_handle = acquire_single_instance_mutex()
    if not mutex_handle and not args.screenshot_test:
        sys.exit(0)

    pet = SherpaDesktopPet(scale=args.scale)
    if args.screenshot_test:
        pet.root.after(800, pet.root.destroy)
    pet.run()
