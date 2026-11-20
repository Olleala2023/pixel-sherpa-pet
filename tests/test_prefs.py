"""Память пса между запусками (sherpa/prefs.py)."""
import json

import pytest

from sherpa import prefs


def test_defaults_without_file(tmp_path):
    assert prefs.load(tmp_path / "нет.json") == {"scale": 3, "roaming": True, "sound": True, "bubble": True}


def test_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    prefs.save({"scale": 5, "roaming": False, "sound": False, "bubble": False, "лишнее": 1}, p)
    assert prefs.load(p) == {"scale": 5, "roaming": False, "sound": False, "bubble": False}
    assert "лишнее" not in json.loads(p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("text", ["{битый", "[]", '"строка"', ""])
def test_broken_file_gives_defaults(tmp_path, text):
    p = tmp_path / "s.json"
    p.write_text(text, encoding="utf-8")
    assert prefs.load(p) == prefs.DEFAULTS


def test_bad_values_are_dropped(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"scale": 9, "roaming": "да", "sound": False, "bubble": None}), encoding="utf-8")
    assert prefs.load(p) == {"scale": 3, "roaming": True, "sound": False, "bubble": True}


def test_save_to_readonly_place_does_not_crash(tmp_path):
    prefs.save({"scale": 4}, tmp_path / "нет-папки" / "s.json")  # просто не сохранилось


def test_update_does_not_touch_settings():
    from sherpa import updater

    assert "sherpa-settings.json" in updater.PROTECTED
