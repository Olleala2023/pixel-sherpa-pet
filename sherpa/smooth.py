# -*- coding: utf-8 -*-
"""Увеличение пиксельного пса со сглаженными углами (для размеров больше 3×).

Как: сначала Scale2x дважды (алгоритм EPX для пиксель-арта: скругляет диагонали, новых цветов не добавляет),
потом до нужного размера — «гладкие маски»: каждый цвет пса становится своей маской, маски увеличиваются
бикубически и чуть размываются, и каждый пиксель берёт цвет с самой сильной маской. Получаются ровные
кривые границы, но цвета остаются чистыми (палитра пса), а прозрачность — без полутонов: окно пса
прозрачно по одному цвету-ключу, полупрозрачная кайма там была бы грязной.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

SMOOTH_FROM = 4  # с какого масштаба сглаживаем; 2× и 3× — честные пиксели


def scale2x(im: Image.Image) -> Image.Image:
    """EPX / Scale2x: каждый пиксель → 2×2, угол берёт цвет соседей, если они образуют диагональ."""
    a = np.asarray(im)
    p = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
    e, b, d, f, h = p[1:-1, 1:-1], p[:-2, 1:-1], p[1:-1, :-2], p[1:-1, 2:], p[2:, 1:-1]

    def eq(x, y):
        return np.all(x == y, axis=-1)

    db, bf, dh, hf = eq(d, b), eq(b, f), eq(d, h), eq(h, f)
    out = np.empty((a.shape[0] * 2, a.shape[1] * 2, a.shape[2]), a.dtype)
    out[0::2, 0::2] = np.where((db & ~bf & ~dh)[..., None], d, e)
    out[0::2, 1::2] = np.where((bf & ~db & ~hf)[..., None], f, e)
    out[1::2, 0::2] = np.where((dh & ~db & ~hf)[..., None], d, e)
    out[1::2, 1::2] = np.where((hf & ~dh & ~bf)[..., None], f, e)
    return Image.fromarray(out, "RGBA")


def _blur(v: np.ndarray, sigma: float) -> np.ndarray:
    """Гауссово размытие сдвигами (без scipy)."""
    r = max(1, int(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    p = np.pad(v, ((0, 0), (r, r)), mode="edge")
    v = sum(w * p[:, i : i + v.shape[1]] for i, w in enumerate(k))
    p = np.pad(v, ((r, r), (0, 0)), mode="edge")
    return sum(w * p[i : i + v.shape[0], :] for i, w in enumerate(k))


def smooth_masks(im: Image.Image, size: tuple[int, int], blur: float = 0.0) -> Image.Image:
    """Увеличение «гладкими масками»: ровные границы между цветами, без новых цветов и полупрозрачности."""
    a = np.array(im)
    a[a[..., 3] < 128] = 0  # всё полупрозрачное — фон
    cols, idx = np.unique(a.reshape(-1, 4), axis=0, return_inverse=True)
    idx = idx.reshape(a.shape[:2])
    best = arg = None
    for k in range(len(cols)):
        v = np.asarray(Image.fromarray((idx == k).astype(np.float32), "F").resize(size, Image.BICUBIC))
        if blur:
            v = _blur(v, blur)
        if best is None:
            best, arg = v.copy(), np.zeros(v.shape, np.intp)
        else:
            upd = v > best
            best[upd] = v[upd]
            arg[upd] = k
    return Image.fromarray(cols[arg].astype(np.uint8), "RGBA")


def upscale(im: Image.Image, scale: int) -> Image.Image:
    """Пёс в масштабе scale: до SMOOTH_FROM — пиксели, дальше — сглаженные углы."""
    size = (im.width * scale, im.height * scale)
    if scale < SMOOTH_FROM:
        return im.resize(size, Image.NEAREST)
    return smooth_masks(scale2x(scale2x(im)), size, blur=0.15 * scale)
