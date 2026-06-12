"""Тесты разбора конфигурации и интервала."""

from __future__ import annotations

import pytest
import yaml

from ttbot.config import ConfigError, load_config, parse_interval, parse_spoof_ips


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("6h", 6 * 3600),
        ("30m", 30 * 60),
        ("1h30m", 5400),
        (" 2h 15m ", 2 * 3600 + 15 * 60),
    ],
)
def test_parse_interval_ok(text, seconds):
    assert parse_interval(text) == seconds


@pytest.mark.parametrize("bad", ["", "abc", "0h", "h", "10s"])
def test_parse_interval_bad(bad):
    with pytest.raises(ConfigError):
        parse_interval(bad)


def _base_cfg() -> dict:
    return {
        "spoof_ips": ["10.0.0.53", "10.0.0.53", "fd00::53"],  # дубль 10.0.0.53
        "update_interval": "1h30m",
        "technitium": {"url": "127.0.0.1:5380", "token": "tok"},
        "telegram": {"bot_token": "bt", "whitelist": [111, 111, 222]},  # дубль 111
        "blocklists": [{"name": "L1", "url": "https://example.com/a.json"}],
        "state_file": "state.json",
    }


def _write(tmp_path, data: dict):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def test_load_config_roundtrip(tmp_path):
    cfg = load_config(_write(tmp_path, _base_cfg()))
    assert cfg.spoof_ipv4 == ["10.0.0.53"]  # дубль убран
    assert cfg.spoof_ipv6 == ["fd00::53"]
    assert cfg.update_interval_seconds == 5400
    assert cfg.technitium.url == "http://127.0.0.1:5380"  # схема добавлена
    assert cfg.telegram.whitelist == {111, 222}  # set без дублей
    assert len(cfg.blocklists) == 1


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_load_config_empty_whitelist_rejected(tmp_path):
    data = _base_cfg()
    data["telegram"]["whitelist"] = []
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, data))


def test_load_config_bad_ip_rejected(tmp_path):
    data = _base_cfg()
    data["spoof_ips"] = ["not-an-ip"]
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, data))


@pytest.mark.parametrize(
    "raw,v4,v6",
    [
        ("10.0.0.53", ["10.0.0.53"], []),
        ("10.0.0.53, fd00::53", ["10.0.0.53"], ["fd00::53"]),
        ("10.0.0.1 10.0.0.1  10.0.0.2", ["10.0.0.1", "10.0.0.2"], []),  # дедуп
        ("fd00::53;fd00::54", [], ["fd00::53", "fd00::54"]),
    ],
)
def test_parse_spoof_ips_ok(raw, v4, v6):
    assert parse_spoof_ips(raw) == (v4, v6)


@pytest.mark.parametrize("raw", ["", "   ", "garbage", "10.0.0.999", "not.an.ip"])
def test_parse_spoof_ips_bad(raw):
    with pytest.raises(ConfigError):
        parse_spoof_ips(raw)
