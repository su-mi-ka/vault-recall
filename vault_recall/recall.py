# -*- coding: utf-8 -*-
"""二段想起: 起点 → タグ → リンク。起点は返さない。

考え方（名前の付いた物）:

* **起点と想起結果を分ける** — ベクトル検索（や全文検索）が引いた記憶は「足場」であって
  結果ではない。そこから vault の構造（タグ・[[link]]）で辿った記憶だけを返す。
* **リンク主軸（link-first）** — 枠はリンク:タグ = 2:1 で分け合う。
* **IDF で常在タグを割り引く** — 全体の三分の一に付くタグで数えると「よく喋った話題」ばかりが
  上位に来る。``log(総数/出現数) + 1`` を掛けて、その入力に固有の細い糸を沈めない。
* **段の減衰と網の結び目** — 一段離れるごとに ``hop_decay`` を掛ける。遠い記憶は
  複数の道から指されている時だけ浮く。

LLM にクエリを作らせる段を、この構造で置き換える。
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from .index import Note, VaultIndex, normalize_tag

__all__ = [
    "LINK_HOP_DECAY",
    "RecallTrace",
    "recency",
    "seed_tags",
    "tag_scores",
    "recall_by_tags",
    "link_scores",
    "expand_links",
    "slots",
    "structured_recall",
    "structured_recall_trace",
]

#: 一段離れるごとにスコアへ掛ける減衰。隣は隣として重く、隣の隣は連想として軽く。
LINK_HOP_DECAY = 0.45


def _now(now: Optional[int]) -> int:
    return int(now if now is not None else _dt.datetime.now().timestamp())


# =============================================================================
# 鮮度
# =============================================================================
def recency(last_accessed: int, now: int, half_life_days: float = 30.0) -> float:
    """最後に触れてからの日数で割り引く。``half_life_days`` 日で 0.5 になる。

    元の実装は ``0.995 ** days``（半減期 ≒ 138 日）。``half_life_days=138.3`` で同じ曲線になる。
    未来の時刻（時計のずれ）は 0 日として扱い、1.0 を超えない。
    """
    days = max(0.0, (int(now) - int(last_accessed)) / 86400.0)
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (days / float(half_life_days))


def _weight(note: Note, now: int, half_life_days: float) -> float:
    return (1.0 + note.importance / 1000.0) * recency(note.last_accessed, now, half_life_days)


# =============================================================================
# 起点のタグ
# =============================================================================
def seed_tags(index: VaultIndex, seed_keys: Iterable[str], limit: int = 12) -> set[str]:
    """起点の記憶が実際に背負っているタグを、多くの起点が共有する順に ``limit`` 本採る。

    タグは vault 側に在り、クエリ側に無い。入力文字列を手書きのキーワード表へ当てるより、
    起点が持つタグをそのまま採った方が「今の話題」に近い。
    """
    count = index.tags_of(seed_keys)
    if not count:
        return set()
    order = sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))
    return {t for t, _ in order[: max(1, int(limit))]}


# =============================================================================
# タグ想起（IDF）
# =============================================================================
def tag_scores(
    index: VaultIndex,
    active_tags: Iterable[str],
    exclude_keys: Iterable[str] = (),
    now: Optional[int] = None,
    half_life_days: float = 30.0,
) -> dict[str, float]:
    """今の文脈タグを共有する記憶を、タグの珍しさ（IDF）で重み付けして点にする。

    点 = Σ(共有タグごとに ``log(総数/出現数) + 1``) × (1 + importance/1000) × 鮮度。
    全件に付くタグでも 1.0 は残る（共有している事実は消さない）。
    """
    now = _now(now)
    excluded = set(exclude_keys)
    total = max(1, len(index.entries))
    overlap: dict[str, float] = {}
    for raw in active_tags:
        t = normalize_tag(raw)
        posting = index.by_tag.get(t)
        if not posting:
            continue
        idf = math.log(total / len(posting)) + 1.0
        for key in posting:
            if key in excluded:
                continue
            overlap[key] = overlap.get(key, 0.0) + idf
    scored: dict[str, float] = {}
    for key, ov in overlap.items():
        note = index.entries.get(key)
        if note is None:
            continue
        scored[key] = ov * _weight(note, now, half_life_days)
    return scored


def recall_by_tags(
    index: VaultIndex,
    active_tags: Iterable[str],
    exclude_keys: Iterable[str] = (),
    top_n: int = 5,
    now: Optional[int] = None,
    half_life_days: float = 30.0,
) -> list[str]:
    """今の文脈タグを共有する記憶の key を、点の高い順に ``top_n`` 件返す。

    ``active_tags`` が空なら何も返さない（無関係な記憶を引かない）。
    """
    tags = [t for t in active_tags if normalize_tag(t)]
    if not tags or top_n <= 0:
        return []
    scored = tag_scores(index, tags, exclude_keys, now, half_life_days)
    order = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    return [k for k, _ in order[:top_n]]


# =============================================================================
# リンク想起（段の減衰）
# =============================================================================
def link_scores(
    index: VaultIndex,
    seed_keys: Sequence[str],
    depth: int = 1,
    hop_decay: float = LINK_HOP_DECAY,
    now: Optional[int] = None,
    half_life_days: float = 30.0,
) -> dict[str, float]:
    """起点から [[link]] を ``depth`` 段辿り、指された記憶の点を返す（起点自身は含まない）。

    点 = (指された道の数 × 段ごとに ``hop_decay`` を掛けた物の和) × (1 + importance/1000) × 鮮度。
    道は「別の道から来れば別に数える」。同じ結び目へ二本の道が集まれば二倍になり、
    その結び目から先へもその分だけ流れる＝網の結び目が浮く。
    """
    now = _now(now)
    seen = {k for k in seed_keys if k in index.entries}
    if not seen:
        return {}
    refs: dict[str, float] = {}
    frontier: dict[str, float] = {k: 1.0 for k in seed_keys if k in index.entries}
    for hop in range(max(1, int(depth))):
        decay = hop_decay ** hop
        nxt: dict[str, float] = {}
        for key, ways in frontier.items():
            for target in index.neighbors(key):
                if target in seen:
                    continue
                refs[target] = refs.get(target, 0.0) + decay * ways
                nxt[target] = nxt.get(target, 0.0) + ways
        if not nxt:
            break
        frontier = nxt
    scored: dict[str, float] = {}
    for key, r in refs.items():
        note = index.entries.get(key)
        if note is None:
            continue
        scored[key] = r * _weight(note, now, half_life_days)
    return scored


def expand_links(
    index: VaultIndex,
    seed_keys: Sequence[str],
    depth: int = 1,
    max_expand: int = 2,
    hop_decay: float = LINK_HOP_DECAY,
    now: Optional[int] = None,
    half_life_days: float = 30.0,
) -> list[str]:
    """選ばれた記憶の [[link]] を辿り、隣接記憶を最大 ``max_expand`` 件足して返す。

    候補は先着順ではなく ``link_scores`` の点で選ぶ。**元の seed_keys は先頭に保つ。**
    重複は除く。``max_expand <= 0`` なら seed_keys をそのまま返す。
    """
    seeds: list[str] = []
    for k in seed_keys:
        if k not in seeds:
            seeds.append(k)
    if not seeds or max_expand <= 0:
        return seeds
    scored = link_scores(index, seeds, depth, hop_decay, now, half_life_days)
    order = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    picked = [k for k, _ in order if k not in seeds][:max_expand]
    return seeds + picked


# =============================================================================
# 枠
# =============================================================================
def slots(total: int, link_min: int = 10, tag_min: int = 5, seed_min: int = 3) -> tuple[int, int, int]:
    """注入枠 ``total`` を (起点, リンク, タグ) に分ける。

    起点はフィオへ渡らないので ``total`` を食わない。``total`` はリンクとタグで 2:1 に分け合い、
    どちらも下限を敷く（枠を切らずに埋めると、注入の切り捨てでリンク由来が一件も残らなかった）。
    起点の数は「何件の足場から辿るか」で、``total - link_min - tag_min`` を ``seed_min`` で下から支える。
    """
    total = max(3, int(total))
    seeds = max(int(seed_min), total - int(link_min) - int(tag_min))
    link = max(int(link_min), total * 2 // 3)
    tag = max(int(tag_min), total - link)
    return seeds, link, tag


# =============================================================================
# 二段想起
# =============================================================================
@dataclass
class RecallTrace:
    """``structured_recall`` の中身。何が起点で、どれがタグ由来で、どれがリンク由来か。"""

    seeds: list[str] = field(default_factory=list)
    active_tags: list[str] = field(default_factory=list)
    tag_recalled: list[str] = field(default_factory=list)
    link_expanded: list[str] = field(default_factory=list)
    result_keys: list[str] = field(default_factory=list)
    degraded: bool = False  #: 構造から一件も引けず、起点で縮退したか
    slots: tuple[int, int, int] = (0, 0, 0)

    def notes(self, index: VaultIndex) -> list[Note]:
        return [index.entries[k] for k in self.result_keys if k in index.entries]


def structured_recall_trace(
    index: VaultIndex,
    seed_keys: Sequence[str],
    ctx_tags: Iterable[str] = (),
    total: int = 15,
    depth: int = 3,
    include_seeds: bool = False,
    fallback_to_seeds: bool = True,
    hop_decay: float = LINK_HOP_DECAY,
    now: Optional[int] = None,
    half_life_days: float = 30.0,
    link_min: int = 10,
    tag_min: int = 5,
    seed_min: int = 3,
) -> RecallTrace:
    """二段想起の正本。手順は 起点 → タグ → リンク。

    1. ``seed_keys`` のうち索引に在る物（辿れる物）だけを起点枠まで採る。
    2. 文脈タグ ``ctx_tags`` と、起点が背負うタグ（``seed_tags``）を合わせて active_tags にする。
    3. active_tags を共有する記憶をタグ枠まで引く（起点は除く）。
    4. 起点＋タグ由来を種に [[link]] を ``depth`` 段辿り、リンク枠まで足す。
    5. 返すのは **タグ由来 ＋ リンク由来** だけ。起点は返さない（``include_seeds=True`` が退路）。
       構造から一件も引けなかった時だけ、``fallback_to_seeds`` なら起点で縮退する。
    """
    now = _now(now)
    seed_n, link_n, tag_n = slots(total, link_min, tag_min, seed_min)
    seeds: list[str] = []
    for k in seed_keys:
        if k in index.entries and k not in seeds:
            seeds.append(k)
    seeds = seeds[:seed_n]
    active = {normalize_tag(t) for t in ctx_tags if normalize_tag(t)} | seed_tags(index, seeds)
    tag_recalled = recall_by_tags(index, active, exclude_keys=seeds, top_n=tag_n, now=now,
                                  half_life_days=half_life_days)
    seeded = seeds + tag_recalled
    merged = expand_links(index, seeded, depth=depth, max_expand=link_n, hop_decay=hop_decay,
                          now=now, half_life_days=half_life_days)
    link_expanded = [k for k in merged if k not in seeded]
    result = list(tag_recalled) + list(link_expanded)
    degraded = False
    if not result and seeds and fallback_to_seeds:
        result = list(seeds)
        degraded = True
    if include_seeds:
        result = seeds + [k for k in result if k not in seeds]
    return RecallTrace(
        seeds=seeds,
        active_tags=sorted(active),
        tag_recalled=list(tag_recalled),
        link_expanded=list(link_expanded),
        result_keys=result,
        degraded=degraded,
        slots=(seed_n, link_n, tag_n),
    )


def structured_recall(
    index: VaultIndex,
    seed_keys: Sequence[str],
    ctx_tags: Iterable[str] = (),
    total: int = 15,
    depth: int = 3,
    include_seeds: bool = False,
    **kwargs,
) -> list[Note]:
    """二段想起。起点 ``seed_keys`` から辿った記憶（``Note``）を返す。起点そのものは返さない。

    詳しい内訳が要るなら ``structured_recall_trace`` を使う。残りの引数はそちらと同じ。
    """
    trace = structured_recall_trace(index, seed_keys, ctx_tags, total, depth, include_seeds, **kwargs)
    return trace.notes(index)
