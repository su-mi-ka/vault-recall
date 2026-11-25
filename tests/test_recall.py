# -*- coding: utf-8 -*-
"""想起の柵: 鮮度・IDF・段の減衰と結び目・expand_links の並び・枠・起点を返さない。"""
import math

import pytest

from vault_recall import (
    LINK_HOP_DECAY,
    Note,
    expand_links,
    link_scores,
    recall_by_tags,
    recency,
    seed_tags,
    slots,
    structured_recall,
    structured_recall_trace,
    tag_scores,
)
from tests.conftest import NOW

DAY = 86400


# ---- 鮮度 -------------------------------------------------------------------------
def test_recency_half_life():
    assert recency(NOW, NOW) == 1.0
    assert recency(NOW + DAY, NOW) == 1.0  # 未来は 0 日扱い
    assert recency(NOW - 30 * DAY, NOW, half_life_days=30) == pytest.approx(0.5)
    assert recency(NOW - 60 * DAY, NOW, half_life_days=30) == pytest.approx(0.25)
    # 元の実装 0.995**days は half_life ≒ 138.3 日
    assert recency(NOW - 100 * DAY, NOW, half_life_days=138.28) == pytest.approx(0.995 ** 100, rel=1e-3)


# ---- IDF --------------------------------------------------------------------------
def test_idf_rare_tag_outweighs_ubiquitous_tags(vault):
    """全件に付くタグを二本共有する記憶より、固有タグを一本共有する記憶が上位に来る。"""
    specs = [{"name": "seed", "tags": ["c1", "c2", "rare"]}]
    specs.append({"name": "X", "tags": ["rare"]})            # 固有タグだけ共有（1 本）
    specs.append({"name": "Y", "tags": ["c1", "c2"]})        # 常在タグだけ共有（2 本）
    for i in range(7):
        specs.append({"name": f"filler{i}", "tags": ["c1", "c2"]})
    index = vault(specs)
    active = index.get("seed").tags
    # 素の本数なら Y（2 本）> X（1 本）
    assert len(index.get("Y").tags & active) > len(index.get("X").tags & active)
    scores = tag_scores(index, active, exclude_keys=["seed"], now=NOW)
    assert scores["X"] > scores["Y"]
    total = len(index)
    idf_c = math.log(total / len(index.by_tag["c1"])) + 1.0
    idf_rare = math.log(total / len(index.by_tag["rare"])) + 1.0
    weight = (1 + 500 / 1000) * recency(NOW - DAY, NOW)
    assert scores["Y"] == pytest.approx(2 * idf_c * weight)
    assert scores["X"] == pytest.approx(idf_rare * weight)
    assert recall_by_tags(index, active, exclude_keys=["seed"], top_n=1, now=NOW) == ["X"]


def test_ubiquitous_tag_still_counts_one(vault):
    """全件に付くタグでも 1.0 は残る（共有している事実は消さない）。"""
    index = vault([{"name": "a", "tags": ["all"]}, {"name": "b", "tags": ["all"]}])
    scores = tag_scores(index, ["all"], now=NOW)
    weight = (1 + 500 / 1000) * recency(NOW - DAY, NOW)
    assert scores["a"] == pytest.approx(1.0 * weight)


def test_recall_by_tags_excludes_and_limits_and_normalizes(vault):
    index = vault(
        [
            {"name": "seed", "tags": ["t"]},
            {"name": "hi", "tags": ["t"], "importance": 900},
            {"name": "lo", "tags": ["t"], "importance": 100},
        ]
    )
    assert recall_by_tags(index, ["#t"], exclude_keys=["seed"], now=NOW) == ["hi", "lo"]
    assert recall_by_tags(index, ["t"], exclude_keys=["seed"], top_n=1, now=NOW) == ["hi"]
    assert recall_by_tags(index, [], now=NOW) == []
    assert recall_by_tags(index, ["unknown"], now=NOW) == []


def test_recall_by_tags_importance_and_recency_weigh(vault):
    index = vault(
        [
            {"name": "seed", "tags": ["t"]},
            {"name": "fresh", "tags": ["t"], "last_accessed": NOW},
            {"name": "stale", "tags": ["t"], "last_accessed": NOW - 90 * DAY},
        ]
    )
    assert recall_by_tags(index, ["t"], exclude_keys=["seed"], now=NOW) == ["fresh", "stale"]


# ---- リンク（段の減衰・結び目）-----------------------------------------------------
def test_hop_decay_second_hop_is_lighter(vault):
    index = vault(
        [
            {"name": "seed", "links": ["A"]},
            {"name": "A", "links": ["B"]},
            {"name": "B"},
        ]
    )
    one = link_scores(index, ["seed"], depth=1, now=NOW)
    assert set(one) == {"A"}
    two = link_scores(index, ["seed"], depth=2, now=NOW)
    assert set(two) == {"A", "B"}
    assert two["B"] / two["A"] == pytest.approx(LINK_HOP_DECAY)
    assert LINK_HOP_DECAY == 0.45


def test_node_pointed_from_two_paths_rises(vault):
    index = vault(
        [
            {"name": "seed", "links": ["A", "C"]},
            {"name": "A", "links": ["B", "D"]},
            {"name": "C", "links": ["B"]},
            {"name": "B"},
            {"name": "D"},
        ]
    )
    s = link_scores(index, ["seed"], depth=2, now=NOW)
    # B は A と C の二本の道から指される: 0.45 + 0.45。D は一本: 0.45
    assert s["B"] / s["D"] == pytest.approx(2.0)
    assert s["B"] > s["D"]
    # 隣（1.0）より二段先の結び目（0.9）はまだ軽い
    assert s["A"] > s["B"]


def test_two_seeds_pointing_same_node_double_it(vault):
    index = vault(
        [
            {"name": "s1", "links": ["hub"]},
            {"name": "s2", "links": ["hub"]},
            {"name": "hub"},
            {"name": "s3", "links": ["lone"]},
            {"name": "lone"},
        ]
    )
    s = link_scores(index, ["s1", "s2", "s3"], depth=1, now=NOW)
    assert s["hub"] / s["lone"] == pytest.approx(2.0)


def test_links_back_to_seeds_are_not_counted(vault):
    index = vault([{"name": "seed", "links": ["A"]}, {"name": "A", "links": ["seed"]}])
    assert set(link_scores(index, ["seed"], depth=3, now=NOW)) == {"A"}


def test_expand_links_keeps_seeds_first_and_dedupes(vault):
    index = vault(
        [
            {"name": "s1", "links": ["A", "B"]},
            {"name": "s2", "links": ["B", "C"]},
            {"name": "A"},
            {"name": "B"},
            {"name": "C", "importance": 900},
        ]
    )
    out = expand_links(index, ["s1", "s2", "s1"], depth=1, max_expand=2, now=NOW)
    assert out[:2] == ["s1", "s2"]            # 元の順・重複なし
    assert out[2] == "B"                      # 二本の道から指された結び目が先
    assert len(out) == 4 and len(set(out)) == 4
    assert "s1" not in out[2:] and "s2" not in out[2:]
    assert expand_links(index, ["s1"], max_expand=0, now=NOW) == ["s1"]
    assert expand_links(index, [], max_expand=3, now=NOW) == []


def test_expand_links_with_unresolvable_link(vault):
    index = vault([{"name": "seed", "links": ["nowhere"]}])
    assert expand_links(index, ["seed"], max_expand=3, now=NOW) == ["seed"]


# ---- 枠 ---------------------------------------------------------------------------
def test_slots_two_to_one_and_floors():
    assert slots(30) == (15, 20, 10)
    assert slots(300) == (285, 200, 100)
    assert slots(6) == (3, 10, 5)      # 下限が効く
    assert slots(0) == (3, 10, 5)
    assert slots(15) == (3, 10, 5)
    assert slots(12, link_min=3, tag_min=2, seed_min=1) == (7, 8, 4)
    seeds, link, tag = slots(90)
    assert link == 2 * tag


# ---- seed_tags --------------------------------------------------------------------
def test_seed_tags_shared_first_and_limit(vault):
    index = vault(
        [
            {"name": "a", "tags": ["x", "y"]},
            {"name": "b", "tags": ["x", "z"]},
            {"name": "c", "tags": ["x"]},
        ]
    )
    assert seed_tags(index, ["a", "b", "c"], limit=1) == {"x"}
    assert seed_tags(index, ["a", "b"], limit=10) == {"x", "y", "z"}
    assert seed_tags(index, ["nope"]) == set()


# ---- 二段想起 ---------------------------------------------------------------------
@pytest.fixture
def web(vault):
    return vault(
        [
            {"name": "seed", "tags": ["tea", "life"], "links": ["A", "B"]},
            {"name": "A", "tags": ["tea"], "links": ["C"]},
            {"name": "B", "tags": ["tea", "life"], "links": ["C", "D"]},
            {"name": "C", "tags": ["tools"]},
            {"name": "D", "tags": ["tools"], "links": ["E"]},
            {"name": "E", "tags": ["other"]},
            {"name": "T", "tags": ["life"]},
        ]
    )


def test_structured_recall_does_not_return_seeds(web):
    notes = structured_recall(web, ["seed"], total=9, depth=3, now=NOW)
    keys = [n.key for n in notes]
    assert keys and "seed" not in keys
    assert all(isinstance(n, Note) for n in notes)
    assert len(keys) == len(set(keys))


def test_structured_recall_include_seeds_is_the_escape_hatch(web):
    notes = structured_recall(web, ["seed"], total=9, depth=3, include_seeds=True, now=NOW)
    assert [n.key for n in notes][0] == "seed"
    assert [n.key for n in notes].count("seed") == 1


def test_structured_recall_trace_shape(web):
    tr = structured_recall_trace(web, ["seed", "ghost"], total=9, depth=3, now=NOW)
    assert tr.seeds == ["seed"]                       # 索引に無い起点は落ちる
    assert set(tr.active_tags) == {"tea", "life"}     # 起点が背負うタグ
    assert tr.tag_recalled and "seed" not in tr.tag_recalled
    assert tr.link_expanded and not (set(tr.link_expanded) & set(tr.tag_recalled))
    assert tr.result_keys == tr.tag_recalled + tr.link_expanded   # タグ由来が先
    assert tr.degraded is False
    assert tr.slots == slots(9)
    # 2 段先の C（A と B の二本の道）・D・3 段先の E まで届く
    assert {"C", "D", "E"} <= set(tr.result_keys)


def test_structured_recall_ctx_tags_join_active(web):
    tr = structured_recall_trace(web, ["seed"], ctx_tags=["#other"], total=9, depth=1, now=NOW)
    assert "other" in tr.active_tags
    assert "E" in tr.tag_recalled


def test_structured_recall_fallback_to_seeds_only_when_structure_is_empty(vault):
    index = vault([{"name": "alone", "tags": []}, {"name": "far", "tags": ["z"]}])
    tr = structured_recall_trace(index, ["alone"], total=6, now=NOW)
    assert tr.result_keys == ["alone"] and tr.degraded is True
    tr2 = structured_recall_trace(index, ["alone"], total=6, fallback_to_seeds=False, now=NOW)
    assert tr2.result_keys == [] and tr2.degraded is False
    assert structured_recall(index, [], total=6, now=NOW) == []


def test_structured_recall_respects_slots(web):
    tr = structured_recall_trace(web, ["seed"], total=3, depth=3, link_min=1, tag_min=1, now=NOW)
    seed_n, link_n, tag_n = tr.slots
    assert len(tr.tag_recalled) <= tag_n
    assert len(tr.link_expanded) <= link_n
