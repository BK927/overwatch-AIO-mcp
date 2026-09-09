"""Validate the installable root, metadata and referenced files without a Codex runtime."""

import re
import sys
import tomllib
from pathlib import Path

import yaml


def validate(root: Path):
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        raise ValueError("SKILL.md needs YAML frontmatter")
    metadata = yaml.safe_load(match[1])
    name = metadata["name"]
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
        or len(name) >= 64
    ):
        raise ValueError("Invalid skill name")
    description = metadata["description"]
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError("Invalid skill description")
    package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if name != package["project"]["name"]:
        raise ValueError("Skill and package names differ")
    ui = yaml.safe_load((root / "agents/openai.yaml").read_text(encoding="utf-8"))
    if not 25 <= len(ui["interface"]["short_description"]) <= 64:
        raise ValueError("UI description must contain 25–64 characters")
    if f"${name}" not in ui["interface"]["default_prompt"]:
        raise ValueError("Default prompt must mention the skill")
    if ui.get("policy", {}).get("allow_implicit_invocation", True) is not True:
        raise ValueError("This skill requires automatic selection")
    if ui.get("dependencies"):
        raise ValueError("This skill must run without connector dependencies")
    for document in [
        root / "SKILL.md",
        *root.glob("README*.md"),
        root / "SPEC.md",
        *root.glob("references/*.md"),
        *root.glob("docs/*.md"),
    ]:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            resolved = (document.parent / target.split("#")[0]).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                raise ValueError(f"Missing or external reference in {document.name}: {target}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    try:
        validate(root)
    except (ValueError, KeyError, OSError, yaml.YAMLError) as exc:
        print(f"Skill validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    print("Skill package and references are valid.")
