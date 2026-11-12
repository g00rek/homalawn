"""Recipes (spec D8): written procedures for jobs inside other apps, one markdown file each in the
package's `recipes/` folder (shipped inside the installed tool; read with importlib.resources), or
the directory in HOMALAWN_RECIPES. Served to agents by the MCP tools and listed on the window's
Agents page.

A recipe starts with `key: value` lines between `---` fences: `name`, `title` (what a person
reads), `description`, `needs` (a comma-separated string) and `verified` (`yes` once the recipe
has been walked through on a phone or the test emulator, `no` while it is only the intended path;
the window hides unverified ones). Parsed by hand; no YAML dependency.
"""
from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from . import env

RECIPES_DIR: Traversable = resources.files("homalawn") / "recipes"
FIELDS = ("name", "title", "description", "needs", "verified")


def _dir() -> Traversable:
    p = env.get("RECIPES")
    return Path(p) if p else RECIPES_DIR


def _front(text: str) -> dict[str, str]:
    """The `key: value` lines between the opening and closing `---`; empty if there are none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return {}  # no closing fence: not frontmatter


def _title(front: dict[str, str], text: str, name: str) -> str:
    """The frontmatter's title, else the first `# ` heading, else the name."""
    if front.get("title"):
        return front["title"]
    heading = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), "")
    return heading or name


def _all() -> dict[str, tuple[dict, str]]:
    """name -> (fields, full text), for every `*.md` with frontmatter, sorted by name."""
    d = _dir()
    found: dict[str, tuple[dict, str]] = {}
    files = sorted((f for f in d.iterdir() if f.name.endswith(".md") and f.is_file()), key=lambda f: f.name) \
        if d.is_dir() else []
    for path in files:
        text = path.read_text(encoding="utf-8")
        front = _front(text)
        if not front:
            continue  # README.md and other notes
        name = front.get("name") or path.name[:-3]
        found[name] = ({"name": name, "title": _title(front, text, name),
                        "description": front.get("description", ""), "needs": front.get("needs", ""),
                        "verified": front.get("verified", "").lower() in ("yes", "true")}, text)
    return dict(sorted(found.items()))


def list_() -> list[dict]:
    """[{name, title, description, needs, verified}] for every recipe, by name."""
    return [fields for fields, _ in _all().values()]


def get(name: str) -> str:
    """The full markdown of one recipe; LookupError naming the known ones if there is no such recipe."""
    found = _all()
    if name not in found:
        known = ", ".join(found) or "none"
        raise LookupError(f"No recipe {name!r}. Known recipes: {known}")
    return found[name][1]
