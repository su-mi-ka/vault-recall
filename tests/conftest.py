# -*- coding: utf-8 -*-
import os
from typing import Iterable, Optional

import pytest

from vault_recall import VaultIndex

#: テストの「今」。鮮度が日付に左右されないよう固定する（2026-06-01 00:00 UTC）。
NOW = 1780272000


def write_note(
    root: str,
    name: str,
    body: str = "",
    tags: Iterable[str] = (),
    links: Iterable[str] = (),
    importance: Optional[int] = None,
    created: Optional[int] = None,
    last_accessed: Optional[int] = None,
    frontmatter: bool = True,
) -> str:
    """一枚の .md を書く。``name`` は ``dir/name`` も受ける。返すのはパス。"""
    path = os.path.join(root, *name.split("/")) + ".md"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    head = []
    if frontmatter:
        head.append("---")
        head.append("type: episodic")
        if importance is not None:
            head.append(f"importance: {importance}")
        if created is not None:
            head.append(f"created: {created}")
        if last_accessed is not None:
            head.append(f"last_accessed: {last_accessed}")
        tags = list(tags)
        if tags:
            head.append("tags: [" + ", ".join(tags) + "]")
        head.append("---")
    lines = head + [f"# {name.rsplit('/', 1)[-1]}", "", body or "本文。"]
    for l in links:
        lines.append(f"[[{l}]] を見る。")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


@pytest.fixture
def vault(tmp_path):
    """``vault(spec)`` で索引を組む。spec は write_note の引数の dict の列。"""

    def make(specs: Iterable[dict], memories_subdir: Optional[str] = None) -> VaultIndex:
        root = str(tmp_path / "vault")
        base = os.path.join(root, memories_subdir) if memories_subdir else root
        os.makedirs(base, exist_ok=True)
        for spec in specs:
            spec = dict(spec)
            spec.setdefault("created", NOW - 86400)
            spec.setdefault("last_accessed", NOW - 86400)
            write_note(base, **spec)
        return VaultIndex(root, memories_subdir=memories_subdir).build(now=NOW)

    return make
