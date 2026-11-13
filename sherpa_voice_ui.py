# -*- coding: utf-8 -*-
"""Окна голосовой справки в стиле Шерпы: карточка без рамки со скруглёнными углами, палитра пса,
иконки Segoe MDL2 Assets, ответы блоками как в чате. Логика записи и сети — в sherpa_voice.py."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from build_sherpa_pack import H, W, draw_chibi_frame
from sherpa_voice import MAX_REC_S, BrainClient, Recorder, _Worker, err_text

# Палитра из build_sherpa_pack.py: контур K, корпус w, фирменный голубой b/B, свечение o
C = {
    "key": "#010203",  # прозрачный цвет окна (как у самого пса)
    "card": "#F6F9FC",
    "border": "#D3DFEA",
    "chat": "#ECF2F8",
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
RADIUS = 18
INSET = 14


def rounded_rect(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


_avatar_cache: dict[str, ImageTk.PhotoImage] = {}


def avatar(state: str, scale: int = 2) -> ImageTk.PhotoImage:
    key = f"{state}:{scale}"
    if key not in _avatar_cache:
        img = draw_chibi_frame(state, 0).resize((W * scale, H * scale), Image.NEAREST)
        _avatar_cache[key] = ImageTk.PhotoImage(img)
    return _avatar_cache[key]


class IconButton(tk.Canvas):
    """Круглая кнопка с иконкой; ring — место под пульсирующее кольцо вокруг."""

    def __init__(self, parent, icon, size=40, bg=C["blue"], fg=C["white"], hover=C["blue_dk"],
                 parent_bg=C["card"], command=None, ring=0, icon_size=None):
        d = size + 2 * ring
        super().__init__(parent, width=d, height=d, bg=parent_bg, highlightthickness=0, bd=0, cursor="hand2")
        self.size, self.ring, self.command = size, ring, command
        self.colors = {"bg": bg, "fg": fg, "hover": hover}
        self.enabled, self.hovered = True, False
        self.icon_size = icon_size or max(10, size // 3)
        self.icon = icon
        self.pulse_phase: float | None = None
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

    def redraw(self):
        self.delete("all")
        r0, s = self.ring, self.size
        if self.pulse_phase is not None and r0:
            for k in (0, 0.5):
                ph = (self.pulse_phase + k) % 1.0
                rr = s / 2 + ph * r0
                c = s / 2 + r0
                col = C["red"] if ph < 0.35 else ("#EE8A8D" if ph < 0.7 else "#F6C4C5")
                self.create_oval(c - rr, c - rr, c + rr, c + rr, outline=col, width=2)
        bg = self.colors["hover"] if (self.hovered and self.enabled) else self.colors["bg"]
        if not self.enabled:
            bg = C["border"]
        self.create_oval(r0, r0, r0 + s, r0 + s, fill=bg, outline=bg)
        self.create_text(r0 + s / 2, r0 + s / 2 + 1, text=self.icon, font=(ICONS, self.icon_size),
                         fill=self.colors["fg"] if self.enabled else C["white"])


class LinkButton(tk.Label):
    def __init__(self, parent, text, command, icon=None, bg=C["card"]):
        super().__init__(parent, text=(f"{icon}  " if icon else "") + text, bg=bg, fg=C["blue_dk"],
                         font=(UI, 9), cursor="hand2", padx=6, pady=3)
        self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self.config(bg=C["hover"]))
        self.bind("<Leave>", lambda e: self.config(bg=bg))


class CardWindow(tk.Toplevel):
    """Окно-карточка без системной рамки: скруглённые углы, шапка с аватаром, перетаскивание за шапку."""

    def __init__(self, master, pet, w, h, title, subtitle="", pose="idle"):
        super().__init__(master)
        self.pet = pet
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=C["key"])
        self.wm_attributes("-transparentcolor", C["key"])
        self._place(w, h)

        self.bgc = tk.Canvas(self, bg=C["key"], highlightthickness=0, bd=0)
        self.bgc.pack(fill="both", expand=True)
        self.bgc.bind("<Configure>", self._draw_card)
        self.body = tk.Frame(self.bgc, bg=C["card"])
        self.body.place(x=INSET, y=INSET - 4, relwidth=1, width=-2 * INSET, relheight=1, height=-2 * INSET + 6)

        head = tk.Frame(self.body, bg=C["card"])
        head.pack(fill="x", pady=(0, 8))
        self.avatar_lbl = tk.Label(head, image=avatar(pose), bg=C["card"], bd=0)
        self.avatar_lbl.pack(side="left")
        txt = tk.Frame(head, bg=C["card"])
        txt.pack(side="left", padx=(10, 0), fill="x", expand=True)
        self.title_lbl = tk.Label(txt, text=title, bg=C["card"], fg=C["ink"], font=(UI_SB, 13), anchor="w")
        self.title_lbl.pack(fill="x")
        self.sub_lbl = tk.Label(txt, text=subtitle, bg=C["card"], fg=C["muted"], font=(UI, 9), anchor="w")
        self.sub_lbl.pack(fill="x")
        self.head_btns = tk.Frame(head, bg=C["card"])
        self.head_btns.pack(side="right", anchor="n")
        IconButton(self.head_btns, I_CLOSE, size=30, bg=C["card"], fg=C["muted"], hover=C["hover"],
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
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _draw_card(self, e):
        self.bgc.delete("card")
        rounded_rect(self.bgc, 1, 1, e.width - 2, e.height - 2, RADIUS, fill=C["border"], outline="", tags="card")
        rounded_rect(self.bgc, 2, 2, e.width - 3, e.height - 3, RADIUS - 1, fill=C["card"], outline="", tags="card")

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def set_subtitle(self, text, error=False):
        self.sub_lbl.config(text=text, fg=C["red"] if error else C["muted"])

    def set_pose(self, pose):
        self.avatar_lbl.config(image=avatar(pose))

    def close(self):
        self.destroy()


class VoiceWindow(CardWindow):
    PLACEHOLDER = "Скажи или напиши вопрос…"

    def __init__(self, pet, client: BrainClient):
        super().__init__(pet.root, pet, 410, 560, "Шерпа", "Готов слушать")
        self.client = client
        self.rec = Recorder()
        self.worker = _Worker(self)
        self.session_id: str | None = None
        self.last_answer = ""
        self.stt_meta: dict = {}
        self.busy = False
        self.has_messages = False

        IconButton(self.head_btns, I_HISTORY, size=30, bg=C["card"], fg=C["muted"], hover=C["hover"],
                   command=pet.open_history, icon_size=10).pack(side="right", padx=(0, 2))

        # --- переписка ---
        chat_cv = tk.Canvas(self.body, bg=C["card"], highlightthickness=0, bd=0)
        chat_cv.pack(fill="both", expand=True)
        self.a_text = tk.Text(chat_cv, wrap="word", font=(UI, 10), bg=C["chat"], fg=C["ink"], relief="flat",
                              bd=0, padx=12, pady=12, cursor="arrow", highlightthickness=0, spacing2=2)
        chat_cv.bind("<Configure>", lambda e: self._round_box(chat_cv, self.a_text, e, C["chat"], 14, 6))
        self.a_text.tag_configure("you", background=C["blue"], foreground=C["white"], lmargin1=70, lmargin2=70,
                                  rmargin=4, spacing1=8, spacing3=8, font=(UI, 10))
        self.a_text.tag_configure("dog", background=C["white"], foreground=C["ink"], lmargin1=4, lmargin2=4,
                                  rmargin=44, spacing1=8, spacing3=8, font=(UI, 10))
        self.a_text.tag_configure("gap", font=(UI, 4))
        self.a_text.tag_configure("hint1", foreground=C["ink"], justify="center", spacing1=120, font=(UI_SB, 11))
        self.a_text.tag_configure("hint", foreground=C["muted"], justify="center", spacing1=4, font=(UI, 10))
        self.a_text.tag_configure("hint3", foreground=C["soft"], justify="center", spacing1=18, font=(UI, 8))
        self._show_hint("Спроси что угодно голосом")

        # --- строка ввода: микрофон, поле, отправить ---
        bar = tk.Frame(self.body, bg=C["card"])
        bar.pack(fill="x", pady=(10, 0))
        self.mic = IconButton(bar, I_MIC, size=48, ring=8, command=self.toggle_record, icon_size=17)
        self.mic.pack(side="left")
        self.send = IconButton(bar, I_SEND, size=38, command=self.ask, icon_size=13)
        self.send.pack(side="right", padx=(6, 0))
        field_cv = tk.Canvas(bar, bg=C["card"], highlightthickness=0, bd=0, height=56)
        field_cv.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.q_text = tk.Text(field_cv, height=2, wrap="word", font=(UI, 10), bg=C["white"], fg=C["ink"],
                              relief="flat", bd=0, highlightthickness=0, insertbackground=C["blue_dk"], undo=True)
        field_cv.bind("<Configure>", lambda e: self._round_box(field_cv, self.q_text, e, C["white"], 14, 9,
                                                               outline=C["border"]))
        self.q_text.bind("<Return>", self._on_enter)
        self.q_text.bind("<FocusIn>", self._ph_off)
        self.q_text.bind("<FocusOut>", self._ph_on)
        self.q_text.bind("<Button-1>", lambda e: self.focus_force())
        self._ph_on()

        # --- низ: действия и служебная строка ---
        foot = tk.Frame(self.body, bg=C["card"])
        foot.pack(fill="x", pady=(8, 0))
        LinkButton(foot, "Копировать", self.copy, icon=None).pack(side="left")
        LinkButton(foot, "Новая тема", self.new_topic).pack(side="left")
        self.status = tk.Label(foot, text="", bg=C["card"], fg=C["soft"], font=(UI, 8), anchor="e")
        self.status.pack(side="right")

        self.bind("<space>", self._on_space)
        self.focus_force()
        self.mic.focus_set()
        self.set_subtitle("Бужу мозг…")
        self.worker.run(self.client.ensure_running, lambda _: self._ready(), self._on_error)

    # --- оформление ---
    def _show_hint(self, title):
        self.a_text.config(state="normal")
        self.a_text.delete("1.0", "end")
        self.a_text.insert("end", title + "\n", "hint1")
        self.a_text.insert("end", "Отвечу коротко, как справка\n", "hint")
        self.a_text.insert("end", "микрофон или пробел — начать и остановить запись", "hint3")
        self.a_text.config(state="disabled")

    @staticmethod
    def _round_box(cv, inner, e, fill, r, pad, outline=None):
        cv.delete("box")
        if outline:
            rounded_rect(cv, 0, 0, e.width - 1, e.height - 1, r, fill=outline, outline="", tags="box")
            rounded_rect(cv, 1, 1, e.width - 2, e.height - 2, r - 1, fill=fill, outline="", tags="box")
        else:
            rounded_rect(cv, 0, 0, e.width - 1, e.height - 1, r, fill=fill, outline="", tags="box")
        cv.delete("inner")
        cv.create_window(pad, pad, window=inner, anchor="nw", width=e.width - 2 * pad, height=e.height - 2 * pad,
                         tags="inner")

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
        """Совместимость: ошибки — в подзаголовок, служебные цифры — в нижнюю строку."""
        if error:
            self.set_subtitle(text, True)
        else:
            self.status.config(text=text)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send.set_enabled(not busy)
        self.mic.set_enabled(not busy)

    def append(self, who: str, text: str) -> None:
        self.a_text.config(state="normal")
        if not self.has_messages:
            self.a_text.delete("1.0", "end")
            self.has_messages = True
        else:
            self.a_text.insert("end", "\n", "gap")
        self.a_text.insert("end", text.strip() + "\n", "you" if who == "you" else "dog")
        self.a_text.config(state="disabled")
        self.a_text.see("end")

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
        self._show_hint("Новая тема — спрашивай")
        self.set_subtitle("Готов слушать")
        self.status.config(text="")

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
    COLS = (("time", "Время", 110), ("question", "Вопрос", 250), ("answer", "Ответ", 280),
            ("tokens", "Токены", 80), ("cost", "$", 80))

    def __init__(self, pet, client: BrainClient):
        super().__init__(pet.root, pet, 860, 470, "История", "Загружаю…", pose="look")
        self.client = client
        self.worker = _Worker(self)
        self.rows: dict[str, dict] = {}
        IconButton(self.head_btns, I_REFRESH, size=30, bg=C["card"], fg=C["muted"], hover=C["hover"],
                   command=self.load, icon_size=10).pack(side="right", padx=(0, 2))

        self.cards = tk.Frame(self.body, bg=C["card"])
        self.cards.pack(fill="x", pady=(0, 10))
        self.today = self._stat_card("Сегодня")
        self.month = self._stat_card("Месяц")

        box = tk.Canvas(self.body, bg=C["card"], highlightthickness=0, bd=0)
        box.pack(fill="both", expand=True)
        inner = tk.Frame(box, bg=C["white"])
        box.bind("<Configure>", lambda e: VoiceWindow._round_box(box, inner, e, C["white"], 14, 8,
                                                                 outline=C["border"]))
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
                 font=(UI, 8), anchor="w").pack(fill="x", pady=(6, 0))
        self.load()

    def _stat_card(self, title):
        cv = tk.Canvas(self.cards, bg=C["card"], highlightthickness=0, bd=0, height=64)
        cv.pack(side="left", fill="x", expand=True, padx=(0, 10) if title == "Сегодня" else 0)
        lab = {}

        def draw(e):
            cv.delete("all")
            rounded_rect(cv, 0, 0, e.width - 1, e.height - 1, 14, fill=C["chat"], outline="")
            cv.create_text(16, 18, text=title.upper(), anchor="w", fill=C["muted"], font=(UI_SB, 8))
            lab["big"] = cv.create_text(16, 42, text=lab.get("big_t", "—"), anchor="w", fill=C["ink"],
                                        font=(UI_SB, 14))
            lab["small"] = cv.create_text(e.width - 16, 42, text=lab.get("small_t", ""), anchor="e",
                                          fill=C["muted"], font=(UI, 9))

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
        box = tk.Canvas(self.body, bg=C["card"], highlightthickness=0, bd=0)
        box.pack(fill="both", expand=True)
        txt = tk.Text(box, wrap="word", font=(UI, 10), bg=C["chat"], fg=C["ink"], relief="flat", bd=0,
                      highlightthickness=0, padx=6, pady=6, spacing2=2)
        box.bind("<Configure>", lambda e: VoiceWindow._round_box(box, txt, e, C["chat"], 14, 8))
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
