# vault-recall

## Summary (English)

- Two-stage memory recall over an Obsidian-compatible Markdown vault (YAML frontmatter, `#tag`, `[[wikilink]]`), standard library only (PyYAML is used if present, never required).
- Core idea: **seeds are not results.** Whatever a vector/full-text search returns is only a *foothold*; the notes handed back are the ones reached from it through the vault's own structure (tags and links).
- Stage 1, tags: notes sharing the seeds' tags are scored with an IDF weight `log(N / df) + 1`, so a tag that sits on a third of the vault cannot bury the one thin thread specific to this input.
- Stage 2, links: `[[wikilinks]]` are followed up to `depth` hops; each hop multiplies by `hop_decay` (0.45). A note pointed at from several paths accumulates score, so "knots in the net" surface while distant single-path notes stay quiet.
- Link-first budget: the injection budget is split link:tag = 2:1 with floors; seeds do not consume it because they are not returned (`include_seeds=True` is the escape hatch).
- No LLM is asked to write a search query: context inference is done by vector (meaning) + tags/links (structure).
- `VaultIndex` builds an in-memory index (`entries`, `by_tag`, `by_slug`), resolves all four wikilink forms (`[[a]]`, `[[a|alias]]`, `[[a#section]]`, `[[dir/a]]`), merges frontmatter tags with body `#tags`, and can `touch()` a note's `last_accessed`.
- `ShortMemo` is a one-file todo ledger whose open items are rendered at the top of every recall until marked done; done/cancelled items are never deleted, only marked (`- [x]` / `- [-]`).
- Extracted from a running personal-assistant system; the README records the design decisions and the measurements that motivated them.
- 53 pytest tests cover both frontmatter parsers, link forms, tag merging, IDF ordering, hop decay, knot accumulation, slot arithmetic, seed exclusion and the memo ledger.

---

## 何か

Markdown の vault（Obsidian 互換: YAML frontmatter・`#tag`・`[[wikilink]]`）を**記憶**として読み、
ベクトル検索などの結果は**起点にだけ使って**、そこからタグとリンクで辿った記憶を返す二段想起です。
LLM に検索クエリを作らせません。文脈の類推は「ベクトル（意味）＋タグ／リンク（構造）」で担います。

同梱の `ShortMemo` は、日付の無い「後でやること」を一枚の Markdown に一行一件で持ち、
済むまで毎ターン想起の先頭に載せる短期メモです。済んだ物は消さず、印を付けます。

依存は標準ライブラリだけ。PyYAML が import できれば frontmatter の読みに使い、無ければ簡易パーサで読みます。

## なぜ注目に値するか

- **ベクトル検索の結果をそのまま LLM に流し込まない。** 意味が近いだけの記憶は、今の話題の「足場」には
  なるが「答え」にはならない。足場から vault の構造（人が張ったリンク・付けたタグ）を辿った先を返すと、
  会話の文脈が「よく似た文」ではなく「繋がっている記憶」で埋まる。
- **LLM にクエリを書かせる段を消した。** 「この入力で何を思い出すべきか」を LLM に訊くと、一往復ぶんの
  時間と費用が掛かり、しかも出来るクエリは入力の言い換えになりがち。タグ共有とリンク隣接という構造で置き換える。
- **常在タグの問題を IDF で解いた。** 大きくなった vault では、全体の三分の一に付くタグが出てくる
  （元のシステムの実測: 索引 4,742 件中 1,689 件＝36% に付くタグ、1,552 件＝33% に付くタグ）。
  素の重なり数で数えると「よく喋った話題」ばかりが上位を占め、その入力に固有の細い糸が沈む。
- **リンクの網を一段では使い切れない。** 元の vault は 4,936 件に 28,238 本（1 件あたり平均 5.7 本）の
  リンクが張られていた。隣だけを見るのは網の表層しか使っていない。段ごとに減衰を掛けて深く辿ると、
  遠い記憶は「複数の道から指されている」時だけ浮く。
- **Todo を記憶の経路に繋いだ。** 短期メモは別のツールではなく、想起の先頭に置かれる一塊。
  済んだ物も消さないので、何を済ませたかが台帳として残る。

## どう動くか（仕組み・名前の付いた考え方）

### 起点と想起結果を分ける（ベクトル検索は足場であって結果ではない）

```
ベクトル検索 → 起点を得る → vault を探索 → タグ・リンクを辿る → 関連記憶を取得 → 最終想起結果
```

`structured_recall(index, seed_keys, ...)` は `seed_keys` 自体を返しません。返すのは
**タグ由来 ＋ リンク由来** だけ。元の実装の docstring の言葉を借りれば
「起点は（縮退時を除いて）フィオへは渡らない」。構造から一件も引けなかった時だけ、
`fallback_to_seeds=True`（既定）なら起点で縮退します。`include_seeds=True` は退路で、起点を先頭に足します。

起点は「索引に実在する（＝そこから辿れる）」物だけを採ります。元のシステムではベクトルが返す上位 30 件のうち
索引に解決できるのは 17〜43% しか無かった（ベクトル DB にだけ在って .md が無い記憶）。
辿れない起点を選ぶと一歩も進めないので、辿れる物を優先するのが「起点」の第一条件です。

### リンク主軸（link-first）と枠

`slots(total, link_min=10, tag_min=5, seed_min=3)` は注入枠 `total` を (起点, リンク, タグ) に分けます。

```
seeds = max(seed_min, total - link_min - tag_min)   # 何件の足場から辿るか（返さないので total を食わない）
link  = max(link_min, total * 2 // 3)               # リンク:タグ = 2:1
tag   = max(tag_min,  total - link)
```

枠を切る理由（元の実測）: 枠を切らずに起点で埋めてからタグとリンクを末尾へ足していたら、注入の切り捨てで
**リンク由来が一件も残らなかった**（実測 0/5・0/1）。確保すると決めた枠が絞りで消えないよう、下限を敷きます。

### IDF で常在タグを割り引く（細い糸を沈めない）

`recall_by_tags(index, active_tags, exclude_keys, top_n, now)`:

```
点 = Σ_{共有タグ t} ( log(総数 / 出現数(t)) + 1 ) × (1 + importance / 1000) × 鮮度
```

全件に付くタグでも `+1` で 1.0 は残す（共有している事実は消さない）。固有のタグ一本は、常在タグ二本より重くなる。
`active_tags` は「入力から取った文脈タグ」と、**起点が実際に背負っているタグ**（`seed_tags`）の合算です。
タグは vault 側に在り、クエリ側に無い——入力文字列を手書きのキーワード表へ当てるだけでは、
元のシステムで 12 本の入力すべてがタグ 0 件でした。

### 段の減衰と網の結び目

`expand_links(index, seed_keys, depth=1, max_expand=2, hop_decay=0.45, now)`:

```
refs(x) = Σ_{x へ届く道} hop_decay ** 段      # 隣は 1.0、隣の隣は 0.45、その先は 0.2025 …
点(x)   = refs(x) × (1 + importance / 1000) × 鮮度
```

「隣は隣として重く、隣の隣は連想として軽く」。道は**別の道から来れば別に数える**ので、二つの記憶から
指されている結び目は二倍になり、そこから先へもその分だけ流れます。起点へ戻るリンクは数えません。
候補は先着順ではなく点で選び、**元の seed_keys は先頭に保ち**、重複は除きます。

### 鮮度

`recency(last_accessed, now, half_life_days=30)` は `0.5 ** (日数 / half_life_days)`。
元の実装は `0.995 ** days`（半減期 ≒ 138 日）で、`half_life_days=138.3` を渡せば同じ曲線になります。
`VaultIndex.touch(key, now)` でメモリ内の `last_accessed` を更新できます（ファイルには書きません）。

### Todo は済むまで先頭に載る短期メモ（済んだ物は消さず印を付ける）

一行一件。形は元のシステムと同じです。

```
- [ ] 内容｜語=a,b｜作 2026-11-04 21:20｜id=abc123
- [x] 内容｜語=a,b｜作 2026-11-04 21:20｜id=abc123｜済 2026-11-05
- [-] 内容｜語=a,b｜作 2026-11-04 21:20｜id=abc123｜取消 2026-11-05 理由
```

- `語` はその語が今の入力に出たら先に載せるための引っ掛け。
- 上限（既定 12）を超えた時は、語が今の入力に出た物を先に、残りは新しい順に載せる。
- `render(query_text)` が想起の先頭に置く一塊を返す。まだの物が無ければ空文字。例外は出さない。
- `done` / `cancel` は id でも内容の一部でも当てられる。二件以上当たれば `ValueError`（勝手に片方を選ばない）。

## 使い方（数行で動く例）

```python
from vault_recall import VaultIndex, structured_recall, ShortMemo

index = VaultIndex("path/to/vault").build()          # memories_subdir="02_Memories" で下位だけに絞れる
seed = index.resolve("紅茶の淹れ方")                   # ベクトル検索などで引いた一枚、という想定
for note in structured_recall(index, [seed], ctx_tags=["道具"], total=15, depth=3):
    print(note.key, sorted(note.tags))                 # 起点そのものは入らない

memo = ShortMemo("path/to/vault/短期メモ.md", limit=12)
mid = memo.add("茶葉の缶を買い足す", words=["紅茶", "缶"])
print(memo.render(query_text="紅茶を淹れる"))          # 想起の先頭に置く塊
memo.done(mid)                                       # 消えずに - [x] ｜済 日付 が付く
```

内訳が要るなら `structured_recall_trace(...)` が `RecallTrace`（起点・active_tags・タグ由来・リンク由来・
縮退したか・枠）を返します。点そのものは `tag_scores(...)` / `link_scores(...)`。

動く例（ダミーの vault 10 枚）:

```
cd <このフォルダ>
python examples/demo.py
```

## 出所

元のシステム（フィオ）の次の箇所から切り出しました（行番号は切り出し時点）。

| ここ | 元 |
|---|---|
| 三つの責務（タグ導出・キャッシュ索引・構造的想起＝LLM クエリ生成の置換） | `fio/core/obsidian_index.py:1-30`（冒頭 docstring） |
| `parse_frontmatter` / `parse_frontmatter_simple` | `fio/core/obsidian_index.py:276-324`（`_scalar_fm_lines`・`_parse_md`）、参考 `fio/core/obsidian_adapter.py:60-104`（`_parse_frontmatter`・`_parse_fm_lines`） |
| `extract_link_targets` / `normalize_link_target` | `fio/core/obsidian_index.py:335-356`（`_extract_link_targets`）、`fio/core/obsidian_index.py:1107-1153`（`_リンク先を解決` の slug 解決） |
| `VaultIndex`（entries / by_tag / by_slug・`_build_entry`・posting） | `fio/core/obsidian_index.py:357-374`（`_Index`）、`:426-458`（`_build_entry`）、`:463-475`（`_populate_postings`） |
| `recency` | `fio/core/obsidian_index.py:713-715`（`_recency`、`0.995 ** days`） |
| `seed_tags` | `fio/core/obsidian_index.py:229-270` |
| `recall_by_tags`（IDF） | `fio/core/obsidian_index.py:770-812` |
| `LINK_HOP_DECAY = 0.45` | `fio/core/obsidian_index.py:814` |
| `expand_links`（道の数 × importance × 鮮度・段の減衰・元の docs を先頭に） | `fio/core/obsidian_index.py:1156-1250` |
| `structured_recall`（起点 → タグ → リンク・起点は返さない・空なら縮退） | `fio/core/obsidian_index.py:1251-1277`、`fio/core/claude_client/__init__.py:559-600`（`_obsidian_second_stage` の手順と裁定） |
| `slots`（リンク:タグ = 2:1・下限・起点は枠を食わない） | `fio/core/claude_client/__init__.py:374-378`（枠の既定）、`:535-556`（`_想起の枠`） |
| `ShortMemo`（一行形式・済／取消の印・上限 12・語の優先・想起の先頭） | `fio/core/短期メモ.py:1-148`（全部）、先頭に置く所は `fio/core/obsidian_index.py:976-983` |

## フィオ本体との違い

落とした物（名前だけ挙げます）:

- **書庫（`05_書庫`）へのリンクと「名のシンボリックリンク」**（記憶の本文に作品名が出ていれば札へ辿る）
- **長期記憶保存（`06`）の札・年表・日記の想起経路**と、`expand_links` の中の書庫／長期の枠の確保
- **ディスクキャッシュ**（`.fio_obsidian_index.json`・件数と mtime を鍵にした再走査回避・`note_written` の増分反映）
- **`tag_rules.yaml` による多軸タグ導出**（`#k` `#imp` `#t` `#ctx` `#mode` `#rel`）と `query_tags` のキーワード表。
  ここでは frontmatter の `tags` と本文の `#tag` をそのまま使う
- **意識層の熱**・場面（`turn_event`）による枠の細め・env による枠の設定
- `rerank`（ベクトル候補を importance／鮮度／タグ重なりで並べ直す段）。起点の順序は呼び出し側に任せる
- 語句リンクの解決表（`リンクの解決`）と、頭の同じ記憶を別の鍵で持つ `by_head`
- 短期メモのプロセスをまたぐ錠（`台帳の書き込み.錠`）。ここでは同一プロセス内の錠と、一時ファイル＋置換の書き戻しだけ

単純化した物:

- 索引の鍵を「本文の頭 120 字」から**相対パス（拡張子なし）**へ。`[[dir/name]]` も `[[name]]` も `resolve` で引ける
- タグは `#` を剥がして持つ（Obsidian の frontmatter と同じ形）
- 本文で索引を引く口（`_本文の鍵`）は無い。起点は key で渡す（ベクトル検索の結果を key にするのは呼び出し側）
- 鮮度の半減期を引数にした（既定 30 日。元は 0.995**days ≒ 138 日）
- `structured_recall` の戻りは `list[Note]`。内訳は `structured_recall_trace` の `RecallTrace`

## 柵（tests）

53 本。`tests/test_index.py`（17）・`tests/test_recall.py`（24）・`tests/test_short_memo.py`（12）。

- frontmatter の両パーサ（PyYAML あり／なしを `monkeypatch` で切る）・壊れた YAML からスカラーを救う・日付の各形
- wikilink の四形（`[[a]]` `[[a|alias]]` `[[a#section]]` `[[dir/a]]`）と埋め込みの除外・大文字小文字
- `#tag` の抽出（見出し・`[[a#sec]]`・コード・数字だけは拾わない）と frontmatter との合算
- IDF: 全件に付くタグを二本共有する記憶より、固有タグを一本共有する記憶が上位・常在タグでも 1.0 は残る
- 段の減衰: 二段目は 0.45 倍・二本の道から指された結び目が二倍・起点へ戻るリンクは数えない
- `expand_links` が元の seeds を先頭に保つ・重複を除く・`max_expand=0`
- `slots` の 2:1 と下限
- `structured_recall` が起点を返さない・`include_seeds=True` なら先頭に・構造が空の時だけ縮退・枠を守る
- 短期メモ: 一行形式の往復・済の印・取消と理由・同じ内容の二重登録なし・部分一致と曖昧さ・上限と溢れ・語の優先・`render`

走らせたコマンドと結果:

```
cd <このフォルダ>
python -m pytest tests -q -p no:cacheprovider
53 passed in 0.35s
```

`python examples/demo.py` も実行して、起点が返す物に入っていないこと（demo 内の assert）を確かめました。

## 未了

- 索引のディスクキャッシュと増分反映（本体に在る。ここは毎回全走査。数千枚なら数秒）
- ベクトル検索との接続（起点を key で渡す所まで。どのベクトル DB で起点を得るかは呼び出し側）
- 本文で索引を引く口（本体の `_本文の鍵`。本文の頭が同じ記憶の見分けを含む）
- 短期メモのプロセスをまたぐ錠

ライセンス: [MIT](LICENSE)
