# -*- coding: utf-8 -*-
"""Облачко над псом: голосовой вопрос и ответ без окна справки (проба).

Двойной щелчок по псу — сразу запись, щелчок по псу — готово. Вопрос уходит в мозг без правки текста,
ответ появляется облачком с хвостиком к голове пса и сам гаснет, когда его успели прочитать
(пока мышь над облачком — не гаснет). «В окне» переносит разговор в обычное окно справки.

Окно без рамки с прозрачным цветом, как у пса: край облачка рисует Pillow в 4× и режет по альфе,
чтобы у прозрачного цвета не было полупрозрачной каймы.
"""
from __future__ import annotations

import time
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from .markdown_view import MdStyle, parse
from .voice import MAX_REC_S, BrainClient, LiveUpload, Recorder, _Worker, err_text
from .voice_ui import C, I_CLOSE, I_COPY, I_MIC, ICONS, SS, UI, UI_SB, _rgb, _shrink

MAX_W = 320        # ширина текста ответа, px
MIN_W = 180
MAX_TEXT_H = 240   # выше — текст прокручивается колесом
PAD = 12
R = 14
TAIL_H = 12
TAIL_W = 18
FOOT_H = 24
SESSION_TTL = 600  # через 10 мин тишины следующий вопрос — новая тема
I_OPEN = ""  # Segoe MDL2: «открыть в новом окне»


def bubble_image(w: int, body_h: int, tail_x: int, fill: str, outline: str) -> ImageTk.PhotoImage:
    """Облачко w×(body_h+TAIL_H): скруглённый прямоугольник и хвостик вниз в точке tail_x."""
    h = body_h + TAIL_H
    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)

    def shape(inset: int, color: str) -> None:
        i = inset * SS
        d.rounded_rectangle((i, i, w * SS - 1 - i, body_h * SS - 1 - i), (R - inset) * SS, fill=color)
        tx = tail_x * SS
        half = (TAIL_W // 2 - inset) * SS
        top = body_h * SS - 1 - i - R * SS // 2
        d.polygon([(tx - half, top), (tx + half, top), (tx + half // 4, (h - inset * 2) * SS - 1)], fill=color)

    shape(0, outline)
    shape(1, fill)
    small = _shrink(big, (w, h))
    # прозрачный цвет окна не смешивается: всё полупрозрачное — либо облачко, либо дырка
    out = Image.new("RGB", (w, h), _rgb(C["key"]))
    mask = small.getchannel("A").point(lambda a: 255 if a >= 110 else 0)
    out.paste(small.convert("RGB"), (0, 0), mask)
    return ImageTk.PhotoImage(out)


class Bubble(tk.Toplevel):
    def __init__(self, pet, client: BrainClient):
        super().__init__(pet.root)
        self.pet, self.client = pet, client
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=C["key"])
        self.wm_attributes("-transparentcolor", C["key"])
        self.withdraw()

        self.rec = Recorder()
        self.worker = _Worker(self)
        self.busy = False
        self.shown = False
        self.session_id: str | None = None
        self.turns: list[tuple[str, str]] = []
        self.last_answer = ""
        self.last_at = 0.0
        self.stt_meta: dict = {}
        self.partial = ""
        self.streaming = False
        self.flush_job = None
        self.notice_action = None
        self.action_btn = None
        self.live = None  # LiveUpload текущей записи
        self.live_text = ""
        self.hide_job = None
        self.hover = False
        self.mode = "idle"  # listen / busy / answer / error

        self.canvas = tk.Canvas(self, bg=C["key"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.img = None
        self.img_key = None
        self.body_w = self.body_h = 0

        self.style = MdStyle(self, UI, "Consolas", 10, ink=C["ink"], muted=C["muted"], soft=C["soft"],
                             link=C["blue_dk"], code_bg="#DCE6EF", select_bg=C["sel"], bold_family=UI_SB)
        self.text = self._new_text()
        self.meter = self._new_text()
        self.q_lbl = tk.Label(self, bg=C["card"], fg=C["muted"], font=(UI, 8, "italic"), anchor="w", justify="left")
        self.status = tk.Label(self, bg=C["card"], fg=C["ink"], font=(UI_SB, 10), anchor="w", justify="left")
        self.hint = tk.Label(self, bg=C["card"], fg=C["soft"], font=(UI, 8), anchor="w", justify="left")
        self.foot = tk.Frame(self, bg=C["card"], height=FOOT_H)
        self._icon(self.foot, I_MIC, "Ещё", self.ask_more).pack(side="left")
        self._icon(self.foot, I_COPY, "Копировать", self.copy).pack(side="left", padx=(4, 0))
        self._icon(self.foot, I_OPEN, "В окне", self.to_window).pack(side="left", padx=(4, 0))
        self._icon(self.foot, I_CLOSE, None, self.hide).pack(side="right")
        self.close_btn = self._icon(self, I_CLOSE, None, self.cancel)

        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.text.bind("<MouseWheel>", lambda e: (self.text.yview_scroll(-1 if e.delta > 0 else 1, "units"), "break")[1])

    # --- виджеты ---
    def _new_text(self) -> tk.Text:
        t = tk.Text(self, wrap="word", width=1, height=1, font=self.style.base, bg=C["card"], fg=C["ink"],
                    relief="flat", bd=0, highlightthickness=0, padx=0, pady=0, cursor="xterm",
                    exportselection=False, takefocus=0, insertwidth=0, selectbackground=C["glow"],
                    selectforeground=C["ink"], inactiveselectbackground=C["glow"])
        self.style.setup(t)
        t.bind("<Button-1>", lambda e: (self.focus_force(), t.focus_set()), add="+")
        return t

    def _icon(self, parent, icon, text, command) -> tk.Label:
        """Иконка Segoe MDL2 и подпись (у них разные шрифты — две метки в рамке), подсветка при наведении."""
        box = tk.Frame(parent, bg=C["card"], cursor="hand2")
        ic = tk.Label(box, text=icon, bg=C["card"], fg=C["muted"], font=(ICONS, 9 if text else 8), cursor="hand2")
        ic.pack(side="left", padx=(4, 0 if text else 4), pady=2)
        parts = [box, ic]
        if text:
            lbl = tk.Label(box, text=text, bg=C["card"], fg=C["muted"], font=(UI, 8), cursor="hand2")
            lbl.pack(side="left", padx=(3, 4))
            parts.append(lbl)

        def hover(on):
            for p in parts:
                p.config(fg=C["blue_dk"] if on else C["muted"], bg=C["hover"] if on else C["card"])

        for p in parts:
            p.bind("<Enter>", lambda e: hover(True))
            p.bind("<Leave>", lambda e: hover(False))
            p.bind("<ButtonRelease-1>", lambda e: command())
        return box

    # --- раскладка ---
    def _height(self, w: int) -> int:
        m = self.meter
        m.place(x=-5000, y=0, width=w, height=4000)
        m.update_idletasks()
        n = m.count("1.0", "end", "update", "ypixels")
        m.place_forget()
        return int(n[0] if isinstance(n, (tuple, list)) else n or 0)

    def _layout(self) -> None:
        """Раскладывает содержимое под текущий режим и перерисовывает облачко."""
        cv = self.canvas
        cv.delete("content")
        for w in (self.text, self.q_lbl, self.status, self.hint, self.foot, self.close_btn):
            w.place_forget()
        y = PAD
        if self.mode == "answer":
            spans = parse(self.last_answer)
            self.update_idletasks()  # короткий ответ: облачко не уже строки кнопок
            tw = max(MIN_W, self.foot.winfo_reqwidth() - 8, min(MAX_W, self.style.natural_width(spans)))
            q = self.turns[-1][0] if self.turns else ""
            self.q_lbl.config(text=f"«{q}»" if len(q) <= 70 else f"«{q[:68]}…»", wraplength=tw)
            self.style.insert(self.meter, spans)
            th = min(MAX_TEXT_H, self._height(tw))
            self.style.insert(self.text, spans)
            self.text.yview_moveto(0)
            self.update_idletasks()
            qh = self.q_lbl.winfo_reqheight()
            cv.create_window(PAD, y, window=self.q_lbl, anchor="nw", width=tw, tags="content")
            y += qh + 2
            cv.create_window(PAD, y, window=self.text, anchor="nw", width=tw, height=th, tags="content")
            y += th + 4
            cv.create_window(PAD - 4, y, window=self.foot, anchor="nw", width=tw + 8, height=FOOT_H, tags="content")
            y += FOOT_H + PAD - 6
        else:
            tw = MIN_W + (100 if self.notice_action else 40)
            self.update_idletasks()
            cv.create_window(PAD, y, window=self.status, anchor="nw", width=tw - 22, tags="content")
            cv.create_window(PAD + tw - 14, y - 2, window=self.close_btn, anchor="nw", tags="content")
            self.status.config(wraplength=tw - 22)
            self.hint.config(wraplength=tw)
            self.update_idletasks()
            y += self.status.winfo_reqheight()
            if self.hint.cget("text"):
                cv.create_window(PAD, y + 2, window=self.hint, anchor="nw", width=tw, tags="content")
                y += self.hint.winfo_reqheight() + 2
            if self.action_btn is not None:  # кнопка уведомления («Обновить»)
                y += 6
                cv.create_window(PAD - 4, y, window=self.action_btn, anchor="nw", tags="content")
                self.update_idletasks()
                y += self.action_btn.winfo_reqheight()
            y += PAD
        self.body_w, self.body_h = tw + 2 * PAD, y
        self.img_key = None
        self.follow()

    def follow(self) -> None:
        """Ставит облачко над головой пса; хвостик смотрит на голову, даже когда облачко упёрлось в край."""
        if not self.shown or not self.body_w:
            return
        pet = self.pet
        flip = pet.facing_left and pet.state in ("walk", "idle")
        head_x = int(pet.x + pet.scale * (18 if flip else 12))
        head_y = int(pet.y + pet.scale * 3)
        w, h = self.body_w, self.body_h + TAIL_H
        x = max(pet.work_left + 8, min(head_x - w // 2, pet.work_right - w - 8))
        y = max(pet.work_top + 8, head_y - h + 4)
        tail_x = max(R + TAIL_W // 2, min(head_x - x, w - R - TAIL_W // 2))
        key = (w, self.body_h, tail_x)
        if key != self.img_key:
            self.img = bubble_image(w, self.body_h, tail_x, C["card"], C["border"])
            self.img_key = key
            self.canvas.delete("bg")
            self.canvas.create_image(0, 0, image=self.img, anchor="nw", tags="bg")
            self.canvas.tag_lower("bg")
            self.canvas.config(width=w, height=h)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _show(self, mode: str, status: str = "", hint: str = "", error: bool = False, action=None) -> None:
        """action — (иконка, подпись, функция): одна кнопка под текстом уведомления."""
        self.mode = mode
        if self.action_btn is not None:
            self.action_btn.destroy()
            self.action_btn = None
        self.notice_action = action
        if action:
            icon, label, cb = action
            self.action_btn = self._icon(self, icon, label, lambda: (self.hide(), cb()))
        self.status.config(text=status, fg=C["red"] if error else C["ink"])
        self.hint.config(text=hint)
        self._cancel_hide()
        self.attributes("-alpha", 1.0)
        if not self.shown:
            self.shown = True
            self.deiconify()
        self._layout()
        self.lift()

    # --- автоскрытие ---
    def _cancel_hide(self) -> None:
        if self.hide_job is not None:
            self.after_cancel(self.hide_job)
            self.hide_job = None

    def _schedule_hide(self, seconds: float) -> None:
        self._cancel_hide()
        self._hide_in = seconds
        if not self.hover:
            self.hide_job = self.after(int(seconds * 1000), self._fade)

    def _hover(self, on: bool) -> None:
        self.hover = on
        if self.mode in ("answer", "error", "notice"):
            if on:
                self._cancel_hide()
                self.attributes("-alpha", 1.0)
            else:
                self._schedule_hide(4.0)

    def _fade(self, step: int = 0) -> None:
        self.hide_job = None
        if step >= 6:
            self.hide()
            return
        self.attributes("-alpha", 1.0 - step / 6)
        self.hide_job = self.after(40, lambda: self._fade(step + 1))

    def hide(self) -> None:
        self._cancel_hide()
        self.shown = False
        self.mode = "idle"
        self.withdraw()
        self.attributes("-alpha", 1.0)

    # --- поза пса ---
    def _pose(self, pose) -> None:
        self.pet.voice_pose = pose

    # --- запись ---
    @property
    def recording(self) -> bool:
        return self.rec.active

    def toggle(self) -> None:
        if self.busy:
            return
        if self.rec.active:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self) -> None:
        if time.monotonic() - self.last_at > SESSION_TTL:
            self.session_id, self.turns = None, []
        try:
            self.rec.start()
        except Exception as e:  # noqa: BLE001
            self._error(e)
            return
        self._pose("look")
        self.live_text = ""
        self._show("listen", "● Слушаю · 0:00", "щёлкни по мне — готово")
        self.status.config(fg=C["red"])
        # звук уходит мозгу по ходу записи: он просыпается, грузит модель и распознаёт фразы между паузами
        self.live = LiveUpload(self.client, self.rec, lambda t: self.worker.post(self._on_live_text, t))
        self._tick()

    def _on_live_text(self, text: str) -> None:
        """Мозг распознал очередную фразу — показываем, что уже понято (хвост, если длинно)."""
        self.live_text = text.strip()
        if self.mode not in ("listen", "busy") or not self.live_text:
            return
        t = self.live_text if len(self.live_text) <= 160 else "…" + self.live_text[-158:]
        self.hint.config(text=f"«{t}»")
        self._layout()

    def _tick(self) -> None:
        if not self.rec.active:
            return
        sec = self.rec.elapsed()
        self.status.config(text=f"{'●' if int(sec * 2) % 2 == 0 else '○'} Слушаю · {int(sec) // 60}:{int(sec) % 60:02d}")
        if sec >= MAX_REC_S:
            self.stop_record()
            return
        self.after(250, self._tick)

    def stop_record(self) -> None:
        wav = self.rec.stop()
        self.busy = True
        self._pose("think")
        t = self.live_text if len(self.live_text) <= 160 else "…" + self.live_text[-158:]
        self._show("busy", "Дослушиваю…" if t else "Расшифровываю…", f"«{t}»" if t else "")
        live = self.live
        self.worker.run(lambda: live.finish(wav), self._on_transcript, self._error)

    def cancel(self) -> None:
        if self.live is not None:
            self.live.cancel()
        if self.rec.active:
            try:
                self.rec.stop()
            except Exception:  # noqa: BLE001
                pass
        self._pose(None)
        self.hide()

    def _on_transcript(self, r: dict) -> None:
        text = (r.get("text") or "").strip()
        if not text:
            self.busy = False
            self._error(None, "Не расслышал — щёлкни по мне дважды и повтори")
            return
        meta = {k: r.get(k) for k in ("audio_id", "audio_sec", "stt_ms")}
        self._ask(text, {**meta, "transcript_raw": text})

    def _ask(self, question: str, meta: dict | None = None) -> None:
        self.busy = True
        self.turns.append((question, ""))
        self._pose("think")
        q = question if len(question) <= 90 else question[:88] + "…"
        self._show("busy", "Думаю…", f"«{q}»")
        payload = {"session_id": self.session_id, "question": question, "source": "bubble", **(meta or {})}
        self.partial = ""
        self.streaming = True
        self.worker.run(lambda: self.client.ask_stream(payload, lambda t: self.worker.post(self._on_delta, t)),
                        self._on_answer, self._error)

    # --- ответ по мере генерации: текст копится, облачко перерисовывается не чаще раза в 0,15 с ---
    def _on_delta(self, t: str) -> None:
        if not self.streaming:
            return
        self.partial += t
        if self.flush_job is None:
            self.flush_job = self.after(150, self._flush)

    def _flush(self) -> None:
        self.flush_job = None
        if not self.streaming or not self.partial.strip():
            return
        self.last_answer = self.partial.strip()
        if self.mode != "answer":
            self._pose(None)
            self.pet.voice_pose = "think"  # пока пишет — думает
        self._show("answer")
        self.text.yview_moveto(1.0)  # видно, что дописывается

    def _on_answer(self, r: dict) -> None:
        self.busy = False
        self.streaming = False
        if self.flush_job is not None:
            self.after_cancel(self.flush_job)
            self.flush_job = None
        self.session_id = r.get("session_id")
        self.last_answer = (r.get("answer") or "").strip()
        self.last_at = time.monotonic()
        if self.turns:
            self.turns[-1] = (self.turns[-1][0], self.last_answer)
        self._pose(None)
        self.pet.hold_pose("wave", 2.5)
        self._show("answer")
        # время на чтение: ~15 знаков в секунду, но не меньше 8 и не больше 45 с
        self._schedule_hide(min(45.0, max(8.0, 4.0 + len(self.last_answer) / 15)))
        self.pet.handle_actions(r.get("actions"))

    def say(self, text: str, hint: str = "", seconds: float = 60.0, action=None) -> bool:
        """Уведомление от пса (напоминание, перезапуск мозга, обновление). action — (иконка, подпись, функция)
        для кнопки. False — облачко занято разговором."""
        if self.busy or self.rec.active:
            return False
        self._show("notice", text, hint, action=action)
        self._schedule_hide(seconds)
        return True

    def _error(self, e: Exception | None, text: str | None = None) -> None:
        self.busy = False
        if self.turns and not self.turns[-1][1]:
            self.turns.pop()
        self._pose(None)
        self._show("error", text or err_text(e), "двойной щелчок по мне — ещё раз", error=True)
        self._schedule_hide(10.0)

    # --- кнопки ответа ---
    def ask_more(self) -> None:
        if not self.busy:
            self.start_record()

    def copy(self) -> None:
        if self.last_answer:
            self.clipboard_clear()
            self.clipboard_append(self.last_answer)
            self.hint.config(text="")

    def to_window(self) -> None:
        """Переносит разговор в окно справки и продолжает ту же тему."""
        turns, sid = [t for t in self.turns if t[1]], self.session_id
        self.hide()
        self.pet.open_voice()
        vw = self.pet.voice_window
        if vw is None or vw.has_messages:
            return
        vw.session_id = sid
        for q, a in turns:
            vw.append("you", q)
            vw.append("dog", a)
        if turns:
            vw.last_answer = turns[-1][1]

    def close(self) -> None:
        self.cancel()
        self.worker.alive = False
        self.destroy()
