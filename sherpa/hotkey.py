# -*- coding: utf-8 -*-
"""Глобальная горячая клавиша Windows (RegisterHotKey): работает, даже когда пёс не в фокусе.

Клавиша регистрируется в отдельном потоке с собственной очередью сообщений Windows; нажатия попадают
в queue, а пёс забирает их в своём tick (Tk трогается только из главного потока).
"""
from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK = {"SPACE": 0x20, **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
      **{f"F{i}": 0x6F + i for i in range(1, 13)}}
MODS = {"CTRL": MOD_CONTROL, "ALT": MOD_ALT, "SHIFT": MOD_SHIFT, "WIN": MOD_WIN}
DEFAULT = "Ctrl+Alt+Space"
# занята другой программой (ошибка 1409) — берём следующую; Q — «вопрос»
CANDIDATES = ("Ctrl+Alt+Space", "Ctrl+Alt+Q", "Win+Alt+S")


def parse(combo: str) -> tuple[int, int]:
    """'Ctrl+Alt+Space' → (модификаторы, код клавиши). ValueError, если не понял."""
    mods, vk = 0, None
    for part in combo.replace(" ", "").upper().split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif part in VK and vk is None:
            vk = VK[part]
        else:
            raise ValueError(f"не понял клавишу «{part}» в «{combo}»")
    if vk is None or not mods:
        raise ValueError(f"нужны модификатор и клавиша: «{combo}»")
    return mods, vk


class GlobalHotkey:
    def __init__(self, combos: tuple[str, ...] | str = CANDIDATES):
        self.combos = (combos,) if isinstance(combos, str) else tuple(combos)
        self.combo = self.combos[0]  # после регистрации — та, что досталась
        self.events: queue.Queue = queue.Queue()
        self.ok: bool | None = None  # None — ещё регистрируется, False — занята другой программой
        self._tid = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkey")
        self._thread.start()
        self._ready.wait(2)

    def _run(self) -> None:
        try:
            user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        except AttributeError:  # не Windows
            self.ok = False
            self._ready.set()
            return
        self._tid = k32.GetCurrentThreadId()
        self.ok = False
        for combo in self.combos:
            try:
                mods, vk = parse(combo)
            except ValueError:
                continue
            if user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
                self.combo, self.ok = combo, True
                break
        self._ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.events.put(1)
        finally:
            user32.UnregisterHotKey(None, 1)

    def pressed(self) -> bool:
        """Было ли нажатие с прошлой проверки (несколько подряд — одно)."""
        hit = False
        while True:
            try:
                self.events.get_nowait()
                hit = True
            except queue.Empty:
                return hit

    def stop(self) -> None:
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
