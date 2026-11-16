"""The test catalogue: every profile under config/profiles, addressed by a short name.

    wifi/carplay-wireless   ->  config/profiles/wifi/carplay-wireless.yaml
    carplay-wireless        ->  the same (unique part of the name is enough)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ctt.paths import PROFILES


@dataclass(frozen=True)
class Entry:
    key: str            # "wifi/carplay-wireless"
    category: str       # "wifi", "usb", "bluetooth", "" (top level)
    path: Path
    title: str          # first comment line of the file

    @property
    def short(self) -> str:
        return self.key.rsplit("/", 1)[-1]


def _title(path: Path) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#"):
                    t = line.lstrip("#").strip()
                    if t:
                        return t
                elif line:
                    break
    except OSError:
        pass
    return ""


def entries(root: Path = PROFILES) -> list[Entry]:
    out = []
    for p in sorted(root.rglob("*.y*ml")):
        rel = p.relative_to(root).with_suffix("")
        category = rel.parts[0] if len(rel.parts) > 1 else ""
        out.append(Entry(rel.as_posix(), category, p, _title(p)))
    return out


def categories(items: list[Entry]) -> list[str]:
    return sorted({e.category for e in items}, key=lambda c: (c == "", c))


def find(name: str, root: Path = PROFILES) -> Entry:
    """Profile by key, short name or file path; raises ValueError when unknown or ambiguous."""
    p = Path(name)
    if p.suffix in (".yaml", ".yml") and p.is_file():
        rel = p.resolve()
        cat = rel.parent.name if rel.parent != root.resolve() else ""
        return Entry(p.stem, cat, p, _title(p))
    items = entries(root)
    want = name.strip().strip("/").removesuffix(".yaml").replace("_", "-").lower()
    norm = lambda s: s.replace("_", "-").lower()  # noqa: E731
    exact = [e for e in items if norm(e.key) == want or norm(e.short) == want]
    if len(exact) == 1:
        return exact[0]
    partial = exact or [e for e in items if want in norm(e.key)]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ValueError(f"no test named '{name}' - list them with: ctt test")
    raise ValueError(f"'{name}' matches several tests: " + ", ".join(e.key for e in partial))
