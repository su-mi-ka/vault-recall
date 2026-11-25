# -*- coding: utf-8 -*-
"""短期メモ: 日付の無い「後でやること」。済むまで毎ターン想起の先頭に載る。

置き場は一枚の Markdown。一行一件で、**済んだ物は消さず印を付ける**:

    - [ ] 内容｜語=a,b｜作 2026-11-04 21:20｜id=abc123
    - [x] 内容｜語=a,b｜作 2026-11-04 21:20｜id=abc123｜済 2026-11-05
    - [-] 内容｜語=a,b｜作 2026-11-04 21:20｜id=abc123｜取消 2026-11-05 理由

``語`` は「この語が今の入力に出たら先に載せる」ための引っ掛け。上限（既定 12 件）を超えた時は、
語が今の入力に出た物を先に、残りは新しい順に載せる。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional, Union

__all__ = ["MemoItem", "ShortMemo", "DEFAULT_LIMIT", "format_line", "parse_line"]

DEFAULT_LIMIT = 12

STATE_OPEN = " "
STATE_DONE = "x"
STATE_CANCELLED = "-"

_LINE_RE = re.compile(
    r"^- \[(?P<state> |x|-)\] (?P<text>[^｜]*)｜語=(?P<words>[^｜]*)｜作 (?P<created>[^｜]*)"
    r"｜id=(?P<id>[0-9a-f]+)(?:｜(?P<tail>.*))?$"
)

_DEFAULT_HEADER = (
    "# 短期メモ（後でやること）",
    "",
    "まだの物（`- [ ]`）は済むまで毎ターン想起の先頭に載る。`- [x]` は済んだ物、`- [-]` は取り消した物。",
    "",
)

Clock = Union[None, str, _dt.datetime]


@dataclass
class MemoItem:
    """一行一件。``line_no`` はファイルの中の行番号（0 始まり）。"""

    id: str
    text: str
    words: list[str] = field(default_factory=list)
    created: str = ""
    state: str = STATE_OPEN
    tail: str = ""
    line_no: int = -1

    @property
    def is_open(self) -> bool:
        return self.state == STATE_OPEN

    @property
    def is_done(self) -> bool:
        return self.state == STATE_DONE

    @property
    def is_cancelled(self) -> bool:
        return self.state == STATE_CANCELLED


def parse_line(line: str, line_no: int = -1) -> Optional[MemoItem]:
    """一行を ``MemoItem`` に読む。形が違えば None。"""
    m = _LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    g = m.groupdict()
    return MemoItem(
        id=g["id"],
        text=g["text"],
        words=[w for w in g["words"].split(",") if w],
        created=g["created"],
        state=g["state"],
        tail=g["tail"] or "",
        line_no=line_no,
    )


def format_line(item: MemoItem) -> str:
    s = f"- [{item.state}] {item.text}｜語={','.join(item.words)}｜作 {item.created}｜id={item.id}"
    return s + (f"｜{item.tail}" if item.tail else "")


def _one_line(text: str) -> str:
    """改行と連続空白を畳み、区切り字 ``｜`` を本文から追い出す。"""
    return " ".join((text or "").split()).replace("｜", "/")


def _stamp(now: Clock, fmt: str) -> str:
    if isinstance(now, str) and now:
        return now
    if isinstance(now, _dt.datetime):
        return now.strftime(fmt)
    return _dt.datetime.now().strftime(fmt)


class ShortMemo:
    """一枚の Markdown を台帳にした短期メモ。

    ``path`` が無ければ最初の ``add`` で作る。書き戻しは一時ファイル＋置換（途中で落ちても
    前の版が残る）。同じプロセスの中では錠で一本ずつ通す。
    """

    def __init__(self, path: str, limit: int = DEFAULT_LIMIT, header: Iterable[str] = _DEFAULT_HEADER,
                 title: str = "〔短期メモ｜後でやること。済んだら done〕"):
        self.path = path
        self.limit = int(limit)
        self.header = list(header)
        self.title = title
        self._lock = threading.RLock()

    # ---- 読み書き ---------------------------------------------------------------
    def _read(self) -> tuple[list[str], list[MemoItem]]:
        if not os.path.exists(self.path):
            return [], []
        with open(self.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        items = [it for it in (parse_line(ln, i) for i, ln in enumerate(lines)) if it is not None]
        return lines, items

    def _write(self, lines: list[str]) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        content = "\n".join(lines).rstrip("\n") + "\n"
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp, self.path)

    # ---- 操作 -------------------------------------------------------------------
    def add(self, text: str, words: Iterable[str] = (), now: Clock = None) -> str:
        """一件足して id を返す。同じ内容のまだのメモが在れば、その id を返す（二重に足さない）。"""
        text = _one_line(text)
        if not text:
            raise ValueError("text is empty")
        ws = [w for w in (_one_line(x).replace(",", " ") for x in words) if w]
        with self._lock:
            lines, items = self._read()
            for it in items:
                if it.is_open and it.text == text:
                    return it.id
            item = MemoItem(id=uuid.uuid4().hex[:6], text=text, words=ws,
                            created=_stamp(now, "%Y-%m-%d %H:%M"))
            self._write((lines or list(self.header)) + [format_line(item)])
        return item.id

    def _mark(self, key: str, state: str, tail: str) -> Optional[MemoItem]:
        key = (key or "").strip()
        if not key:
            return None
        with self._lock:
            lines, items = self._read()
            open_items = [it for it in items if it.is_open]
            hits = [it for it in open_items if it.id == key]
            if not hits and len(key) >= 2:
                hits = [it for it in open_items if key in it.text]
            if len(hits) > 1:
                raise ValueError(
                    "ambiguous key: " + " / ".join(f"id={it.id} {it.text}" for it in hits)
                )
            if not hits:
                return None
            it = hits[0]
            it.state = state
            it.tail = tail
            lines[it.line_no] = format_line(it)
            self._write(lines)
            return it

    def done(self, key: str, now: Clock = None) -> Optional[MemoItem]:
        """id（または内容の一部）で一件を済にする。当たらなければ None。二件以上当たれば ValueError。"""
        return self._mark(key, STATE_DONE, f"済 {_stamp(now, '%Y-%m-%d')}")

    def cancel(self, key: str, reason: str = "", now: Clock = None) -> Optional[MemoItem]:
        """id（または内容の一部）で一件を取消にする。理由は尾に残る。"""
        tail = f"取消 {_stamp(now, '%Y-%m-%d')}" + (f" {_one_line(reason)}" if reason else "")
        return self._mark(key, STATE_CANCELLED, tail)

    def all_items(self) -> list[MemoItem]:
        """済・取消も含めた全件（ファイルの順）。"""
        return self._read()[1]

    def open_items(self, limit: Optional[int] = None, query_text: str = "") -> list[MemoItem]:
        """まだの物。``query_text`` に語が出た物を先に（当たった語の数の多い順）、残りは新しい順。

        ``limit`` が None なら全件。
        """
        items = [it for it in self._read()[1] if it.is_open]
        q = query_text or ""
        order = sorted(
            enumerate(items),
            key=lambda x: (-sum(1 for w in x[1].words if w and w in q), -x[0]),
        )
        out = [it for _, it in order]
        return out if limit is None else out[: max(0, int(limit))]

    def render(self, query_text: str = "") -> str:
        """想起の先頭に置く一塊。まだの物が無ければ空文字。例外は出さない。"""
        try:
            items = self.open_items(query_text=query_text)
            if not items:
                return ""
            shown = items[: self.limit]
            out = [self.title]
            out += [f"- {it.text}｜id={it.id}" for it in shown]
            if len(items) > self.limit:
                out.append(f"（ほか {len(items) - self.limit} 件）")
            return "\n".join(out)
        except Exception:
            return ""
