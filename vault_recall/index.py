# -*- coding: utf-8 -*-
"""Markdown の vault（Obsidian 互換）を記憶として読む索引。

一枚の .md を一つの ``Note`` にする。読むのは三つだけ:

* YAML frontmatter（``---`` で囲んだ頭。PyYAML が在ればそれで、無ければ簡易パーサで）
* 本文の ``#tag``（frontmatter の ``tags`` と合算する）
* 本文の ``[[wikilink]]``（``[[name]]`` / ``[[name|alias]]`` / ``[[name#section]]`` /
  ``[[dir/name]]`` の四形。指し先の名前だけを正規化して持ち、解決は ``resolve`` が行う）

索引は ``entries``（key → Note）・``by_tag``（tag → [key...] の posting）・``by_slug``
（名前 → key）の三つ。全部メモリの中に置き、ディスクには何も書かない。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

try:  # PyYAML は在れば使う・無くても動く（依存にしない）
    import yaml as _yaml  # type: ignore
except Exception:  # pragma: no cover - 環境依存
    _yaml = None

__all__ = [
    "Note",
    "VaultIndex",
    "DEFAULT_IMPORTANCE",
    "parse_frontmatter",
    "parse_frontmatter_simple",
    "extract_body_tags",
    "extract_link_targets",
    "normalize_link_target",
    "normalize_tag",
    "to_epoch",
]

#: frontmatter に importance が無い時の既定。
DEFAULT_IMPORTANCE = 500

_FM_DELIM = "---"

# [[...]] の中身。``![[...]]`` の埋め込みも同じ形で拾う（後で捨てる）。
_WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]]+)\]\]")
# 本文の #tag。直前が語の一部・``/``・``#`` なら拾わない（URL の fragment や ``##`` 見出し）。
# タグに使える字は Obsidian と同じ範囲に寄せる: 英数・``_``・``-``・``/``・非 ASCII。
_BODY_TAG_RE = re.compile(r"(?<![\w/#])#([\w\-/]+)", re.UNICODE)
# 句読点や括弧がタグの尾に付いたら落とす（``\w`` は記号を含まないので通常は不要だが保険）
_TAG_TRAIL_RE = re.compile(r"[、。，．！？!?,.;:）)\]」』]+$")


# =============================================================================
# Note
# =============================================================================
@dataclass
class Note:
    """vault の一枚。``key`` は索引の中で唯一（既定はルートからの相対パス・拡張子なし）。"""

    key: str
    path: str
    title: str
    body: str
    tags: set[str] = field(default_factory=set)
    links: list[str] = field(default_factory=list)
    importance: int = DEFAULT_IMPORTANCE
    created: int = 0
    last_accessed: int = 0
    meta: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def slug(self) -> str:
        """ファイル名（拡張子なし）。``[[slug]]`` で指される名前。"""
        return self.key.rsplit("/", 1)[-1]


# =============================================================================
# frontmatter
# =============================================================================
def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Markdown の頭の YAML frontmatter を ``(meta, body)`` に分ける。

    frontmatter が無ければ ``({}, text)``。PyYAML が import できればそれで読み、
    無い（または壊れていて読めない）時は ``parse_frontmatter_simple`` へ倒れる。
    YAML が読めても ``importance`` / ``created`` のような一行スカラーが欠けていれば、
    簡易パーサの結果で補う（元の実装と同じ作法: 一行スカラーは壊れた YAML からも救う）。
    """
    text = text.replace("\r\n", "\n")
    if not text.startswith(_FM_DELIM + "\n"):
        return {}, text
    parts = text.split("\n" + _FM_DELIM + "\n", 1)
    if len(parts) < 2:
        # 本文が無く ``---`` で終わっている形も許す
        stripped = text.rstrip("\n")
        if stripped.endswith("\n" + _FM_DELIM):
            parts = [stripped[: -len(_FM_DELIM)].rstrip("\n"), ""]
        else:
            return {}, text
    fm_raw = parts[0][len(_FM_DELIM) + 1 :]
    body = parts[1].lstrip("\n")
    meta: dict = {}
    if _yaml is not None:
        try:
            loaded = _yaml.safe_load(fm_raw)
            if isinstance(loaded, dict):
                meta = dict(loaded)
        except Exception:
            meta = {}
    if not meta or "importance" not in meta or "created" not in meta:
        simple = parse_frontmatter_simple(fm_raw)
        for k, v in simple.items():
            meta.setdefault(k, v)
    return meta, body


def parse_frontmatter_simple(fm_raw: str) -> dict:
    """PyYAML 不在時の簡易パーサ（一階層だけ）。

    読める形:
      * ``key: value``（整数は int に、引用符は剥がす）
      * ``key: [a, b, c]``
      * ``key:`` の次の行から続く ``- item`` のブロックリスト
    入れ子のマップは読まない（記憶のメタは一階層しか使わない）。
    """
    meta: dict = {}
    pending_list_key: Optional[str] = None
    for raw in fm_raw.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") or stripped == "-":
            if pending_list_key is not None:
                item = stripped[1:].strip()
                if item:
                    meta.setdefault(pending_list_key, []).append(_unquote(item))
            continue
        if line[0] in " \t":
            continue  # 入れ子の中身は読まない
        pending_list_key = None
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if not val:
            pending_list_key = key   # 次の行から ``- item`` が続けばリスト。続かなければ鍵ごと無い扱い
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            meta[key] = [_unquote(v.strip()) for v in inner.split(",") if v.strip()]
        elif val.startswith(("[", "{")):
            continue  # 閉じていないリスト・マップ値は読まない（壊れた行は飛ばして他を救う）
        else:
            meta[key] = _scalar(val)
    return meta


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _scalar(val: str):
    v = _unquote(val)
    if v.lstrip("-").isdigit():
        try:
            return int(v)
        except ValueError:
            return v
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    return v


# =============================================================================
# タグとリンク
# =============================================================================
def normalize_tag(tag: str) -> str:
    """タグの正規化。頭の ``#`` を剥がし、前後の空白と尾の句読点を落とす。"""
    t = str(tag).strip()
    while t.startswith("#"):
        t = t[1:]
    t = _TAG_TRAIL_RE.sub("", t).strip()
    return t


def _tags_from_meta(meta: dict) -> set[str]:
    raw = meta.get("tags")
    if raw is None:
        raw = meta.get("tag")
    out: set[str] = set()
    if raw is None:
        return out
    if isinstance(raw, str):
        items: Iterable = re.split(r"[,\s]+", raw)
    elif isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = [raw]
    for x in items:
        t = normalize_tag(str(x))
        if t:
            out.add(t)
    return out


def extract_body_tags(body: str) -> set[str]:
    """本文の ``#tag`` を集める。``[[link#section]]`` の中・コードの中・数字だけのタグは拾わない。"""
    if not body:
        return set()
    cleaned = _WIKILINK_RE.sub(" ", body)
    cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.S)
    cleaned = re.sub(r"`[^`\n]*`", " ", cleaned)
    out: set[str] = set()
    for m in _BODY_TAG_RE.finditer(cleaned):
        t = normalize_tag(m.group(1))
        if not t or t.replace("/", "").replace("-", "").replace("_", "").isdigit():
            continue
        out.add(t)
    return out


def normalize_link_target(raw: str) -> str:
    """``[[...]]`` の中身から、指し先の名前だけを取り出す。

    ``name|alias`` → ``name`` / ``name#section`` → ``name`` / ``dir/name`` はそのまま
    （``resolve`` が末尾の名前でも引ける）/ ``.md`` の拡張子は落とす / 空白は一つに畳む。
    ``#section`` だけ（同じノート内の見出し）なら空文字。
    """
    t = str(raw).strip()
    t = t.split("|", 1)[0]
    t = t.split("#", 1)[0]
    t = t.strip().replace("\\", "/")
    if t.lower().endswith(".md"):
        t = t[:-3]
    t = " ".join(t.split())
    return t.strip("/").strip()


def extract_link_targets(meta: dict, body: str) -> list[str]:
    """frontmatter の ``links`` と本文の ``[[wikilink]]`` から指し先の名前を集める。

    埋め込み（``![[画像.png]]``）はリンクではないので捨てる。出た順・重複なし。
    """
    targets: list[str] = []
    raw = meta.get("links")
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, (list, tuple)):
        for x in raw:
            s = str(x)
            found = _WIKILINK_RE.findall(s)
            if found:
                targets.extend(name for bang, name in found if not bang)
            elif s.strip():
                targets.append(s.strip())
    for bang, name in _WIKILINK_RE.findall(body or ""):
        if not bang:
            targets.append(name)
    out: list[str] = []
    for t in targets:
        n = normalize_link_target(t)
        if n and n not in out:
            out.append(n)
    return out


# =============================================================================
# 日付
# =============================================================================
def to_epoch(value, default: int) -> int:
    """frontmatter の日時を epoch 秒（int）にする。

    int / float / ``YYYY-MM-DD`` / ``YYYY-MM-DD HH:MM[:SS]`` / ISO 8601 /
    PyYAML が作る ``datetime.date`` ``datetime.datetime`` を受ける。読めなければ ``default``。
    naive な日時はローカル時刻として扱う。
    """
    if value is None or value == "" or isinstance(value, bool):
        return int(default)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, _dt.datetime):
        return int(value.timestamp())
    if isinstance(value, _dt.date):
        return int(_dt.datetime(value.year, value.month, value.day).timestamp())
    s = str(value).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s, fmt).timestamp())
        except ValueError:
            pass
    try:
        return int(_dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(default)


def _title_of(meta: dict, body: str, slug: str) -> str:
    t = meta.get("title")
    if isinstance(t, str) and t.strip():
        return t.strip()
    for line in (body or "").splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s:
            break
    return slug


# =============================================================================
# VaultIndex
# =============================================================================
class VaultIndex:
    """vault（ディレクトリ）を一回走査して、記憶の索引をメモリに持つ。

    ``root`` の下の ``*.md`` を再帰的に読む。``memories_subdir`` を与えると、その下だけを
    読む（本体では ``02_Memories`` だけが想起の索引だった）。``.`` で始まるディレクトリ
    （``.obsidian`` など）は飛ばす。
    """

    def __init__(self, root: str, memories_subdir: Optional[str] = None):
        self.root = os.path.abspath(root)
        self.memories_subdir = memories_subdir
        self.entries: dict[str, Note] = {}
        self.by_tag: dict[str, list[str]] = {}
        self.by_slug: dict[str, str] = {}
        self._by_slug_fold: dict[str, str] = {}

    # ---- 構築 ---------------------------------------------------------------
    @property
    def scan_root(self) -> str:
        if self.memories_subdir:
            return os.path.join(self.root, self.memories_subdir)
        return self.root

    def build(self, now: Optional[int] = None) -> "VaultIndex":
        """全走査して索引を作り直す。``now`` は created/last_accessed の欠けた時の既定。"""
        now = int(now if now is not None else _dt.datetime.now().timestamp())
        self.entries.clear()
        base = self.scan_root
        if os.path.isdir(base):
            for path in sorted(self._iter_md(base)):
                note = self._load(path, base, now)
                if note is not None:
                    self.entries[note.key] = note
        self._populate_postings()
        return self

    def _iter_md(self, base: str) -> Iterator[str]:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in filenames:
                if fn.lower().endswith(".md") and not fn.startswith("."):
                    yield os.path.join(dirpath, fn)

    def _load(self, path: str, base: str, now: int) -> Optional[Note]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                meta, body = parse_frontmatter(f.read())
        except (OSError, UnicodeDecodeError):
            return None
        rel = os.path.relpath(path, base).replace("\\", "/")
        key = rel[:-3] if rel.lower().endswith(".md") else rel
        slug = key.rsplit("/", 1)[-1]
        tags = _tags_from_meta(meta) | extract_body_tags(body)
        try:
            importance = int(meta.get("importance", DEFAULT_IMPORTANCE) or DEFAULT_IMPORTANCE)
        except (TypeError, ValueError):
            importance = DEFAULT_IMPORTANCE
        created = to_epoch(meta.get("created"), now)
        last_accessed = to_epoch(meta.get("last_accessed"), created)
        return Note(
            key=key,
            path=path,
            title=_title_of(meta, body, slug),
            body=body,
            tags=tags,
            links=extract_link_targets(meta, body),
            importance=importance,
            created=created,
            last_accessed=last_accessed,
            meta=meta,
        )

    def _populate_postings(self) -> None:
        self.by_tag.clear()
        self.by_slug.clear()
        self._by_slug_fold.clear()
        for key, note in self.entries.items():
            # 相対パス（拡張子なし）は必ず引ける。末尾の名前だけでも引ける（先に読んだ物が勝つ）。
            self.by_slug[key] = key
            self._by_slug_fold.setdefault(key.casefold(), key)
            self.by_slug.setdefault(note.slug, key)
            self._by_slug_fold.setdefault(note.slug.casefold(), key)
            for t in sorted(note.tags):
                self.by_tag.setdefault(t, []).append(key)

    # ---- 参照 ---------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, key: object) -> bool:
        return key in self.entries

    def get(self, key: str) -> Optional[Note]:
        return self.entries.get(key)

    def resolve(self, name: str) -> Optional[str]:
        """``[[name]]`` の指し先を索引の key にする。無ければ None。

        ``dir/name`` も ``name`` も ``name|alias`` も ``name#section`` も受ける。
        大文字小文字は区別しない（Obsidian と同じ）。
        """
        n = normalize_link_target(name)
        if not n:
            return None
        hit = self.by_slug.get(n)
        if hit is not None:
            return hit
        hit = self._by_slug_fold.get(n.casefold())
        if hit is not None:
            return hit
        # ``dir/name`` で dir が違っていても、末尾の名前で引ける
        tail = n.rsplit("/", 1)[-1]
        if tail != n:
            return self.by_slug.get(tail) or self._by_slug_fold.get(tail.casefold())
        return None

    def neighbors(self, key: str) -> list[str]:
        """一枚の記憶から [[link]] で出ている先を、解決できた key だけ出た順に返す（重複なし）。"""
        note = self.entries.get(key)
        if note is None:
            return []
        out: list[str] = []
        for target in note.links:
            k = self.resolve(target)
            if k is not None and k not in out:
                out.append(k)
        return out

    def touch(self, key: str, now: Optional[int] = None) -> bool:
        """``last_accessed`` をメモリの中で今に更新する（ファイルには書かない）。"""
        note = self.entries.get(key)
        if note is None:
            return False
        note.last_accessed = int(now if now is not None else _dt.datetime.now().timestamp())
        return True

    def tags_of(self, keys: Iterable[str]) -> dict[str, int]:
        """与えた記憶たちが背負うタグを、共有している枚数で数える。"""
        count: dict[str, int] = {}
        for k in keys:
            note = self.entries.get(k)
            if note is None:
                continue
            for t in note.tags:
                count[t] = count.get(t, 0) + 1
        return count
