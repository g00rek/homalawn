import pytest

from homalawn import recipes


def test_list_and_get(tmp_path, monkeypatch):
    (tmp_path / "clock-widget.md").write_text("---\nname: clock-widget\ntitle: Add a clock widget\n"
                                              "description: Add a clock\nneeds: a clock app\nverified: yes\n---\n# Goal\n")
    (tmp_path / "plain.md").write_text("---\nname: plain\ndescription: d\nneeds: n\n---\n# A plain recipe\n")
    monkeypatch.setenv("HOMALAWN_RECIPES", str(tmp_path))
    assert recipes.list_() == [
        {"name": "clock-widget", "title": "Add a clock widget", "description": "Add a clock", "needs": "a clock app",
         "verified": True},
        {"name": "plain", "title": "A plain recipe", "description": "d", "needs": "n", "verified": False}]
    assert recipes.get("clock-widget").startswith("---")


def test_unknown_recipe_names_the_known_ones(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("---\nname: a\ndescription: d\nneeds: n\n---\n")
    monkeypatch.setenv("HOMALAWN_RECIPES", str(tmp_path))
    with pytest.raises(LookupError, match="a"):
        recipes.get("nope")


def test_shipped_recipes_all_parse_and_have_verify_sections():
    found = recipes.list_()
    assert {r["name"] for r in found} == {"clock-widget", "ha-entity-widget", "icon-pack", "nova-import",
                                          "widget-permission"}
    for r in found:
        body = recipes.get(r["name"])
        assert r["description"] and r["title"] != r["name"] and "## Verify" in body and "## The user's part" in body
    assert "UNVERIFIED" not in recipes.get("nova-import") and "phone_nova_import" in recipes.get("nova-import")
    assert [r["name"] for r in found if not r["verified"]] == ["ha-entity-widget"]


def test_readme_is_not_a_recipe_and_default_dir_is_inside_the_package(monkeypatch):
    monkeypatch.delenv("HOMALAWN_RECIPES", raising=False)
    import homalawn
    from pathlib import Path
    assert Path(str(recipes.RECIPES_DIR)) == Path(homalawn.__file__).parent / "recipes"
    assert (recipes.RECIPES_DIR / "README.md").is_file()
    assert "README" not in [r["name"] for r in recipes.list_()]


def test_name_cannot_leave_the_recipes_dir(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("---\nname: a\ndescription: d\nneeds: n\n---\n")
    (tmp_path.parent / "secret.md").write_text("---\nname: secret\n---\n")
    monkeypatch.setenv("HOMALAWN_RECIPES", str(tmp_path))
    with pytest.raises(LookupError):
        recipes.get("../secret")
