"""YAML test profiles.

    name: bt-rfcomm-smoke
    defaults:            # applied to every test
      transport: tcp
      duration: 10s
    tests:
      - name: up
        mode: tx
        expect: {up_mbps_min: 1.5}
      - name: block-sweep
        mode: tx
        matrix:          # cartesian product -> one test per combination
          block: [1K, 16K, 128K]
"""

from __future__ import annotations

import itertools
from pathlib import Path

from ctt.runner import TestSpec


def _load_yaml(path: Path):
    try:
        import yaml
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("profiles need PyYAML: pip install pyyaml "
                           "(or apt install python3-yaml)") from e
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def expand(doc: dict, overrides: dict | None = None) -> tuple[str, list[TestSpec]]:
    name = doc.get("name", "profile")
    defaults = doc.get("defaults") or {}
    tests = doc.get("tests") or []
    if not tests:
        raise ValueError("profile has no tests")
    specs: list[TestSpec] = []
    for i, t in enumerate(tests):
        t = dict(t)
        matrix = t.pop("matrix", None) or {}
        base_name = t.get("name", f"test{i + 1}")
        keys = list(matrix)
        combos = list(itertools.product(*(matrix[k] for k in keys))) if keys else [()]
        for combo in combos:
            d = {**defaults, **t}
            # nested dicts merge instead of replace
            for k in ("expect", "transport_opts"):
                if isinstance(defaults.get(k), dict) and isinstance(t.get(k), dict):
                    d[k] = {**defaults[k], **t[k]}
            d.update(dict(zip(keys, combo)))
            d.update(overrides or {})
            d["name"] = base_name + "".join(f"-{k}={v}" for k, v in zip(keys, combo))
            specs.append(TestSpec.from_dict(d))
    return name, specs


def load_profile(path: str | Path, overrides: dict | None = None) -> tuple[str, list[TestSpec]]:
    return expand(_load_yaml(Path(path)), overrides)
