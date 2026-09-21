#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

# Kept in sync with GITHUB_RAW_BASE in serv.py, which rewrites this prefix.
POLICY_URL = 'https://raw.githubusercontent.com/Nerahikada/claude-code-installer/master/policy/policy.json'


def read_policy(source: str) -> dict:
    req = urllib.request.Request(source, headers={'User-Agent': 'claude-code-installer/apply.py'})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode('utf-8')
    policy = json.loads(raw)
    if not isinstance(policy, dict) or not all(isinstance(v, dict) for v in policy.values()):
        raise ValueError(f'{source} is not an object mapping config paths to patches')
    return policy


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding='utf-8')
    if not text.strip():
        return {}
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError(f'{path} holds a JSON {type(doc).__name__}, not an object')
    return doc


def deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = path.stat().st_mode & 0o777
    else:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(json.dumps(doc))
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    policy = read_policy(POLICY_URL)
    print(f'policy from {POLICY_URL}')

    for name, patch in policy.items():
        path = Path(name).expanduser()
        before = read_json(path)
        after = deep_merge(json.loads(json.dumps(before)), patch)
        if after == before:
            print(f'  {path}: already current')
            continue
        write_json(path, after)
        print(f'  {path}: updated')

    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f'policy not applied: {e}', file=sys.stderr)
        sys.exit(1)
