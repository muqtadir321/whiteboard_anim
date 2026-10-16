from pathlib import Path

import pytest

from engine.config import ConfigError, load_config, parse_bool, parse_target_languages
from engine.input_discovery import PathError, discover_inputs, safe_path
from engine.script_analyzer import ScriptError, normalize_script


def test_missing_optional_inputs_are_skipped(cfg, repo):
    found = discover_inputs(cfg, None, repo)
    assert set(found) == {"english", "urdu", "roman_urdu", "pashto"}
    assert all(not li.has_content and not li.errors for li in found.values())


def test_default_and_alternative_extension(cfg, repo):
    (repo / "input/scripts/english.txt").write_text("Hello world. Money grows.")
    (repo / "input/audio/urdu.wav").write_bytes(b"RIFF0000WAVE")
    found = discover_inputs(cfg, None, repo)
    assert found["english"].script_source == "config"
    assert found["urdu"].audio_path.name == "urdu.wav"
    assert found["urdu"].audio_source == "config-extension-variant"


def test_explicit_path_wins_and_invalid_explicit_is_reported(cfg, repo):
    (repo / "input/scripts/english.txt").write_text("Default script.")
    (repo / "custom.txt").write_text("Custom script.")
    found = discover_inputs(cfg, {"english": {"script": "custom.txt"}, "urdu": {"script": "nope/urdu.txt"}}, repo)
    assert found["english"].script_path.name == "custom.txt"
    assert found["english"].script_source == "explicit"
    assert found["urdu"].script_path is None
    assert any("does not exist" in e for e in found["urdu"].errors)


def test_path_traversal_rejected(repo):
    with pytest.raises(PathError):
        safe_path("../../etc/passwd.txt", repo, (".txt",), 1000)
    with pytest.raises(PathError):
        safe_path("/etc/hosts.txt", repo, (".txt",), 1000)


def test_wrong_extension_rejected(repo):
    (repo / "x.sh").write_text("echo hi")
    with pytest.raises(PathError):
        safe_path("x.sh", repo, (".txt",), 1000)


def test_legacy_alias(cfg, repo):
    (repo / "input/english.txt").write_text("Legacy location script.")
    found = discover_inputs(cfg, None, repo)
    assert found["english"].script_source == "alias"


def test_corrupt_scripts_rejected():
    with pytest.raises(ScriptError):
        normalize_script("   \n\n  ")
    with pytest.raises(ScriptError):
        normalize_script(b"\x00\x01\x02binary")
    with pytest.raises(ScriptError):
        normalize_script(b"\xff\xfe\xfa invalid utf8")


def test_config_overrides_and_validation(repo):
    (repo / "input/config.yaml").write_text("video:\n  fps: 25\n")
    cfg = load_config(overrides={"video": {"fps": 30}}, repo_root=repo)
    assert cfg["video"]["fps"] == 30  # explicit override beats config file
    (repo / "input/config.yaml").write_text("video:\n  unknown_key: 1\n")
    with pytest.raises(ConfigError):
        load_config(repo_root=repo)
    (repo / "input/config.yaml").write_text("video:\n  width: 1081\n")
    with pytest.raises(ConfigError):
        load_config(repo_root=repo)


def test_parse_helpers():
    assert parse_bool("") is None and parse_bool("true") is True and parse_bool("No") is False
    assert parse_target_languages("auto") is None
    assert parse_target_languages("en, roman-urdu,ps") == ["english", "roman_urdu", "pashto"]
    with pytest.raises(ConfigError):
        parse_target_languages("klingon")


def test_quality_profile_fast():
    cfg = load_config(overrides={"video": {"quality": "fast"}})
    assert (cfg["video"]["width"], cfg["video"]["height"]) == (720, 1280)
    assert cfg["video"]["width"] % 2 == 0


def test_topic_discovery_parses_rss_without_network():
    from engine.topic_discovery import parse_feed, script_from_item

    rss = b"""<?xml version='1.0'?><rss><channel><item><title>Prices rise again</title>
    <description><![CDATA[<p>Inflation hit 5% this year. Families save less.</p>]]></description></item></channel></rss>"""
    items = parse_feed(rss)
    script = script_from_item(items[0])
    assert script.startswith("Today's topic: Prices rise again.")
    assert "5%" in script
