"""Чистые функции stt; модель распознавания не загружается."""
import time

import numpy as np
import pytest

from brain.config import Config
from brain.stt import SR, BadAudio, Stt, decode_wav, quiet_split

from conftest import make_wav


def test_decode_mono_16k():
    x = decode_wav(make_wav(0.1, value=16384))
    assert x.dtype == np.float32 and len(x) == 1600
    assert np.allclose(x, 0.5)


def test_decode_stereo_averaged():
    x = decode_wav(make_wav(0.1, ch=2, value=8192))
    assert len(x) == 1600 and np.allclose(x, 0.25)


def test_decode_resamples_to_16k():
    x = decode_wav(make_wav(0.5, sr=48000))
    assert abs(len(x) - 8000) <= 1
    assert np.allclose(x, 1000 / 32768, atol=1e-4)


def test_decode_empty_wav():
    assert len(decode_wav(make_wav(0.0))) == 0


def test_decode_not_wav():
    with pytest.raises(BadAudio):
        decode_wav(b"hello")
    with pytest.raises(BadAudio):
        decode_wav(b"")


def test_decode_8bit_rejected():
    with pytest.raises(BadAudio):
        decode_wav(make_wav(sw=1))


def test_decode_truncated_odd_byte():
    with pytest.raises(BadAudio):
        decode_wav(make_wav(0.1)[:-1])


def test_quiet_split_short_untouched():
    x = np.ones(SR * 5, dtype=np.float32)
    parts = quiet_split(x)
    assert len(parts) == 1 and len(parts[0]) == len(x)


def test_quiet_split_cuts_at_quietest():
    rng = np.random.default_rng(0)
    x = rng.uniform(-0.5, 0.5, SR * 30).astype(np.float32)
    x[int(15 * SR):int(15.3 * SR)] = 0  # тихое окно
    parts = quiet_split(x)
    assert sum(len(p) for p in parts) == len(x)
    assert all(len(p) <= 20 * SR for p in parts) and len(parts) >= 2
    assert 14.9 * SR < len(parts[0]) < 15.4 * SR


def test_stt_unload_after_idle():
    s = Stt(Config(stt_idle_unload_s=300))
    assert not s.loaded
    s.rec = object()
    s.last_used = time.monotonic()
    s.maybe_unload()
    assert s.loaded
    s.last_used -= 1000
    s.maybe_unload()
    assert not s.loaded


def test_decode_stereo_cut_mid_frame():
    """Стерео, оборванное между каналами одного кадра, — тоже понятная ошибка."""
    with pytest.raises(BadAudio):
        decode_wav(make_wav(0.1, ch=2)[:-2])
