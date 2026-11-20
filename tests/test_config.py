import tomllib

from brain import config
from brain.config import BRAIN_DIR, DEFAULT_TOML, load_config, save_settings


def test_creates_default_file(paths):
    assert not paths.config.exists()
    cfg = load_config(paths.config)
    assert paths.config.read_text(encoding="utf-8") == DEFAULT_TOML
    assert cfg.port == 8765 and cfg.provider == "openrouter"
    assert cfg.audio_keep == 50


def test_default_toml_is_valid():
    raw = tomllib.loads(DEFAULT_TOML)
    assert raw["gigaam"]["model"] and raw["model"] != raw["gigaam"]["model"]


def test_relative_paths_from_brain_dir(paths):
    paths.config.write_text('[gigaam]\nmodel = "models/a.onnx"\ntokens = "models/t.txt"\n'
                            '[vad]\nmodel = "models/v.onnx"\n', encoding="utf-8")
    cfg = load_config(paths.config)
    assert cfg.gigaam_model == BRAIN_DIR / "models" / "a.onnx"
    assert cfg.gigaam_tokens == BRAIN_DIR / "models" / "t.txt"
    assert cfg.vad.model == BRAIN_DIR / "models" / "v.onnx"


def test_absolute_paths_kept(paths, tmp_path):
    m = tmp_path / "abs" / "m.onnx"
    paths.config.write_text(f'[gigaam]\nmodel = "{m.as_posix()}"\n', encoding="utf-8")
    assert load_config(paths.config).gigaam_model == m


def test_top_values_and_vad_overrides(paths):
    paths.config.write_text('daily_limit_usd = 2.5\naudio_keep = 3\n[vad]\nthreshold = 0.7\n', encoding="utf-8")
    cfg = load_config(paths.config)
    assert cfg.daily_limit_usd == 2.5 and cfg.audio_keep == 3 and cfg.vad.threshold == 0.7


def test_save_settings_changes_only_top_level(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(DEFAULT_TOML, encoding="utf-8")
    save_settings("openrouter", "vendor/new-model", p)
    text = p.read_text(encoding="utf-8")
    raw = tomllib.loads(text)
    assert raw["model"] == "vendor/new-model"
    assert raw["gigaam"]["model"] == "models/gigaam-v3-e2e/model.int8.onnx"  # секция не тронута
    assert text.startswith("# Настройки мозга Шерпы")  # комментарии на месте
    assert "# относительно папки brain/" in text
    assert not p.with_suffix(".tmp").exists()


def test_save_settings_adds_missing_lines(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('# шапка\nport = 1\n[gigaam]\nmodel = "x"\n', encoding="utf-8")
    save_settings("openrouter", 'a"b', p)
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    assert raw["provider"] == "openrouter" and raw["model"] == 'a"b'
    assert raw["gigaam"]["model"] == "x" and raw["port"] == 1
    assert p.read_text(encoding="utf-8").startswith("# шапка\n")


def test_save_settings_roundtrip_with_load(paths):
    load_config(paths.config)
    save_settings("openrouter", "x/y", paths.config)
    assert load_config(paths.config).model == "x/y"
