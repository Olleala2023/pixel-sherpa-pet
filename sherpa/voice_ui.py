# -*- coding: utf-8 -*-
"""Окна голосовой справки в стиле Шерпы: карточка без рамки, палитра пса, иконки Segoe MDL2 Assets.

Tk рисует круги и скругления без сглаживания (лесенкой), поэтому все круглые формы здесь рисует Pillow
в 4× и уменьшает — края получаются гладкими. Окно справки компактное: пока вопросов нет, видны только
шапка и строка ввода; переписка появляется над ними и растёт по содержимому.
Логика записи и сети — в voice.py.
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from .markdown_view import MdStyle, parse, plain
from .sprites import H, W, draw_chibi_frame
from .voice import MAX_REC_S, BrainClient, Recorder, _Worker, err_text

# Палитра из sprites.py: контур K, корпус w, фирменный голубой b/B, свечение o
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
    "green": "#2E9E5B",
    "hover": "#E2EBF3",
    "sel": "#D4EBF8",
    "you_bg": "#DDEEFA",  # пузырь вопроса: светло-голубой, не перетягивает внимание
    "dog_bg": "#EDF2F7",  # пузырь ответа: чуть темнее фона карточки
}
UI = "Segoe UI"
UI_SB = "Segoe UI Semibold"
ICONS = "Segoe MDL2 Assets"
I_MIC, I_STOP, I_SEND, I_COPY, I_CLOSE, I_NEW, I_REFRESH, I_HISTORY = (
    "", "", "", "", "", "", "", "",
)
I_SETTINGS, I_EYE = "\ue713", "\ue7b3"
RADIUS = 16
INSET = 12
SS = 4  # во сколько раз крупнее рисуем перед уменьшением (сглаживание)


def clipboard_keys(w) -> None:
    """Ctrl+C/V/X/A и меню правой кнопки в поле ввода (Entry или Text).

    Tk вешает эти сочетания на латинские клавиши, и при русской раскладке они молчат (Ctrl+М вместо Ctrl+V).
    Поэтому смотрим на код клавиши (он не зависит от раскладки) и вызываем те же действия сами."""
    is_text = isinstance(w, tk.Text)
    actions = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>"}  # V, C, X

    def select_all():
        if is_text:
            w.tag_add("sel", "1.0", "end-1c")
        else:
            w.select_range(0, "end")
            w.icursor("end")

    def on_key(e):
        if e.keycode == 65:  # A
            select_all()
            return "break"
        ev = actions.get(e.keycode)
        if ev:
            w.event_generate(ev)
            return "break"
        return None

    def on_menu(e):
        w.focus_set()
        m = tk.Menu(w, tearoff=0, font=(UI, 9))
        m.add_command(label="Вырезать", command=lambda: w.event_generate("<<Cut>>"))
        m.add_command(label="Копировать", command=lambda: w.event_generate("<<Copy>>"))
        m.add_command(label="Вставить", command=lambda: w.event_generate("<<Paste>>"))
        m.add_separator()
        m.add_command(label="Выделить всё", command=select_all)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    w.bind("<Control-KeyPress>", on_key)
    w.bind("<Button-3>", on_menu)


def money(v) -> str:
    return f"${(v or 0):.4f}"


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

        self.head = head = tk.Frame(self.body, bg=C["card"])
        head.pack(fill="x")
        self.avatar_lbl = tk.Label(head, image=avatar(pose), bg=C["card"], bd=0)
        self.avatar_lbl.pack(side="left")
        self.head_btns = tk.Frame(head, bg=C["card"])
        self.head_btns.pack(side="right", anchor="n")
        # width=1: текст не отбирает место у кнопок справа, длинный подзаголовок переносится на 2-ю строку
        self.head_txt = txt = tk.Frame(head, bg=C["card"])
        txt.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self.title_lbl = tk.Label(txt, text=title, bg=C["card"], fg=C["ink"], font=(UI_SB, 12), anchor="w",
                                  width=1)
        self.title_lbl.pack(fill="x")
        self.sub_lbl = tk.Label(txt, text=subtitle, bg=C["card"], fg=C["muted"], font=(UI, 9), anchor="w",
                                justify="left", width=1)
        self.sub_lbl.pack(fill="x")
        txt.bind("<Configure>", lambda e: self.sub_lbl.config(wraplength=max(60, e.width - 2)))
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

    def set_subtitle(self, text, error=False, ok=False):
        self.sub_lbl.config(text=text, fg=C["red"] if error else C["green"] if ok else C["muted"])

    def set_pose(self, pose):
        self.avatar_lbl.config(image=avatar(pose))

    def close(self):
        self.destroy()


class Chat(tk.Canvas):
    """Переписка пузырями: вопрос справа голубым, ответ слева. Пузыри гладкие (Pillow), без рамок вокруг.

    Текст в пузыре — tk.Text только для чтения: его можно выделять мышью, копировать (Ctrl+C, правая кнопка),
    а ответ нейронки показывается с Markdown (жирный, списки, код, ссылки). Высоту пузыря заранее меряет
    невидимый «замерщик» той же ширины: у Text высота в строках, а нужна в пикселях."""

    PADX, PADY, GAP, R = 12, 8, 8, 14

    def __init__(self, parent, width):
        super().__init__(parent, bg=C["card"], highlightthickness=0, bd=0, width=width, height=1,
                         yscrollincrement=12)
        self.w = width
        self.used = 0
        self.imgs: list = []
        self.texts: list[tk.Text] = []
        self.style = MdStyle(self, UI, "Consolas", 10, ink=C["ink"], muted=C["muted"], soft=C["soft"],
                             link=C["blue_dk"], code_bg="#DCE6EF", select_bg=C["sel"], bold_family=UI_SB)
        self._meter: tk.Text | None = None
        self.bind("<MouseWheel>", self._wheel)

    def _wheel(self, e):
        self.yview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"

    def clear(self):
        self.delete("all")
        for t in self.texts:
            t.destroy()
        self.texts.clear()
        self.imgs.clear()
        self.used = 0

    def _new_text(self, master, bg) -> tk.Text:
        t = tk.Text(master, wrap="word", width=1, height=1, font=self.style.base, bg=bg, fg=C["ink"],
                    relief="flat", bd=0, highlightthickness=0, padx=0, pady=0, spacing1=0, spacing2=0,
                    spacing3=0, cursor="xterm", exportselection=False, takefocus=0, insertwidth=0,
                    selectbackground=C["glow"], selectforeground=C["ink"], inactiveselectbackground=C["glow"])
        self.style.setup(t)
        return t

    def _height(self, spans, w: int) -> int:
        """Высота текста в пикселях при ширине w: считает невидимое поле за краем окна, там же перенос строк."""
        if self._meter is None:
            self._meter = self._new_text(self.master, C["card"])
        m = self._meter
        m.place(x=-5000, y=0, width=w, height=4000)
        self.style.insert(m, spans)
        m.update_idletasks()
        n = m.count("1.0", "end", "update", "ypixels")  # до "end", а не "end-1c": иначе последняя строка не в счёте
        return int(n[0] if isinstance(n, (tuple, list)) else n or 0)

    def add(self, who: str, text: str) -> int:
        """Добавляет пузырь, возвращает y его верха. text — как пришёл от нейронки (Markdown)."""
        you = who == "you"
        avail = int(self.w * (0.78 if you else 0.9)) - 2 * self.PADX
        spans = plain(text) if you else parse(text)
        tw = min(avail, self.style.natural_width(spans))
        th = self._height(spans, tw)
        bw, bh = tw + 2 * self.PADX, th + 2 * self.PADY
        used_before = self.used
        y = self.used + (self.GAP if self.used else 0)
        bx = self.w - bw if you else 0
        bg = C["you_bg"] if you else C["dog_bg"]
        img = round_image(bw, bh, self.R, bg, C["card"], tail="br" if you else "bl")
        self.imgs.append(img)
        tag = f"b{len(self.imgs)}"
        self.create_image(bx, y, image=img, anchor="nw", tags=tag)
        t = self._new_text(self, bg)
        self.style.insert(t, spans)
        self._bind_bubble(t, text)
        self.create_window(bx + self.PADX, y + self.PADY, window=t, anchor="nw", width=tw, height=th, tags=tag)
        self.texts.append(t)
        self._last = (tag, t, used_before)
        self.used = y + bh
        self.configure(scrollregion=(0, 0, self.w, self.used))
        return y

    def replace_last(self, who: str, text: str) -> int:
        """Заменяет последний пузырь (ответ, который дописывается по мере генерации)."""
        tag, t, used_before = self._last
        self.delete(tag)
        t.destroy()
        self.texts.remove(t)
        self.imgs.pop()
        self.used = used_before
        return self.add(who, text)

    # --- выделение и копирование ---
    def _bind_bubble(self, t: tk.Text, raw: str) -> None:
        t.bind("<MouseWheel>", self._wheel)
        t.bind("<Button-1>", lambda e: self._focus(t), add="+")
        t.bind("<Button-3>", lambda e: self._menu(e, t, raw))
        t.bind("<Control-a>", lambda e: (t.tag_add("sel", "1.0", "end-1c"), "break")[1])

    def _focus(self, t: tk.Text) -> None:
        # окно без рамки не получает фокус само, а выделение в другом пузыре гасим: выделен один фрагмент
        t.winfo_toplevel().focus_force()
        t.focus_set()
        for o in self.texts:
            if o is not t:
                o.tag_remove("sel", "1.0", "end")

    def _put(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    def _menu(self, e, t: tk.Text, raw: str) -> None:
        self._focus(t)
        sel = t.tag_ranges("sel")
        m = tk.Menu(t, tearoff=0, font=(UI, 9))
        m.add_command(label="Копировать выделенное" if sel else "Копировать текст",
                      command=lambda: self._put(t.get(*sel) if sel else t.get("1.0", "end-1c")))
        m.add_command(label="Копировать как Markdown", command=lambda: self._put(raw))
        m.add_command(label="Выделить всё", command=lambda: t.tag_add("sel", "1.0", "end-1c"))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()


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
        IconButton(self.head_btns, I_SETTINGS, size=28, bg=C["card"], fg=C["muted"], hover=C["hover"],
                   command=pet.open_settings, icon_size=10).pack(side="right")

        # --- переписка: появляется над строкой ввода после первого вопроса ---
        self.chat = Chat(self.body, self.WIDTH - 2 * INSET)

        # --- микрофон и «отправить» — в шапке, на свободном месте справа от имени ---
        ctl = tk.Frame(self.head, bg=C["card"])
        ctl.pack(side="right", padx=(0, 6), before=self.head_txt)
        self.send = IconButton(ctl, I_SEND, size=36, command=self.ask, icon_size=12)
        self.send.pack(side="right", padx=(6, 0))
        self.mic = IconButton(ctl, I_MIC, size=46, ring=5, command=self.toggle_record, icon_size=16)
        self.mic.pack(side="right")

        # --- поле вопроса на всю ширину ---
        self.bar = tk.Frame(self.body, bg=C["card"])
        self.bar.pack(fill="x", pady=(8, 0))
        self.q_text = tk.Text(self.bar, height=2, wrap="word", font=(UI, 10), bg=C["white"], fg=C["ink"],
                              relief="flat", bd=0, highlightthickness=0, insertbackground=C["blue_dk"], undo=True)
        field = Panel(self.bar, C["white"], C["card"], r=14, outline=C["border"], inner=self.q_text, pad=8,
                      height=52)
        self.q_text.lift(field)
        field.pack(fill="x")
        self.q_text.bind("<Return>", self._on_enter)
        self.q_text.bind("<FocusIn>", self._ph_off)
        self.q_text.bind("<FocusOut>", self._ph_on)
        self.q_text.bind("<Button-1>", lambda e: self.focus_force())
        clipboard_keys(self.q_text)
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

    def set_subtitle(self, text, error=False):
        super().set_subtitle(text, error)
        self.fit()  # длинная ошибка в две строки — окно чуть выше

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
        self.set_subtitle(f"Слушаю · {int(sec) // 60}:{int(sec) % 60:02d} из 1:00")
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
        self.partial, self.streamed, self.flush_job = "", False, None
        self.worker.run(lambda: self.client.ask_stream(payload, lambda t: self.worker.post(self._on_delta, t)),
                        self._on_answer, self._on_error)

    # --- ответ по мере генерации: пузырь перерисовывается не чаще раза в 0,15 с ---
    def _on_delta(self, t: str) -> None:
        if not self.busy:
            return
        self.partial += t
        if self.flush_job is None:
            self.flush_job = self.after(150, self._flush)

    def _flush(self) -> None:
        self.flush_job = None
        if not self.busy or not self.partial.strip():
            return
        self._show_dog(self.partial.strip())
        self.set_subtitle("Пишу ответ…")

    def _show_dog(self, text: str) -> None:
        if self.streamed:
            top = self.chat.replace_last("dog", text)
            self._grow_chat()
            self.chat.yview_moveto(1.0 if self.busy else top / max(1, self.chat.used))
        else:
            self.streamed = True
            self.append("dog", text)

    def _on_answer(self, r: dict) -> None:
        if self.flush_job is not None:
            self.after_cancel(self.flush_job)
            self.flush_job = None
        self.set_busy(False)
        self.session_id = r.get("session_id")
        self.last_answer = r.get("answer", "")
        self._show_dog(self.last_answer.strip())
        self.pet.handle_actions(r.get("actions"))
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
        super().__init__(pet.root, pet, 860, 490, "История", "Загружаю…", pose="look")
        self.client = client
        self.worker = _Worker(self)
        self.rows: dict[str, dict] = {}
        IconButton(self.head_btns, I_REFRESH, size=28, bg=C["card"], fg=C["muted"], hover=C["hover"],
                   command=self.load, icon_size=10).pack(side="right", padx=(0, 2))

        self.cards = tk.Frame(self.body, bg=C["card"])
        self.cards.pack(fill="x", pady=(8, 8))
        self.cards_set = {k: self._stat_card(t, last=k == "all") for k, t in
                          (("today", "Сегодня"), ("week", "7 дней"), ("days30", "30 дней"), ("all", "Всё время"))}

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
        foot = tk.Frame(self.body, bg=C["card"])
        foot.pack(fill="x", pady=(4, 0))
        tk.Label(foot, text="Двойной клик по строке — полный ответ", bg=C["card"], fg=C["soft"],
                 font=(UI, 8), anchor="w").pack(side="left")
        LinkButton(foot, "Сбросить счётчик расхода", self.reset_stats).pack(side="right")
        self.load()

    def _stat_card(self, title, last=False):
        cv = Panel(self.cards, C["chat"], C["card"], r=12, height=74, width=10)
        col = self.cards.grid_size()[0]
        self.cards.columnconfigure(col, weight=1, uniform="stat")  # четыре карточки одной ширины
        cv.grid(row=0, column=col, sticky="ew", padx=0 if last else (0, 8))
        lab = {}

        def draw(e):
            cv._draw(e)
            cv.delete("txt")
            cv.create_text(14, 16, text=title.upper(), anchor="w", fill=C["muted"], font=(UI_SB, 8), tags="txt")
            lab["big"] = cv.create_text(14, 38, text=lab.get("big_t", "—"), anchor="w", fill=C["ink"],
                                        font=(UI_SB, 13), tags="txt")
            lab["small"] = cv.create_text(14, 59, text=lab.get("small_t", ""), anchor="w",
                                          fill=C["muted"], font=(UI, 8), tags="txt")

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

    def reset_stats(self) -> None:
        if not messagebox.askyesno(
                "Сброс расхода",
                "Обнулить счётчики расхода (сегодня, 7 дней, 30 дней, всё время)?\n\n"
                "История запросов останется. Дневной лимит считается по-прежнему по реальным тратам за сегодня.",
                parent=self):
            return
        self.set_subtitle("Сбрасываю…")
        self.worker.run(self.client.reset_stats, lambda _: self.load(),
                        lambda e: self.set_subtitle(err_text(e), True))

    def _fill(self, res) -> None:
        st, hist = res
        for key, put in self.cards_set.items():
            s = st[key]
            big = money(s["cost_usd"]) + (f"  из ${s['limit_usd']:.2f}" if key == "today" else "")
            put(big, f"{s['requests']} запросов · {s['tokens_in'] + s['tokens_out']} токенов")
        since = (f" · счётчики с {st['reset_at'].replace('T', ' ')[5:16]}" if st.get("reset_at") else "")
        self.set_subtitle(f"Последние {len(hist)} запросов · модель {st.get('model', '')}{since}")
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
        md = MdStyle(self, UI, "Consolas", 10, ink=C["ink"], muted=C["muted"], soft=C["soft"], link=C["blue_dk"],
                     code_bg="#D3DFEA", select_bg=C["sel"], bold_family=UI_SB)
        md.setup(txt)
        txt.configure(selectbackground=C["glow"], selectforeground=C["ink"], inactiveselectbackground=C["glow"])
        txt.tag_configure("cap", foreground=C["muted"], font=(UI_SB, 8), spacing1=10)
        txt.tag_configure("meta", foreground=C["muted"], font=(UI, 9))
        cost = r.get("cost_usd")
        r = {k: ("—" if v is None else v) for k, v in r.items()}
        parts = [
            ("ВОПРОС\n", "cap"), ((r.get("question") or "") + "\n", None),
            ("ОТВЕТ\n", "cap"), ((r.get("answer") or ("Ошибка: " + (r.get("error") or ""))) + "\n", "md"),
            ("РАСПОЗНАНО\n", "cap"), ((r.get("transcript_raw") or "—") + "\n", "meta"),
            ("ЦИФРЫ\n", "cap"),
            (f"токены {r.get('tokens_in')} → {r.get('tokens_out')} · "
             f"{'$%.5f' % cost if isinstance(cost, (int, float)) else '$—'} · распознавание {r.get('stt_ms')} мс · "
             f"нейронка {r.get('llm_ms')} мс\nсессия {r.get('session_id')}, ход {r.get('turn')} · "
             f"аудио {r.get('audio_file') or '—'} · статус {r.get('status')}", "meta"),
        ]
        for text, tag in parts:
            if tag == "md":  # ответ нейронки — с Markdown
                md.insert(txt, parse(text.strip()) + [("\n", ("n",))], clear=False)
            else:
                txt.config(state="normal")
                txt.insert("end", text, tag or ())
        txt.config(state="disabled")


class PillButton(tk.Canvas):
    """Кнопка-таблетка с текстом (гладкие края через round_image)."""

    def __init__(self, parent, text, command, w=120, h=34, bg=C["blue"], hover=C["blue_dk"], fg=C["white"],
                 parent_bg=C["card"]):
        super().__init__(parent, width=w, height=h, bg=parent_bg, highlightthickness=0, bd=0, cursor="hand2")
        self.command, self.enabled = command, True
        self.imgs = {k: round_image(w, h, h // 2, c, parent_bg) for k, c in
                     (("bg", bg), ("hover", hover), ("off", C["border"]))}
        self.img = self.create_image(0, 0, image=self.imgs["bg"], anchor="nw")
        self.create_text(w / 2, h / 2, text=text, fill=fg, font=(UI_SB, 10))
        self.bind("<Enter>", lambda e: self.enabled and self.itemconfig(self.img, image=self.imgs["hover"]))
        self.bind("<Leave>", lambda e: self.enabled and self.itemconfig(self.img, image=self.imgs["bg"]))
        self.bind("<ButtonRelease-1>", lambda e: self.enabled and self.command())

    def set_enabled(self, on: bool):
        self.enabled = on
        self.config(cursor="hand2" if on else "arrow")
        self.itemconfig(self.img, image=self.imgs["bg" if on else "off"])


def _combo_style(root) -> str:
    st = ttk.Style(root)
    if st.theme_use() != "clam":
        st.theme_use("clam")
    st.configure("Sherpa.TCombobox", fieldbackground=C["white"], background=C["white"], foreground=C["ink"],
                 bordercolor=C["border"], lightcolor=C["white"], darkcolor=C["white"], arrowcolor=C["muted"],
                 padding=(8, 6), relief="flat")
    st.map("Sherpa.TCombobox", fieldbackground=[("readonly", C["white"])], foreground=[("readonly", C["ink"])],
           bordercolor=[("focus", C["blue"])], selectbackground=[("readonly", C["white"])],
           selectforeground=[("readonly", C["ink"])])
    return "Sherpa.TCombobox"


class SettingsWindow(CardWindow):
    """Провайдер, модель и ключ API. Мозг бесплатно проверяет ключ и модель и кладёт ключ в диспетчер
    учётных данных Windows — не в config.toml и не в базу. Пустое поле ключа — ключ не меняется."""

    WIDTH = 420
    SRC = {"windows": "хранится в диспетчере учётных данных Windows",
           "env": "берётся из переменной окружения OPENROUTER_API_KEY"}

    def __init__(self, pet, client: BrainClient):
        super().__init__(pet.root, pet, self.WIDTH, 300, "Настройки", "Загружаю…", pose="look")
        self.client = client
        self.worker = _Worker(self)
        self.providers: dict[str, str] = {"openrouter": "OpenRouter"}
        self.key_source = None
        self.key_hint: str | None = None  # маска сохранённого ключа (…a1b2), пока поле пустое
        self._key_ph = False  # в поле ключа сейчас серая подсказка, а не набранный текст
        self._eye_open = False

        self._label("Провайдер")
        self.provider = tk.StringVar(value="OpenRouter")
        self.combo = ttk.Combobox(self.body, textvariable=self.provider, state="readonly",
                                  values=list(self.providers.values()), style=_combo_style(self), font=(UI, 10))
        self.combo.pack(fill="x")

        self._label("Модель")
        self.model = self._entry()
        self._link_line("Точное имя — на ", "openrouter.ai/models", "https://openrouter.ai/models")

        self._label("Ключ API")
        self.key = self._entry(show="•", eye=True)
        self.key.bind("<FocusIn>", self._key_ph_off)
        self.key.bind("<FocusOut>", self._key_ph_on)
        self.key_info = tk.Label(self.body, text="", bg=C["card"], fg=C["muted"], font=(UI, 8), anchor="w",
                                 justify="left", width=1, wraplength=self.WIDTH - 2 * INSET - 4)
        self.key_info.pack(fill="x", pady=(4, 0))
        row = tk.Frame(self.body, bg=C["card"])
        row.pack(fill="x")
        self._link_line("Где взять — ", "openrouter.ai/keys", "https://openrouter.ai/keys", parent=row)
        self.del_link = LinkButton(row, "Удалить сохранённый ключ", self.clear_key)

        self._label("Характер (как отвечает Шерпа)")
        self.prompt = tk.Text(self.body, height=4, wrap="word", font=(UI, 9), bg=C["white"], fg=C["ink"],
                              relief="flat", bd=0, highlightthickness=0, insertbackground=C["blue_dk"], undo=True)
        pbox = Panel(self.body, C["white"], C["card"], r=10, outline=C["border"], inner=self.prompt, pad=8,
                     height=4 * 17 + 16)
        self.prompt.lift(pbox)
        pbox.pack(fill="x")
        self.prompt.bind("<Button-1>", lambda ev: self.focus_force())
        clipboard_keys(self.prompt)
        self.default_prompt = ""
        self.prompt_loaded = False  # мозг старой версии характер не отдаёт — тогда и не шлём
        row_p = tk.Frame(self.body, bg=C["card"])
        row_p.pack(fill="x")
        tk.Label(row_p, text="Пустое поле — стандартный характер", bg=C["card"], fg=C["soft"],
                 font=(UI, 8)).pack(side="left", pady=(3, 0))
        LinkButton(row_p, "Вернуть стандартный", self._prompt_default).pack(side="right")

        self._label("Расход")
        self.spend = tk.Label(self.body, text="Загружаю…", bg=C["card"], fg=C["ink"], font=(UI, 9), anchor="w",
                              justify="left", width=1, wraplength=self.WIDTH - 2 * INSET - 4)
        self.spend.pack(fill="x")
        row2 = tk.Frame(self.body, bg=C["card"])
        row2.pack(fill="x")
        self.spend_note = tk.Label(row2, text="", bg=C["card"], fg=C["soft"], font=(UI, 8))
        self.spend_note.pack(side="left")
        LinkButton(row2, "Сбросить счётчик", self.reset_stats).pack(side="right")

        btns = tk.Frame(self.body, bg=C["card"])
        btns.pack(fill="x", pady=(14, 0))
        self.save_btn = PillButton(btns, "Сохранить", self.save)
        self.save_btn.pack(side="right")
        LinkButton(btns, "Закрыть", self.close).pack(side="right", padx=(0, 8))
        self.check_btn = PillButton(btns, "Проверить", self.check, w=100, bg=C["chat"], hover=C["border"],
                                    fg=C["blue_dk"])
        self.check_btn.pack(side="left")
        tk.Label(self.body, text="Проверка бесплатна: смотрю в OpenRouter, есть ли модель и принят ли ключ, "
                                 "вопрос нейронке не задаю. «Сохранить» проверяет так же.",
                 bg=C["card"], fg=C["soft"], font=(UI, 8), justify="left", anchor="w", width=1,
                 wraplength=self.WIDTH - 2 * INSET - 4).pack(fill="x", pady=(6, 0))

        self.fit()
        self.focus_force()
        self.worker.run(self.client.settings, self._fill, self._on_error)
        self.worker.run(self.client.stats, self._fill_stats, lambda e: self.spend.config(text="не загрузился"))

    # --- поля ---
    def _label(self, text):
        tk.Label(self.body, text=text.upper(), bg=C["card"], fg=C["muted"], font=(UI_SB, 8),
                 anchor="w").pack(fill="x", pady=(12, 4))

    def _entry(self, show=None, eye=False) -> tk.Entry:
        e = tk.Entry(self.body, font=(UI, 10), bg=C["white"], fg=C["ink"], relief="flat", bd=0,
                     highlightthickness=0, insertbackground=C["blue_dk"], show=show or "")
        box = Panel(self.body, C["white"], C["card"], r=10, outline=C["border"], inner=e, pad=8, height=36)
        e.lift(box)
        box.pack(fill="x")
        e.bind("<Button-1>", lambda ev: self.focus_force())
        e.bind("<Return>", lambda ev: self.save())
        clipboard_keys(e)
        if eye:  # глазок справа в поле: показать/скрыть набранный ключ
            b = IconButton(box, I_EYE, size=26, bg=C["white"], fg=C["soft"], hover=C["hover"],
                           parent_bg=C["white"], icon_size=10, command=self._toggle_eye)

            def draw(ev):
                box._draw(ev)
                box.itemconfig("inner", width=ev.width - 16 - 30)
                box.delete("eye")
                box.create_window(ev.width - 5, ev.height / 2, window=b, anchor="e", tags="eye")

            box.bind("<Configure>", draw)
        return e

    def _link_line(self, prefix, text, url, parent=None):
        row = tk.Frame(parent or self.body, bg=C["card"])
        if parent:
            row.pack(side="left", pady=(3, 0))
        else:
            row.pack(fill="x", pady=(3, 0))
        tk.Label(row, text=prefix, bg=C["card"], fg=C["soft"], font=(UI, 8)).pack(side="left")
        lnk = tk.Label(row, text=text, bg=C["card"], fg=C["blue_dk"], font=(UI, 8, "underline"), cursor="hand2")
        lnk.pack(side="left")
        lnk.bind("<Button-1>", lambda e: webbrowser.open(url))

    # --- данные ---
    def _fill(self, st: dict) -> None:
        self.providers = st.get("providers") or self.providers
        self.combo.config(values=list(self.providers.values()))
        self.provider.set(self.providers.get(st.get("provider"), "OpenRouter"))
        self.model.delete(0, "end")
        self.model.insert(0, st.get("model", ""))
        self._key_state(st.get("key_source"), st.get("key_hint"))
        if "system_prompt" in st:
            self.prompt_loaded = True
            self.default_prompt = st.get("default_system_prompt") or ""
            self.prompt.delete("1.0", "end")
            self.prompt.insert("1.0", st["system_prompt"])
            self.prompt.edit_reset()
        self.set_subtitle("Нейронка для справки")

    def _prompt_default(self) -> None:
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", self.default_prompt)

    def _fill_stats(self, st: dict) -> None:
        def one(title, s):
            return f"{title} {money(s['cost_usd'])} ({s['requests']})"

        self.spend.config(text=" · ".join((one("7 дней", st["week"]), one("30 дней", st["days30"]),
                                           one("всё время", st["all"]))))
        r = st.get("reset_at")
        self.spend_note.config(text=f"в скобках — запросов · с {r.replace('T', ' ')[5:16]}" if r
                               else "в скобках — число запросов")
        self.fit()

    def reset_stats(self) -> None:
        if not messagebox.askyesno("Сброс расхода", "Обнулить счётчики расхода?\n\nИстория запросов останется.",
                                   parent=self):
            return
        self.worker.run(self.client.reset_stats, self._fill_stats, self._on_error)

    # --- поле ключа: серая подсказка «ключ задан», пока оно пустое ---
    def _key_clear(self) -> None:
        self.key.delete(0, "end")
        self._key_ph = False
        self.key.config(fg=C["ink"], show="" if self._eye_open else "•")

    def _key_ph_on(self, _e=None) -> None:
        if self.key_hint and not self._key_ph and not self.key.get():
            self.key.config(fg=C["soft"], show="")
            self.key.insert(0, f"Ключ задан · {self.key_hint}")
            self._key_ph = True

    def _key_ph_off(self, _e=None) -> None:
        if self._key_ph:
            self._key_clear()

    def _key_value(self) -> str:
        return "" if self._key_ph else self.key.get().strip()

    def _toggle_eye(self) -> None:
        self._eye_open = not self._eye_open
        if not self._key_ph:
            self.key.config(show="" if self._eye_open else "•")

    def _key_state(self, src, hint=None) -> None:
        self.key_source, self.key_hint = src, hint if src else None
        if src:
            self.key_info.config(
                text=f"✓ Ключ задан ({hint}), {self.SRC[src]}. Чтобы заменить — вставь новый и нажми «Сохранить».",
                fg=C["green"])
        else:
            self.key_info.config(text="Ключа нет — справка не сможет отвечать. Вставь ключ и нажми «Сохранить».",
                                 fg=C["red"])
        if src == "windows":
            self.del_link.pack(side="right")
        else:
            self.del_link.pack_forget()
        if self._key_ph:  # подсказка устарела (ключ сменился или удалён)
            self._key_clear()
        try:
            focused = self.focus_get() is self.key
        except KeyError:  # фокус в выпадающем списке
            focused = False
        if not focused:
            self._key_ph_on()
        self.fit()

    def _provider_id(self) -> str:
        name = self.provider.get()
        return next((k for k, v in self.providers.items() if v == name), "openrouter")

    def _payload(self, clear_key: bool = False) -> dict:
        p = {"provider": self._provider_id(), "model": self.model.get().strip(),
             "api_key": self._key_value() or None, "clear_key": clear_key}
        if self.prompt_loaded:
            p["system_prompt"] = self.prompt.get("1.0", "end").strip()
        return p

    def _busy(self, on: bool) -> None:
        self.save_btn.set_enabled(not on)
        self.check_btn.set_enabled(not on)

    @staticmethod
    def _verdict(chk: dict, has_key: bool) -> tuple[str, bool]:
        """Итог бесплатной проверки: (текст, это хорошо?)."""
        mo, ko = chk.get("model_ok"), chk.get("key_ok")
        if ko is False:
            return "Ключ не принят OpenRouter — проверь, что он вставлен целиком", False
        if mo is False:
            return "Такой модели нет в OpenRouter — проверь имя", False
        if mo is None and ko is None:
            return "Проверить не удалось: нет связи с OpenRouter", False
        if not has_key:
            return "Модель есть, но ключа нет", False
        if ko is None:
            return "Модель есть, ключ проверить не удалось: нет связи", False
        return "Подключение работает: ключ принят, модель найдена", True

    def check(self) -> None:
        """Проверка подключения без сохранения: берёт то, что сейчас в полях (пустой ключ — сохранённый)."""
        if not self.check_btn.enabled:
            return
        payload = self._payload()
        self._busy(True)
        self.set_subtitle("Проверяю подключение…")
        self.worker.run(lambda: self.client.check_settings(payload), self._on_checked, self._on_error)

    def _on_checked(self, chk: dict) -> None:
        self._busy(False)
        text, good = self._verdict(chk, bool(chk.get("has_key")))
        self.set_subtitle(f"Проверка: {text}", error=not good, ok=good)

    def save(self, clear_key: bool = False) -> None:
        if not self.save_btn.enabled:
            return
        payload = self._payload(clear_key)
        self._busy(True)
        self.set_subtitle("Проверяю в OpenRouter…")
        self.worker.run(lambda: self.client.save_settings(payload), self._on_saved, self._on_error)

    def clear_key(self) -> None:
        self._key_clear()
        self.save(clear_key=True)

    def _on_saved(self, st: dict) -> None:
        self._busy(False)
        self._key_clear()
        self._key_state(st.get("key_source"), st.get("key_hint"))
        if self.prompt_loaded and "system_prompt" in st:  # пустое стало стандартным — покажем, что сохранилось
            self.prompt.delete("1.0", "end")
            self.prompt.insert("1.0", st["system_prompt"])
        text, good = self._verdict(st.get("checked") or {}, bool(st.get("key_source")))
        self.set_subtitle(f"Сохранено · {text}", error=not good, ok=good)

    def _on_error(self, e: Exception) -> None:
        self._busy(False)
        self.set_subtitle(err_text(e), True)

    def set_subtitle(self, text, error=False, ok=False):
        super().set_subtitle(text, error, ok)
        self.fit()

    def close(self) -> None:
        self.worker.alive = False
        self.pet.settings_window = None
        self.destroy()
