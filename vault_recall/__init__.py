# -*- coding: utf-8 -*-
"""vault_recall — Markdown の vault を記憶として読む二段想起（起点 → タグ → リンク）。

* ``index``      : vault を一回走査して ``Note`` の索引を作る（frontmatter・#tag・[[wikilink]]）
* ``recall``     : 起点から IDF 付きタグ想起と段の減衰付きリンク想起で辿り、起点は返さない
* ``short_memo`` : 済むまで想起の先頭に載る短期メモ（Todo）。済んだ物は消さず印を付ける
"""
from .index import (
    DEFAULT_IMPORTANCE,
    Note,
    VaultIndex,
    extract_body_tags,
    extract_link_targets,
    normalize_link_target,
    normalize_tag,
    parse_frontmatter,
    parse_frontmatter_simple,
    to_epoch,
)
from .recall import (
    LINK_HOP_DECAY,
    RecallTrace,
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
from .short_memo import DEFAULT_LIMIT, MemoItem, ShortMemo, format_line, parse_line

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "DEFAULT_IMPORTANCE",
    "Note",
    "VaultIndex",
    "extract_body_tags",
    "extract_link_targets",
    "normalize_link_target",
    "normalize_tag",
    "parse_frontmatter",
    "parse_frontmatter_simple",
    "to_epoch",
    "LINK_HOP_DECAY",
    "RecallTrace",
    "expand_links",
    "link_scores",
    "recall_by_tags",
    "recency",
    "seed_tags",
    "slots",
    "structured_recall",
    "structured_recall_trace",
    "tag_scores",
    "DEFAULT_LIMIT",
    "MemoItem",
    "ShortMemo",
    "format_line",
    "parse_line",
]
