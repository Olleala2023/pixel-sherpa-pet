"""Обновление с GitHub (sherpa/updater.py): git — на настоящих временных репозиториях, zip — на архиве в памяти.
В сеть не ходим, настоящую папку проекта не трогаем."""
import io
import json
import subprocess
import zipfile

import pytest

from sherpa import updater as up


# ---------- git ----------

def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, name, text, msg):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def repos(tmp_path):
    """origin (bare) ← author (пишет новые версии); user — клон, как у пользователя."""
    origin, author, user = tmp_path / "origin.git", tmp_path / "author", tmp_path / "user"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(author)], check=True, capture_output=True)
    for r in (author,):
        git(r, "config", "user.email", "t@t"), git(r, "config", "user.name", "t")
        git(r, "checkout", "-q", "-b", "main")
    commit(author, "app.py", "v1\n", "первая версия")
    commit(author, "requirements.txt", "numpy\n", "пакеты")
    git(author, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(user)], check=True, capture_output=True)
    git(user, "config", "user.email", "u@u"), git(user, "config", "user.name", "u")
    return origin, author, user


def test_git_up_to_date(repos):
    _, _, user = repos
    info = up.check_git(user)
    assert info.mode == "git" and info.behind == 0 and not info.available and info.blocked is None


def test_git_update_available_and_applied(repos, monkeypatch):
    _, author, user = repos
    commit(author, "app.py", "v2\n", "вторая версия")
    commit(author, "sherpa/new.py", "x\n", "новый модуль")
    git(author, "push", "-q", "origin", "main")
    info = up.check_git(user)
    assert info.available and info.behind == 2 and info.changes == ["новый модуль", "вторая версия"]
    called = []
    monkeypatch.setattr(up, "install_requirements", lambda root: called.append(root))
    assert up.apply_git(info, user) is False and called == []  # requirements не менялся — pip не зовём
    assert (user / "app.py").read_text(encoding="utf-8") == "v2\n" and (user / "sherpa/new.py").exists()
    assert up.check_git(user).behind == 0


def test_git_requirements_changed_runs_pip(repos, monkeypatch):
    _, author, user = repos
    commit(author, "requirements.txt", "numpy\nrequests\n", "новый пакет")
    git(author, "push", "-q", "origin", "main")
    called = []
    monkeypatch.setattr(up, "install_requirements", lambda root: called.append(root))
    assert up.apply_git(up.check_git(user), user) is True and called == [user]


def test_git_dirty_blocks(repos):
    _, author, user = repos
    commit(author, "app.py", "v2\n", "вторая версия")
    git(author, "push", "-q", "origin", "main")
    (user / "app.py").write_text("моя правка\n", encoding="utf-8")
    info = up.check_git(user)
    assert info.available and "правки" in info.blocked
    with pytest.raises(up.UpdateError):
        up.apply_git(info, user)
    assert (user / "app.py").read_text(encoding="utf-8") == "моя правка\n"  # не затёрто


def test_git_untracked_files_do_not_block(repos):
    _, author, user = repos
    commit(author, "app.py", "v2\n", "вторая версия")
    git(author, "push", "-q", "origin", "main")
    (user / "sherpa-state.txt").write_text("idle", encoding="utf-8")
    assert up.check_git(user).blocked is None


def test_git_local_commits_block(repos):
    _, author, user = repos
    commit(user, "mine.py", "1\n", "свой коммит")
    commit(author, "app.py", "v2\n", "вторая версия")
    git(author, "push", "-q", "origin", "main")
    info = up.check_git(user)
    assert info.available and "коммиты" in info.blocked


def test_git_other_branch_blocks(repos):
    _, author, user = repos
    git(user, "checkout", "-q", "-b", "опыт")
    commit(author, "app.py", "v2\n", "вторая версия")
    git(author, "push", "-q", "origin", "main")
    assert "ветке" in up.check_git(user).blocked


def test_detect_mode(tmp_path, repos):
    _, _, user = repos
    assert up.detect_mode(user) == "git" and up.detect_mode(tmp_path / "author" / "..") in ("git", "zip")
    (tmp_path / "zipinstall").mkdir()
    assert up.detect_mode(tmp_path / "zipinstall") == "zip"


# ---------- zip ----------

def make_zip(files: dict[str, str], top="pixel-sherpa-pet-abc123") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(top + "/", "")
        for name, text in files.items():
            z.writestr(name if name.startswith(("/", "..", top)) else f"{top}/{name}", text)
    return buf.getvalue()


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "Шерпа"
    (root / "brain" / "data").mkdir(parents=True)
    (root / "sherpa").mkdir()
    (root / "sherpa" / "pet.py").write_text("старый", encoding="utf-8")
    (root / "brain" / "config.toml").write_text("мой конфиг", encoding="utf-8")
    (root / "brain" / "data" / "brain.db").write_text("моя история", encoding="utf-8")
    (root / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    return root


def test_extract_over_replaces_and_protects(install):
    data = make_zip({"sherpa/pet.py": "новый", "sherpa/bubble.py": "облачко", "requirements.txt": "numpy\n",
                     "brain/config.toml": "чужой конфиг", "brain/data/brain.db": "чужая база",
                     ".venv/x": "y", ".sherpa-version": "подделка"})
    changed = up.extract_over(data, install)
    assert sorted(changed) == ["sherpa/bubble.py", "sherpa/pet.py"]  # requirements тот же — не тронут
    assert (install / "sherpa/pet.py").read_text(encoding="utf-8") == "новый"
    assert (install / "brain/config.toml").read_text(encoding="utf-8") == "мой конфиг"
    assert (install / "brain/data/brain.db").read_text(encoding="utf-8") == "моя история"
    assert not (install / ".venv").exists() and not (install / ".sherpa-version").exists()
    assert not list(install.rglob("*.sherpa-new"))


@pytest.mark.parametrize("bad", ["../evil.py", "/abs.py", "pixel-sherpa-pet-abc123/../../evil.py",
                                 "pixel-sherpa-pet-abc123/C:/evil.py"])
def test_extract_over_rejects_unsafe_paths(install, bad):
    with pytest.raises(up.UpdateError):
        up.extract_over(make_zip({"sherpa/pet.py": "новый", bad: "зло"}), install)
    assert not (install.parent / "evil.py").exists()


class FakeGitHub:
    """Подмена _get: API коммитов/сравнения и архив."""

    def __init__(self, latest="bbb", compare=None, archive=b""):
        self.latest, self.compare, self.archive, self.urls = latest, compare, archive, []

    def __call__(self, url, timeout=20):
        self.urls.append(url)
        if url.endswith("/commits/main"):
            return json.dumps({"sha": self.latest}).encode()
        if "/compare/" in url:
            if self.compare is None:
                raise up.UpdateError("HTTP 404")
            return json.dumps(self.compare).encode()
        if "codeload" in url:
            return self.archive
        raise AssertionError(url)


def test_zip_check_up_to_date(install, monkeypatch):
    (install / up.VERSION_FILE).write_text("bbb\n", encoding="utf-8")
    monkeypatch.setattr(up, "_get", FakeGitHub())
    info = up.check_zip(install)
    assert info.behind == 0 and not info.available


def test_zip_check_with_changes(install, monkeypatch):
    (install / up.VERSION_FILE).write_text("aaa", encoding="utf-8")
    cmp = {"ahead_by": 2, "commits": [{"commit": {"message": "первое\n\nподробно"}}, {"commit": {"message": "второе"}}]}
    monkeypatch.setattr(up, "_get", FakeGitHub(compare=cmp))
    info = up.check_zip(install)
    assert info.available and info.behind == 2 and info.changes == ["второе", "первое"] and info.current == "aaa"


def test_zip_check_unknown_version(install, monkeypatch):
    monkeypatch.setattr(up, "_get", FakeGitHub())
    info = up.check_zip(install)
    assert info.current is None and info.behind == -1 and info.available


def test_zip_apply_downloads_exact_version(install, monkeypatch):
    gh = FakeGitHub(archive=make_zip({"sherpa/pet.py": "новый", "requirements.txt": "numpy\nrequests\n"}))
    monkeypatch.setattr(up, "_get", gh)
    called = []
    monkeypatch.setattr(up, "install_requirements", lambda root: called.append(root))
    info = up.check_zip(install)
    assert up.apply_zip(info, install) is True and called == [install]
    assert gh.urls[-1].endswith("/zip/bbb")  # ровно проверенная версия, а не «что сейчас в main»
    assert up.read_version(install) == "bbb" and up.check_zip(install).behind == 0


def test_zip_network_error_is_update_error(install, monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("нет сети")

    monkeypatch.setattr(up.urllib.request, "urlopen", boom)
    with pytest.raises(up.UpdateError, match="Нет связи"):
        up.check_zip(install)


# ---------- автопроверка и текст ----------

def test_auto_check_once_a_day(tmp_path):
    assert up.due_for_auto_check(tmp_path)
    up.mark_checked(tmp_path)
    assert not up.due_for_auto_check(tmp_path)
    assert up.due_for_auto_check(tmp_path, every_s=0)


@pytest.mark.parametrize("n,word", [(1, "изменение"), (2, "изменения"), (5, "изменений"), (11, "изменений"),
                                    (21, "изменение"), (24, "изменения")])
def test_describe_plural(n, word):
    info = up.UpdateInfo("git", "a", "b", n, [f"c{i}" for i in range(n)])
    text = up.describe(info)
    assert f"{n} {word}" in text and "• c0" in text and ("ещё" in text) == (n > 3)
