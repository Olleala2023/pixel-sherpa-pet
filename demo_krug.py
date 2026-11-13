# -*- coding: utf-8 -*-
"""
Демо звуков на простом кружке: физика падения и winsound — как пойдут в Шерпу.
- Схвати кружок и потащи вверх (по умолчанию без звука).
- Отпусти -> падает с ускорением, бьётся о панель -> «бонк», маленький отскок с тихим бонком.
- Правый клик -> меню: звуки, отскок, сила тяжести, закрыть. Esc -> закрыть.
Запуск: pyw -3 demo_krug.py   (самопроверка: py -3 demo_krug.py --test)
"""
import math
import sys
import time
import tkinter as tk
import winsound
from pathlib import Path

from sherpa_pet import get_work_area

SOUNDS = Path(__file__).resolve().parent / "sounds" / "candidates"
UP_CHOICES = [
    ("", "без звука"),
    ("up_2a_whee_quiet.wav", "«уииии» тише"),
    ("up_2b_whee_short.wav", "«уи!» короткое"),
    ("up_2c_whee_soft.wav", "мягкое"),
    ("up_2d_whee_double.wav", "«уи-уи»"),
]
LAND_CHOICES = [
    ("land_2a_bonk_quiet.wav", "«бонк» тише"),
    ("land_2b_bonk_light.wav", "лёгкий бонк"),
    ("land_2c_tok.wav", "деревянное «ток»"),
    ("land_2d_bonk_pop.wav", "бонк + «поп»"),
]
BOUNCE_SND = "bounce_bonk.wav"

# Сила тяжести в пикселях за секунду². Скорость растёт всё время падения —
# предел только у очень высоких бросков, чтобы кружок не «телепортировался».
GRAVITY = {"normal": 3000.0, "fast": 4500.0, "soft": 2000.0}
MAX_SPEED = 3000.0          # px/с
RESTITUTION = [0.2, 0.12]   # доля скорости при 1-м и 2-м отскоке
MAX_BOUNCE_PX = 14.0        # отскок не выше этого, с какой высоты ни урони
MIN_BOUNCE_SPEED = 120.0    # px/с: медленнее — уже не отскакивает и молчит
FRAME_MS = 16

TRANSPARENT = "#010203"
SIZE = 64


def play(name: str):
    if not name:
        return
    # SND_ASYNC: не блокирует окно; новый звук обрывает предыдущий
    winsound.PlaySound(str(SOUNDS / name), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


class Krug:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        self.root.wm_attributes("-transparentcolor", TRANSPARENT)
        c = tk.Canvas(self.root, width=SIZE, height=SIZE, bg=TRANSPARENT, highlightthickness=0, bd=0)
        c.pack()
        c.create_oval(3, 3, SIZE - 3, SIZE - 3, fill="#3298d4", outline="#1c2836", width=3)
        c.create_oval(14, 12, 26, 24, fill="#f6f9fc", outline="")

        left, top, right, bottom = get_work_area()
        self.ground_y = bottom - SIZE
        self.top_y = top
        self.x = float(right - SIZE - 300)
        self.y = float(self.ground_y)
        self.vy = 0.0
        self.falling = False
        self.bounces = 0
        self.dragging = False
        self.moved = False
        self.lifted = False
        self.last_t = time.monotonic()

        self.up_snd = tk.StringVar(value=UP_CHOICES[0][0])
        self.land_snd = tk.StringVar(value=LAND_CHOICES[0][0])
        self.bounce_on = tk.BooleanVar(value=True)
        self.gravity = tk.StringVar(value="normal")
        self._build_menu()

        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Button-3>", self.on_menu)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self._place()

    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        up = tk.Menu(m, tearoff=0)
        for f, label in UP_CHOICES:
            up.add_radiobutton(label=label, variable=self.up_snd, value=f, command=lambda f=f: play(f))
        land = tk.Menu(m, tearoff=0)
        for f, label in LAND_CHOICES:
            land.add_radiobutton(label=label, variable=self.land_snd, value=f, command=lambda f=f: play(f))
        m.add_cascade(label="Звук «подняли»", menu=up)
        m.add_cascade(label="Звук «упал»", menu=land)
        m.add_checkbutton(label="Отскок", variable=self.bounce_on)
        m.add_separator()
        m.add_radiobutton(label="Падает обычно", variable=self.gravity, value="normal")
        m.add_radiobutton(label="Падает резче", variable=self.gravity, value="fast")
        m.add_radiobutton(label="Падает мягче", variable=self.gravity, value="soft")
        m.add_separator()
        m.add_command(label="Закрыть", command=self.root.destroy)
        self.menu = m

    def on_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _place(self):
        self.root.geometry(f"{SIZE}x{SIZE}+{int(self.x)}+{int(self.y)}")

    def on_press(self, e):
        self.dragging, self.moved, self.lifted = True, False, False
        self.falling = False
        self.px, self.py, self.sx, self.sy = e.x_root, e.y_root, self.x, self.y

    def on_drag(self, e):
        dx, dy = e.x_root - self.px, e.y_root - self.py
        if not self.moved and (abs(dx) > 4 or abs(dy) > 4):
            self.moved = True
        if not self.moved:
            return
        self.x = self.sx + dx
        self.y = max(float(self.top_y), min(float(self.ground_y), self.sy + dy))
        if not self.lifted and self.y < self.ground_y - 6:
            self.lifted = True
            play(self.up_snd.get())
        self._place()

    def on_release(self, e):
        self.dragging = False
        if self.y < self.ground_y:
            self.vy = 0.0
            self.bounces = 0
            self.falling = True

    def step(self, dt: float):
        """Шаг падения на dt секунд. Возвращает имя звука, если был удар, иначе None."""
        g = GRAVITY[self.gravity.get()]
        # полу-неявный Эйлер: сначала скорость, потом позиция — ускорение видно с первого кадра
        self.vy = min(MAX_SPEED, self.vy + g * dt)
        self.y += self.vy * dt
        if self.y < self.ground_y:
            return None
        self.y = float(self.ground_y)
        impact = self.vy
        if self.bounces == 0:
            snd = self.land_snd.get()
        else:
            snd = BOUNCE_SND if impact > MIN_BOUNCE_SPEED else None
        if self.bounce_on.get() and self.bounces < len(RESTITUTION) and impact > MIN_BOUNCE_SPEED:
            v = impact * RESTITUTION[self.bounces]
            v = min(v, math.sqrt(2 * g * MAX_BOUNCE_PX))  # не выше MAX_BOUNCE_PX
            self.vy = -v
            self.bounces += 1
        else:
            self.vy = 0.0
            self.falling = False
        return snd

    def tick(self):
        now = time.monotonic()
        dt = min(0.05, now - self.last_t)  # если окно подвисло, не прыгаем сквозь пол
        self.last_t = now
        if not self.dragging and self.falling:
            snd = self.step(dt)
            if snd:
                play(snd)
            self._place()
        self.root.after(FRAME_MS, self.tick)

    def run(self):
        self.last_t = time.monotonic()
        self.root.after(FRAME_MS, self.tick)
        self.root.mainloop()


def self_test():
    for f, _ in UP_CHOICES + LAND_CHOICES + [(BOUNCE_SND, "")]:
        assert not f or (SOUNDS / f).exists(), f"нет файла {f}"
    k = Krug()
    dt = FRAME_MS / 1000
    for grav in ("soft", "normal", "fast"):
        k.gravity.set(grav)
        for height in (150, 300, 600):
            k.y, k.vy, k.bounces, k.falling = float(k.ground_y - height), 0.0, 0, True
            t, hits, peak, speeds = 0.0, [], 0.0, []
            while k.falling and t < 5:
                snd = k.step(dt)
                t += dt
                if not hits:
                    speeds.append(k.vy)
                if snd:
                    hits.append((round(t, 2), snd.split("_")[0]))
                if hits:
                    peak = max(peak, k.ground_y - k.y)
            # скорость за четверть пути и перед ударом: видно, что разгоняется
            q = speeds[len(speeds) // 4] if speeds else 0
            print(f"{grav:6s} {height:3d}px: hits {hits}, speed {q:.0f} -> {speeds[-2]:.0f} px/s, bounce {peak:.0f}px")
    k.root.destroy()


if __name__ == "__main__":
    if "--test" in sys.argv:
        self_test()
    else:
        Krug().run()
