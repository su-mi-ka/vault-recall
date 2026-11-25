# -*- coding: utf-8 -*-
"""索引の柵: frontmatter の両パーサ・wikilink の四形・#tag の合算・build/resolve/touch。"""
import datetime as dt
import os

import pytest

import vault_recall.index as idx_mod
from vault_recall import (
    DEFAULT_IMPORTANCE,
    VaultIndex,
    extract_body_tags,
    extract_link_targets,
    normalize_link_target,
    parse_frontmatter,
    parse_frontmatter_simple,
    to_epoch,
)
from tests.conftest import NOW, write_note

SAMPLE = """---
type: semantic
importance: 640
created: 2026-03-01
title: "紅茶の淹れ方"
tags: [紅茶, "暮らし"]
links:
  - "[[茶器の手入れ]]"
  - 茶葉の保存
---
# 紅茶の淹れ方

本文。 #手順
"""


@pytest.fixture(params=["yaml", "simple"])
def parser_mode(request, monkeypatch):
    """PyYAML あり／なしの両方で同じ柵を回す。"""
    if request.param == "simple":
        monkeypatch.setattr(idx_mod, "_yaml", None)
    else:
        if idx_mod._yaml is None:
            pytest.skip("PyYAML がこの環境に無い")
    return request.param


def test_frontmatter_both_parsers_agree(parser_mode):
    meta, body = parse_frontmatter(SAMPLE)
    assert meta["type"] == "semantic"
    assert meta["importance"] == 640
    assert meta["title"] == "紅茶の淹れ方"
    assert list(meta["tags"]) == ["紅茶", "暮らし"]
    assert list(meta["links"]) == ["[[茶器の手入れ]]", "茶葉の保存"]
    assert to_epoch(meta["created"], 0) == to_epoch("2026-03-01", 0)
    assert body.startswith("# 紅茶の淹れ方")


def test_frontmatter_absent_returns_text_untouched():
    text = "# 見出し\n\n本文だけ。\n"
    assert parse_frontmatter(text) == ({}, text)


def test_frontmatter_simple_parser_forms():
    meta = parse_frontmatter_simple(
        "a: 1\nb: 'x'\nc: [p, q]\nd:\n  - r\n  - s\ne: true\nnested:\n  k: v\n# comment\n"
    )
    assert meta == {"a": 1, "b": "x", "c": ["p", "q"], "d": ["r", "s"], "e": True}


def test_frontmatter_broken_yaml_still_saves_scalars(monkeypatch):
    if idx_mod._yaml is None:
        pytest.skip("PyYAML がこの環境に無い")
    text = "---\nimportance: 300\nlinks: [[[#壊れた\n---\n本文\n"
    meta, body = parse_frontmatter(text)
    assert meta["importance"] == 300
    assert body == "本文\n"


def test_to_epoch_accepts_many_forms():
    d = dt.date(2026, 3, 1)
    assert to_epoch(d, 0) == to_epoch("2026-03-01", 0) == to_epoch(dt.datetime(2026, 3, 1), 0)
    assert to_epoch(1700000000, 0) == 1700000000
    assert to_epoch("1700000000", 0) == 1700000000
    assert to_epoch("2026-03-01 09:30", 0) == int(dt.datetime(2026, 3, 1, 9, 30).timestamp())
    assert to_epoch("not a date", 42) == 42
    assert to_epoch(None, 42) == 42


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("link", "link"),
        ("link|alias", "link"),
        ("link#section", "link"),
        ("dir/link", "dir/link"),
        ("dir/link#sec|alias", "dir/link"),
        ("  spaced   name.md ", "spaced name"),
        ("#section-only", ""),
    ],
)
def test_normalize_link_target_four_forms(raw, expected):
    assert normalize_link_target(raw) == expected


def test_extract_link_targets_merges_frontmatter_and_body_and_drops_embeds():
    meta = {"links": ["[[A]]", "B", "[[A]]"]}
    body = "x [[C|見せ名]] y [[D#節]] z [[dir/E]] ![[img.png]] [[A]]"
    assert extract_link_targets(meta, body) == ["A", "B", "C", "D", "dir/E"]


def test_extract_body_tags_rules():
    body = (
        "# 見出し\n"
        "本文 #紅茶 と #暮らし/朝、それから [[link#節]] と `#code` と\n"
        "```\n#block\n```\n"
        "URL https://host/page#frag と #123 と ##二重\n"
    )
    assert extract_body_tags(body) == {"紅茶", "暮らし/朝"}


def test_build_merges_frontmatter_tags_with_body_tags(vault):
    index = vault([{"name": "A", "tags": ["紅茶"], "body": "本文 #手順 #紅茶"}])
    note = index.get("A")
    assert note is not None
    assert note.tags == {"紅茶", "手順"}
    assert index.by_tag["紅茶"] == ["A"]
    assert index.by_tag["手順"] == ["A"]


def test_build_defaults(vault):
    index = vault([{"name": "A", "created": None, "last_accessed": None}])
    note = index.get("A")
    assert note.importance == DEFAULT_IMPORTANCE == 500
    assert note.created == NOW  # 欠けていれば build の now
    assert note.last_accessed == note.created
    assert note.title == "A"


def test_build_nested_dir_slug_and_resolve_four_forms(vault):
    index = vault(
        [
            {"name": "紅茶の淹れ方", "links": ["茶器の手入れ|茶器", "工作/はんだ付けの基本", "茶葉の保存#湿気"]},
            {"name": "茶器の手入れ"},
            {"name": "工作/はんだ付けの基本"},
            {"name": "茶葉の保存"},
        ]
    )
    assert set(index.entries) == {"紅茶の淹れ方", "茶器の手入れ", "工作/はんだ付けの基本", "茶葉の保存"}
    assert index.get("工作/はんだ付けの基本").slug == "はんだ付けの基本"
    # by_slug は相対パスと末尾の名前の両方で引ける
    assert index.by_slug["はんだ付けの基本"] == "工作/はんだ付けの基本"
    assert index.by_slug["工作/はんだ付けの基本"] == "工作/はんだ付けの基本"
    # resolve の四形
    assert index.resolve("茶器の手入れ") == "茶器の手入れ"
    assert index.resolve("茶器の手入れ|茶器") == "茶器の手入れ"
    assert index.resolve("茶葉の保存#湿気") == "茶葉の保存"
    assert index.resolve("工作/はんだ付けの基本") == "工作/はんだ付けの基本"
    assert index.resolve("はんだ付けの基本") == "工作/はんだ付けの基本"
    assert index.resolve("別の場所/はんだ付けの基本") == "工作/はんだ付けの基本"
    assert index.resolve("存在しない") is None
    assert index.resolve("") is None
    # neighbors は出た順・解決できた物だけ
    assert index.neighbors("紅茶の淹れ方") == ["茶器の手入れ", "工作/はんだ付けの基本", "茶葉の保存"]


def test_resolve_is_case_insensitive(vault):
    index = vault([{"name": "Keyboard Build"}])
    assert index.resolve("keyboard build") == "Keyboard Build"
    assert index.resolve("KEYBOARD BUILD|kb") == "Keyboard Build"


def test_memories_subdir_and_hidden_dirs(tmp_path):
    root = str(tmp_path / "v")
    write_note(os.path.join(root, "02_Memories"), "inside", created=NOW, last_accessed=NOW)
    write_note(root, "outside", created=NOW, last_accessed=NOW)
    write_note(os.path.join(root, "02_Memories", ".obsidian"), "hidden", created=NOW, last_accessed=NOW)
    index = VaultIndex(root, memories_subdir="02_Memories").build(now=NOW)
    assert set(index.entries) == {"inside"}
    whole = VaultIndex(root).build(now=NOW)
    assert set(whole.entries) == {"02_Memories/inside", "outside"}
    assert whole.resolve("inside") == "02_Memories/inside"


def test_touch_updates_last_accessed_in_memory_only(vault):
    index = vault([{"name": "A", "last_accessed": NOW - 86400 * 10}])
    path = index.get("A").path
    before = open(path, encoding="utf-8").read()
    assert index.touch("A", now=NOW) is True
    assert index.get("A").last_accessed == NOW
    assert index.touch("nope", now=NOW) is False
    assert open(path, encoding="utf-8").read() == before


def test_note_without_frontmatter_is_indexed(vault):
    index = vault([{"name": "plain", "frontmatter": False, "body": "本文 #タグ", "created": None, "last_accessed": None}])
    note = index.get("plain")
    assert note is not None and note.tags == {"タグ"} and note.importance == 500


def test_rebuild_is_idempotent(vault):
    index = vault([{"name": "A", "tags": ["t"]}, {"name": "B", "tags": ["t"]}])
    index.build(now=NOW)
    assert index.by_tag["t"] == ["A", "B"]
    assert len(index) == 2
