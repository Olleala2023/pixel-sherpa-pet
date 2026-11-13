# -*- coding: utf-8 -*-
"""
Генератор пакета пиксельных спрайтов Чиби-пса Sherpa:
1) sherpa-chibi-pack/frames/<state>_<frame>.png (оригинальные 30x28 и увеличенные 4x)
2) sherpa-chibi-pack/gifs/sherpa_<state>.gif (анимированные GIF)
3) sherpa-chibi-pack/shimeji/img/shime1..46.png (формат для claude-pet / Shimeji)
4) sherpa.ico (иконка с точным целочисленным масштабом 32x32 -> 256x256)
"""
from pathlib import Path
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
PACK_DIR = BASE_DIR / "sherpa-chibi-pack"

COLORS = {
    "K": (28, 40, 54, 255),      # Тёмно-синий контур
    "w": (246, 249, 252, 255),   # Белый корпус
    "h": (255, 255, 255, 255),   # Чистый белый блик
    "s": (220, 230, 239, 255),   # Светло-серая тень на животике
    "e": (96, 112, 137, 255),    # Сине-серые уши и хвост
    "E": (70, 85, 108, 255),     # Тёмная часть уха и основание хвоста
    "b": (50, 152, 212, 255),    # Фирменный голубой Sherpa
    "B": (29, 119, 173, 255),    # Насыщенный синий (нос, акценты)
    "o": (123, 213, 245, 255),   # Светло-голубое свечение антенны
    "p": (15, 23, 34, 255),      # Чёрный зрачок и улыбка
    "z": (91, 192, 235, 255),    # Голубые буквы z и точки
}

W, H = 30, 28


def make_grid(w=W, h=H):
    return [["." for _ in range(w)] for _ in range(h)]


def pix(g, x, y, c):
    if 0 <= y < len(g) and 0 <= x < len(g[0]):
        g[y][x] = c


def fill(g, x, y, w, h, c):
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            pix(g, xx, yy, c)


def set_rows(g, ox, oy, rows):
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            if ch != ".":
                pix(g, ox + x, oy + y, ch)


def draw_chibi_frame(state: str, frame: int, flip_x: bool = False, blink=None, wag=None) -> Image.Image:
    g = make_grid(W, H)
    if wag is None:
        wag = frame % 2 == 0
    if blink is None:
        blink = state not in ("sleep", "jump") and frame == 3
    y_off = 0
    if state == "jump":
        y_off = [0, -2, -3, -1][frame % 4]
    elif state == "walk":
        y_off = -1 if frame % 2 == 1 else 0

    by = 3 + y_off
    ox = 3

    # Базовый корпус, голова и обе нижние ножки (18x23)
    base = [
        "........KK........",  # 0: верхушка антенны
        ".......KooK.......",  # 1: шарик антенны
        "........KK........",  # 2: стержень
        ".....KKKKKKKK.....",  # 3: макушка
        "...KKhhhhhhhhKK...",  # 4
        "..KhhwwwwwwwwhhK..",  # 5
        ".KKwwwwwwwwwwwwKK.",  # 6
        "KeeKwwbbbbbbwwKeeK",  # 7: уши + голубые маски глаз
        "KeeKwbpphwbpphKeeK",  # 8
        "KeeKwbpppwbpppKeeK",  # 9
        "KEEewbbbbwbbbbweEK",  # 10
        "KeeewwwwwBBwwwwweK",  # 11: голубой носик
        ".KKwwwwwwwwwwwwKK.",  # 12: улыбка
        "...KKbbbbbbbbKK...",  # 13: голубой ошейник
        "....KwwwwwwwwK....",  # 14: грудь
        "....KwwwbbwwwK....",  # 15: шестерёнка верх
        "....KwwbhhbwwK....",  # 16: шестерёнка центр
        "....KwwwbbwwwK....",  # 17: шестерёнка низ
        "....KssssssssK....",  # 18: животик (отделяет верхние лапки от хвоста)
        "....KwwKKKKwwK....",  # 19: обе нижние ножки (всегда на месте!)
        "....KbbK..KbbK....",  # 20: голубые суставы
        "....KwwK..KwwK....",  # 21: стопы
        "....KKKK..KKKK....",  # 22: подошва
    ]
    set_rows(g, ox, by, base)

    # ХВОСТ: выходит из левого бедра (ox..ox+4, строки by+19..by+21),
    # плотно примыкает к корпусу на ox+4 и отделён от левой верхней лапки строкой by+18
    if wag:
        set_rows(g, ox, by + 18, [
            "KK...",
            "KeeKK",
            "KeeeK",
            ".KKKK",
        ])
    else:
        set_rows(g, ox, by + 19, [
            ".KKK.",
            "KeeeK",
            ".KKKK",
        ])

    # ВЕРХНИЕ ЛАПКИ (РУКИ) ПО БОКАМ ТУЛОВИЩА — широкие (2px внутри контура: KbbK / KwwK)
    if state == "jump":
        # В прыжке обе верхние лапки радостно вскинуты вверх в стороны
        set_rows(g, ox, by + 12, [
            ".KK..",
            "KwwK.",
            "KbbK.",
            ".KKKK",
        ])
        set_rows(g, ox + 13, by + 12, [
            "..KK.",
            ".KwwK",
            ".KbbK",
            "KKKK.",
        ])
    else:
        # Левая верхняя лапка (ox+1..ox+4, строки by+14..by+17)
        set_rows(g, ox + 1, by + 14, [
            ".KKK",
            "KbbK",
            "KwwK",
            ".KKK",
        ])

        # Правая верхняя лапка (зависит от позы; обе нижние ножки всегда стоят внизу!)
        if state == "wave":
            if frame % 2 == 0:
                # Поднята вверх и машет
                set_rows(g, ox + 13, by + 11, [
                    "..KK.",
                    ".KwwK",
                    "KKbbK",
                    "KwwwK",
                    "KKKK.",
                ])
            else:
                # Чуть ниже в махе
                set_rows(g, ox + 13, by + 13, [
                    ".KKK.",
                    "KbbwK",
                    "KwwwK",
                    "KKKK.",
                ])
        elif state == "look":
            # Правая верхняя лапка держит экран чата
            set_rows(g, ox + 13, by + 14, [
                "KKKKKK",
                "Kbbwwb",
                "KKKKKK",
            ])
        elif state == "think":
            # Правая верхняя лапка поднята к щеке
            set_rows(g, ox + 13, by + 12, [
                ".KK.",
                "KwwK",
                "KbbK",
                "KKK.",
            ])
        else:
            # Обычное положение правой верхней лапки (симметрично левой: ox+13..ox+16)
            set_rows(g, ox + 13, by + 14, [
                "KKK.",
                "KbbK",
                "KwwK",
                "KKK.",
            ])

    # Переносица и круги вокруг глаз
    fill(g, ox + 6, by + 7, 4, 4, "b")
    fill(g, ox + 12, by + 7, 4, 4, "b")
    fill(g, ox + 10, by + 7, 2, 3, "w")

    # Носик с бликом
    fill(g, ox + 10, by + 10, 2, 2, "B")
    pix(g, ox + 10, by + 10, "o")

    # Анимация шага нижних ножек (обе ножки всегда видны целиком!)
    if state == "walk":
        if frame % 4 == 0:
            fill(g, ox + 4, by + 19, 10, 4, ".")
            set_rows(g, ox + 4, by + 19, [
                "KbbKKKKwwK",
                "KwwK..KbbK",
                "KKKK..KwwK",
                "......KKKK",
            ])
        elif frame % 4 == 2:
            fill(g, ox + 4, by + 19, 10, 4, ".")
            set_rows(g, ox + 4, by + 19, [
                "KwwKKKKbbK",
                "KbbK..KwwK",
                "KwwK..KKKK",
                "KKKK......",
            ])

    # Глаза
    if state == "sleep" or blink:
        fill(g, ox + 6, by + 7, 4, 4, "b")
        fill(g, ox + 12, by + 7, 4, 4, "b")
        fill(g, ox + 7, by + 9, 2, 1, "p")
        fill(g, ox + 13, by + 9, 2, 1, "p")
    elif state == "jump":
        pix(g, ox + 7, by + 8, "p")
        pix(g, ox + 8, by + 8, "p")
        pix(g, ox + 6, by + 9, "p")
        pix(g, ox + 9, by + 9, "p")
        pix(g, ox + 13, by + 8, "p")
        pix(g, ox + 14, by + 8, "p")
        pix(g, ox + 12, by + 9, "p")
        pix(g, ox + 15, by + 9, "p")
    elif state in ("think", "drag"):
        fill(g, ox + 7, by + 7, 2, 2, "p")
        fill(g, ox + 13, by + 7, 2, 2, "p")
        pix(g, ox + 7, by + 7, "h")
        pix(g, ox + 13, by + 7, "h")
    else:
        fill(g, ox + 7, by + 8, 2, 2, "p")
        fill(g, ox + 13, by + 8, 2, 2, "p")
        pix(g, ox + 7, by + 8, "h")
        pix(g, ox + 13, by + 8, "h")

    # Улыбка
    if state == "sleep":
        pix(g, ox + 10, by + 12, "p")
        pix(g, ox + 11, by + 12, "p")
    elif state in ("jump", "drag"):
        fill(g, ox + 9, by + 12, 4, 1, "p")
        pix(g, ox + 8, by + 11, "p")
        pix(g, ox + 13, by + 11, "p")
    else:
        pix(g, ox + 8, by + 11, "p")
        pix(g, ox + 9, by + 12, "p")
        pix(g, ox + 10, by + 12, "p")
        pix(g, ox + 11, by + 11, "p")

    # Мигание антенны
    if frame % 2 == 1 and state in ("think", "jump", "wave"):
        fill(g, ox + 8, by + 1, 2, 1, "h")

    # Вращение шестерёнки
    if frame % 2 == 1:
        pix(g, ox + 9, by + 15, "b")
        pix(g, ox + 12, by + 17, "b")

    # Экран чата (look): ox + 19 = 22, ширина 6 -> x = 22..27
    if state == "look":
        set_rows(g, ox + 19, by + 9, [
            ".KKKK.",
            "KbbbbK",
            "KbhhbK",
            "KbhbbK",
            "KbbbbK",
            "KbhhbK",
            "KbbbbK",
            ".KKKK.",
        ])

    # Точки размышления (think)
    if state == "think":
        shift = frame % 2
        pix(g, ox + 18, by + 5 - shift, "z")
        pix(g, ox + 20, by + 3 - shift, "z")
        pix(g, ox + 22, by + 1 - shift, "o")

    # Буквы z во сне (sleep)
    if state == "sleep":
        shift = frame % 2
        set_rows(g, ox + 18, by + 4 - shift, [
            "zz",
            ".z",
            "zz",
        ])
        pix(g, ox + 22, by + 1 - shift, "z")

    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = im.load()
    for y in range(H):
        for x in range(W):
            ch = g[y][x]
            if ch in COLORS:
                px[x, y] = COLORS[ch]

    if flip_x:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    return im


def build_pack():
    frames_dir = PACK_DIR / "frames"
    gifs_dir = PACK_DIR / "gifs"
    shime_dir = PACK_DIR / "shimeji" / "img"
    for d in (frames_dir, gifs_dir, shime_dir):
        d.mkdir(parents=True, exist_ok=True)

    states = ["idle", "walk", "wave", "think", "jump", "sleep", "look", "drag"]

    for st in states:
        flist = []
        for f in range(4):
            im = draw_chibi_frame(st, f, flip_x=False)
            im.save(frames_dir / f"{st}_{f}.png")
            im_4x = im.resize((W * 4, H * 4), Image.NEAREST)
            im_4x.save(frames_dir / f"{st}_{f}_4x.png")
            flist.append(im_4x)

        flist[0].save(
            gifs_dir / f"sherpa_{st}.gif",
            save_all=True,
            append_images=flist[1:],
            duration=220,
            loop=0,
            disposal=2,
        )

    def save_shime(idx: int, state: str, f: int):
        im = draw_chibi_frame(state, f % 4)
        scaled = im.resize((W * 4, H * 4), Image.NEAREST)
        canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        ox = (128 - scaled.width) // 2
        oy = 128 - scaled.height
        canvas.alpha_composite(scaled, (ox, oy))
        canvas.save(shime_dir / f"shime{idx}.png")

    for i in range(1, 47):
        if i in (1, 2, 3):
            save_shime(i, "walk", i - 1)
        elif i == 4:
            save_shime(i, "jump", 1)
        elif 5 <= i <= 10:
            save_shime(i, "drag", i)
        elif i == 11:
            save_shime(i, "idle", 0)
        elif 12 <= i <= 14:
            save_shime(i, "walk", i)
        elif 15 <= i <= 17:
            save_shime(i, "wave", i)
        elif 18 <= i <= 21:
            save_shime(i, "sleep", i)
        elif i in (22, 37):
            save_shime(i, "jump", 2)
        elif 26 <= i <= 29:
            save_shime(i, "jump", i)
        elif 30 <= i <= 33:
            save_shime(i, "look", i)
        elif 38 <= i <= 43:
            save_shime(i, "think", i)
        else:
            save_shime(i, "idle", i)

    im_ico = draw_chibi_frame("idle", 0)
    canvas32 = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    canvas32.alpha_composite(im_ico, (1, 2))
    ico_256 = canvas32.resize((256, 256), Image.NEAREST)
    ico_path = BASE_DIR / "sherpa.ico"
    ico_256.save(ico_path, format="ICO", sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])

    print("Pack & ICO generated successfully at:", PACK_DIR)


if __name__ == "__main__":
    build_pack()
