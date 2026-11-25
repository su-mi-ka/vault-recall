# -*- coding: utf-8 -*-
"""短期メモの柵: 一行形式の往復・済の印・取消・上限・語の優先・render。"""
import re

import pytest

from vault_recall import MemoItem, ShortMemo, format_line, parse_line

LINE_RE = re.compile(r"^- \[( |x|-)\] [^｜]*｜語=[^｜]*｜作 [^｜]*｜id=[0-9a-f]{6}(｜.*)?$")


@pytest.fixture
def memo(tmp_path):
    return ShortMemo(str(tmp_path / "09_短期メモ" / "短期メモ.md"), limit=3)


def read(memo):
    with open(memo.path, encoding="utf-8") as f:
        return f.read()


def test_add_creates_file_with_header_and_one_line_format(memo):
    mid = memo.add("茶葉の缶を買い足す", words=["紅茶", "缶"], now="2026-05-08 09:00")
    text = read(memo)
    assert text.startswith("# 短期メモ（後でやること）\n")
    line = text.rstrip("\n").splitlines()[-1]
    assert line == f"- [ ] 茶葉の缶を買い足す｜語=紅茶,缶｜作 2026-05-08 09:00｜id={mid}"
    assert LINE_RE.match(line)


def test_line_round_trip():
    line = "- [x] 内容｜語=a,b｜作 2026-11-04 21:20｜id=abc123｜済 2026-11-05"
    item = parse_line(line, 7)
    assert item == MemoItem(id="abc123", text="内容", words=["a", "b"], created="2026-11-04 21:20",
                            state="x", tail="済 2026-11-05", line_no=7)
    assert format_line(item) == line
    assert parse_line("- 普通の箇条書き") is None
    assert parse_line("- [ ] 形が違う｜id=zz") is None


def test_done_marks_without_deleting(memo):
    a = memo.add("一つ目", now="2026-05-08 09:00")
    b = memo.add("二つ目", now="2026-05-08 09:01")
    hit = memo.done(a, now="2026-05-09")
    assert hit is not None and hit.id == a and hit.is_done
    lines = read(memo).rstrip("\n").splitlines()
    assert lines[-2] == f"- [x] 一つ目｜語=｜作 2026-05-08 09:00｜id={a}｜済 2026-05-09"
    assert lines[-1].startswith("- [ ] 二つ目")
    assert [it.id for it in memo.open_items()] == [b]
    assert [it.id for it in memo.all_items()] == [a, b]
    assert memo.done(a) is None  # 済んだ物にはもう当たらない


def test_cancel_with_reason(memo):
    a = memo.add("やめる予定", now="2026-05-08 09:00")
    hit = memo.cancel(a, reason="基板を\n替えた", now="2026-05-09")
    assert hit.is_cancelled
    assert read(memo).rstrip("\n").splitlines()[-1] == (
        f"- [-] やめる予定｜語=｜作 2026-05-08 09:00｜id={a}｜取消 2026-05-09 基板を 替えた"
    )
    assert memo.open_items() == []


def test_add_same_open_text_returns_existing_id(memo):
    a = memo.add("同じ内容", now="2026-05-08 09:00")
    assert memo.add("  同じ内容 ", now="2026-05-08 09:01") == a   # 前後の空白は畳まれて同じ
    assert len(memo.all_items()) == 1
    memo.done(a)
    b = memo.add("同じ内容", now="2026-05-08 09:02")              # 済んだ後なら新しく足せる
    assert b != a and len(memo.all_items()) == 2


def test_key_by_substring_and_ambiguity(memo):
    memo.add("紅茶を買う", now="2026-05-08 09:00")
    memo.add("紅茶の缶を洗う", now="2026-05-08 09:01")
    with pytest.raises(ValueError):
        memo.done("紅茶")
    assert memo.done("缶を洗う") is not None
    assert memo.done("存在しない") is None
    assert memo.done("") is None
    assert memo.done("x") is None   # 一文字では当てない


def test_open_items_order_words_in_query_first_then_newest(memo):
    a = memo.add("古い・語なし", now="2026-05-08 09:00")
    b = memo.add("紅茶の缶", words=["紅茶"], now="2026-05-08 09:01")
    c = memo.add("新しい・語なし", now="2026-05-08 09:02")
    d = memo.add("紅茶と缶", words=["紅茶", "缶"], now="2026-05-08 09:03")
    assert [it.id for it in memo.open_items()] == [d, c, b, a]                      # 新しい順
    assert [it.id for it in memo.open_items(query_text="紅茶を淹れる")] == [d, b, c, a]  # 語が出た物が先
    assert [it.id for it in memo.open_items(query_text="紅茶の缶を洗う")] == [d, b, c, a]  # 当たった語の数が多い順
    assert [it.id for it in memo.open_items(limit=2)] == [d, c]


def test_render_limit_and_overflow(memo):
    ids = [memo.add(f"件 {i}", words=["語"] if i == 0 else [], now=f"2026-05-08 09:0{i}") for i in range(5)]
    out = memo.render()
    lines = out.splitlines()
    assert lines[0] == memo.title
    assert len(lines) == 1 + 3 + 1                      # 題・上限 3 件・溢れの行
    assert lines[1:4] == [f"- 件 4｜id={ids[4]}", f"- 件 3｜id={ids[3]}", f"- 件 2｜id={ids[2]}"]
    assert lines[-1] == "（ほか 2 件）"
    # 語が今の入力に出た物は、上限を超えていても先に残る
    out2 = memo.render(query_text="この語が出た")
    assert out2.splitlines()[1] == f"- 件 0｜id={ids[0]}"


def test_render_empty_when_nothing_open(memo):
    assert memo.render() == ""
    a = memo.add("一件", now="2026-05-08 09:00")
    assert memo.render() != ""
    memo.done(a)
    assert memo.render() == ""


def test_text_sanitized_one_line_and_separator(memo):
    memo.add("改行を\n含む｜区切りも", words=["a,b", " c "], now="2026-05-08 09:00")
    it = memo.open_items()[0]
    assert it.text == "改行を 含む/区切りも"
    assert it.words == ["a b", "c"]
    last_line = read(memo).rstrip("\n").splitlines()[-1]
    assert parse_line(last_line, it.line_no) == it


def test_add_empty_raises(memo):
    with pytest.raises(ValueError):
        memo.add("   ")
