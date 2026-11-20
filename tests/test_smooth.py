"""Сглаженное увеличение пса (sherpa/smooth.py): размеры, палитра, прозрачность без полутонов."""
import numpy as np
import pytest
from PIL import Image

from sherpa.smooth import SMOOTH_FROM, scale2x, upscale
from sherpa.sprites import COLORS, H, W, draw_chibi_frame

STATES = ["idle", "walk", "wave", "think", "jump", "sleep", "look", "drag"]
PALETTE = {c[:3] for c in COLORS.values()}


@pytest.mark.parametrize("scale", [2, 3])
def test_small_scales_stay_pixel(scale):
    im = draw_chibi_frame("idle", 0)
    assert np.array_equal(np.asarray(upscale(im, scale)), np.asarray(im.resize((W * scale, H * scale), Image.NEAREST)))


@pytest.mark.parametrize("scale", [4, 5])
@pytest.mark.parametrize("state", STATES)
def test_smooth_keeps_palette_and_binary_alpha(scale, state):
    out = np.asarray(upscale(draw_chibi_frame(state, 1), scale))
    assert out.shape == (H * scale, W * scale, 4)
    assert set(np.unique(out[..., 3])) <= {0, 255}  # окно прозрачно по цвету-ключу: полутонов быть не должно
    opaque = out[out[..., 3] == 255][:, :3]
    assert {tuple(c) for c in np.unique(opaque, axis=0)} <= PALETTE  # новых цветов не появилось


@pytest.mark.parametrize("state", STATES)
def test_smooth_keeps_size_of_dog(state):
    """Силуэт почти тот же: доля закрашенного отличается от пиксельного не больше чем на 6%."""
    im = draw_chibi_frame(state, 1)
    a = (np.asarray(upscale(im, 5))[..., 3] > 0).mean()
    b = (np.asarray(im.resize((W * 5, H * 5), Image.NEAREST))[..., 3] > 0).mean()
    assert abs(a - b) / b < 0.06


def test_scale2x_rounds_diagonal():
    """Ступенька по диагонали: Scale2x закрашивает внутренний угол."""
    k, t = (0, 0, 0, 255), (0, 0, 0, 0)
    im = Image.new("RGBA", (2, 2))
    im.putdata([k, t, k, k])
    a = np.asarray(scale2x(im))
    assert a.shape == (4, 4, 4) and tuple(a[1, 2]) == k  # угол между двумя чёрными соседями стал чёрным


def test_smooth_from_is_four():
    assert SMOOTH_FROM == 4
