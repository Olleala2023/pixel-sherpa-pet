# -*- coding: utf-8 -*-
"""Обновление Шерпы с GitHub: два способа, выбирается сам.

- git: папка — клон репозитория (`git clone`). Проверка — `git fetch`, обновление — `git pull --ff-only`.
  Свои правки в отслеживаемых файлах или свои неотправленные коммиты — не трогаем, честно говорим почему.
- zip: папка из Download ZIP (нет .git). Проверка — последний коммит main через API GitHub, обновление —
  архив main с codeload.github.com, файлы программы заменяются поверх. Какая версия стоит, помнит файл
  `.sherpa-version` (sha коммита).

В обоих случаях не трогаются: .venv, brain/data (история, аудио, лог), brain/config.toml, модель речи,
ключ (он в Windows). Если поменялся requirements.txt — доустанавливаются пакеты тем же Python.
Модуль без Tk: его зовут из фонового потока пса, а тесты — напрямую.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
REPO = "Olleala2023/pixel-sherpa-pet"
BRANCH = "main"
API = "https://api.github.com/repos/{repo}"
ZIP_URL = "https://codeload.github.com/{repo}/zip/{ref}"
VERSION_FILE = ".sherpa-version"
STATE_FILE = "sherpa-update.json"  # когда проверяли в последний раз (автопроверка раз в сутки)
UA = "sherpa-pet-updater"
# что обновление ZIP не перезаписывает, даже если такое окажется в архиве
PROTECTED = (".venv", ".git", "brain/data", "brain/config.toml", "brain/models/gigaam-v3-e2e", VERSION_FILE,
             STATE_FILE, "sherpa-state.txt", "sherpa-settings.json")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    mode: str                 # git / zip
    current: str | None       # sha установленной версии (None — неизвестна)
    latest: str               # sha последней на GitHub
    behind: int               # сколько коммитов не хватает (-1 — неизвестно, но версия другая)
    changes: list[str] = field(default_factory=list)  # заголовки новых коммитов, новые сверху
    blocked: str | None = None  # почему обновить нельзя (свои правки и т. п.)

    @property
    def available(self) -> bool:
        return self.behind != 0 and self.current != self.latest


# ---------- общее ----------

def detect_mode(root: Path = ROOT) -> str:
    return "git" if (root / ".git").exists() and shutil.which("git") else "zip"


def _git(root: Path, *args: str, timeout: float = 60) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError(f"git не отработал: {e}") from None
    if r.returncode != 0:
        raise UpdateError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def _get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise UpdateError("GitHub временно ограничил запросы — попробуйте через час") from None
        raise UpdateError(f"GitHub ответил HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise UpdateError(f"Нет связи с GitHub: {getattr(e, 'reason', e)}") from None


def install_requirements(root: Path = ROOT) -> None:
    """pip install -r requirements.txt тем же Python, что и пёс (python.exe рядом с pythonw.exe)."""
    exe = Path(sys.executable)
    py = exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" else exe
    r = subprocess.run([str(py if py.exists() else exe), "-m", "pip", "install", "-q", "-r",
                        str(root / "requirements.txt")], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise UpdateError(f"Не поставились пакеты: {(r.stderr or r.stdout).strip()[-300:]}")


# ---------- git ----------

def check_git(root: Path = ROOT) -> UpdateInfo:
    _git(root, "fetch", "--quiet", "origin", BRANCH)
    head, latest = _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", f"origin/{BRANCH}")
    behind = int(_git(root, "rev-list", "--count", f"HEAD..origin/{BRANCH}") or 0)
    ahead = int(_git(root, "rev-list", "--count", f"origin/{BRANCH}..HEAD") or 0)
    changes = _git(root, "log", "--format=%s", f"HEAD..origin/{BRANCH}").splitlines() if behind else []
    info = UpdateInfo("git", head, latest, behind, changes)
    if behind:
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
        if branch != BRANCH:
            info.blocked = f"Папка на ветке «{branch}», а обновления идут в «{BRANCH}»"
        elif dirty:
            info.blocked = "В папке есть свои несохранённые правки — обновление их не затирает. Закоммитьте или отмените их"
        elif ahead:
            info.blocked = f"Есть свои коммиты, которых нет на GitHub ({ahead}) — сначала отправьте их (git push)"
    return info


def apply_git(info: UpdateInfo, root: Path = ROOT) -> bool:
    """Подтягивает обновление. True — поменялся requirements.txt и пакеты доставлены."""
    if info.blocked:
        raise UpdateError(info.blocked)
    before = _git(root, "rev-parse", "HEAD")
    _git(root, "pull", "--ff-only", "--quiet", "origin", BRANCH, timeout=180)
    changed = _git(root, "diff", "--name-only", before, "HEAD").splitlines()
    if "requirements.txt" in changed:
        install_requirements(root)
        return True
    return False


# ---------- zip ----------

def read_version(root: Path = ROOT) -> str | None:
    try:
        return (root / VERSION_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def check_zip(root: Path = ROOT, repo: str = REPO) -> UpdateInfo:
    api = API.format(repo=repo)
    latest = json.loads(_get(f"{api}/commits/{BRANCH}"))["sha"]
    current = read_version(root)
    if current == latest:
        return UpdateInfo("zip", current, latest, 0)
    behind, changes = -1, []
    if current:
        try:
            cmp = json.loads(_get(f"{api}/compare/{current}...{latest}"))
            behind = int(cmp.get("ahead_by", -1))
            changes = [c["commit"]["message"].splitlines()[0] for c in reversed(cmp.get("commits", []))]
        except (UpdateError, KeyError, ValueError):
            pass  # своя версия неизвестна GitHub — просто «есть другая»
    if behind == 0:  # установленная новее main (такого не бывает у обычной установки) — не трогаем
        return UpdateInfo("zip", current, latest, 0)
    return UpdateInfo("zip", current, latest, behind, changes)


def _protected(rel: PurePosixPath) -> bool:
    s = rel.as_posix()
    return any(s == p or s.startswith(p + "/") for p in PROTECTED)


def extract_over(data: bytes, root: Path) -> list[str]:
    """Кладёт файлы из архива GitHub (одна верхняя папка) поверх root. Возвращает изменённые пути."""
    changed = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if not names:
            raise UpdateError("Архив пустой")
        top = names[0].split("/", 1)[0]
        for name in names:
            parts = PurePosixPath(name).parts
            if parts[0] != top or len(parts) < 2:
                raise UpdateError(f"Странный путь в архиве: {name}")
            rel = PurePosixPath(*parts[1:])
            if rel.is_absolute() or ".." in rel.parts or ":" in rel.as_posix():
                raise UpdateError(f"Опасный путь в архиве: {name}")
            if _protected(rel):
                continue
            dst = root.joinpath(*rel.parts)
            new = z.read(name)
            # у клона на Windows концы строк CRLF, в архиве GitHub — LF: это не изменение
            if dst.exists() and dst.read_bytes().replace(b"\r\n", b"\n") == new.replace(b"\r\n", b"\n"):
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".sherpa-new")
            tmp.write_bytes(new)
            tmp.replace(dst)  # замена одним шагом: половинчатого файла не бывает
            changed.append(rel.as_posix())
    return changed


def apply_zip(info: UpdateInfo, root: Path = ROOT, repo: str = REPO) -> bool:
    """Скачивает архив ровно той версии, что проверили, и кладёт поверх. True — доставлены пакеты."""
    data = _get(ZIP_URL.format(repo=repo, ref=info.latest), timeout=120)
    changed = extract_over(data, root)
    (root / VERSION_FILE).write_text(info.latest + "\n", encoding="utf-8")
    if "requirements.txt" in changed:
        install_requirements(root)
        return True
    return False


# ---------- для пса ----------

def check(root: Path = ROOT) -> UpdateInfo:
    return check_git(root) if detect_mode(root) == "git" else check_zip(root)


def apply(info: UpdateInfo, root: Path = ROOT) -> bool:
    return apply_git(info, root) if info.mode == "git" else apply_zip(info, root)


def due_for_auto_check(root: Path = ROOT, every_s: float = 24 * 3600) -> bool:
    """Автопроверка не чаще раза в сутки; отметку ставит mark_checked."""
    try:
        last = json.loads((root / STATE_FILE).read_text(encoding="utf-8")).get("last_check", 0)
    except (OSError, ValueError):
        last = 0
    return time.time() - float(last) >= every_s


def mark_checked(root: Path = ROOT) -> None:
    try:
        (root / STATE_FILE).write_text(json.dumps({"last_check": time.time()}), encoding="utf-8")
    except OSError:
        pass


def describe(info: UpdateInfo) -> str:
    n = f"{info.behind} " + _plural(info.behind, "изменение", "изменения", "изменений") if info.behind > 0 else "новая версия"
    head = "\n".join(f"• {c}" for c in info.changes[:3])
    more = f"\n…и ещё {len(info.changes) - 3}" if len(info.changes) > 3 else ""
    return f"Есть обновление: {n}" + (f"\n{head}{more}" if head else "")


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many
