#!/usr/bin/env python3
"""マニフェストの項目を、テストケースごとに組みながら順に走らせ、証跡と同名のダンプを撮る。

  run_flows.py <manifest.json>
  run_flows.py <manifest.json> --value '<値>'   入力が未定で止まったときの返事。止まった手の値
                                                 （打つ文字、選んだ要素の ID）を inputs に書いて続ける
  run_flows.py <manifest.json> --next          条件つきの選択で止まったとき、見せた候補に合うものが
                                                 無い。1画面送って、次の候補を出す

**止まったときの返事は引数で渡す。マニフェストは手で書き換えない。** どの項目・端末・鍵の値かは、
止まったときに `resume.ask` に書いてある（呼び出し元が場所を指し直さない）。`inputs` に書くのは
ここで、書く前に確かめられる。

**何をするかはマニフェストで決まる**（引数では選ばない）。

  1. `resume` があれば、止まったところから続ける
  2. 判定が `RETAKE` の項目か、撮れなかった項目（`unexpected`）があれば、それだけを撮り直す（下の「撮り直し」）
  3. どちらも無ければ、全部を最初から撮る

**どのシミュレーターで撮るかはマニフェストの `devices` に、記録の置き場は `flows` に
入っている**（手順0で manifest.py が書いたもの）。ここで渡し直さない。その UDID が起動していなければ止まる — 別の端末で代用しない。

**フローはテストケースごとに、走らせる直前に組む**（flowgen/flow.py）。前のテストケースが
終わった状態から組む。組むのに要るものはマニフェストから読み、plan.json は読まない。

**1項目は「経路のフロー」と「1手ずつ」に分けて流す**。

  - **経路**（前の画面から `from` まで）は1本のフロー。要素は `scrollUntilVisible` で探す。
    マップに載っていて基本的に必ずある要素なので、それで困らない。ただし経路の途中で
    パターンの要素を押す手（`経路 tap:…`）と、子の要素・その親を決める手（`経路 in …`）は
    `do` と同じく1手ずつ流す
  - **`do` の要素（と上の経路の1手）は、ここがダンプを読みながら探す**（`Seeker.hunt()`）。見つからなければ1回送って
    読み直し、画面の中に見えている ID が送る前と同じなら端とする。下の端 → 上の端まで探して
    無ければ、送らずに少し読み直してから、その項目は撮れなかったことにして次へ進む。
    フローの `scrollUntilVisible` はスクロールの端を検知しないので、無い要素では上限の60秒
    （上にも探せば120秒）を払ってしまう。着いた画面が意図した状態になっていない（ログインして
    いない、データが無い、絞り込みの結果が空）と、確かめたい要素はそもそも無い
  - **見つかったら、1手ぶんのフロー（操作と、着いた確認）を流す。** 値（打つ文字、押す行、行が
    含む語）はここで決め、フローに直接書く。着いた確認（anchor・`ready`・`expect`）は
    `extendedWaitUntil` の10秒上限で、端の問題が無いのでフローに任せる
  - 子の要素（マップの `children`。横スクロールの中）は、親を縦に寄せてから、親の枠の中に見えて
    いる ID を比べながら親を横に送る。端の決め方は縦と同じ
  - マップに無い文言を待つ `see:text:<文言>` は探さない。フローで出るまで待つ（10秒）

**流したものは `<flows>/<端末>/<名前>.yaml` に1本で残す**（記録）。経路のフローと1手ずつの
フローを流した順に並べ、ダンプで探して送った回数も `scroll` / `swipe` の行で残す。値は
埋めたまま。何を待って撮ったかを確かめるのはこれで、Maestro でそのまま流しても再現する
（データが変われば、同じ回数送っても同じ位置には着かない）。

**1回で全端末を撮る。** マニフェストの順に1台ずつ撮り切ってから次の端末へ移る（Maestro の
デーモンが握れるのは1台で、行き来させると切り替えのたびに約10秒かかる）。撮影先は
`<出力先>/shots/<端末>`。進捗ログは `<出力先>/progress_<端末>.log`。実行時に決める値は端末ごと
— その端末の画面を見て決める。

**前の回の残りを片付けてから撮る。** 最初から撮るときは進捗ログを書き直し、マニフェストに
無い名前の証跡とダンプと、前の回の記録を消す。撮る項目は、撮る前にその項目の前の回の証跡とダンプを消し、
判定を `PENDING` に戻す（撮れずに終わっても、前の回の画像と OK がレポートに残らない）。
再開と撮り直しのときは進捗ログに区切りの行を足して続ける。

**未定で止まったら、叩き直すと続きから走る**（撮り直しは除く。下の「撮り直し」）。撮り終えた端末は飛ばし、止まった端末の
止まった項目の止まった手から走る。状態はマニフェストの `resume` に書き、走り切ったら消す。`resume` には
止まったテストケースを組んだ状態（`cursor`）も入れ、叩き直したときに同じ手順を組み直す。

`flow` を持つ項目だけを対象にする。持たない項目（経路が組めず探索で
撮るもの）は飛ばすので、**そちらは sim-driver に任せる。**

**一息に走らせる。** 続きは前が終わった画面から始まるので、間にアプリの画面を動かすものが
入ると次の到達判定が落ちる。ここが最後まで走り切ってから sim-driver に渡せば、そうならない。
マニフェストの並びに探索のテストケースが混ざっていてもよい（実行しないだけ）。

**撮り直し: 判定が `RETAKE` の項目と、撮れなかった項目だけを撮り直す。** 撮れなかった項目は、
どれかの端末に `unexpected` が付いた項目。**判定に回す前に撮り直す** — 撮れなかったのは撮る側の
問題なので、retaker が直してから叩き直す。**拾うのは判定がまだ（`PENDING`）の項目だけ。**
`unexpected` は探索で撮っても消えない記録なので、判定が付いたあと（探索で撮って OK / NG に
なった、このまま出すと決めて `SKIP` になった）まで拾うと、別の項目の撮り直しのついでに撮り直してしまう。
撮り直す項目を名前で渡さない — 判定
（evidence-judge）と撮り直し（ここ）の間で名前を写し直すと、写し間違える。テストケースの中の
項目は前の項目に依存するので、その項目だけを走らせても前提の状態が無い。**そのテスト
ケースの頭で起動し直して走らせ、手前の項目は撮らずになぞる。** テストケースどうしは依存
しないので、前のテストケースはなぞらない。撮り直しの前にアプリに何が残っているかは
分からない（前の回の最後のテストケースは後始末をしない）ので、居る画面からは繋がない。
なぞった項目の証跡と判定には触らない（撮影先は作業用の置き場に逃がす）。撮り直した項目だけ
判定を `PENDING` に戻す。手前の項目の実行時の値はマニフェストに残っているものを使う —
そこが空なら走らせられないので止まる。

**撮り直す項目は、走り始めるときに `resume` の `retake` に書き、走り切ったら消す。** 撮る前に
判定を `PENDING` に戻すので、途中で止まると `RETAKE` の印が消える。印だけを見ていると、叩き直した
ときに全部を最初から撮ってしまう。撮れなかった項目は `RETAKE` に戻さず、`unexpected` に書く。
探索で撮る項目（`flow` が無い）の `RETAKE` は撮らない。sim-driver に渡す。

**撮った項目の判定は捨てる。** 画像とダンプが入れ替わった以上、前の `desc` と
`note` は別の証跡についての文章になる。残すと、古い説明が新しい画像の隣で `OK` のまま
レポートに出る。`build_report.py` は `PENDING` を弾くので、そこで止まる。
初回は元から `PENDING` なので何も起きない。

やることは項目ごとに、流すのと読むのだけ。

  maestrod.py run     <UDID> <フロー> <名前> <出力先>/shots/<端末>  操作して撮る
  maestrod.py inspect <UDID> <名前> [<出力先>/shots/<端末>]         画面を読む（撮った項目は同名のダンプを置く）

**これをモデルに打たせない。** 導線は組んだ手順の中にあり、座標の判断も要らないので、
打つ以外の仕事が無い。人（モデル）が組み立てると書式が崩れ、打ち間違いの余地が
残る。進捗ログもここで固定の書式で書く。

**実行時に決める値の鍵は、plan の `do` に書いた操作の文字列**（`text:list.search_field`。
flowgen/steps.py の KEYS）。決め方は2つ。

  - **パターンの要素を、条件なしで押す**（`pick` が無い）: 画面を読み、**画面に見えている
    1件目**を選ぶ（`locate()`）。モデルには訊かない。選んだ値は `devices.<端末>.picked` に書く
    （判定する側が、どれを押したかを知るため）。撮るたびに選び直す — データが変わっても
    古い値で走らないように。経路の途中で押す行（`経路 tap:…`）もこれ
  - **打つ文字、条件つきの選択**: `devices.<端末>.inputs` の値を使う。空なら
    止まる（下の「入力が未定なら」）。再開のときは止まった手から走る（もうそこに居る）
  - **条件つきの選択（`tap` / `see` / `in` の `pick`）は、いま画面に見えている行からだけ選ばせる。**
    止まるときに、並びの先頭（縦の一覧なら上の端、カルーセルなら先頭）まで戻し、見えている行を
    候補として出す。呼び出し元は、条件に合う行があればその ID を `--value` で、無ければ `--next` を
    付けて叩き直す。`inputs` には決まった値だけが入る — 「無い」は値ではなく、止まったときの問いへの返事。
    `--next` なら1画面だけ送ってまた候補を出し、送っても見えている行が変わらなければ端。条件に合う
    要素が無かった記録（`not_found`）を付けて先へ進む（撮る側の失敗ではないので `unexpected` にしない）。集めてから選んだ行まで戻る形にしない — 選ぶのが画面に見えて
    いない行になり、戻ったときに同じ状態である保証も無い

**パターンの要素を押すときは、同じ ID の行のうち何番目かも数える**（`locate()`）。行の ID は
表示中の名前なので、同じ名前の行は ID も同じになる。数えた番号を Maestro の `index` に渡して
1件に絞る。値はアクセシビリティ ID そのもの（ダンプの id の欄）。条件つきの選択は
`<ID>#2`（見えている同じ ID の行のうち上から2件目）とも書ける。

**値はフローにだけ書き、マニフェストに書き戻さない**（`picked` を除く）。`inputs` は撮影する側が
決めた値で、ここは読むだけ。セレクタに入る値は正規表現としてエスケープし（flowgen/maestro.py）、
`inputs` に書く側はエスケープをかけない。

**続きから走るとき、前を撮り直さない** — 止まった時点でアプリはその画面に居るので、
続きはそのまま走る。手前からやり直すと、撮れている証跡を捨てて撮り直すことになる。

**入力が未定ならそこで終える。** 落ちたときは次のテストケースへ進むが、こちらは進めない
— 先へ走らせると画面が変わり、値を決めるために見ることができなくなる。

**落ちたら、そのテストケースの残りの項目だけ飛ばし、次のテストケースへ繋いで続ける。**
テストケースの中の項目は前の項目が終わった画面を当てにしているが、テストケースどうしは
依存しない。1回で取れる証跡は取っておいたほうが、直して走らせ直すときの手がかりが増える。

次のテストケースをどこから組むかは、落ちた地点の画面を読んで決める（`recover()`）。

  - **anchor が見えている画面がちょうど1つなら、その画面から繋ぐ**（flowgen/flow.py の
    `recovered()`）。1つに決まらなければ起動し直す
  - **繋いだ頭の項目が落ちたら、起動し直してそのテストケースをもう1回だけ走らせる。** 後始末の
    `reset` の要素がもう無かった、繋いだ経路が合わなかった、を拾う
  - 前の画面から経路が組めなければ、起動し直して起点から組む（起点から組めることは手順0で確かめてある）
  - 次が起動し直すテストケースなら、画面は読まない

撮れなかった項目は、その端末の `devices.<端末>.unexpected` に書く（形は manifest.py --help）。
どこから繋いだかは進捗ログに書く。落ちた地点の画面は maestrod.py run が出す。
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import manifest_items            # マニフェストの項目を名前で引く
from device import simulators   # UDID から起動しているかを引く
from flowgen import flow as flows_of   # テストケースごとに手順を組む
from flowgen.actions import HideKeyboard, Scroll, Tap
from flowgen.maestro import decided_by_caller, exact, picks_pattern
from flowgen.steps import Act, Enter, See
from screenmap.map import load_map
from screenmap.screen import is_pattern, pattern_prefix

HERE = Path(__file__).resolve().parent
MAESTROD = HERE / "maestrod.py"


def sh(args, quiet=True):
    """出力は捨てる。画面を読むときは `read_screen()` が maestrod.py の置いたダンプをファイルから
    読むので、ここの標準出力は使わない。

    `quiet=False` でも、**落ちたときだけ**出す。通ったフローの `OK {...}` は
    1本ごとに出ると読むものが増えるだけで、判断には使わない。
    """
    r = subprocess.run([sys.executable, str(MAESTROD)] + args,
                       capture_output=True, text=True)
    if r.returncode != 0 and not quiet:
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
    return r.returncode


def pause():
    """送らずに読み直す前に待つ（Seeker.hunt() の最後）。テストで差し替える。"""
    time.sleep(1)


def save(manifest_path, manifest):
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")


def retake_runs(cases, names):
    """撮り直しで走らせるテストケースと項目。[(テストケース, [(項目, "shot" / "replay")])]。

    撮り直す項目ごとに、**そのテストケースの頭（起動し直して）からその項目まで**を走らせる。
    手前の項目は `replay`（なぞるだけで撮らない）。同じテストケースに撮り直す項目が2つあれば、
    後ろのほうまで1回だけ走らせる。テストケースどうしは依存しないので、前のテストケースは
    なぞらない。
    """
    out = []
    for c in cases:
        hit = [k for k, it in enumerate(c["items"]) if it["name"] in names]
        if hit:
            out.append((c, [(it, "shot" if it["name"] in names else "replay")
                            for it in c["items"][:max(hit) + 1]]))
    return out


def dump_rows(dump):
    """elements.py の出力を [{"cx", "cy", "on", "where", "top", "id", "text"}] で。

    行はタブ区切り（tap / 画面内 / 上端 / id / テキスト / 状態）。**id に空白が入っても
    1回で取れる**（`browse.book_row.BOITEUX ・ BOITEUSE`）。1行目の画面と2行目の欄名は飛ばす。
    """
    out = []
    for line in dump.splitlines():
        cols = line.split("\t")
        m = re.match(r"^\((-?\d+),(-?\d+)\)$", cols[0])
        if not m or len(cols) < 6 or not cols[2].lstrip("-").isdigit():
            continue
        out.append({"cx": int(m.group(1)), "cy": int(m.group(2)), "on": cols[1] == "○", "where": cols[1],
                    "top": int(cols[2]), "id": cols[3], "text": cols[4]})
    return out


def shown(dump):
    """ダンプのうち、画面の中にある（`×` でない）要素の (ID の並び, 表示テキストの並び)。
    居る画面を決めるのに使う。表示テキストは、anchor をラベルで書いた画面のため。"""
    ids, texts = [], []
    for line in dump.splitlines():
        cols = line.split("\t")
        if len(cols) >= 6 and re.match(r"^\((-?\d+),(-?\d+)\)$", cols[0]) and cols[1] != "×":
            if cols[3]:
                ids.append(cols[3])
            if cols[4]:
                texts.append(cols[4])
    return ids, texts


def children_of(n):
    return n.get("c") or []


def node_of(raw, rid):
    """生のダンプ（maestrod.py が置く JSON）で、その ID の要素。同じ ID の要素が複数あれば、ツリーで先のもの。
    無ければ None。生のダンプは Maestro の MCP の形（属性名が略号: b / rid / a11y / c）。"""
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    stack = list(d["elements"]) if isinstance(d, dict) and "elements" in d else [d]
    while stack:
        n = stack.pop(0)
        if not isinstance(n, dict):
            continue
        if n.get("rid") == rid:
            return n
        stack[0:0] = children_of(n)
    return None


def box_of(raw, rid):
    """その ID の要素の枠 (x0, y0, x1, y1)。無ければ None。

    **親の中の行だけから選ぶのに使う。** elements.py の行は中心しか持たないので、親の枠は
    生のほうから取る。
    """
    n = node_of(raw, rid)
    if n is None:
        return None
    m = re.match(r"^\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]$", n.get("b") or "")
    return tuple(map(int, m.groups())) if m else None


def inner_texts(raw, rid):
    """その ID の要素の中（子孫）にある要素の表示テキスト。重ねずに、ツリーの順で。

    elements.py の行は中心しか持たないので、枠の中かで拾うと画面全体の要素（スクロールバー、
    ウィンドウ）まで混ざる。行の中のものは生のダンプの入れ子から取る。
    """
    n = node_of(raw, rid)
    out, stack = [], list(children_of(n)) if n else []
    while stack:
        c = stack.pop(0)
        if not isinstance(c, dict):
            continue
        text = (c.get("a11y") or c.get("txt") or c.get("val") or "").strip()
        if text:
            out.append(text)
        stack[0:0] = children_of(c)
    return list(dict.fromkeys(out))


def pattern_rows(dump, pattern, box=None):
    """ダンプのうちパターンに当たる行を、Maestro の index と同じ順で [(ID, 画面内か)]。

    ID はアクセシビリティ ID そのもの（ダンプの id の欄）。**順は上端の y、次に x**（Maestro の Filters.index が
    当たった要素を並べる INDEX_COMPARATOR と同じ基準。x は中心で代える — 同じ ID の行は
    幅がそろう）。**画面外の行も返す** — Maestro の index はそれも数える。

    `box` を渡すと、中心がその枠の中にある行だけ（子の要素を、選んだ親の中から選ぶとき）。
    """
    prefix = pattern[:-1] if pattern.endswith("*") else pattern
    rows = []
    for r in dump_rows(dump):
        rid = r["id"]
        if box and not (box[0] <= r["cx"] <= box[2] and box[1] <= r["cy"] <= box[3]):
            continue
        if rid.startswith(prefix) and len(rid) > len(prefix):
            rows.append((r["top"], r["cx"], rid, r["on"]))
    return [(v, on) for _, _, v, on in sorted(rows, key=lambda x: (x[0], x[1]))]


def locate(dump, pattern, value=None, box=None):
    """押す行を決める。(ID, Maestro の index, 同じ ID の行の数)。決められなければ None。

    `value` が None なら**画面に見えている1件目**。**Maestro のツリー順の0番目ではない** —
    ツリー順の先頭は画面外のことがあり、画面外の要素を ID で押すと Maestro はその位置を
    叩いて別の要素を押す（mobile-dev-inc/Maestro#1275 と同じ症状）。

    `value` を渡すとその ID の行（LLM が条件で選んだもの。ダンプの id の欄をそのまま写す）。
    **同じ名前の行が複数あると ID も同じになる**ので、`<ID>#2` と書けば、画面に見えている
    同じ ID の行のうち上から2件目。ID そのものが `#2` で終わる行があれば、そちらを採る。
    パターンに当たらない ID（別の画面の ID、接頭辞の書き間違い）は None。

    index は、同じ ID の行を位置順（上端の y、次に x）に並べたときの番号（画面外も数える）。
    Maestro の `index` がその順で数えるため（Filters.index の INDEX_COMPARATOR）。
    """
    rows = pattern_rows(dump, pattern, box)
    if value is None:
        chosen = next((r for r in rows if r[1]), None)
        nth = 1
    else:
        name, nth = value, 1
        m = re.match(r"^(.*)#(\d+)$", value)
        if m and not any(v == value for v, _ in rows):
            name, nth = m.group(1), int(m.group(2))
        seen = [r for r in rows if r[1] and r[0] == name]
        chosen = seen[nth - 1] if 0 < nth <= len(seen) else None
    if chosen is None:
        return None
    same = [i for i, r in enumerate(rows) if r[0] == chosen[0]]
    visible_same = [i for i in same if rows[i][1]]
    return chosen[0], same.index(visible_same[nth - 1]), len(same)


def read_screen(udid, name, where):
    """画面を読んで、(elements.py の出力, 生のダンプ) を返す。読めなければ (None, None)。

    `where` は作業用の置き場（DeviceRun.scratch）。maestrod.py はそこからダンプの置き場を
    実行ごと・端末ごとに決める（run_key()）。渡さないと日付ごとの置き場に入り、同じ日の
    別の実行と上書きし合う。証跡の置き場（shots）は渡さない — 同名の .txt が証跡の隣に並ぶ。
    """
    if sh(["inspect", udid, name, str(where)]) != 0:
        return None, None
    state = HERE.parent / ".work" / "state"                 # maestrod.py が置く
    last, raw = state / f"last_dump_{udid}.txt", state / f"last_raw_{udid}.json"
    if not last.exists():
        return None, None
    return last.read_text(encoding="utf-8"), (raw.read_text(encoding="utf-8") if raw.exists() else "")


def read_dump(udid, name, where):
    """画面を読んで、elements.py の出力を返す。読めなければ None。"""
    return read_screen(udid, name, where)[0]


# ---------- 探すもの ----------

class Want:
    """ダンプの中で探すもの。ID そのもの（`exact`）、パターン（`pattern`。`word` を含む行だけ）、
    ラベルの文言（`label`）のどれか。"""

    def __init__(self, exact=None, pattern=None, word=None, label=None):
        self.exact, self.pattern, self.word, self.label = exact, pattern, word, label

    def rows(self, dump, box=None):
        """当たる行を、Maestro の index と同じ順（上端の y、次に x）で。`box` の中だけ。"""
        out = []
        prefix = pattern_prefix(self.pattern) if self.pattern else None
        for r in dump_rows(dump):
            if box and not (box[0] <= r["cx"] <= box[2] and box[1] <= r["cy"] <= box[3]):
                continue
            rid = r["id"]
            if self.exact is not None:
                hit = rid == self.exact
            elif self.label is not None:
                hit = self.label in r["text"]
            else:
                hit = rid.startswith(prefix) and len(rid) > len(prefix)
                if hit and self.word is not None:
                    hit = self.word in rid[len(prefix):]
            if hit:
                out.append(r)
        return sorted(out, key=lambda r: (r["top"], r["cx"]))

    def __str__(self):
        if self.exact is not None:
            return self.exact
        if self.label is not None:
            return "「{}」".format(self.label)
        return self.pattern + ("（「{}」を含む行）".format(self.word) if self.word is not None else "")


def target_of(st):
    """ステップの対象を探す Want。探せない（要素の無い操作、マップに無い文言）なら None。"""
    if isinstance(st, Enter):
        return Want(pattern=st.target)
    if isinstance(st, See):
        if st.text:
            return None
        if st.by_label:
            return Want(label=st.target)
        if is_pattern(st.target):
            return Want(pattern=st.target, word=st.contains)
        return Want(exact=st.target)
    if isinstance(st, Act):
        a = st.action
        if isinstance(a, (Scroll, HideKeyboard)):
            return None
        if a.by_label:
            return Want(label=a.target)
        if is_pattern(a.target):
            return Want(pattern=a.target)
        return Want(exact=a.target)
    return None


def chain_of(st, values):
    """子の要素の親の並び（外から順）。[(具体的な ID, 横に送るか)]。"""
    within = st.outer if isinstance(st, Enter) else st.within
    return [(w.value if w.enter is None else values[w.enter.key], bool(w.scroll)) for w in within]


# ---------- 探す ----------

SEEK_SWIPES = 20   # 1つの向きに送る回数の上限。理由は Seeker.hunt()
SEEK_WAITS = 10    # 両端で見つからなかったあと、送らずに読み直す回数（1秒おき）


class Record:
    """1項目で流したものの記録（`<flows>/<端末>/<名前>.yaml`）。流した順に、フローの中身と、
    ダンプで探して送った回数を並べる。"""

    def __init__(self, app):
        self.app, self.lines = app, []

    def flow(self, body):
        self.lines += body.split("\n---\n", 1)[1].rstrip("\n").split("\n")

    def note(self, text):
        self.lines.append("# " + text)

    def commands(self, body):
        self.lines += body.rstrip("\n").split("\n")

    def load(self, path):
        """前に書いた記録の続きから書く（止まった項目を再開したとき）。無ければ何もしない。"""
        if path.exists():
            body = path.read_text(encoding="utf-8").split("\n---\n", 1)
            self.lines = body[1].rstrip("\n").split("\n") + ["# ここから再開"]

    def write(self, path):
        path.write_text("appId: {}\n---\n{}\n".format(self.app, "\n".join(self.lines)), encoding="utf-8")


def scroll_body(direction, parent=None):
    """1回だけ送るコマンド。縦は FlowWriter.scroll と同じ書き方。横は親の上から送る
    （画面の中央からのスワイプでは、中央にある別のスクロールが動く）。"""
    if parent is not None:
        return "- swipe:\n    from:\n      id: '{}'\n    direction: {}\n".format(
            exact(parent).replace("'", "''"), "LEFT" if direction == "next" else "RIGHT")
    return "- scroll\n" if direction == "down" else "- swipe:\n    direction: DOWN\n"


class Seeker:
    """1手の対象を、ダンプを読みながら探す。見つけたら `dump` / `raw` / `box` に
    その画面と、対象を探した枠（子の要素なら親の枠）が残る。"""

    def __init__(self, dv, name, k, record):
        self.dv, self.name, self.k, self.record = dv, name, k, record
        self.dump = self.raw = self.box = None

    def read(self):
        self.dump, self.raw = read_screen(self.dv.udid, f"{self.name}.seek{self.k}", self.dv.scratch)
        return self.dump is not None

    def run(self, body):
        """探すために1回送る。流したものは記録に残す。"""
        self.record.commands(body)
        return sh(["run", self.dv.udid, "appId: {}\n---\n{}".format(self.dv.app, body),
                   f"{self.name}.seek{self.k}", str(self.dv.scratch)]) == 0

    def boxed(self, parent):
        return box_of(self.raw, parent) if parent is not None else None

    def hits(self, want, parent=None):
        box = self.boxed(parent)
        if parent is not None and box is None:
            return []
        return want.rows(self.dump, box)

    def seen(self, parent=None):
        """画面の中に見えている ID（親の中なら、親の枠の中のもの）。送る前後で比べて端を知る。"""
        box = self.boxed(parent)
        return set((r["id"], r["cx"], r["cy"]) if parent is not None else r["id"]
                   for r in dump_rows(self.dump) if r["on"] and r["id"]
                   and (box is None or (box[0] <= r["cx"] <= box[2] and box[1] <= r["cy"] <= box[3])))

    def hunt(self, want, parent=None):
        """`want` が画面の中（`parent` の中）に見えるまで送る。見えれば None、無ければ理由の文。

        - 今の画面に見えていれば送らない
        - 無ければ1回送っては読み直す。**画面の中に見えている ID が送る前と同じなら、その向きの端。**
          縦は下 → 上の順（ツリーに画面外で出ていれば、そちらから）。上にも必ず探す — 画面が
          一番上なら1回送って端と分かる
        - 横（親の中）は次の向き → 戻る向き。見比べるのは親の枠の中の ID
        - 読み込みで伸び続ける一覧では端に着かないので、1つの向きに SEEK_SWIPES 回で諦める。
          1回（送って読み直す）は実測で約3秒なので、20回で約60秒 — フローの上限と同じ時間で、
          届く距離は長い（`scrollUntilVisible` は60秒で8〜12回）。30回にしたら、伸び続ける一覧で
          約90秒かかり、今までより遅くなった（AozoraReader のさがす画面）
        - **縦で両端まで探して無ければ、送らずに SEEK_WAITS 回（1秒おき）読み直す。** ダンプは
          「今あるか」しか答えない。読み込みが遅れて出るもの（絞り込んだ結果の行）を、
          出る前に読んで諦めないように
        - 見えている行が無くても、画面の中の前面の要素（キーボード、バー）の裏（`裏`）にあれば、
          最後に見つかったとする。押せるかはフローに任せる。画面の外（`×`）の行は見つかったとしない
        """
        if not self.read():
            return "画面を読めない"
        if parent is not None and self.boxed(parent) is None:
            return f"親 {parent} が画面に無い"
        if any(r["on"] for r in self.hits(want, parent)):
            return None
        if parent is not None:
            directions = ("next", "back")
        else:
            above = [r for r in self.hits(want) if r["cy"] < 0]
            directions = ("up", "down") if above else ("down", "up")
        before = self.seen(parent)
        for direction in directions:
            for _ in range(SEEK_SWIPES):
                if not self.run(scroll_body(direction, parent)):
                    return "探すために送るフローが落ちた"
                if not self.read():
                    return "画面を読めない"
                if any(r["on"] for r in self.hits(want, parent)):
                    return None
                now = self.seen(parent)
                if now == before:
                    break                                 # 送っても変わらない。この向きの端
                before = now
            else:
                return f"{want} が見つからない（{SEEK_SWIPES}回送っても端に着かない）"
        if parent is None:
            for _ in range(SEEK_WAITS):
                pause()
                if not self.read():
                    return "画面を読めない"
                if any(r["on"] for r in self.hits(want)):
                    return None
        if any(r["where"] == "裏" for r in self.hits(want, parent)):
            return None                                   # 画面の中の、前面の要素の裏にある
        where = f"{parent} の中の左右" if parent is not None else "上下"
        return f"{want} が画面に無い（{where}の端まで探した）"

    def rewind(self, parent=None):
        """並びの先頭（縦なら上の端、親の中なら先頭）まで戻す。戻せれば None、だめなら理由の文。
        画面の中に見えている ID が送る前と同じなら端（hunt() と同じ）。"""
        before = self.seen(parent)
        for _ in range(SEEK_SWIPES):
            if not self.run(scroll_body("back" if parent is not None else "up", parent)):
                return "先頭まで戻すフローが落ちた"
            if not self.read():
                return "画面を読めない"
            now = self.seen(parent)
            if now == before:
                return None
            before = now
        return None                                       # 戻りきらなくても、見えている行から選ばせる

    def advance(self, parent=None):
        """1画面だけ先へ送る。送れれば None、送っても見えている ID が変わらなければ "端"、だめなら理由の文。"""
        before = self.seen(parent)
        if not self.run(scroll_body("next" if parent is not None else "down", parent)):
            return "送るフローが落ちた"
        if not self.read():
            return "画面を読めない"
        return "端" if self.seen(parent) == before else None

    def find(self, want, chain):
        """対象を探す。子の要素なら、いちばん外の親を縦に探してから、親の中を順に横へ。
        見つかれば None（`box` は対象を探した枠）、無ければ理由の文。"""
        first = Want(exact=chain[0][0]) if chain else want
        why = self.hunt(first)
        if why:
            return why
        if any(scroll for _, scroll in chain):
            # 送る親を縦に寄せる。画面の下端にかかったカルーセルの上からスワイプすると、タブバーを叩く
            body = ("- scrollUntilVisible:\n    element:\n      id: '{}'\n    direction: DOWN\n"
                    "    centerElement: true\n    timeout: 10000\n").format(exact(chain[0][0]).replace("'", "''"))
            if not self.run(body):
                return "親を寄せるフローが落ちた"
        for i, (parent, scroll) in enumerate(chain):
            nxt = Want(exact=chain[i + 1][0]) if i + 1 < len(chain) else want
            if scroll:
                why = self.hunt(nxt, parent)             # 寄せたあとの画面を読み直してから探す
                if why:
                    return why
            elif self.boxed(parent) is None:
                return f"親 {parent} が画面に無い"
            elif not self.hits(nxt, parent):
                return f"{nxt} が {parent} の中に無い"
        self.box = self.boxed(chain[-1][0]) if chain else None
        return None


def value_hint(st):
    """止まったときに、何を決めるのかを言う文。取り違えると、行の ID を渡すべきところに打つ文字を書いてしまう。

    - 条件つきの選択（押す行・見る行・親）: 行の ID をまるごと
    - 打つ文字: 入力欄に打つ文字
    """
    if isinstance(st, Enter):
        return f"条件「{st.pick.condition}」に合う {st.target} を選んで、その ID（ダンプの id の欄をまるごと）を"
    if isinstance(st, See):
        return (f"条件「{st.pick.condition}」に合う {st.target} を選んで、"
                "その ID（ダンプの id の欄をまるごと）を")
    if isinstance(st, Act) and isinstance(st.action, Tap):
        return (f"条件「{st.action.pick.condition}」に合う {st.action.target} を選んで、"
                "その ID（ダンプの id の欄をまるごと）を")
    return "打つ文字を"


def pick_of(st):
    """条件つきの選択のステップの、選ぶ条件（Pick）。"""
    return st.pick if isinstance(st, (Enter, See)) else st.action.pick


def candidates(dump, raw, pattern, box=None):
    """いま画面に見えている、パターンの行（選ぶ候補）。[(値, 行の表示テキスト, 行の中の文字の並び)]。

    値は `inputs` にそのまま書けるもの。同じ ID の行が複数見えていれば、2件目からは `<ID>#2`
    （locate() と同じ書き方）。行の中の文字は、行の子孫の要素の表示テキスト（inner_texts()）。
    """
    rows = {r["id"]: r for r in dump_rows(dump)}
    out, counts = [], {}
    for rid, on in pattern_rows(dump, pattern, box):
        if not on:
            continue
        counts[rid] = counts.get(rid, 0) + 1
        value = rid if counts[rid] == 1 else f"{rid}#{counts[rid]}"
        out.append((value, rows[rid]["text"], inner_texts(raw, rid) if raw else []))
    return out


def candidates_text(found):
    lines = []
    for value, text, inner in found:
        line = f"  - {value}"
        if text:
            line += f" ｜ {text}"
        if inner:
            line += " ｜ 中: " + " / ".join(inner)
        lines.append(line)
    return "\n".join(lines)


class Hand:
    """1手を、探して値を決めるところまで（run_item() から呼ぶ）。結果は `status` が None なら
    流してよく、("failed", 理由) / ("stopped", 何手目か) なら流さない。"""

    def __init__(self, dv, sec, k, st, values, indexes, notes, record, replay):
        self.dv, self.k, self.st = dv, k, st
        self.values, self.indexes, self.notes = values, indexes, notes
        self.record, self.replay = record, replay
        self.dev = sec["devices"][dv.device]
        self.name = sec["name"]

    def fail(self, why):
        self.dv.logline(f"{self.name} 撮影できず {why}")
        print(f"{self.name} {why}", file=sys.stderr)
        return ("failed", why)

    def again(self):
        """止まったあと叩き直すとどうなるか。撮り直しは止まった手からは続けず、テストケースの頭からなぞり直す。"""
        if self.dv.retake:
            return "撮り直す項目のテストケースの頭からなぞり直す"
        return "続きから走る"

    def answer(self, what):
        """値の返し方。値は空白や記号を含むので、引用符で囲んで渡させる。"""
        return (f"同じコマンドに --value '<{what}>' を付けて叩き直す（{self.again()}。"
                "値にシングルクォートが入るなら '\\'' と書く）")

    def stop(self, log, message):
        """止まる。**止まった時点でアプリはその画面に居る。** 叩き直すときの返事（--value / --next）は、
        この手の値として受け取る（`dv.ask["key"]`。呼ぶ側が resume.ask に書く）。"""
        self.dv.ask["key"] = self.st.key
        self.dv.logline(f"{self.name} 撮影せず {log}")
        print(f"\n{self.dv.device} {self.name} {message}")
        return ("stopped", self.k)

    def missing(self, key, found=None):
        """撮影する側が決める値が空。なぞる項目なら止まらずに終える（前に撮ったときに決めてあるはず）。
        `found` は選ぶ候補（candidates()）。条件つきの選択なら渡す。"""
        if self.replay:
            # ここで決めさせると、撮り直しのたびに手前の項目の判断をやり直すことになる
            sys.exit(f"{self.dv.device} {self.name} の値が未定（{key}）。撮り直しは手前の項目を"
                     f"なぞるので、前に撮ったときの値（devices.{self.dv.device}.inputs）が要る。"
                     "前の回の値が残っていない。最初から撮り直す")
        if found is not None:
            return self.stop(f"入力が未定（{key}）",
                             f"入力が未定（{key}）。\nいま画面に見えている行（候補）:\n{candidates_text(found)}\n"
                             f"この中に条件「{pick_of(self.st).condition}」に合うものがあれば、その ID（候補の行の先頭。"
                             f"ダンプの id の欄をまるごと）を、{self.answer('ID')}。\n"
                             "無ければ、同じコマンドに --next を付けて叩き直す（1画面送って、次の候補を出す）。")
        return self.stop(f"入力が未定（{key}）",
                         f"入力が未定（{key}）。\nいまこの画面に居る。見て{value_hint(self.st)}決め、"
                         f"{self.answer('値')}。")

    def play(self):
        st, key, mp = self.st, self.st.key, self.dv.mp
        given = (self.dev.get("inputs") or {}).get(key) or None
        caller = decided_by_caller(st)
        picks = isinstance(st, Enter) or picks_pattern(st)
        # この手で止まったら、条件つきの選択で止まったことになる（--next を受け付ける）。
        # 止まった理由（値が未定、ID の書き間違い）によらない
        self.dv.ask["key"] = None
        self.dv.ask["pick"] = bool(caller and picks and not self.replay)
        if caller and given and picks:
            pattern = st.target if isinstance(st, (Enter, See)) else st.action.target
            prefix = pattern_prefix(pattern)
            if not given.startswith(prefix):
                # 書いた ID がこの操作のパターンに当たらない（別の画面の ID、接頭辞の書き間違い）。
                # 押すと別物を押すか落ちる。まだ画面は動いていないので、直して叩き直す
                return self.stop(f"{key} の {given} が {pattern} に当たらない",
                                 f"{key} の値 {given} が {pattern} に当たらない。ダンプの id の欄"
                                 f"（{prefix}…）をまるごと、{self.answer('ID')}。")
        want = target_of(st)
        if want is None:
            if caller and not given:
                return self.missing(key)
            if caller:
                self.values[key] = given
            return None                                   # 探せないもの。フローで待つ
        seeker = Seeker(self.dv, self.name, self.k, self.record)
        self.record.note(f"run_flows.py がダンプで探す: {want}")
        why = seeker.find(want, chain_of(st, self.values))
        if why:
            return self.fail(why)
        if caller and picks and not given:
            return self.choose(seeker, want)
        if caller and not given:
            return self.missing(key)
        if picks:
            # どの行を押す・見るか（どの親の中でするか）を決め、同じ ID の行のうち何番目か（Maestro の index）を
            # 数える。条件が無ければ画面に見えている1件目（モデルに訊かない）
            found = locate(seeker.dump, want.pattern, given if caller else None, seeker.box)
            if found is None:
                what = f"{given} が" if caller else f"{want.pattern} が"
                return self.fail(f"{what}画面に見えていない")
            value, index, count = found
            self.values[key] = value
            self.indexes[key] = index
            self.dev["picked"][key] = value
            if count > 1:
                self.notes[key] = f"同じ名前 {count}件のうち上から{index + 1}件目"
            self.record.note(f"{key} = {value}" + (f"（{self.notes[key]}）" if key in self.notes else ""))
        elif caller:
            self.values[key] = given
        return None

    def choose(self, seeker, want):
        """条件つきの選択で、まだ値が決まっていない。いま見えている行を候補に出して止まる。

        ふつうは並びの先頭まで戻してから出す。`--next` で叩き直されたら（`dv.ask["next"]`）1画面だけ
        送ってから出し、送っても見えている行が変わらなければ、端まで見て無かったとする（not_found()）。
        送る並びは、いちばん内側の親が横に送るもの（`scroll`）ならその中、そうでなければ画面の縦。

        **`--next` で送るのは SEEK_SWIPES 回まで。** 読み込みで伸び続ける一覧は端に着かないので、数えないと
        呼び出し元が `--next` を付け続ける限り終わらない。送った回数は手ごとに `dv.ask["paged"]` に持ち、
        止まるときにマニフェストの `resume` に書く（止まっている間だけの状態なので、項目の記録には置かない）。
        先頭まで戻したときに0に戻す。

        **撮り直しでは、送った位置から続けられない。** 止まったあと叩き直すと、テストケースの頭から
        なぞり直すので、画面は先頭に戻っている。先頭まで戻してから、それまでに送った回数ぶん送り直し、
        そこから1画面進める。
        """
        if self.replay:
            return self.missing(self.st.key)           # なぞる項目では選ばない（前に撮った値が要る）
        key = self.st.key
        paged = self.dv.ask["paged"]
        chain = chain_of(self.st, self.values)
        parent = chain[-1][0] if chain and chain[-1][1] else None
        forward, self.dv.ask["next"] = self.dv.ask["next"], False      # --next はこの1回だけに効く
        if not forward:
            paged[key] = 0
            why = seeker.rewind(parent)
        else:
            cond = pick_of(self.st).condition
            where = f"{parent} の中の左右" if parent is not None else "上下"
            if paged.get(key, 0) >= SEEK_SWIPES:
                return self.not_found(seeker, cond, f"{where}に{SEEK_SWIPES}画面送っても端に着かない")
            why = None
            if self.dv.retake:
                why = seeker.rewind(parent)
                for _ in range(paged.get(key, 0)):
                    why = why or seeker.advance(parent)
            why = why or seeker.advance(parent)
            paged[key] = paged.get(key, 0) + 1
            if why == "端":
                return self.not_found(seeker, cond, f"{where}の端まで見た")
        if why:
            return self.fail(why)
        found = candidates(seeker.dump, seeker.raw, want.pattern,
                           seeker.boxed(chain[-1][0]) if chain else None)
        if not found:
            return self.fail(f"{want.pattern} が画面に見えていない")
        return self.missing(self.st.key, found)

    def not_found(self, seeker, cond, how_far):
        """探しきって、条件に合う要素が無かった。("not_found", 記録)。

        期待どおりのものが画面に無かったので、判定は `NG` になる。撮る側の失敗（`unexpected`）に入れると
        撮り直しに回って「撮れなかった」になり、NG なのか撮る側の問題なのかが分からなくなるので分ける。
        **最後に見た画面（端まで送ったところ）を、ふつうの項目と同じ名前で撮ってダンプを置く。** NG の証跡になる。
        """
        shot = str((self.dv.shots / self.name).resolve()).replace("'", "''")
        seeker.run(f"- takeScreenshot: '{shot}'\n")
        sh(["inspect", self.dv.udid, self.name, str(self.dv.shots)])   # 出力は捨てる
        self.dv.logline(f"{self.name} 撮影済み 条件「{cond}」に合う要素が無い（{how_far}）。最後に見た画面を撮った")
        print(f"{self.name} 条件「{cond}」に合う要素が無い（{how_far}）", file=sys.stderr)
        return ("not_found", {"condition": cond, "reason": how_far})


def run_item(dv, sec, row, replay, first=0):
    """1項目を、`first` 手目から順に流す。("done", None) / ("failed", 理由) / ("stopped", 何手目か)。

    `row` はその項目を組んだ行（flowgen の render_case()）。`units` を順に、経路はそのまま、
    `do` の1手はダンプで探して値を決めてから流す（Hand）。流したものは記録に書く。
    """
    device, udid, logline = dv.device, dv.udid, dv.logline
    name = sec["name"]
    dest = dv.scratch if replay else dv.shots
    dev = sec["devices"][device]
    dev["picked"] = {} if first == 0 else dev["picked"]
    # 前の手で決めた値。再開のときは、止まる前に決めたもの（picked）と撮影する側が決めたもの
    values = dict(dev["picked"], **{k: v for k, v in (dev.get("inputs") or {}).items() if v})
    notes = {}   # 同じ名前の行があったときの、何件目を押したか
    record = Record(dv.app)
    if first and not replay:
        record.load(dv.fdir / row["flow"])       # 止まる前に流した手を記録から落とさない
    units = row["units"]

    def done(status):
        if not replay:
            record.write(dv.fdir / row["flow"])
        return status

    for k in range(first, len(units)):
        u = units[k]
        indexes = {}
        if u.kind == "hand":
            status = Hand(dv, sec, k, u.step, values, indexes, notes, record, replay).play()
            if status:
                return done(status)
        body = flows_of.render_unit(dv.mp, dv.app, dv.clear, u, values, indexes, str(dest.resolve()))
        if body is None:
            continue                                      # 親を決めただけ。流すものが無い
        record.flow(body)
        last = k == len(units) - 1
        run_name = name if last else f"{name}.{k + 1}"
        # 落ちたら、その run をもう一度は走らせない。1回目の出力をそのまま見せる
        if sh(["run", udid, body, run_name, str(dest)], quiet=False) != 0:
            where = "" if last else f"（{k + 1}手目）"
            logline(f"{name} 撮影できず フローが失敗{where}")
            print(f"{name} 失敗{where}", file=sys.stderr)
            return done(("failed", f"フローが失敗{where}"))
    if replay:
        logline(f"{name} なぞった（撮り直しの前提）")
        return done(("done", None))
    sh(["inspect", udid, name, str(dv.shots)])   # 出力は捨てる
    picked = ", ".join(f"{k}={v}" + (f"（{notes[k]}）" if k in notes else "")
                       for k, v in dev["picked"].items())
    logline(f"{name} 撮影済み" + (f"（選んだ: {picked}）" if picked else ""))
    print(name + " 撮影済み" + (f"（選んだ: {picked}）" if picked else ""))
    return done(("done", None))


def forget(shots, sec):
    """その項目の前の回の証跡・ダンプを消し、判定を PENDING に戻す。"""
    for ext in (".png", ".txt"):
        f = shots / (sec["name"] + ext)
        if f.exists():
            f.unlink()
    sec["desc"], sec["note"], sec["result"] = "", "", "PENDING"


class DeviceRun:
    """1台ぶんを撮るときの置き場。run_item() に渡す。"""

    def __init__(self, device, udid, shots, scratch, fdir, log, app, mp, clear, retake, ask=None):
        self.device, self.udid, self.app, self.mp, self.clear = device, udid, app, mp, clear
        self.retake = retake                          # 撮り直し中か（止まったときの案内が変わる）
        # 止まったときの問答。next: --next で叩き直された（まだ使っていない）。paged: 条件つきの選択で
        # 手ごとに送った回数（止まったら resume.ask に書き、叩き直したら読む）。key: 止まった手の鍵。
        # pick: 止まった手が条件つきの選択か（--next を受け付けるか）
        ask = ask or {}
        self.ask = {"next": bool(ask.get("next")), "paged": dict(ask.get("paged") or {}), "key": None, "pick": False}
        self.shots, self.scratch, self.fdir, self.log = shots, scratch, fdir, log

    def logline(self, text):
        with self.log.open("a", encoding="utf-8") as f:
            f.write(text + "\n")


def run_device(manifest, manifest_path, flow_dir, device, udid, resume, retake=None, ask=None):
    """1台ぶんを、テストケースごとに組んで走らせる。(止まった位置, 撮った数, 撮れなかった行) を返す。

    入力が未定で止まったら、{"from": 項目の名前, "part": 何手目か, "cursor": そのテストケースを
    組んだ状態, "ask": 止まったときの問い（項目・端末・鍵、条件つきの選択か、送った回数）} を返す（呼ぶ側が
    再開位置に書く）。`resume` も同じ形で、同じ状態からそのテストケースを組み直し、その項目のその手から
    走らせる。`retake` を渡すと、その項目だけ撮り直す（`retake_runs()`）。`ask` は条件つきの選択の問答
    （DeviceRun）で、`--next` と、止まる前に送った回数を渡す。
    """
    out = manifest_path.parent
    shots, log = out / "shots" / device, out / f"progress_{device}.log"
    shots.mkdir(parents=True, exist_ok=True)
    # なぞる項目の撮影先。証跡を上書きしないように、作業用の置き場に逃がす
    scratch = HERE.parent / ".work" / "replay" / out.resolve().name / device
    scratch.mkdir(parents=True, exist_ok=True)
    # 記録は端末ごとに置く。落ちたあとの繋ぎ方で、端末ごとに中身が変わる
    fdir = flow_dir / device
    fdir.mkdir(parents=True, exist_ok=True)

    plan = flows_of.plan_of(manifest)
    mp = load_map(plan["repo"])
    cases = flows_of.flow_cases(plan)
    secs = {it["name"]: it for _, it in manifest_items.walk(manifest)}
    if retake:
        runs = retake_runs(cases, retake)
    else:
        runs = [(c, [(it, "shot") for it in c["items"]]) for c in cases]
    resume_name, resume_part = (resume["from"], resume["part"]) if resume else (None, 0)
    if resume_name:
        at = next((k for k, (_, its) in enumerate(runs) if any(it["name"] == resume_name for it, _ in its)), None)
        if at is None:
            names = [it["name"] for _, its in runs for it, _ in its]
            sys.exit(f"再開先 {resume_name} が見つからない。ある名前: {', '.join(names)}")
        runs = runs[at:]

    dv = DeviceRun(device, udid, shots, scratch, fdir, log, plan["app"], mp, plan["clear_state"], bool(retake), ask)
    logline = dv.logline

    if retake:
        logline("--- 撮り直し: " + ", ".join(retake))
    elif resume_name:
        logline(f"--- 再開: {resume_name} の {resume_part + 1}手目から")
    else:
        # 最初から撮るときは、進捗ログと、マニフェストに無い名前の証跡と、前の回の記録を消す
        log.write_text("", encoding="utf-8")
        current = set(secs)
        for old in list(shots.glob("*.png")) + list(shots.glob("*.txt")):
            if old.stem not in current:
                old.unlink()
        for old in fdir.glob("*.yaml"):
            old.unlink()

    def recover(made, case, name):
        """落ちたあと、次のテストケースをどこから組むか（Cursor）。落ちた地点の画面を読んで決める。"""
        if retake or case["next"] is None or case["next"]["restart"]:
            return flows_of.Cursor.fresh(mp)          # 次が無いか、次はどのみち起動し直す
        dump = read_dump(udid, f"{name}.where", scratch)
        screen, hits = flows_of.screen_at(mp, *shown(dump or ""))
        nxt = case["next"]["name"]
        if screen is None:
            why = ("どの画面の anchor も見えていない" if not hits
                   else "anchor が複数見えている（{}）".format(", ".join(hits)))
            logline(f"{name} のあと: {why}。{nxt} は起動し直して始める")
            return flows_of.Cursor.fresh(mp)
        logline(f"{name} のあと: 画面 {screen} に居る。{nxt} はそこから繋ぐ")
        return flows_of.recovered(mp, made, name, screen)

    def build(case, start):
        """テストケースを組む。(組んだもの, 項目ごとの行, 次の Cursor, 組んだ状態)。

        前のテストケースが終わった画面から組めなければ（戻る操作が要るのに履歴が無い、など）、
        起動し直して起点から組む。起点から組めることは手順0で確かめてある。それでも組めなければ
        （マップが手順0の後で変わった）、組めなかったものを返す。
        """
        got = flows_of.build_case(mp, case["items"], start)
        if got.problems and not start.restart:
            head = case["items"][0]["name"]
            logline(f"{head} {start.at} から繋げない（{'; '.join(m for _, m in got.problems)}）。"
                    "起動し直して始める")
            start = flows_of.Cursor.fresh(mp)
            got = flows_of.build_case(mp, case["items"], start)
        if got.problems:
            return got, None, None, start
        rows, end = flows_of.render_case(mp, start, got)
        return got, {r["name"]: r for r in rows}, end, start

    def unexpected(sec, kind, reason=""):
        """撮れなかったことを、その端末の欄に書く（kind が None なら消す）。判定（`result`）ではない。
        消すときは、条件に合う要素が無かった記録（`not_found`）も消す。"""
        dev = sec["devices"][device]
        if kind:
            dev["unexpected"] = {"kind": kind, "reason": reason}
        else:
            dev.pop("unexpected", None)
            dev.pop("not_found", None)

    print(f"--- {device}（{udid}）")
    cursor = flows_of.Cursor.from_json(resume["cursor"]) if resume else flows_of.Cursor.fresh(mp)
    done, lost, absent = 0, [], []
    for ci, (case, items) in enumerate(runs):
        if retake:
            cursor = flows_of.Cursor.fresh(mp)        # 撮り直しは、テストケースの頭で起動し直す
        for attempt in (0, 1):
            made, rows, end, start = build(case, cursor)
            if rows is None:
                # 手順0では起点から組めた（マップが manifest.py の後で変わった）。このテストケースは撮れない
                msg = "; ".join(m for _, m in made.problems)
                for it, mode in items:
                    lost.append(f"{device} {it['name']}")
                    logline(f"{it['name']} 撮影できず フローが組めない（{msg}）")
                    if mode == "shot":
                        forget(shots, secs[it["name"]])
                        unexpected(secs[it["name"]], "failed", f"フローが組めない（{msg}）")
                cursor = flows_of.Cursor.fresh(mp)
                break
            got, missed, failed_at, failed_why = 0, [], None, "落ちた"
            skipping = resume_name is not None       # 再開: 止まった項目の手前はもう撮ってある
            for n, (it, mode) in enumerate(items):
                name = it["name"]
                if skipping and name != resume_name:
                    continue
                skipping = False
                sec, replay = secs[name], mode == "replay"
                continuing = name == resume_name and resume_part
                if not replay and not continuing:
                    # 撮れずに終わっても前の回の証跡と OK が残らないように、先に消す
                    forget(shots, sec)
                if not replay:
                    unexpected(sec, None)
                if failed_at is not None:
                    if failed_why == "落ちた":
                        missed.append(f"{device} {name}")
                    logline(f"{name} 撮影できず 同じテストケースの {failed_at} が{failed_why}ので飛ばした")
                    if not replay and failed_why == "落ちた":
                        unexpected(sec, "skipped", f"同じテストケースの {failed_at} が落ちた")
                    elif not replay:
                        # 前提の要素が無かった巻き添え。撮る側の失敗ではないので、撮り直しに拾わせない
                        sec["devices"][device]["not_found"] = {
                            "after": failed_at, "reason": f"同じテストケースの {failed_at} で条件に合う要素が無かった"}
                        absent.append(f"{device} {name}（{failed_at} の巻き添え）")
                    continue
                status = run_item(dv, sec, rows[name], replay, resume_part if continuing else 0)
                if status[0] == "stopped":
                    rest = len(items) - n + sum(len(its) for _, its in runs[ci + 1:])
                    print(f"{device} のここから先の {rest}件はまだ撮っていない。")
                    # 止まったときの問い。叩き直すときの返事（--value / --next）を、どこに受けるか
                    ask = {"item": name, "device": device, "key": dv.ask["key"], "pick": dv.ask["pick"],
                           "paged": dv.ask["paged"]}
                    return {"from": name, "part": status[1], "cursor": start.to_json(), "ask": ask}, done + got, lost
                if status[0] == "not_found" and not replay:
                    # 探しきって無かった。撮る側の失敗ではないので unexpected にしない（撮り直さない）。
                    # 後ろの項目はこの項目を当てにしているので、落ちたときと同じく飛ばす
                    failed_at, failed_why = name, "条件に合う要素が無かった"
                    sec["devices"][device]["not_found"] = status[1]
                    absent.append(f"{device} {name}（条件「{status[1]['condition']}」。{status[1]['reason']}）")
                elif status[0] == "failed":
                    failed_at = name
                    missed.append(f"{device} {name}" + ("（なぞる途中で落ちた）" if replay else ""))
                    if not replay:
                        unexpected(sec, "failed", status[1])
                elif not replay:
                    got += 1
            resume_name, resume_part = None, 0
            done += got
            if failed_at == items[0][0]["name"] and failed_why == "落ちた" and start.recovered and attempt == 0:
                # 前が落ちた地点から繋いだ頭の項目が落ちた。繋ぎ方（後始末の reset、落ちた画面からの
                # 経路）が悪かったのかもしれないので、起動し直してこのテストケースをもう1回だけ走らせる
                logline(f"{failed_at} 繋いだ頭で落ちた。起動し直してテストケース「{case['title']}」を走らせ直す")
                cursor = flows_of.Cursor.fresh(mp)
                continue
            lost += missed
            cursor = recover(made, case, failed_at) if failed_at else end
            break
    print(f"{done}件を撮った: {shots}")
    if absent:
        print(f"\n条件に合う要素が無かった {len(absent)}件（撮り直さない。そのまま判定に回す）:")
        for line in absent:
            print("  " + line)
    return None, done, lost


def to_retake(manifest):
    """撮り直す項目。(フローで撮り直す名前, 探索で撮る名前)。

    判定が `RETAKE` の項目と、どれかの端末で撮れなかった（`unexpected` の付いた）項目のうち
    判定がまだ（`PENDING`）のもの。判定が付いたもの（探索で撮った、`SKIP` にした）は拾わない。
    """
    def lost(it):
        return any((d or {}).get("unexpected") for d in (it.get("devices") or {}).values())
    marked = [it for _, it in manifest_items.walk(manifest)
              if it.get("result") == "RETAKE" or (it.get("result") == "PENDING" and lost(it))]
    return ([it["name"] for it in marked if it.get("flow")],
            [it["name"] for it in marked if not it.get("flow")])


def main():
    # 標準出力を行ごとに流す。既定のバッファのままだと、即時に出る標準エラーと
    # 混ざったときに順番が入れ替わり、失敗の行が撮影済みの行より前に出る
    sys.stdout.reconfigure(line_buffering=True)

    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    forward = "--next" in argv
    argv = [a for a in argv if a != "--next"]
    value = None
    if "--value" in argv:
        i = argv.index("--value")
        if i + 1 >= len(argv):
            sys.exit("--value の後ろに値が無い（--value '<値>'）")
        value = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    unknown = [a for a in argv if a.startswith("--")]
    if unknown:
        sys.exit("知らない引数: " + ", ".join(unknown) + "（どの端末で撮るかはマニフェストに入っている）")
    if len(argv) != 1:
        sys.exit(__doc__)
    manifest_path = Path(argv[0])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("flows"):
        sys.exit("マニフェストに flows が無い。manifest.py で作り直す")
    flow_dir = Path(manifest["flows"])

    devices = manifest.get("devices") or {}
    if not devices:
        sys.exit("マニフェストに devices が無い。manifest.py を --device <端末>=<UDID> で叩き直す")
    pairs = [(d, v["udid"]) for d, v in devices.items()]
    # 手順0で決めた端末が起動していなければ止まる。別の端末で代用しない
    down = [f"{d}: {v['model']} ({v['os']}) {v['udid']}" for d, v in devices.items()
            if not simulators.lookup(v["udid"])["booted"]]
    if down:
        sys.exit("起動していないシミュレーターがある。起動してから叩き直す:\n  " + "\n  ".join(down))

    if not any(it.get("flow") for _, it in manifest_items.walk(manifest)):
        sys.exit("flow を持つ項目が無い。全部探索なので sim-driver に渡す。")

    state = manifest.get("resume") or {}
    question = state.get("ask") or {}
    if forward and value is not None:
        sys.exit("--value と --next は一緒に付けない。合うものがあったなら --value、無かったなら --next")
    if forward and not question.get("pick"):
        sys.exit("--next は、条件つきの選択で止まって候補が出たあとにだけ付ける（いまは止まっていないか、"
                 "別のことで止まっている）。--next を外して叩く")
    if forward:
        # 「無い」という返事なので、その手の値は空にする（ID の書き間違いで止まったあとでも）
        _, sec = manifest_items.find(manifest, question["item"])
        sec["devices"][question["device"]].setdefault("inputs", {})[question["key"]] = ""
        save(manifest_path, manifest)
    if value is not None:
        if not question.get("key"):
            sys.exit("--value は、入力が未定で止まったあとにだけ付ける（いまは止まっていない）。--value を外して叩く")
        if not value:
            sys.exit("--value の値が空。決めた値を渡す")
        # 止まった手の値として inputs に書く。書くのはここだけで、呼び出し元はマニフェストを書き換えない
        _, sec = manifest_items.find(manifest, question["item"])
        sec["devices"][question["device"]].setdefault("inputs", {})[question["key"]] = value
        save(manifest_path, manifest)
    # 条件つきの選択の問答。止まった端末にだけ渡す（--next と、それまでに送った回数）
    ask = {"next": forward, "paged": question.get("paged") or {}}
    retake = state.get("retake")
    if retake is None and not state.get("device"):
        retake, explore = to_retake(manifest)
        if explore:
            print(f"撮り直す項目のうち探索で撮る項目（sim-driver に渡す）: {', '.join(explore)}")
        if not retake and explore:
            return
        retake = retake or None

    if retake:
        # 撮り直し。止まったら、同じコマンドでまたテストケースの頭から走らせる（止まった手からは続けない）
        finished = list(state.get("done") or [])
        manifest["resume"] = {"retake": retake, "done": finished}
        save(manifest_path, manifest)
        total, lost_all = 0, []
        for d, u in pairs:
            if d in finished:
                print(f"--- {d} は撮り直し終えている。飛ばす")
                continue
            stopped, done, lost = run_device(manifest, manifest_path, flow_dir, d, u, None, retake, ask)
            ask = None                                    # 撮り直しで止まるのは1台ずつ。次の端末には渡さない
            total += done
            lost_all += lost
            if stopped:
                manifest["resume"]["ask"] = stopped["ask"]
                save(manifest_path, manifest)
                print("返事（--value / --next）を付けて同じコマンドを叩き直す。撮り直す項目のテストケースの頭からなぞり直す。")
                sys.exit(1)
            finished.append(d)
            manifest["resume"]["done"] = finished
            save(manifest_path, manifest)
        manifest.pop("resume", None)
        save(manifest_path, manifest)
        print(f"\nこの実行で {total}件を撮り直した（{', '.join(retake)}）")
        if lost_all:
            print(f"\n撮れなかった {len(lost_all)}件:", file=sys.stderr)
            for line in lost_all:
                print("  " + line, file=sys.stderr)
            sys.exit(1)
        return

    # 前の実行が未定で止まっていれば、撮り終えた端末は飛ばし、止まった端末の続きから走る
    finished = list(state.get("done") or [])
    total, lost_all = 0, []
    for d, u in pairs:
        if d in finished:
            print(f"--- {d} は撮り終えている。飛ばす")
            continue
        resume = state if state.get("device") == d else None
        stopped, done, lost = run_device(manifest, manifest_path, flow_dir, d, u, resume, None,
                                         ask if resume else None)
        total += done
        lost_all += lost
        if stopped:
            manifest["resume"] = dict(stopped, done=finished, device=d)
            save(manifest_path, manifest)
            sys.exit(1)
        finished.append(d)
    manifest.pop("resume", None)
    save(manifest_path, manifest)

    skipped = [it.get("name") for _, it in manifest_items.walk(manifest) if not it.get("flow")]
    print(f"\nこの実行で {total}件を撮った")
    if skipped:
        print(f"飛ばした（フローが無い。探索で撮る）: {', '.join(skipped)}")
    if lost_all:
        print(f"\n撮れなかった {len(lost_all)}件:", file=sys.stderr)
        for line in lost_all:
            print("  " + line, file=sys.stderr)
        sys.exit(1)

main()
