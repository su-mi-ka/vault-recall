# -*- coding: utf-8 -*-
"""vault_recall のデモ。

    python examples/demo.py

1. examples/sample_vault を索引にする
2. 起点一枚（紅茶の淹れ方）→ タグ（IDF）→ リンク 2 段 で何が返るかを出す。起点は返らない
3. 短期メモの往復（足す → 想起の先頭に載る → 済 → 消えずに印が付く）
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from vault_recall import (  # noqa: E402
    ShortMemo,
    VaultIndex,
    link_scores,
    structured_recall_trace,
    tag_scores,
)

HERE = os.path.dirname(os.path.abspath(__file__))

# Windows のコンソールが cp932 でも日本語をそのまま出す
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    index = VaultIndex(os.path.join(HERE, "sample_vault")).build()
    print(f"索引: {len(index)} 枚 / タグ {len(index.by_tag)} 種")
    for key, note in index.entries.items():
        print(f"  - {key}: tags={sorted(note.tags)} links={note.links}")

    # 鮮度は「今」からの日数で決まる。例が日付に左右されないよう、
    # 「今」を一番新しい last_accessed の翌日に固定する。
    now = max(n.last_accessed for n in index.entries.values()) + 86400

    seed = index.resolve("紅茶の淹れ方")
    assert seed is not None
    print()
    print(f"起点: {seed}（ベクトル検索などで引いた一枚、という想定）")

    # --- タグ想起（IDF）------------------------------------------------------------
    active = sorted(index.get(seed).tags)
    print(f"起点が背負うタグ: {active}")
    print("タグ想起の点（IDF 付き・起点は除く。全件に付く #メモ だけ共有する記憶は後ろに回る）:")
    for key, score in sorted(tag_scores(index, active, exclude_keys=[seed], now=now).items(),
                             key=lambda kv: -kv[1]):
        shared = sorted(index.get(key).tags & set(active))
        print(f"  {score:6.3f}  {key:<22} 共有={shared}")

    # --- リンク想起（2 段・減衰 0.45）-------------------------------------------------
    print("リンク想起の点（起点から 2 段。隣は 1.0、隣の隣は 0.45。二本の道から指された物が浮く）:")
    for key, score in sorted(link_scores(index, [seed], depth=2, now=now).items(), key=lambda kv: -kv[1]):
        print(f"  {score:6.3f}  {key}")

    # --- 二段想起（正本）--------------------------------------------------------------
    trace = structured_recall_trace(index, [seed], ctx_tags=["道具"], total=6, depth=2, now=now,
                                    link_min=3, tag_min=2)
    print()
    print(f"二段想起 total=6 → 枠(起点, リンク, タグ)={trace.slots}")
    print(f"  active_tags  = {trace.active_tags}")
    print(f"  タグ由来     = {trace.tag_recalled}")
    print(f"  リンク由来   = {trace.link_expanded}")
    print(f"  返す物       = {trace.result_keys}")
    assert seed not in trace.result_keys, "起点は返さない（include_seeds=True が退路）"
    print("  起点は返す物に入っていない: OK")

    # --- 短期メモ -------------------------------------------------------------------
    print()
    with tempfile.TemporaryDirectory() as d:
        memo = ShortMemo(os.path.join(d, "短期メモ.md"), limit=12)
        a = memo.add("茶葉の缶を買い足す", words=["紅茶", "缶"], now="2026-05-08 09:00")
        b = memo.add("キーボードのファームウェアを書き直す", words=["キーボード"], now="2026-05-08 09:05")
        print("短期メモに 2 件足した。想起の先頭に置く塊（入力に『紅茶』が出たので缶の方が先）:")
        print(memo.render(query_text="紅茶を淹れる"))
        memo.done(a, now="2026-05-09")
        memo.cancel(b, reason="基板を替えた", now="2026-05-09")
        print()
        print("済と取消を付けた後のファイル（消さずに印が付く）:")
        with open(memo.path, encoding="utf-8") as f:
            print(f.read().rstrip())
        print(f"render() → {memo.render()!r}（まだの物が無いので空）")


if __name__ == "__main__":
    main()
