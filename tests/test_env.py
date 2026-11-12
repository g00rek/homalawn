"""HOMALAWN_* variables with the Homar-era HOMAR_* and OmaLawn-era OMALAWN_* fallbacks, and the
one-time folder move (homar -> homalawn, or omalawn -> homalawn for a computer never moved)."""
from pathlib import Path

from homalawn import config, env, snapshots


def test_new_name_wins_and_old_name_still_works(monkeypatch):
    monkeypatch.delenv("HOMALAWN_SERIAL", raising=False)
    assert env.get("SERIAL") is None
    monkeypatch.setenv("OMALAWN_SERIAL", "oldest")
    assert env.get("SERIAL") == "oldest"
    monkeypatch.setenv("HOMAR_SERIAL", "old")
    assert env.get("SERIAL") == "old"
    monkeypatch.setenv("HOMALAWN_SERIAL", "new")
    assert env.get("SERIAL") == "new"


def test_config_path_follows_old_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("HOMALAWN_CONFIG")
    monkeypatch.setenv("OMALAWN_CONFIG", str(tmp_path / "c.toml"))
    assert config.path() == tmp_path / "c.toml"
    monkeypatch.setenv("HOMAR_CONFIG", str(tmp_path / "h.toml"))
    assert config.path() == tmp_path / "h.toml"


def _home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("HOMALAWN_CONFIG")
    monkeypatch.delenv("HOMALAWN_DATA")
    return home


def test_old_folders_move_once_and_leave_links(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    old_cfg, old_data = home / ".config" / "omalawn", home / ".local" / "share" / "omalawn"
    (old_cfg / "link").mkdir(parents=True)
    (old_cfg / "config.toml").write_text('serial = "emu-1"\n')
    (old_cfg / "link" / "key.pem").write_text("k")
    (old_data / "snapshots" / "auto-1").mkdir(parents=True)

    assert config.path() == home / ".config" / "homalawn" / "config.toml"
    assert config.load().serial == "emu-1"
    assert (home / ".config" / "homalawn" / "link" / "key.pem").read_text() == "k"
    assert old_cfg.is_symlink() and old_cfg.resolve() == (home / ".config" / "homalawn").resolve()
    assert snapshots.root() == home / ".local" / "share" / "homalawn" / "snapshots"
    assert (snapshots.root() / "auto-1").is_dir() and old_data.is_symlink()
    err = capsys.readouterr().err
    assert err.count("moved") == 2 and err.count("\n") == 2

    config.path(), snapshots.root()  # second run: nothing to move, nothing said
    assert capsys.readouterr().err == ""


def test_nothing_moves_when_new_folder_exists(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    (home / ".config" / "omalawn").mkdir(parents=True)
    (home / ".config" / "homalawn").mkdir(parents=True)
    config.path()
    assert not (home / ".config" / "omalawn").is_symlink()
    assert capsys.readouterr().err == ""


def test_fresh_computer_has_nothing_to_move(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    assert config.path() == home / ".config" / "homalawn" / "config.toml"
    assert not (home / ".config" / "omalawn").exists() and capsys.readouterr().err == ""


def test_homar_folders_move_once_and_old_links_keep_working(tmp_path, monkeypatch, capsys):
    """The usual case: the first rename left ~/.config/homar with omalawn linking to it."""
    home = _home(tmp_path, monkeypatch)
    monkeypatch.delenv("HOMALAWN_STATE_DIR")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    cfg, data, state = home / ".config", home / ".local" / "share", home / ".local" / "state"
    (cfg / "homar" / "link").mkdir(parents=True)
    (cfg / "homar" / "config.toml").write_text('serial = "emu-2"\n')
    (cfg / "omalawn").symlink_to(cfg / "homar", target_is_directory=True)
    (data / "homar" / "snapshots" / "auto-1").mkdir(parents=True)
    (state / "homar").mkdir(parents=True)
    (state / "homar" / "applied.json").write_text("{}")

    assert config.path() == cfg / "homalawn" / "config.toml"
    assert config.load().serial == "emu-2"
    assert (cfg / "homar").is_symlink() and (cfg / "homar").resolve() == (cfg / "homalawn").resolve()
    assert (cfg / "omalawn" / "config.toml").read_text() == 'serial = "emu-2"\n'  # link -> link -> folder
    assert (snapshots.root() / "auto-1").is_dir() and (data / "homar").is_symlink()
    assert env.state_dir() == state / "homalawn"
    assert (state / "homalawn" / "applied.json").read_text() == "{}" and (state / "homar").is_symlink()
    err = capsys.readouterr().err
    assert err.count("moved") == 3 and err.count("\n") == 3 and "Homar is now Homa Lawn" in err

    config.path(), snapshots.root(), env.state_dir()  # second run: nothing to move, nothing said
    assert capsys.readouterr().err == ""


def test_state_dir_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_STATE_DIR", str(tmp_path / "s"))
    assert env.state_dir() == tmp_path / "s"
