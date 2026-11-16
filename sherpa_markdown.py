# -*- coding: utf-8 -*-
"""Мини-Markdown для переписки Шерпы: разбор текста в куски «текст + теги» и вставка в tk.Text.

Поддержано то, что нейронки пишут в коротких ответах: **жирный**, *курсив*, `код`, ```блоки кода```,
заголовки #, списки (- * 1.) с вложенностью, цитаты >, ссылки [текст](url) и голые http(s)://, линия ---,
простые таблицы | a | b |. Всё остальное остаётся обычным текстом. Разбор (parse) не зависит от Tk —
его можно проверять отдельно; вставка (MdStyle) — в конце файла.

Кусок = (текст, теги). Первый тег — шрифт: n, b, i, bi, code, h1..h3, gap; остальные — оформление строки
(li0..li3, cb, quote, hr) и ссылка «a:<url>». Теги строки стоят на каждом куске строки: поля (lmargin) в Tk
берутся по первому символу экранной строки, а при переносе им может оказаться середина абзаца.
"""
from __future__ import annotations

import re
import tkinter as tk
import webbrowser
from tkinter import font as tkfont

Span = tuple[str, tuple[str, ...]]

LIST_STEP = 16  # отступ одного уровня списка, px
CODE_PAD = 8  # отступ блока кода слева и справа, px
QUOTE_PAD = 12
MAX_LEVEL = 3

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_LI = re.compile(r"^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

_INLINE = re.compile(
    r"""
    (?P<esc>\\[\\`*_{}\[\]()#+\-.!|>~])
  | (?P<tick>`+)(?P<code>.+?)(?P=tick)
  | \*\*\*(?=\S)(?P<bi>.+?)(?<=\S)\*\*\*
  | \*\*(?=\S)(?P<ba>.+?)(?<=\S)\*\*
  | (?<!\w)__(?=\S)(?P<bu>.+?)(?<=\S)__(?!\w)
  | \[(?P<label>[^\]\n]+)\]\((?P<url>[^)\s]+)(?:\s+"[^"]*")?\)
  | (?<![\w*])\*(?=[^\s*])(?P<ia>.+?)(?<=[^\s*])\*(?![\w*])
  | (?<!\w)_(?=[^\s_])(?P<iu>.+?)(?<=[^\s_])_(?!\w)
  | (?P<bare>https?://[^\s<>\]]*[^\s<>\].,;:!?)"'])
    """,
    re.VERBOSE,
)


def _merge(fmt: str, add: str) -> str:
    """Жирный внутри курсива и наоборот; в заголовке и коде шрифт не меняется."""
    if fmt not in ("n", "b", "i", "bi"):
        return fmt
    letters = (set(fmt) - {"n"}) | set(add)
    return "".join(sorted(letters)) or "n"


def _inline(s: str, fmt: str = "n", extra: tuple[str, ...] = ()) -> list[Span]:
    out: list[Span] = []
    pos = 0
    for m in _INLINE.finditer(s):
        if m.start() > pos:
            out.append((s[pos:m.start()], (fmt, *extra)))
        pos = m.end()
        g = m.groupdict()
        if g["esc"]:
            out.append((g["esc"][1], (fmt, *extra)))
        elif g["code"] is not None:
            out.append((g["code"].strip(), ("code", *extra)))
        elif g["bi"] is not None:
            out += _inline(g["bi"], _merge(fmt, "bi"), extra)
        elif g["ba"] is not None or g["bu"] is not None:
            out += _inline(g["ba"] if g["ba"] is not None else g["bu"], _merge(fmt, "b"), extra)
        elif g["label"] is not None:
            out += _inline(g["label"], fmt, (*extra, "a:" + g["url"]))
        elif g["ia"] is not None or g["iu"] is not None:
            out += _inline(g["ia"] if g["ia"] is not None else g["iu"], _merge(fmt, "i"), extra)
        else:  # bare
            out.append((g["bare"], (fmt, *extra, "a:" + g["bare"])))
    if pos < len(s):
        out.append((s[pos:], (fmt, *extra)))
    return out


def plain(src: str) -> list[Span]:
    """Без разметки: вопрос пользователя показываем как есть."""
    return [(src, ("n",))] if src else []


def parse(src: str) -> list[Span]:
    lines: list[list[Span]] = []  # строка = куски без перевода строки
    in_code = False
    stack: list[int] = []  # отступы открытых уровней списка
    pending_gap = False

    def push(spans: list[Span], block: tuple[str, ...] = ()) -> None:
        nonlocal pending_gap
        if pending_gap and lines:
            lines.append([("", ("gap",))])
        pending_gap = False
        lines.append([(t, (tags[0], *block, *tags[1:])) for t, tags in spans] or [("", ("n", *block))])

    for raw in src.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(raw):
            in_code = not in_code
            stack.clear()
            continue
        if in_code:
            push([(raw.replace("\t", "    ") or " ", ("code",))], ("cb",))
            continue
        if not raw.strip():
            pending_gap = bool(lines)
            continue
        if m := _HR.match(raw):
            stack.clear()
            push([("─" * 24, ("n",))], ("hr",))
        elif m := _HEAD.match(raw):
            stack.clear()
            push(_inline(m.group(2), f"h{min(len(m.group(1)), 3)}"))
        elif m := _QUOTE.match(raw):
            stack.clear()
            push(_inline(m.group(1)), ("quote",))
        elif m := _LI.match(raw):
            ind = len(m.group(1).expandtabs(4))
            while stack and stack[-1] > ind:
                stack.pop()
            if not stack or stack[-1] < ind:
                stack.append(ind)
            level = min(len(stack) - 1, MAX_LEVEL)
            mark = m.group(2)
            mark = ("•" if level == 0 else "◦") if mark in "-*+•" else mark
            push([(mark + "\t", ("n",)), *_inline(m.group(3))], (f"li{level}",))
        elif "|" in raw and raw.strip().startswith("|") and raw.strip().endswith("|"):
            stack.clear()
            if _TABLE_SEP.match(raw):
                continue
            cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            spans: list[Span] = []
            for i, c in enumerate(cells):
                if i:
                    spans.append(("  │  ", ("n",)))
                spans += _inline(c)
            push(spans)
        else:
            stack.clear()
            push(_inline(raw.strip()))
    out: list[Span] = []
    for i, line in enumerate(lines):
        out += line
        if i < len(lines) - 1:
            last = line[-1][1]  # перевод строки — без ссылки, иначе подчёркивание уйдёт за конец строки
            out.append(("\n", tuple(t for t in last if not t.startswith("a:"))))
    return out


def _block_margin(tags: tuple[str, ...]) -> int:
    for t in tags:
        if t.startswith("li") and t[2:].isdigit():
            return LIST_STEP * (int(t[2:]) + 1)
        if t == "cb":
            return 2 * CODE_PAD
        if t == "quote":
            return QUOTE_PAD
    return 0


class MdStyle:
    """Шрифты и цвета разметки: один набор на окно, теги настраиваются на каждом tk.Text."""

    def __init__(self, root, family: str, mono: str, size: int, *, ink: str, muted: str, soft: str, link: str,
                 code_bg: str, select_bg: str, bold_family: str | None = None):
        def mk(fam=family, sz=size, **kw):
            return tkfont.Font(root=root, family=fam, size=sz, **kw)

        self.base = mk()
        self.fonts = {
            "n": self.base, "b": mk(weight="bold"), "i": mk(slant="italic"), "bi": mk(weight="bold", slant="italic"),
            "code": mk(mono, size - 1),
            "h1": mk(bold_family or family, size + 3, weight="bold"),
            "h2": mk(bold_family or family, size + 2, weight="bold"),
            "h3": mk(bold_family or family, size + 1, weight="bold"),
            "gap": mk(sz=4),
        }
        self.ink, self.muted, self.soft, self.link = ink, muted, soft, link
        self.code_bg, self.select_bg = code_bg, select_bg

    def setup(self, text: tk.Text) -> None:
        for name, f in self.fonts.items():
            text.tag_configure("f_" + name, font=f)
        text.tag_configure("f_code", background=self.code_bg)
        for h in ("f_h1", "f_h2", "f_h3"):
            text.tag_configure(h, foreground=self.ink, spacing1=2)
        for i in range(MAX_LEVEL + 1):
            text.tag_configure(f"li{i}", lmargin1=LIST_STEP * i, lmargin2=LIST_STEP * (i + 1),
                               tabs=(LIST_STEP * (i + 1),))
        text.tag_configure("cb", lmargin1=CODE_PAD, lmargin2=CODE_PAD, rmargin=CODE_PAD, background=self.code_bg)
        text.tag_configure("quote", lmargin1=QUOTE_PAD, lmargin2=QUOTE_PAD, foreground=self.muted)
        text.tag_configure("hr", foreground=self.soft)
        text.tag_raise("sel")

    def insert(self, text: tk.Text, spans: list[Span], clear: bool = True) -> None:
        """Заменяет содержимое text (clear=False — дописывает в конец); потом поле только для чтения:
        выделять и копировать можно, печатать нельзя."""
        text.config(state="normal")
        if clear:
            text.delete("1.0", "end")
        for t, tags in spans:
            names = ["f_" + tags[0], *tags[1:]]
            for tag in tags[1:]:
                if tag.startswith("a:"):
                    self._link_tag(text, tag)
            text.insert("end", t, tuple(names))
        text.tag_raise("sel")
        text.config(state="disabled")

    def _link_tag(self, text: tk.Text, tag: str) -> None:
        if tag in getattr(text, "_md_links", ()):
            return
        text.__dict__.setdefault("_md_links", set()).add(tag)
        url = tag[2:]
        text.tag_configure(tag, foreground=self.link, underline=True)
        text.tag_bind(tag, "<Enter>", lambda e: text.config(cursor="hand2"))
        text.tag_bind(tag, "<Leave>", lambda e: text.config(cursor="xterm"))
        text.tag_bind(tag, "<ButtonRelease-1>", lambda e: self._open(text, url))

    @staticmethod
    def _open(text: tk.Text, url: str) -> None:
        if text.tag_ranges("sel"):  # человек выделял текст, а не нажимал на ссылку
            return
        if url.lower().startswith(("http://", "https://", "mailto:")):
            webbrowser.open(url)

    def natural_width(self, spans: list[Span]) -> int:
        """Ширина самой длинной строки без переносов, px. Нужна, чтобы короткий ответ не растягивал пузырь."""
        best = cur = margin = 0
        for t, tags in spans:
            font = self.fonts[tags[0]]
            margin = max(margin, _block_margin(tags[1:]))
            for k, part in enumerate(t.split("\n")):
                if k:  # перевод строки: итог по строке, дальше счёт заново
                    best, cur, margin = max(best, cur + margin), 0, _block_margin(tags[1:])
                # маркер списка «•\t»: его место уже входит в отступ уровня, табуляцию не меряем
                cur += font.measure(part.replace("\t", ""))
        return max(best, cur + margin) + 4
