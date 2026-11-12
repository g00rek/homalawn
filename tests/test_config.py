from homalawn import config

def test_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "c.toml"))
    config.save(config.Config(serial="saved"))
    monkeypatch.setenv("HOMALAWN_SERIAL", "fromenv")
    assert config.load().serial == "fromenv"

def test_round_trip(tmp_path, monkeypatch):
    monkeypatch.delenv("HOMALAWN_SERIAL", raising=False)
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "sub" / "c.toml"))
    assert config.load().serial is None
    config.save(config.Config(serial="192.168.1.5:5555"))
    assert config.load().serial == "192.168.1.5:5555"


def test_config_has_no_phone_addresses(tmp_path, monkeypatch):
    """A phone's addresses live in peers.json; config.toml's old [peers] hosts are not read."""
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "c.toml"))
    (tmp_path / "c.toml").write_text('[peers.658d8fe8]\nhosts = ["10.0.0.2"]\n')
    c = config.load()
    assert not hasattr(c, "hosts") and not hasattr(config, "add_host")


def test_other_settings_survive_homalawn_saves(tmp_path, monkeypatch):
    monkeypatch.delenv("HOMALAWN_SERIAL", raising=False)
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "c.toml"))
    (tmp_path / "c.toml").write_text(
        '# mine\nserial = "emu-1"\ntheme = "dark"\nretries = 3\n\n[peers."Pixel 8"]\nhosts = ["a"]\nnote = "kitchen"\n'
        '\n[future]\nflag = true\nlist = [1, 2]\n[future.deep]\n"odd key" = 1.5\n\n[[profiles]]\nname = "x"\n')
    config.save_serial("emu-2")
    config.save(config.Config(serial="emu-3"))
    import tomllib
    raw = tomllib.loads((tmp_path / "c.toml").read_text())
    assert raw["serial"] == "emu-3" and raw["theme"] == "dark" and raw["retries"] == 3
    assert raw["peers"]["Pixel 8"] == {"hosts": ["a"], "note": "kitchen"}   # not moved yet: kept as written
    assert raw["future"] == {"flag": True, "list": [1, 2], "deep": {"odd key": 1.5}}
    assert raw["profiles"] == [{"name": "x"}]
    assert (tmp_path / "c.toml").read_text().startswith("# homalawn settings.")
    assert (tmp_path / "c.toml").read_text().count("# homalawn settings.") == 1
