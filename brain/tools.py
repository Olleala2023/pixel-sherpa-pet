"""Инструменты нейронки: время, счёт, напоминание, буфер обмена.

Нейронка сама решает, когда их звать. Напоминание мозг не исполняет: он кладёт его в deps.actions,
а /ask отдаёт псу — пёс и напомнит (облачко, прыжок, звук). Буфер обмена читается только по просьбе
в вопросе («что у меня в буфере», «переведи скопированное») и уходит нейронке целиком, до 4000 знаков.
"""
from __future__ import annotations

import ast
import ctypes
import math
import operator
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic_ai import RunContext, Tool

CLIP_MAX = 4000
REMINDER_MAX_MIN = 24 * 60
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


@dataclass
class Deps:
    actions: list[dict] = field(default_factory=list)


def now() -> str:
    """Текущие дата, время и день недели на компьютере пользователя."""
    t = datetime.now()
    return f"{t:%d.%m.%Y %H:%M}, {WEEKDAYS[t.weekday()]}"


_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "log": math.log, "log10": math.log10, "exp": math.exp, "floor": math.floor, "ceil": math.ceil}
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("слишком большая степень")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and not node.keywords):
        return _FUNCS[node.func.id](*(_eval(a) for a in node.args))
    raise ValueError(f"нельзя: {ast.dump(node)[:60]}")


def calc(expression: str) -> str:
    """Точно считает арифметическое выражение: + - * / // % **, скобки, sqrt, abs, round, sin, cos, tan,
    log, log10, exp, floor, ceil, pi, e. Пример: '(1250*0.13)+sqrt(2)'. Вызывай для любых вычислений."""
    try:
        expr = re.sub(r"(?<=\d),(?=\d)", ".", expression).replace("^", "**")  # «1,5» — дробь, «round(x, 2)» — нет
        v = _eval(ast.parse(expr, mode="eval"))
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
        return f"Ошибка: {e}"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        v = int(v)
    return str(round(v, 10) if isinstance(v, float) else v)


def set_reminder(ctx: RunContext[Deps], minutes: float, text: str) -> str:
    """Ставит напоминание: через minutes минут пёс покажет text в облачке и подпрыгнет.
    Для «через полчаса» minutes=30, «через 2 часа» minutes=120. Не больше суток.
    Пока Шерпа не перезапущена — напоминание живёт; после перезапуска пропадёт."""
    if not 0 < minutes <= REMINDER_MAX_MIN:
        return f"Ошибка: можно от 1 секунды до {REMINDER_MAX_MIN} минут"
    at = datetime.now() + timedelta(minutes=minutes)
    ctx.deps.actions.append({"type": "reminder", "in_s": round(minutes * 60), "at": at.isoformat(timespec="seconds"),
                             "text": text.strip()[:300]})
    return f"Напоминание поставлено на {at:%H:%M}"


def read_clipboard() -> str:
    """Текст из буфера обмена пользователя. Зови, только если пользователь сам говорит о буфере,
    скопированном или «вот этом тексте»."""
    text = _clipboard_text()
    if text is None:
        return "Буфер обмена пуст или в нём не текст."
    if len(text) > CLIP_MAX:
        return text[:CLIP_MAX] + f"\n…(обрезано, всего {len(text)} знаков)"
    return text


def _clipboard_text() -> str | None:
    """CF_UNICODETEXT из буфера Windows через WinAPI (в мозге нет Tk)."""
    CF_UNICODETEXT = 13
    try:
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    except AttributeError:  # не Windows
        return None
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u32.OpenClipboard(None):
        return None
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p) or None
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


TOOLS = [
    Tool(now, takes_ctx=False),
    Tool(calc, takes_ctx=False),
    Tool(set_reminder, takes_ctx=True),
    Tool(read_clipboard, takes_ctx=False),
]
