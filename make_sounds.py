# -*- coding: utf-8 -*-
"""
Синтез звуков-кандидатов для Шерпы: «подняли» и «упал на панель задач».
Пишет WAV в sounds/candidates/ и страницу sounds/прослушать.html.
Нужен только numpy. Запуск: py -3 make_sounds.py
"""
import wave
from pathlib import Path

import numpy as np

SR = 22050
OUT = Path(__file__).resolve().parent / "sounds" / "candidates"


def t_axis(dur):
    return np.arange(int(SR * dur)) / SR


def sweep(f0, f1, dur, shape="sine", curve=1.0):
    """Тон с плавно меняющейся частотой f0 -> f1."""
    t = t_axis(dur)
    k = (t / dur) ** curve
    freq = f0 + (f1 - f0) * k
    phase = 2 * np.pi * np.cumsum(freq) / SR
    if shape == "square":
        return np.sign(np.sin(phase)) * 0.6
    if shape == "tri":
        return 2 / np.pi * np.arcsin(np.sin(phase))
    return np.sin(phase)


def env(n, attack=0.005, decay=None, power=1.0):
    """Огибающая: быстрая атака и спад к нулю."""
    e = np.ones(n)
    a = max(1, int(SR * attack))
    e[:a] = np.linspace(0, 1, a)
    tail = np.linspace(1, 0, n - a) ** power
    e[a:] = tail
    return e


def noise(dur, lowpass=0.0):
    x = np.random.default_rng(7).uniform(-1, 1, int(SR * dur))
    if lowpass > 0:
        # простой однополюсный ФНЧ: чем больше lowpass, тем глуше
        y = np.zeros_like(x)
        for i in range(1, len(x)):
            y[i] = y[i - 1] + (1 - lowpass) * (x[i] - y[i - 1])
        x = y / (np.max(np.abs(y)) or 1)
    return x


def cat(*parts, gap=0.0):
    sil = np.zeros(int(SR * gap))
    out = []
    for p in parts:
        out += [p, sil]
    return np.concatenate(out[:-1]) if out else np.zeros(0)


def mix(a, b, offset=0.0):
    off = int(SR * offset)
    n = max(len(a), off + len(b))
    y = np.zeros(n)
    y[: len(a)] += a
    y[off: off + len(b)] += b
    return y


def save(name, x, gain=0.8):
    x = x / (np.max(np.abs(x)) or 1) * gain
    pcm = (x * 32767).astype(np.int16)
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------- «Подняли» ----------

def up_chirp():
    d = 0.14
    return sweep(500, 1400, d, "square") * env(int(SR * d), power=1.5)


def up_whee():
    d = 0.38
    t = t_axis(d)
    vib = 1 + 0.04 * np.sin(2 * np.pi * 22 * t)
    base = sweep(600, 1500, d, "sine", curve=0.6)
    phase = 2 * np.pi * np.cumsum((600 + 900 * (t / d) ** 0.6) * vib) / SR
    return (0.7 * np.sin(phase) + 0.3 * base) * env(len(t), attack=0.02, power=0.7)


def up_robot():
    a = sweep(880, 880, 0.06, "square") * env(int(SR * 0.06), power=0.5)
    b = sweep(1320, 1320, 0.08, "square") * env(int(SR * 0.08), power=0.8)
    return cat(a, b, gap=0.03)


# ---------- «Упал» ----------

def land_boing():
    d = 0.6
    t = t_axis(d)
    wobble = 1 + 0.35 * np.exp(-t * 7) * np.sin(2 * np.pi * 14 * t)
    freq = 170 * wobble
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return np.sin(phase) * env(len(t), attack=0.002, power=1.6)


def land_bonk():
    d = 0.22
    body = sweep(140, 55, d, "sine", curve=0.5) * env(int(SR * d), attack=0.001, power=2.2)
    hit = noise(0.04, lowpass=0.85) * env(int(SR * 0.04), attack=0.001, power=3)
    return mix(body, hit * 0.6)


def land_8bit():
    notes = [784, 523, 392]
    parts = [sweep(f, f, 0.05, "square") * env(int(SR * 0.05), power=0.6) for f in notes]
    thump = noise(0.09, lowpass=0.6) * env(int(SR * 0.09), attack=0.001, power=2) * 0.7
    return cat(*parts, thump, gap=0.01)


def land_squeak():
    d = 0.22
    a = sweep(1900, 1300, d, "tri", curve=0.4) * env(int(SR * d), attack=0.01, power=1.2)
    b = sweep(1500, 1100, 0.16, "tri", curve=0.4) * env(int(SR * 0.16), attack=0.01, power=1.2)
    return cat(a, b, gap=0.04)


def land_plop():
    d = 0.12
    return sweep(280, 950, d, "sine", curve=0.35) * env(int(SR * d), attack=0.002, power=2)


def fall_whistle_bonk():
    d = 0.75
    t = t_axis(d)
    vib = 1 + 0.015 * np.sin(2 * np.pi * 6 * t)
    freq = (1500 - 1050 * (t / d) ** 1.2) * vib
    whistle = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.minimum(1, t / 0.05) * 0.55
    return cat(whistle, land_bonk(), gap=0.0)


def land_boing_bounce():
    """Отскок: большой boing, пауза, маленький boing."""
    big = land_boing()
    small = land_boing()[: int(SR * 0.3)] * 0.45
    return cat(big[: int(SR * 0.35)], small, gap=0.08)


# ---------- Вторая волна: вокруг выбранных whee + bonk, легче и тише ----------

def whee(f0, f1, dur, vib_depth=0.04, vib_rate=22, curve=0.6, attack=0.02, power=0.7):
    t = t_axis(dur)
    vib = 1 + vib_depth * np.sin(2 * np.pi * vib_rate * t)
    freq = (f0 + (f1 - f0) * (t / dur) ** curve) * vib
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * env(len(t), attack=attack, power=power)


def up_whee_short():
    return whee(700, 1500, 0.22, vib_depth=0.03, power=1.0)


def up_whee_soft():
    return whee(500, 1100, 0.30, vib_depth=0.015, vib_rate=10, curve=0.8, attack=0.04, power=0.9)


def up_whee_double():
    a = whee(650, 1150, 0.12, vib_depth=0.02, power=1.2)
    b = whee(800, 1450, 0.16, vib_depth=0.03, power=1.0)
    return cat(a, b, gap=0.04)


def bonk(f0, f1, dur, noise_amt=0.6, power=2.2):
    body = sweep(f0, f1, dur, "sine", curve=0.5) * env(int(SR * dur), attack=0.001, power=power)
    hit = noise(0.03, lowpass=0.85) * env(int(SR * 0.03), attack=0.001, power=3)
    return mix(body, hit * noise_amt)


def land_bonk_light():
    return bonk(230, 110, 0.14, noise_amt=0.3)


def land_tok():
    """Деревянное «ток»: короткий тон и щелчок."""
    body = sweep(620, 420, 0.06, "sine", curve=0.4) * env(int(SR * 0.06), attack=0.001, power=2.5)
    click = noise(0.012) * env(int(SR * 0.012), attack=0.0005, power=2) * 0.5
    return mix(body, click)


def land_bonk_pop():
    """Бонк и сразу маленький «поп» вверх — как будто подпрыгнул."""
    return cat(bonk(200, 95, 0.12, noise_amt=0.3), land_plop() * 0.5, gap=0.05)


def bounce_tuk():
    """Тихий звук на отскок: короче и выше бонка."""
    return bonk(320, 200, 0.06, noise_amt=0.15, power=2.5)


def bounce_bonk():
    """Отскок: тот же «бонк», что основной, только вдвое короче (тише задаётся громкостью)."""
    d = 0.11
    body = sweep(140, 90, d, "sine", curve=0.5) * env(int(SR * d), attack=0.001, power=2.2)
    hit = noise(0.025, lowpass=0.85) * env(int(SR * 0.025), attack=0.001, power=3)
    return mix(body, hit * 0.6)


QUIET = 0.35   # новые звуки заметно тише первых (0.8): не пугают
BOUNCE = 0.2

# (файл, функция, группа, описание, громкость, новый?)
SOUNDS_NEW = [
    ("up_2a_whee_quiet.wav", up_whee, "Подняли", "Выбранное «уииии», только тише", QUIET),
    ("up_2b_whee_short.wav", up_whee_short, "Подняли", "Короче и бодрее «уи!»", QUIET),
    ("up_2c_whee_soft.wav", up_whee_soft, "Подняли", "Мягкое, ниже и плавнее", QUIET),
    ("up_2d_whee_double.wav", up_whee_double, "Подняли", "Двойное «уи-уи»", QUIET),
    ("land_2a_bonk_quiet.wav", land_bonk, "Упал", "Выбранный «бонк», только тише", QUIET),
    ("land_2b_bonk_light.wav", land_bonk_light, "Упал", "Лёгкий бонк: выше и короче", QUIET),
    ("land_2c_tok.wav", land_tok, "Упал", "Деревянное «ток»", QUIET),
    ("land_2d_bonk_pop.wav", land_bonk_pop, "Упал", "Бонк и маленький «поп»", QUIET),
    ("bounce_tuk.wav", bounce_tuk, "Отскок", "Тихий «тук» на маленький отскок", BOUNCE),
    ("bounce_bonk.wav", bounce_bonk, "Отскок", "Тот же «бонк», вдвое короче и тише", 0.15),
]

CHOSEN = {
    "land_2a_bonk_quiet.wav": "sherpa_land.wav",
    "bounce_bonk.wav": "sherpa_bounce.wav",
}

SOUNDS = [
    ("up_1_chirp.wav", up_chirp, "Подняли", "Короткий 8-бит «пиу» вверх"),
    ("up_2_whee.wav", up_whee, "Подняли", "Мультяшное «уииии»"),
    ("up_3_robot.wav", up_robot, "Подняли", "Робот «бип-БИП»"),
    ("land_1_boing.wav", land_boing, "Упал", "Пружина «бойнннг»"),
    ("land_2_bonk.wav", land_bonk, "Упал", "Глухой «бонк» об панель"),
    ("land_3_8bit.wav", land_8bit, "Упал", "8-бит: три ноты вниз и удар"),
    ("land_4_squeak.wav", land_squeak, "Упал", "Пищалка, как резиновая игрушка"),
    ("land_5_plop.wav", land_plop, "Упал", "Капля «плоп»"),
    ("land_6_bounce.wav", land_boing_bounce, "Упал", "Пружина с отскоком"),
    ("fall_7_whistle_bonk.wav", fall_whistle_bonk, "Падение целиком", "Свист вниз и «бонк» в конце"),
]


def write_page():
    # Звук встраивается прямо в страницу (data:URI): окно просмотра может не открыть
    # соседние файлы по относительному пути, а так страница самодостаточна.
    import base64

    def row(name, group, desc):
        b64 = base64.b64encode((OUT / name).read_bytes()).decode("ascii")
        return (
            f'<tr><td>{group}</td><td><b>{name}</b><br>{desc}</td>'
            f'<td><audio controls preload="auto" src="data:audio/wav;base64,{b64}"></audio></td></tr>'
        )

    rows = ['<tr><th colspan="3">Новые: вокруг «уииии» и «бонк», тише</th></tr>']
    rows += [row(n, g, d) for n, _, g, d, _ in SOUNDS_NEW]
    rows.append('<tr><th colspan="3">Первая волна (громче)</th></tr>')
    rows += [row(n, g, d) for n, _, g, d in SOUNDS]
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Звуки Шерпы</title>
<style>
body{{font:16px system-ui,sans-serif;margin:24px;background:#f6f9fc;color:#1c2836}}
table{{border-collapse:collapse}} td{{padding:10px 14px;border-bottom:1px solid #dce6ef;vertical-align:middle}}
td:first-child{{color:#3298d4;font-weight:600}}
th{{text-align:left;padding:22px 14px 8px;font-size:18px}}
</style></head><body>
<h1>Звуки Шерпы: кандидаты</h1>
<p>Послушай и назови номера: один для «подняли», один для «упал».</p>
<table>{''.join(rows)}</table>
</body></html>"""
    (OUT.parent / "прослушать.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    for name, fn, _, _ in SOUNDS:
        save(name, fn())
    for name, fn, _, _, gain in SOUNDS_NEW:
        save(name, fn(), gain=gain)
    write_page()
    # Выбранные для Шерпы — под постоянными именами, их читает sherpa_pet.py
    import shutil
    for src, dst in CHOSEN.items():
        shutil.copyfile(OUT / src, OUT.parent / dst)
        print("Шерпа:", dst, "<-", src)
    for p in sorted(OUT.glob("*.wav")):
        with wave.open(str(p)) as w:
            print(f"{p.name:28s} {w.getnframes() / w.getframerate():.2f} s")
