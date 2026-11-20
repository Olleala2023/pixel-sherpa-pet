# -*- coding: utf-8 -*-
"""
Экспорт анимаций пса Шерпы в форматы Telegram -> assets/telegram/
  stickers/sherpa_<поза>.webm   видеостикеры 512x512
  emoji/sherpa_<поза>.webm      custom emoji 100x100
  stickers_static/sherpa_<поза>.png (512x512) и icon_100.png
Кадры берутся из sherpa.sprites.draw_chibi_frame так же, как для GIF
(4 кадра на позу, 220 мс на кадр, blink/wag по умолчанию).
WebM: VP9 yuva420p, без звука, <=256 КБ. Нужен ffmpeg (PATH или imageio_ffmpeg).
Запуск из корня проекта: python tools/telegram_export.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sherpa.sprites import draw_chibi_frame, W, H  # noqa: E402

OUT = ROOT / "assets" / "telegram"
STATES = ["idle", "walk", "wave", "think", "jump", "sleep", "look"]
FRAMES = 4
FRAME_SEC = 0.22
MAX_BYTES = 256 * 1024


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def render(state, frame, size):
    """Пёс, увеличенный NEAREST в целое число раз, по центру/низу холста size x size."""
    im = draw_chibi_frame(state, frame)
    k = size // max(W, H)
    scaled = im.resize((W * k, H * k), Image.NEAREST)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(scaled, ((size - scaled.width) // 2, size - scaled.height))
    return canvas


def encode(ffmpeg, state, size, dst, tmp):
    d = Path(tmp) / f"{state}_{size}"
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for f in range(FRAMES):
        p = d / f"f{f}.png"
        render(state, f, size).save(p)
        lines.append(f"file '{p.as_posix()}'\nduration {FRAME_SEC}")
    lines.append(f"file '{(d / f'f{FRAMES - 1}.png').as_posix()}'")  # хвост для concat
    lst = d / "list.txt"
    lst.write_text("\n".join(lines), encoding="utf-8")
    dst.parent.mkdir(parents=True, exist_ok=True)
    for crf in (30, 36, 42, 50):
        cmd = [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
               "-vf", "format=rgba", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
               "-b:v", "0", "-crf", str(crf), "-auto-alt-ref", "0", "-an",
               "-fps_mode", "vfr", "-metadata:s:v:0", "alpha_mode=1", str(dst)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("ffmpeg ошибка:", r.stderr)
            return False
        if dst.stat().st_size <= MAX_BYTES:
            return True
    print(f"ВНИМАНИЕ: {dst.name} больше 256 КБ")
    return False


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stickers_static").mkdir(exist_ok=True)
    for st in STATES:
        render(st, 0, 512).save(OUT / "stickers_static" / f"sherpa_{st}.png")
    render("idle", 0, 100).save(OUT / "icon_100.png")
    print("PNG: готово")

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("нет ffmpeg: поставьте ffmpeg (например, `choco install ffmpeg` или "
              "`pip install imageio-ffmpeg`) и запустите скрипт снова — WebM не создан.")
        return 1
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for st in STATES:
            ok &= encode(ffmpeg, st, 512, OUT / "stickers" / f"sherpa_{st}.webm", tmp)
            ok &= encode(ffmpeg, st, 100, OUT / "emoji" / f"sherpa_{st}.webm", tmp)
    print("WebM: готово" if ok else "WebM: есть проблемы")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
