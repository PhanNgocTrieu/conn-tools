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
          transport_opts.security: [low, high]   # dotted key = nested field
      - name: projection           # streams that run AT THE SAME TIME (one session of a protocol)
        protocol: CarPlay wired    # label for reports
        parallel:
          - {name: video, mode: tx, burst: 16K, burst_rate: 60}
          - {name: audio, mode: rx, burst: 960, burst_rate: 200}
          - {name: control, mode: echo, block: 64}
        # tcp / udp streams get consecutive ports (5201, 5202, ...) unless they set `port`
"""

from __future__ import annotations

import itertools
from pathlib import Path

from ctt.testing.runner import TestSpec


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
        streams = t.pop("parallel", None)
        if streams is not None:
            specs.extend(_expand_parallel(defaults, t, streams, overrides, i))
            continue
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
            for k, v in zip(keys, combo):
                if "." in k:  # nested, e.g. transport_opts.security
                    parent, child = k.split(".", 1)
                    d[parent] = {**(d.get(parent) or {}), child: v}
                else:
                    d[k] = v
            d.update(overrides or {})
            d["name"] = base_name + "".join(f"-{k.split('.')[-1]}={v}" for k, v in zip(keys, combo))
            specs.append(TestSpec.from_dict(d))
    return name, specs


def _merge(base: dict, extra: dict) -> dict:
    d = {**base, **extra}
    for k in ("expect", "transport_opts"):   # nested dicts merge instead of replace
        if isinstance(base.get(k), dict) and isinstance(extra.get(k), dict):
            d[k] = {**base[k], **extra[k]}
    return d


def _expand_parallel(defaults: dict, test: dict, streams, overrides, index: int) -> list[TestSpec]:
    if not isinstance(streams, list) or not streams:
        raise ValueError(f"test {index + 1}: 'parallel' must be a non-empty list of streams")
    if "matrix" in test:
        raise ValueError(f"test {index + 1}: 'parallel' and 'matrix' cannot be combined")
    group = test.get("name", f"test{index + 1}")
    shared = {k: v for k, v in test.items() if k != "name"}
    out = []
    for j, st in enumerate(streams):
        d = _merge(_merge(defaults, shared), dict(st))
        d.update(overrides or {})
        d["name"] = f"{group}/{st.get('name', f'stream{j + 1}')}"
        d["group"] = group
        if d.get("transport", "tcp") in ("tcp", "udp") and d.get("port") is None:
            d["port"] = 5201 + j
        elif d.get("transport") == "usb":
            raise ValueError(f"{d['name']}: USB bulk has one data pipe - run its streams one after "
                             "another, or use USB networking (NCM: transport tcp/udp) for parallel streams")
        out.append(TestSpec.from_dict(d))
    return out


def load_profile(path: str | Path, overrides: dict | None = None) -> tuple[str, list[TestSpec]]:
    return expand(_load_yaml(Path(path)), overrides)
