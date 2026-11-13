# -*- coding: utf-8 -*-
"""Окна голосовой справки в стиле Шерпы: карточка без рамки, палитра пса, иконки Segoe MDL2 Assets.

Tk рисует круги и скругления без сглаживания (лесенкой), поэтому все круглые формы здесь рисует Pillow
в 4× и уменьшает — края получаются гладкими. Окно справки компактное: пока вопросов нет, видны только
шапка и строка ввода; переписка появляется над ними и растёт по содержимому.
Логика записи и сети — в sherpa_voice.py.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from build_sherpa_pack import H, W, draw_chibi_frame
from sherpa_voice import MAX_REC_S, BrainClient, Recorder, _Worker, err_text

# Палитра из build_sherpa_pack.py: контур K, корпус w, фирменный голубой b/B, свечение o
C = {
    "key": "#010203",  # прозрачный цвет окна (как у самого пса)
    "card": "#F6F9FC",
    "border": "#D3DFEA",
    "chat": "#E9F0F7",
    "ink": "#1C2836",
    "muted": "#607089",
    "soft": "#9AABBE",
    "blue": "#3298D4",
    "blue_dk": "#1D77AD",
    "glow": "#7BD5F5",
    "white": "#FFFFFF",
    "red": "#E5484D",
    "red_dk": "#C93A3F",
    "hover": "#E2EBF3",
    "sel": "#D4EBF8",
}
UI = "Segoe UI"
UI_SB = "Segoe UI Semibold"
ICONS = "Segoe MDL2 Assets"
I_MIC, I_STOP, I_SEND, I_COPY, I_CLOSE, I_NEW, I_REFRESH, I_HISTORY = (
    "", "", "", "", "", "", "", "",
)
RADIUS = 16
INSET = 12
SS = 4  # во сколько раз крупнее рисуем перед уменьшением (сглаживание)


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _mix(a: str, b: str, t: float) -> tuple[int, int, int]:
    ra, rb = _rgb(a), _rgb(b)
    return tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def _shrink(big: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Уменьшение RGBA с предумножением альфы: края не темнеют."""
    return big.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")


def round_image(w, h, r, fill, bg, outline=None, tail=None) -> ImageTk.PhotoImage:
    """Сглаженный скруглённый прямоугольник на фоне bg. tail='br'/'bl' — один угол почти острый (пузырь)."""
    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    box = (0, 0, w * SS - 1, h * SS - 1)
    if outline:
        d.rounded_rectangle(box, r * SS, fill=outline)
        box = (SS, SS, w * SS - 1 - SS, h * SS - 1 - SS)
    d.rounded_rectangle(box, r * SS, fill=fill)
    if tail:
        x1 = box[0] if tail == "bl" else (box[0] + box[2]) // 2
        x2 = (box[0] + box[2]) // 2 if tail == "bl" else box[2]
        d.rounded_rectangle((x1, (box[1] + box[3]) // 2, x2, box[3]), 4 * SS, fill=fill)
    base = Image.new("RGBA", (w, h), _rgb(bg) + (255,))
    return ImageTk.PhotoImage(Image.alpha_composite(base, _shrink(big, (w, h))).convert("RGB"))


def _corners(r, fill, outline, bg, keyed):
    """Четыре угла панели (r+1)×(r+1). keyed=True — снаружи прозрачный цвет окна, край без полутонов."""
    s = 2 * r + 2
    big = Image.new("RGBA", (s * SS, s * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    box = (0, 0, s * SS - 1, s * SS - 1)
    if outline:
        d.rounded_rectangle(box, r * SS, fill=outline)
        box = (SS, SS, s * SS - 1 - SS, s * SS - 1 - SS)
    d.rounded_rectangle(box, (r - 1 if outline else r) * SS, fill=fill)
    sm = _shrink(big, (s, s))
    if keyed:
        a = sm.getchannel("A").point(lambda v: 255 if v >= 110 else 0)
        img = Image.new("RGB", (s, s), _rgb(bg))
        img.paste(sm.convert("RGB"), mask=a)
    else:
        img = Image.alpha_composite(Image.new("RGBA", (s, s), _rgb(bg) + (255,)), sm).convert("RGB")
    q = r + 1
    return [ImageTk.PhotoImage(img.crop(b)) for b in ((0, 0, q, q), (q, 0, s, q), (0, q, q, s), (q, q, s, s))]


class Panel(tk.Canvas):
    """Скруглённая панель с гладкими углами; inner — виджет, который лежит внутри с отступом pad."""

    def __init__(self, parent, fill, bg, r=14, outline=None, inner=None, pad=0, keyed=False, **kw):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, **kw)
        self.fill, self.outline, self.r, self.pad, self.inner = fill, outline, r, pad, inner
        self.imgs = _corners(r, fill, outline, bg, keyed)
        self.bind("<Configure>", self._draw)

    def _draw(self, e):
        w, h, q = e.width, e.height, self.r + 1
        self.delete("panel")
        edge = self.outline or self.fill
        self.create_rectangle(0, 0, w, h, fill=edge, width=0, tags="panel")
        self.create_rectangle(1 if self.outline else 0, 1 if self.outline else 0,
                              w - 1 if self.outline else w, h - 1 if self.outline else h,
                              fill=self.fill, width=0, tags="panel")
        for img, (x, y) in zip(self.imgs, ((0, 0), (w - q, 0), (0, h - q), (w - q, h - q))):
            self.create_image(x, y, image=img, anchor="nw", tags="panel")
        self.tag_lower("panel")
        if self.inner is not None:
            self.delete("inner")
            p = self.pad
            self.create_window(p, p, window=self.inner, anchor="nw", width=w - 2 * p, height=h - 2 * p,
                               tags="inner")


_avatar_cache: dict[str, ImageTk.PhotoImage] = {}


def avatar(state: str, scale: int = 2) -> ImageTk.PhotoImage:
    """Аватар — сам пёс, он пиксельный нарочно (пиксель-арт), увеличение без сглаживания."""
    key = f"{state}:{scale}"
    if key not in _avatar_cache:
        img = draw_chibi_frame(state, 0).resize((W * scale, H * scale), Image.NEAREST)
        _avatar_cache[key] = ImageTk.PhotoImage(img)
    return _avatar_cache[key]


class IconButton(tk.Canvas):
    """Круглая кнопка с иконкой; ring — место под пульсирующее кольцо вокруг (запись)."""

    STEPS = 24

    def __init__(self, parent, icon, size=40, bg=C["blue"], fg=C["white"], hover=C["blue_dk"],
                 parent_bg=C["card"], command=None, ring=0, icon_size=None):
        d = size + 2 * ring
        super().__init__(parent, width=d, height=d, bg=parent_bg, highlightthickness=0, bd=0, cursor="hand2")
        self.size, self.ring, self.command, self.parent_bg = size, ring, command, parent_bg
        self.colors = {"bg": bg, "fg": fg, "hover": hover}
        self.enabled, self.hovered = True, False
        self.icon_size = icon_size or max(10, size // 3)
        self.icon = icon
        self.pulse_phase: float | None = None
        self._imgs: dict = {}
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.redraw()

    def look(self, icon=None, bg=None, fg=None, hover=None):
        if icon:
            self.icon = icon
        for k, v in (("bg", bg), ("fg", fg), ("hover", hover)):
            if v:
                self.colors[k] = v
        self.redraw()

    def set_enabled(self, on: bool):
        self.enabled = on
        self.config(cursor="hand2" if on else "arrow")
        self.redraw()

    def _hover(self, on):
        self.hovered = on
        self.redraw()

    def _click(self, e):
        if self.enabled and self.command and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.command()

    def _render(self, bg, step):
        d = self.size + 2 * self.ring
        big = Image.new("RGB", (d * SS, d * SS), _rgb(self.parent_bg))
        dr = ImageDraw.Draw(big)
        c = d * SS / 2
        if step is not None and self.ring:
            for k in (0.0, 0.5):
                p = (step / self.STEPS + k) % 1.0
                rr = (self.size / 2 + p * self.ring) * SS
                dr.ellipse((c - rr, c - rr, c + rr, c + rr), outline=_mix(C["red"], self.parent_bg, p), width=2 * SS)
        r = self.size / 2 * SS
        dr.ellipse((c - r, c - r, c + r, c + r), fill=_rgb(bg))
        return ImageTk.PhotoImage(big.resize((d, d), Image.LANCZOS))

    def redraw(self):
        self.delete("all")
        bg = self.colors["hover"] if (self.hovered and self.enabled) else self.colors["bg"]
        if not self.enabled:
            bg = C["border"]
        step = None if self.pulse_phase is None else int(self.pulse_phase * self.STEPS) % self.STEPS
        key = (bg, step)
        if key not in self._imgs:
            self._imgs[key] = self._render(bg, step)
        c = self.size / 2 + self.ring
        self.create_image(0, 0, image=self._imgs[key], anchor="nw")
        self.create_text(c, c + 1, text=self.icon, font=(ICONS, self.icon_size),
                         fill=self.colors["fg"] if self.enabled else C["white"])


class LinkButton(tk.Label):
    def __init__(self, parent, text, command, icon=None, bg=C["card"]):
        super().__init__(parent, text=(f"{icon}  " if icon else "") + text, bg=bg, fg=C["blue_dk"],
                         font=(UI, 9), cursor="hand2", padx=6, pady=2)
        self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self.config(bg=C["hover"]))
        self.bind("<Leave>", lambda e: self.config(bg=bg))


class CardWindow(tk.Toplevel):
    """Окно-карточка без системной рамки: гладкие скруглённые углы, шапка с аватаром, перетаскивание за шапку."""

    def __init__(self, master, pet, w, h, title, subtitle="", pose="idle"):
        super().__init__(master)
        self.pet = pet
        self.win_w = w
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=C["key"])
        self.wm_attributes("-transparentcolor", C["key"])
        self._place(w, h)

        self.bgc = Panel(self, C["card"], C["key"], r=RADIUS, outline=C["border"], keyed=True)
        self.bgc.pack(fill="both", expand=True)
        self.body = tk.Frame(self.bgc, bg=C["card"])
        self.body.place(x=INSET, y=INSET - 2, relwidth=1, width=-2 * INSET, relheight=1, height=-2 * INSET + 2)

        head = tk.Frame(self.body, bg=C["card"])
        head.pack(fill="x")
        self.avatar_lbl = tk.Label(head, image=avatar(pose), bg=C["card"], bd=0)
        self.avatar_lbl.pack(side="left")
        txt = tk.Frame(head, bg=C["card"])
        txt.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self.title_lbl = tk.Label(txt, text=title, bg=C["card"], fg=C["ink"], font=(UI_SB, 12), anchor="w")
        self.title_lbl.pack(fill="x")
        self.sub_lbl = tk.Label(txt, text=subtitle, bg=C["card"], fg=C["muted"], font=(UI, 9), anchor="w")
        self.sub_lbl.pack(fill="x")
        self.head_btns = tk.Frame(head, bg=C["card"])
        self.head_btns.pack(side="right", anchor="n")
        IconButton(self.head_btns, I_CLOSE, size=28, bg=C["card"], fg=C["muted"], hover=C["hover"],
                   command=self.close, icon_size=9).pack(side="right")
        for wdg in (head, self.avatar_lbl, txt, self.title_lbl, self.sub_lbl):
            wdg.bind("<ButtonPress-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag_move)
        self.bind("<Escape>", lambda e: self.close())

    def _place(self, w, h):
        pet = self.pet
        pet_w = int(pet.canvas.cget("width"))
        x = int(pet.x + pet_w / 2 - w / 2)
        y = int(pet.y - h - 8)
        x = max(pet.work_left + 8, min(x, pet.work_right - w - 8))
        y = max(pet.work_top + 8, y)
        self._bottom = y + h
        self.geometry(f"{w}x{h}+{x}+{y}")

    def fit(self, max_h: int | None = None) -> None:
        """Высота окна по содержимому; низ окна остаётся на месте (окно растёт вверх от пса)."""
        self.update_idletasks()
        h = self.body.winfo_reqheight() + 2 * INSET - 2
        if max_h:
            h = min(h, max_h)
        if self.winfo_ismapped():
            x, bottom = self.winfo_rootx(), self.winfo_rooty() + self.winfo_height()
        else:
            g = self.geometry().replace("x", "+").split("+")
            x, bottom = int(g[2]), self._bottom
        y = max(self.pet.work_top + 8, bottom - h)
        self.geometry(f"{self.win_w}x{h}+{x}+{y}")

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.winfo_rootx(), e.y_root - self.winfo_rooty()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def set_subtitle(self, text, error=False):
        self.sub_lbl.config(text=text, fg=C["red"] if error else C["muted"])

    def set_pose(self, pose):
        self.avatar_lbl.config(image=avatar(pose))

    def close(self):
        self.destroy()


class Chat(tk.Canvas):
    """Переписка пузырями: вопрос справа голубым, ответ слева. Пузыри гладкие (Pillow), без рамок вокруг."""

    PADX, PADY, GAP, R = 12, 8, 8, 14

    def __init__(self, parent, width):
        super().__init__(parent, bg=C["card"], highlightthickness=0, bd=0, width=width, height=1,
                         yscrollincrement=12)
        self.w = width
        self.used = 0
        self.imgs: list = []
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def clear(self):
        self.delete("all")
        self.imgs.clear()
        self.used = 0

    def add(self, who: str, text: str) -> int:
        """Добавляет пузырь, возвращает y его верха."""
        you = who == "you"
        maxw = int(self.w * (0.78 if you else 0.9))
        tid = self.create_text(0, 0, text=text, width=maxw - 2 * self.PADX, anchor="nw", font=(UI, 10),
                               fill=C["white"] if you else C["ink"])
        x1, y1, x2, y2 = self.bbox(tid)
        bw, bh = (x2 - x1) + 2 * self.PADX, (y2 - y1) + 2 * self.PADY
        y = self.used + (self.GAP if self.used else 0)
        bx = self.w - bw if you else 0
        img = round_image(bw, bh, self.R, C["blue"] if you else C["chat"], C["card"], tail="br" if you else "bl")
        self.imgs.append(img)
        self.create_image(bx, y, image=img, anchor="nw")
        self.coords(tid, bx + self.PADX, y + self.PADY)
        self.tag_raise(tid)
        self.used = y + bh
        self.configure(scrollregion=(0, 0, self.w, self.used))
        return y


class VoiceWindow(CardWindow):
    PLACEHOLDER = "Скажи или напиши вопрос…"
    WIDTH = 400
    MAX_CHAT = 360

    def __init__(self, pet, client: BrainClient):
        super().__init__(pet.root, pet, self.WIDTH, 170, "Шерпа", "Готов слушать")
        self.client = client
        self.rec = Recorder()
        self.worker = _Worker(self)
        self.session_id: str | None = None
        self.last_answer = ""
        self.stt_meta: dict = {}
        self.busy = False
        self.has_messages = False

        IconButton(self.head_btns, I_HISTORY, size=28, bg=C["card"], fg=C["muted"], hover=C["hover"],
                   command=pet.open_history, icon_size=10).pack(side="right", padx=(0, 2))

        # --- переписка: появляется над строкой ввода после первого вопроса ---
        self.chat = Chat(self.body, self.WIDTH - 2 * INSET)

        # --- строка ввода: микрофон, поле, отправить ---
        self.bar = tk.Frame(self.body, bg=C["card"])
        self.bar.pack(fill="x", pady=(8, 0))
        self.mic = IconButton(self.bar, I_MIC, size=46, ring=6, command=self.toggle_record, icon_size=16)
        self.mic.pack(side="left", padx=(0, 2))
        self.send = IconButton(self.bar, I_SEND, size=36, command=self.ask, icon_size=12)
        self.send.pack(side="right", padx=(6, 0))
        self.q_text = tk.Text(self.bar, height=2, wrap="word", font=(UI, 10), bg=C["white"], fg=C["ink"],
                              relief="flat", bd=0, highlightthickness=0, insertbackground=C["blue_dk"], undo=True)
        field = Panel(self.bar, C["white"], C["card"], r=14, outline=C["border"], inner=self.q_text, pad=8,
                      height=52)
        self.q_text.lift(field)
        field.pack(side="left", fill="x", expand=True)
        self.q_text.bind("<Return>", self._on_enter)
        self.q_text.bind("<FocusIn>", self._ph_off)
        self.q_text.bind("<FocusOut>", self._ph_on)
        self.q_text.bind("<Button-1>", lambda e: self.focus_force())
        self._ph_on()

        # --- низ: подсказка (пока пусто) или действия и цифры (после ответа) ---
        self.foot = tk.Frame(self.body, bg=C["card"])
        self.foot.pack(fill="x", pady=(4, 0))
        self.hint = tk.Label(self.foot, text="пробел — запись · Enter — отправить · Esc — закрыть",
                             bg=C["card"], fg=C["soft"], font=(UI, 8), anchor="w")
        self.links = tk.Frame(self.foot, bg=C["card"])
        LinkButton(self.links, "Копировать", self.copy).pack(side="left")
        LinkButton(self.links, "Новая тема", self.new_topic).pack(side="left")
        self.status = tk.Label(self.foot, text="", bg=C["card"], fg=C["soft"], font=(UI, 8), anchor="e")
        self.status.pack(side="right")
        self._compact(True)

        self.bind("<space>", self._on_space)
        self.focus_force()
        self.mic.focus_set()
        self.set_subtitle("Бужу мозг…")
        self.worker.run(self.client.ensure_running, lambda _: self._ready(), self._on_error)

    # --- раскладка ---
    def _compact(self, on: bool) -> None:
        """on=True — только шапка и строка ввода; False — с перепиской над строкой ввода."""
        if on:
            self.chat.pack_forget()
            self.links.pack_forget()
            self.hint.pack(side="left", padx=(4, 0))
            self.chat.configure(height=1)
        else:
            self.hint.pack_forget()
            self.links.pack(side="left")
            if not self.chat.winfo_manager():
                self.chat.pack(fill="x", pady=(10, 0), before=self.bar)
        self.fit()

    def _grow_chat(self) -> None:
        self.chat.configure(height=min(self.chat.used, self.MAX_CHAT))
        self.fit()

    def _ph_on(self, _e=None):
        if not self.q_text.get("1.0", "end").strip():
            self.q_text.delete("1.0", "end")
            self.q_text.insert("1.0", self.PLACEHOLDER)
            self.q_text.config(fg=C["soft"])
            self._ph = True

    def _ph_off(self, _e=None):
        if getattr(self, "_ph", False):
            self.q_text.delete("1.0", "end")
            self.q_text.config(fg=C["ink"])
            self._ph = False

    def _question(self) -> str:
        return "" if getattr(self, "_ph", False) else self.q_text.get("1.0", "end").strip()

    def _set_question(self, text: str):
        self._ph_off()
        self.q_text.delete("1.0", "end")
        self.q_text.insert("1.0", text)
        if not text:
            self._ph_on()

    # --- служебное ---
    def _ready(self):
        if not self.busy and not self.rec.active:
            self.set_subtitle("Готов слушать")

    def set_status(self, text: str, error: bool = False) -> None:
        """Ошибки — в подзаголовок, служебные цифры — в нижнюю строку."""
        if error:
            self.set_subtitle(text, True)
        else:
            self.status.config(text=text)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send.set_enabled(not busy)
        self.mic.set_enabled(not busy)

    def append(self, who: str, text: str) -> None:
        if not self.has_messages:
            self.has_messages = True
            self._compact(False)
        top = self.chat.add(who, text.strip())
        self._grow_chat()
        # ответ читаем с начала: прокручиваем к верху нового пузыря, вопрос — просто в конец
        self.chat.yview_moveto(top / max(1, self.chat.used) if who != "you" else 1.0)

    def _on_space(self, e):
        if e.widget is self.q_text:
            return None
        self.toggle_record()
        return "break"

    def _on_enter(self, e):
        if e.state & 0x0001:  # Shift+Enter — перенос строки
            return None
        self.ask()
        return "break"

    def _pose(self, pose):
        """Поза и пса, и аватара в шапке. None — пёс живёт как обычно."""
        self.pet.voice_pose = pose
        self.set_pose(pose or "idle")

    # --- запись ---
    def toggle_record(self) -> None:
        if self.busy:
            return
        if self.rec.active:
            self.stop_record()
            return
        try:
            self.rec.start()
        except Exception as e:  # noqa: BLE001
            self.set_subtitle(err_text(e), True)
            return
        self._pose("look")
        self.mic.look(icon=I_STOP, bg=C["red"], hover=C["red_dk"])
        self._tick_timer()

    def _tick_timer(self) -> None:
        if not self.rec.active:
            self.mic.pulse_phase = None
            self.mic.redraw()
            return
        sec = self.rec.elapsed()
        self.set_subtitle(f"Слушаю · {int(sec) // 60}:{int(sec) % 60:02d} из 1:00 · пробел — стоп")
        self.mic.pulse_phase = (sec / 1.4) % 1.0
        self.mic.redraw()
        if sec >= MAX_REC_S:
            self.stop_record()
            return
        self.after(40, self._tick_timer)

    def stop_record(self) -> None:
        wav = self.rec.stop()
        self.mic.pulse_phase = None
        self.mic.look(icon=I_MIC, bg=C["blue"], hover=C["blue_dk"])
        self._pose("think")
        self.set_busy(True)
        self.set_subtitle("Расшифровываю…")
        self.worker.run(lambda: self.client.transcribe(wav), self._on_transcript, self._on_error)

    def _on_transcript(self, r: dict) -> None:
        self.set_busy(False)
        self._pose(None)
        text = r.get("text", "")
        if not text:
            self.set_subtitle("Не расслышал — повтори, пожалуйста", True)
            return
        self.stt_meta = {k: r.get(k) for k in ("audio_id", "audio_sec", "stt_ms")}
        self.stt_meta["transcript_raw"] = text
        self._set_question(text)
        self.q_text.focus_set()
        self.set_subtitle("Проверь текст и жми Enter")
        self.status.config(text=f"распознано за {r.get('stt_ms', 0) / 1000:.1f} с")

    # --- вопрос ---
    def ask(self) -> None:
        if self.busy or self.rec.active:
            return
        question = self._question()
        if not question:
            self.set_subtitle("Сначала запиши или напиши вопрос", True)
            return
        payload = {"session_id": self.session_id, "question": question, "source": "pet", **self.stt_meta}
        self.stt_meta = {}
        self.append("you", question)
        self._set_question("")
        self._pose("think")
        self.set_busy(True)
        self.set_subtitle("Думаю…")
        self.worker.run(lambda: self.client.ask(payload), self._on_answer, self._on_error)

    def _on_answer(self, r: dict) -> None:
        self.set_busy(False)
        self.session_id = r.get("session_id")
        self.last_answer = r.get("answer", "")
        self.append("dog", self.last_answer)
        self._pose(None)
        self.set_pose("wave")
        self.pet.hold_pose("wave", 3.0)
        self.set_subtitle("Ответил · можно спросить ещё")
        cost = r.get("cost_usd")
        cost_s = f"${cost:.5f}" if isinstance(cost, (int, float)) else "$?"
        self.status.config(text=f"{r.get('tokens_in', 0)}→{r.get('tokens_out', 0)} ток · {cost_s} · "
                                f"{r.get('llm_ms', 0) / 1000:.1f} с")

    def _on_error(self, e: Exception) -> None:
        self.set_busy(False)
        self._pose(None)
        self.set_subtitle(err_text(e), True)

    def copy(self) -> None:
        if self.last_answer:
            self.clipboard_clear()
            self.clipboard_append(self.last_answer)
            self.set_subtitle("Ответ скопирован")

    def new_topic(self) -> None:
        self.session_id, self.last_answer, self.has_messages = None, "", False
        self.chat.clear()
        self.status.config(text="")
        self._compact(True)
        self.set_subtitle("Новая тема — спрашивай")

    def ask_more(self) -> None:
        self._set_question("")
        if not self.rec.active:
            self.toggle_record()

    def close(self) -> None:
        if self.rec.active:
            try:
                self.rec.stop()
            except Exception:  # noqa: BLE001
                pass
        self.worker.alive = False
        self.pet.voice_pose = None
        self.pet.voice_window = None
        self.destroy()


def _tree_style(root) -> str:
    st = ttk.Style(root)
    if st.theme_use() != "clam":
        st.theme_use("clam")
    st.configure("Sherpa.Treeview", background=C["white"], fieldbackground=C["white"], foreground=C["ink"],
                 rowheight=30, font=(UI, 9), borderwidth=0, relief="flat")
    st.configure("Sherpa.Treeview.Heading", background=C["chat"], foreground=C["muted"], font=(UI_SB, 9),
                 relief="flat", borderwidth=0, padding=(6, 6))
    st.map("Sherpa.Treeview", background=[("selected", C["sel"])], foreground=[("selected", C["ink"])])
    st.map("Sherpa.Treeview.Heading", background=[("active", C["hover"])])
    st.layout("Sherpa.Treeview", [("Sherpa.Treeview.treearea", {"sticky": "nswe"})])
    st.configure("Sherpa.Vertical.TScrollbar", background=C["border"], troughcolor=C["white"], borderwidth=0,
                 arrowsize=0, relief="flat")
    return "Sherpa.Treeview"


class HistoryWindow(CardWindow):
    COLS = (("time", "Время", 110), ("question", "Вопрос", 250), ("answer", "Ответ", 300),
            ("tokens", "Токены", 80), ("cost", "$", 80))

    def __init__(self, pet, client: BrainClient):
        super().__init__(pet.root, pet, 860, 470, "История", "Загружаю…", pose="look")
        self.client = client
        self.worker = _Worker(self)
        self.rows: dict[str, dict] = {}
        IconButton(self.head_btns, I_REFRESH, size=28, bg=C["card"], fg=C["muted"], hover=C["hover"],
                   command=self.load, icon_size=10).pack(side="right", padx=(0, 2))

        self.cards = tk.Frame(self.body, bg=C["card"])
        self.cards.pack(fill="x", pady=(8, 8))
        self.today = self._stat_card("Сегодня")
        self.month = self._stat_card("Месяц")

        inner = tk.Frame(self.body, bg=C["white"])
        box = Panel(self.body, C["white"], C["card"], r=12, outline=C["border"], inner=inner, pad=6)
        inner.lift(box)
        box.pack(fill="both", expand=True)
        style = _tree_style(self)
        self.tree = ttk.Treeview(inner, columns=[c[0] for c in self.COLS], show="headings", style=style)
        for key, title, width in self.COLS:
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=width, anchor="w", stretch=key in ("question", "answer"))
        self.tree.tag_configure("odd", background="#F8FAFC")
        self.tree.tag_configure("bad", foreground=C["red"])
        sb = ttk.Scrollbar(inner, command=self.tree.yview, style="Sherpa.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.show_row)
        tk.Label(self.body, text="Двойной клик по строке — полный ответ", bg=C["card"], fg=C["soft"],
                 font=(UI, 8), anchor="w").pack(fill="x", pady=(4, 0))
        self.load()

    def _stat_card(self, title):
        cv = Panel(self.cards, C["chat"], C["card"], r=12, height=58)
        cv.pack(side="left", fill="x", expand=True, padx=(0, 8) if title == "Сегодня" else 0)
        lab = {}

        def draw(e):
            cv._draw(e)
            cv.delete("txt")
            cv.create_text(14, 17, text=title.upper(), anchor="w", fill=C["muted"], font=(UI_SB, 8), tags="txt")
            lab["big"] = cv.create_text(14, 39, text=lab.get("big_t", "—"), anchor="w", fill=C["ink"],
                                        font=(UI_SB, 13), tags="txt")
            lab["small"] = cv.create_text(e.width - 14, 39, text=lab.get("small_t", ""), anchor="e",
                                          fill=C["muted"], font=(UI, 9), tags="txt")

        def set_(big, small):
            lab["big_t"], lab["small_t"] = big, small
            if "big" in lab:
                cv.itemconfig(lab["big"], text=big)
                cv.itemconfig(lab["small"], text=small)

        cv.bind("<Configure>", draw)
        return set_

    def load(self) -> None:
        self.set_subtitle("Загружаю…")
        self.worker.run(lambda: (self.client.stats(), self.client.history(50)), self._fill,
                        lambda e: self.set_subtitle(err_text(e), True))

    def _fill(self, res) -> None:
        st, hist = res
        t, m = st["today"], st["month"]
        self.today(f"${t['cost_usd']:.4f}  из ${t['limit_usd']:.2f}",
                   f"{t['requests']} запросов · {t['tokens_in'] + t['tokens_out']} токенов")
        self.month(f"${m['cost_usd']:.4f}", f"{m['requests']} запросов · {m['tokens_in'] + m['tokens_out']} токенов")
        self.set_subtitle(f"Последние {len(hist)} запросов · модель {st.get('model', '')}")
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for i, r in enumerate(hist):
            one = (lambda s: (s or "").replace("\n", " "))
            cost = r.get("cost_usd")
            tags = ("odd",) if i % 2 else ()
            if r.get("status") != "ok":
                tags += ("bad",)
            iid = self.tree.insert("", "end", tags=tags, values=(
                (r.get("created_at") or "").replace("T", " ")[5:16],
                one(r.get("question"))[:90],
                one(r.get("answer") or ("⚠ " + (r.get("error") or r.get("status") or "")))[:120],
                f"{r.get('tokens_in') or 0} → {r.get('tokens_out') or 0}",
                f"{cost:.5f}" if isinstance(cost, (int, float)) else "—",
            ))
            self.rows[iid] = r

    def show_row(self, _e) -> None:
        sel = self.tree.selection()
        if sel:
            DetailWindow(self, self.rows.get(sel[0], {}))

    def close(self) -> None:
        self.worker.alive = False
        self.pet.history_window = None
        self.destroy()


class DetailWindow(CardWindow):
    def __init__(self, parent: HistoryWindow, r: dict):
        super().__init__(parent, parent.pet, 560, 460, f"Запрос #{r.get('id')}",
                         f"{(r.get('created_at') or '').replace('T', ' ')} · {r.get('model') or ''}", pose="think")
        txt = tk.Text(self.body, wrap="word", font=(UI, 10), bg=C["chat"], fg=C["ink"], relief="flat", bd=0,
                      highlightthickness=0, padx=4, pady=2, spacing2=2)
        box = Panel(self.body, C["chat"], C["card"], r=12, inner=txt, pad=8)
        txt.lift(box)
        box.pack(fill="both", expand=True, pady=(8, 0))
        txt.tag_configure("cap", foreground=C["muted"], font=(UI_SB, 8), spacing1=10)
        txt.tag_configure("meta", foreground=C["muted"], font=(UI, 9))
        cost = r.get("cost_usd")
        r = {k: ("—" if v is None else v) for k, v in r.items()}
        parts = [
            ("ВОПРОС\n", "cap"), ((r.get("question") or "") + "\n", None),
            ("ОТВЕТ\n", "cap"), ((r.get("answer") or ("Ошибка: " + (r.get("error") or ""))) + "\n", None),
            ("РАСПОЗНАНО\n", "cap"), ((r.get("transcript_raw") or "—") + "\n", "meta"),
            ("ЦИФРЫ\n", "cap"),
            (f"токены {r.get('tokens_in')} → {r.get('tokens_out')} · "
             f"{'$%.5f' % cost if isinstance(cost, (int, float)) else '$—'} · распознавание {r.get('stt_ms')} мс · "
             f"нейронка {r.get('llm_ms')} мс\nсессия {r.get('session_id')}, ход {r.get('turn')} · "
             f"аудио {r.get('audio_file') or '—'} · статус {r.get('status')}", "meta"),
        ]
        for text, tag in parts:
            txt.insert("end", text, tag or ())
        txt.config(state="disabled")
