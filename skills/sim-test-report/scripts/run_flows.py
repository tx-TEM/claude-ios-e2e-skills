#!/usr/bin/env python3
"""マニフェストの項目を、テストケースごとにフローを組みながら順に走らせ、証跡と同名のダンプを撮る。

  run_flows.py <manifest.json>
  run_flows.py <manifest.json> --only test_15[,test_07]   その項目だけ撮り直す
  run_flows.py <manifest.json> --only test_15 --prepare   撮り直すフローを書くだけ（走らせない）
  run_flows.py <manifest.json> --only test_15 --keep      書いてあるフロー（手で直したもの）で撮り直す

**どのシミュレーターで撮るかはマニフェストの `devices` に、フローの置き場は `flows` に
入っている**（手順0で manifest.py が書いたもの）。ここで渡し直さない。その UDID が起動していなければ止まる — 別の端末で代用しない。

**フローはテストケースごとに、走らせる直前に組む**（flowgen/flow.py）。前のテストケースが
終わった状態から組み、`<flows>/<端末>/` に書いてから走らせる。落ちたあとの繋ぎ方で中身が
変わるので、置き場は端末で分ける。組むのに要るものはマニフェストから読み、plan.json は読まない。

**1回で全端末を撮る。** マニフェストの順に1台ずつ撮り切ってから次の端末へ移る（Maestro の
デーモンが握れるのは1台で、行き来させると切り替えのたびに約10秒かかる）。撮影先の
`${SHOTS}` にここで `<出力先>/shots/<端末>` を入れる。
進捗ログは `<出力先>/progress_<端末>.log`。実行時に決める値は端末ごと — その端末の
画面を見て決める。

**前の回の残りを片付けてから撮る。** 最初から撮るときは進捗ログを書き直し、マニフェストに
無い名前の証跡とダンプと、前の回のフローを消す。撮る項目は、撮る前にその項目の前の回の証跡とダンプを消し、
判定を `PENDING` に戻す（撮れずに終わっても、前の回の画像と OK がレポートに残らない）。
再開と撮り直し（`--only`）のときは進捗ログに区切りの行を足して続ける。

**未定で止まったら、叩き直すと続きから走る。** 撮り終えた端末は飛ばし、止まった端末の
止まった項目から走る。状態はマニフェストの `resume` に書き、走り切ったら消す。`resume` には
止まったテストケースを組んだ状態（`cursor`）も入れ、叩き直したときに同じフローを組み直す。

`flow` を持つセクションだけを対象にする。持たないセクション（経路が組めず探索で
撮るもの）は飛ばすので、**そちらは sim-driver に任せる。**

**フローを一息に走らせる。** 続きのフローは前のフローが終わった画面から始まるので、
間にアプリの画面を動かすものが入ると次の到達判定が落ちる。ここが最後まで
走り切ってから sim-driver に渡せば、そうならない。マニフェストの並びに探索の
セクションが混ざっていてもよい（実行しないだけ）。

**`--only` はその項目だけ撮り直す**（判定の `RETAKE` を撮り直すとき）。テストケースの中の
項目は前の項目に依存するので、その項目だけを走らせても前提の状態が無い。**そのテスト
ケースの頭で起動し直して走らせ、手前の項目は撮らずになぞる。** テストケースどうしは依存
しないので、前のテストケースはなぞらない。撮り直しの前にアプリに何が残っているかは
分からない（前の回の最後のテストケースは後始末をしない）ので、居る画面からは繋がない。
なぞった項目の証跡と判定には触らない（撮影先は作業用の置き場に逃がす）。撮り直した項目だけ
判定を `PENDING` に戻す。手前の項目の実行時の値はマニフェストに残っているものを使う —
そこが空なら走らせられないので止まる。

**フローを手で直して撮り直すときは `--prepare` と `--keep`。** フローは撮る直前に組むので、
前の回のフローを直しても、撮り直すときに組み直されて消える。`--prepare` で撮り直す
フローを書き（走らせない）、それを直してから `--keep` で撮る。`--keep` は書いてある
フローを上書きしない。撮り直しはいつもテストケースの頭で起動し直して組むので、
`--prepare` で書いたものと同じフローになり、直した行だけが違う。

やることは1セクションにつき2つだけ。

  maestrod.py run     <UDID> @<フロー> <名前> <出力先>/shots/<端末>  操作して撮る
  maestrod.py inspect <UDID> <名前> <出力先>/shots/<端末>  同名のダンプを置く

**これをモデルに打たせない。** 導線はフローの中にあり、座標の判断も要らないので、
打つ以外の仕事が無い。人（モデル）が組み立てると書式が崩れ、打ち間違いの余地が
残る。進捗ログもここで固定の書式で書く。

**撮った項目の判定は捨てる。** 画像とダンプが入れ替わった以上、前の `desc` と
`note` は別の証跡についての文章になる。残すと、古い説明が新しい画像の隣で `OK` のまま
レポートに出る。`build_report.py` は `PENDING` を弾くので、そこで止まる。
初回は元から `PENDING` なので何も起きない。

**実行時に決める値があるフローは割れている**（組んだ行の `parts`）。前の本が目的の
画面まで運び、対象が見えるまでスクロールして止まり、次の本が値を使う。`decide` が
その本の前に決める値で、決め方は2つ。

  - **パターンの要素を、条件なしで押す**（`picks` の `pick` が空）: ここで画面を読み、
    **画面に見えている1件目**を選ぶ（`first_visible()`）。モデルには訊かない。選んだ値は
    `devices.<端末>.picked` に書く（判定する側が、どれを押したかを知るため）。撮るたびに
    選び直す — データが変わっても古い値で走らないように
  - **打つ文字と、条件つきの選択**: `devices.<端末>.inputs` の値を使う。空なら止まる
    （下の「入力が未定なら」）。再開のときは止まった本から走る（もうそこに居る）

**パターンの要素を押すときは、同じ ID の行のうち何番目かも数える**（`locate()`）。行の ID は
表示中の名前なので、同じ名前の行は ID も同じになる。数えた番号を Maestro の `index` に渡して
1件に絞る。値はアクセシビリティ ID そのもの（ダンプの id の欄）。条件つきの選択は
`<ID>#2`（見えている同じ ID の行のうち上から2件目）とも書ける。

**見たい行が含む語（`see` の `runtime`）は、走らせる前に今の画面で確かめる**（`contains_problem()`、#85）。
行の ID をまるごと書いた、または語が今の画面のどの行の ID にも入っていないなら、走らせずに止めて
表示中の行を並べる。フローは必ず落ちるうえ、落ちると「撮れなかった」項目として別の判断に回る。

**セレクタに入る値だけ正規表現としてエスケープする**（`fill_env()`）。どれがそうかは
組んだ行の `input_use` を見る。inputs に書く側はエスケープをかけない。

**値はフローに書き戻さない。** マニフェストの `devices.<端末>.inputs` を走らせる直前に env へ
入れる。フローに残すと、次の実行で「もう埋まっている」ことになり、データが
変わっても古い値で走る。


**続きから走るとき、前を撮り直さない** — 止まった時点でアプリはその画面に居るので、
続きのフローはそのまま走る。手前からやり直すと、撮れている証跡を捨てて撮り直すことになる。

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
from pathlib import Path

from device import simulators   # UDID から起動しているかを引く
from flowgen import flow as flows_of   # テストケースごとにフローを組む
from screenmap.map import load_map

HERE = Path(__file__).resolve().parent
MAESTROD = HERE / "maestrod.py"


def required_env(body):
    """フローが要求している env の名前。`---` より前の env: ブロックから拾う。"""
    head = body.split("\n---", 1)[0].splitlines()
    out, inside = [], False
    for line in head:
        if re.match(r"^env:\s*$", line):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+([A-Za-z_][A-Za-z0-9_]*):", line)
            if not m:
                break
            out.append(m.group(1))
    return out


def yaml_quote(v):
    """env の値をシングルクォートで包む。正規表現の `\\` を素通しするため。"""
    return "'" + str(v).replace("'", "''") + "'"


def fill_env(body, need, inputs, uses):
    """フローの env: ブロックに値を埋める。`uses` は変数名 → `selector` / `text`。

    **セレクタに入る値は正規表現としてエスケープする。** tapOn の id / text は
    正規表現で、表示テキストには `(` や `+` や `.` が普通に入る。そのまま入れると
    `牛乳(1L)` に当たらず、`a.b` が `aXb` にも当たる。inputText に入る値は
    打つ文字そのものなので触らない。どちらに入るかは manifest.py がマニフェストに書いている。
    """
    for k in need:
        v = str(inputs[k])
        if uses.get(k) == "selector":
            v = re.escape(v)
        body = re.sub(r"^(\s*{}:\s*).*$".format(re.escape(k)),
                      lambda m, v=v: m.group(1) + yaml_quote(v),
                      body, count=1, flags=re.M)
    return body


def sh(args, quiet=True):
    """出力は捨てる。ダンプを読むのは判定する側で、ここではない。

    `quiet=False` でも、**落ちたときだけ**出す。通ったフローの `OK {...}` は
    1本ごとに出ると読むものが増えるだけで、判断には使わない。
    """
    r = subprocess.run([sys.executable, str(MAESTROD)] + args,
                       capture_output=True, text=True)
    if r.returncode != 0 and not quiet:
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
    return r.returncode


def save(manifest_path, manifest):
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")


def retake_runs(cases, sections, only):
    """`--only` で走らせるテストケースと項目。[(テストケース, [(項目, "shot" / "replay")])]。

    撮り直す項目ごとに、**そのテストケースの頭（起動し直して）からその項目まで**を走らせる。
    手前の項目は `replay`（なぞるだけで撮らない）。同じテストケースに撮り直す項目が2つあれば、
    後ろのほうまで1回だけ走らせる。テストケースどうしは依存しないので、前のテストケースは
    なぞらない。
    """
    names = [it["name"] for c in cases for it in c["items"]]
    for n in only:
        if n not in names:
            sec = next((s for s in sections if s.get("name") == n), None)
            sys.exit(f"{n} はフローを持たない（探索で撮る項目）。sim-driver に渡す" if sec
                     else f"{n} というセクションが無い。ある名前: {', '.join(names)}")
    out = []
    for c in cases:
        hit = [k for k, it in enumerate(c["items"]) if it["name"] in only]
        if hit:
            out.append((c, [(it, "shot" if it["name"] in only else "replay")
                            for it in c["items"][:max(hit) + 1]]))
    return out


def dump_rows(dump):
    """elements.py の出力を [{"cx", "cy", "on", "top", "id", "text", "state"}] で。

    行はタブ区切り（tap / 画面内 / 上端 / id / テキスト / 状態）。**id に空白が入っても
    1回で取れる**（`browse.book_row.BOITEUX ・ BOITEUSE`）。1行目の画面と2行目の欄名は飛ばす。
    """
    out = []
    for line in dump.splitlines():
        cols = line.split("\t")
        m = re.match(r"^\((-?\d+),(-?\d+)\)$", cols[0])
        if not m or len(cols) < 6 or not cols[2].lstrip("-").isdigit():
            continue
        out.append({"cx": int(m.group(1)), "cy": int(m.group(2)), "on": cols[1] == "○",
                    "top": int(cols[2]), "id": cols[3], "text": cols[4], "state": cols[5]})
    return out


def shown_ids(dump):
    """ダンプのうち、画面の中にある（`×` でない）要素の ID。居る画面を決めるのに使う。"""
    out = []
    for line in dump.splitlines():
        cols = line.split("\t")
        if len(cols) >= 6 and re.match(r"^\((-?\d+),(-?\d+)\)$", cols[0]) and cols[1] != "×" and cols[3]:
            out.append(cols[3])
    return out


def box_of(raw, rid):
    """生のダンプ（maestrod.py が置く JSON）で、その ID の要素の枠 (x0, y0, x1, y1)。無ければ None。

    **親の中の行だけから選ぶのに使う。** elements.py の行は中心しか持たないので、親の枠は
    生のほうから取る。同じ ID の要素が複数あれば、ツリーで先のもの。
    """
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    stack = list(d["elements"]) if isinstance(d, dict) and "elements" in d else [d]
    while stack:
        n = stack.pop(0)
        if not isinstance(n, dict):
            continue
        a = n.get("attributes") or {}
        if (n.get("rid") or a.get("resource-id")) == rid:
            m = re.match(r"^\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]$", n.get("b") or a.get("bounds") or "")
            return tuple(map(int, m.groups())) if m else None
        stack[0:0] = n.get("c") or n.get("children") or []
    return None


def pattern_rows(dump, pattern, exclude=(), box=None):
    """ダンプのうちパターンに当たる行を、Maestro の index と同じ順で [(ID, 画面内か)]。

    ID はアクセシビリティ ID そのもの（ダンプの id の欄）。**順は上端の y、次に x**（Maestro の Filters.index が
    当たった要素を並べる INDEX_COMPARATOR と同じ基準。x は中心で代える — 同じ ID の行は
    幅がそろう）。**画面外の行も返す** — Maestro の index はそれも数える。

    `exclude` はマップで別の要素として定義されている ID（行の中のタイトルなど）。
    パターン（`item_list.cell.*`）の前方一致には当たるが、行ではないので数えない。
    パターンになっているもの（`item_list.cell.badge.*`）は前方一致で外す。

    `box` を渡すと、中心がその枠の中にある行だけ（子の要素を、選んだ親の中から選ぶとき）。
    """
    prefix = pattern[:-1] if pattern.endswith("*") else pattern
    rows = []
    for r in dump_rows(dump):
        rid = r["id"]
        if any(rid == x or (x.endswith("*") and rid.startswith(x[:-1])) for x in exclude):
            continue
        if box and not (box[0] <= r["cx"] <= box[2] and box[1] <= r["cy"] <= box[3]):
            continue
        if rid.startswith(prefix) and len(rid) > len(prefix):
            rows.append((r["top"], r["cx"], rid, r["on"]))
    return [(v, on) for _, _, v, on in sorted(rows, key=lambda x: (x[0], x[1]))]


def locate(dump, pattern, exclude=(), value=None, box=None):
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
    rows = pattern_rows(dump, pattern, exclude, box)
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


def first_visible(dump, pattern, exclude=()):
    """画面に見えている1件目の ID。無ければ None。"""
    found = locate(dump, pattern, exclude)
    return found[0] if found else None


def parent_of(pk, dev):
    """選ぶ範囲の親の具体的な ID（`picks` の `within`）。親が無ければ None。"""
    w = pk.get("within")
    if not w:
        return None
    if "id" in w:
        return w["id"]
    return (dev.get("picked") or {}).get(w["var"]) or (dev.get("inputs") or {}).get(w["var"])


def read_dump(udid, name):
    """画面を読んで、elements.py の出力を返す。読めなければ None。"""
    if sh(["inspect", udid, name]) != 0:
        return None
    last = HERE.parent / ".work" / "state" / f"last_dump_{udid}.txt"   # maestrod.py が置く
    if not last.exists():
        return None
    return last.read_text(encoding="utf-8")


def value_hint(pk, use):
    """止まったときに、何を決めるのかを言う文。取り違えると、行の ID を渡すべきところに語を、
    語を渡すべきところに行の ID を書いてしまう。

    - `pick`（パターンの要素・親のどれか）: 行の ID をまるごと
    - 打つ文字（`input_use` が `text`）: 入力欄に打つ文字
    - 見る行が含む語（`see` の `runtime`。`selector` で `picks` に無い）: 行の ID の一部になる語。
      行の ID をまるごと書くと、その後ろに何も続かない行を探すことになり当たらない
    """
    if pk:
        return f"条件「{pk['pick']}」に合う {pk['pattern']} を選んで、その ID（ダンプの id の欄をまるごと）を"
    if use == "text":
        return "打つ文字を"
    return "見たい行が含む語（行の ID の一部。ID をまるごと書かない）を"


def contains_problem(dump, see, value):
    """見たい行が含む語（see の runtime）が、今の画面の行に当たるか。当たれば None、
    当たらなければ呼び出し元に見せる文。

    フローは `^<接頭辞>.*<語>.*` の ID を探すので、次のどちらでも必ず落ちる（#85）。

    - **行の ID をまるごと書いた**（接頭辞から始まる）。後ろに何も続かない行を探すことになる
    - **語が、今の画面の行の ID のどれにも入っていない**。作者で絞り込んだのに、ID が作品名の行を
      作者名で待つ、など。止まった時点でアプリは値を使う直前の画面（絞り込まれた一覧）に居るので、
      そこにある行と照らせば分かる。画面外の行もダンプに出ている範囲で数える
    """
    prefix = see["pattern"][:-1] if see["pattern"].endswith("*") else see["pattern"]
    if value.startswith(prefix):
        return (f"「{value}」は行の ID をまるごと書いている。行の ID の一部（{prefix} を除いた"
                f"語）を書く")
    rows = [v for v, _ in pattern_rows(dump, see["pattern"], see.get("exclude") or ())]
    if any(value in v[len(prefix):] for v in rows):
        return None
    shown = ", ".join(v[len(prefix):] for v in rows[:10]) or "（パターンに当たる行が無い）"
    return (f"「{value}」を ID に含む {see['pattern']} の行が、いまの画面に無い。"
            f"表示中の行（{prefix} を除いた ID）: {shown}。"
            "この中の行の ID の一部を書く — 打った語と同じとは限らない（作者で絞り込めば、行の ID は作品名）")


def forget(shots, sec):
    """その項目の前の回の証跡・ダンプを消し、判定を PENDING に戻す。"""
    for ext in (".png", ".txt"):
        f = shots / (sec["name"] + ext)
        if f.exists():
            f.unlink()
    sec["desc"], sec["note"], sec["result"] = "", "", "PENDING"


class DeviceRun:
    """1台ぶんを撮るときの置き場。run_item() に渡す。"""

    def __init__(self, device, udid, shots, scratch, fdir, log):
        self.device, self.udid = device, udid
        self.shots, self.scratch, self.fdir, self.log = shots, scratch, fdir, log

    def logline(self, text):
        with self.log.open("a", encoding="utf-8") as f:
            f.write(text + "\n")


def run_item(dv, sec, row, replay, first=0):
    """1項目のフローを、`first` 本目から順に走らせる。("done", None) / ("failed", 理由) / ("stopped", 何本目か)。

    `row` はその項目を組んだ行（flowgen の render_case()）。`parts` を順に走らせ、値を決める本の
    前で値を決める（picks / sees / input_use）。
    """
    device, udid, logline = dv.device, dv.udid, dv.logline
    shots, fdir = dv.shots, dv.fdir
    name = line = sec["name"]
    dest = dv.scratch if replay else shots
    dev = sec["devices"][device]
    dev["picked"] = {} if first == 0 else dev.get("picked") or {}
    picks, sees, uses = row["picks"], row["sees"], row["input_use"]
    notes = {}   # 同じ名前の行があったときの、何件目を押したか
    parts = [(p["flow"], p.get("decide")) for p in row["parts"]]
    for k in range(first, len(parts)):
        flow_name, decide = parts[k]
        last = k == len(parts) - 1
        flow = fdir / flow_name
        if decide:
            pk = picks.get(decide)
            given = (dev.get("inputs") or {}).get(decide)
            if (pk is None or pk.get("pick")) and not given:
                # **未定ならそこで止まる。** 落ちたときは次のテストケースへ進むが、こちらは進めない。
                # 先へ走らせると画面が変わってしまい、値を決めるために見ることができない。
                # **止まった時点でアプリはその画面に居る。** 値を埋めて叩き直せば続きから走る。
                if replay:
                    # なぞる項目の値は、前に撮ったときに決めてあるはず。ここで決めさせると、
                    # 撮り直しのたびに手前の項目の判断をやり直すことになる
                    sys.exit(f"{device} {name} の値が未定（{decide}）。撮り直しは手前の項目を"
                             f"なぞるので、devices.{device}.inputs に前に撮ったときの値が要る")
                logline(f"{line} 撮影せず 入力が未定（{decide}）")
                how = value_hint(pk, uses.get(decide))
                print(f"\n{device} {line} 入力が未定（{decide}）。")
                print(f"いまこの画面に居る。見て {how} {decide} に決め、"
                      f"devices.{device}.inputs に書き、同じコマンドをもう一度叩けば続きから走る。")
                if pk:
                    print(f"同じ ID の行が複数あるなら「<ID>#2」のように書く"
                          "（画面に見えている同じ ID の行のうち上から2件目）。")
                return ("stopped", k)
            prefix = pk["pattern"][:-1] if pk and pk["pattern"].endswith("*") else None
            if pk and pk.get("pick") and prefix and not given.startswith(prefix):
                # 書いた ID がこの操作のパターンに当たらない（別の画面の ID、接頭辞の書き間違い）。
                # 押すと別物を押すか落ちる。まだ画面は動いていないので、直して叩き直せば続きから走る
                logline(f"{line} 撮影せず {decide} の {given} が {pk['pattern']} に当たらない")
                print(f"\n{device} {line} {decide} の値 {given} が {pk['pattern']} に当たらない。"
                      f"ダンプの id の欄（{prefix}…）をそのまま devices.{device}.inputs に書き、"
                      "同じコマンドをもう一度叩く。")
                return ("stopped", k)
            see = sees.get(decide)
            if see and given:
                # 見たい行が含む語は、走らせる前に今の画面で確かめる（#85）。まだ画面は動いて
                # いないので、直して叩き直せば続きから走る
                dump = read_dump(udid, f"{name}.see{k}")
                problem = contains_problem(dump, see, given) if dump else None
                if problem:
                    if replay:
                        sys.exit(f"{device} {name} の {decide}: {problem}。撮り直しは手前の項目を"
                                 f"なぞるので、devices.{device}.inputs を直してから叩き直す")
                    logline(f"{line} 撮影せず {decide} の「{given}」を含む行が無い")
                    print(f"\n{device} {line} {decide}: {problem}。"
                          f"devices.{device}.inputs を直し、同じコマンドをもう一度叩けば続きから走る。")
                    return ("stopped", k)
            if pk is not None:
                # どの行を押すかを決め、同じ ID の行のうち何番目か（Maestro の index）を数える。
                # 条件が無ければ画面に見えている1件目（モデルに訊かない）
                dump = read_dump(udid, f"{name}.pick{k}")
                box, parent = None, parent_of(pk, dev)
                if dump and parent:
                    # 子の要素は、選んだ親（カルーセルなど）の枠の中から選ぶ
                    raw = HERE.parent / ".work" / "state" / f"last_raw_{udid}.json"
                    box = box_of(raw.read_text(encoding="utf-8"), parent) if raw.exists() else None
                    if box is None:
                        why = f"親 {parent} が画面に無い"
                        logline(f"{line} 撮影できず {why}")
                        print(f"{line} {why}", file=sys.stderr)
                        return ("failed", why)
                found = locate(dump, pk["pattern"], pk.get("exclude") or (),
                               given if pk.get("pick") else None, box) if dump else None
                if found is None:
                    what = f"{given} が" if pk.get("pick") else f"押す {pk['pattern']} が"
                    why = f"{what}画面に見えていない"
                    logline(f"{line} 撮影できず {why}")
                    print(f"{line} {why}", file=sys.stderr)
                    return ("failed", why)
                value, index, count = found
                dev["picked"][decide] = value
                dev["picked"][decide + "_INDEX"] = index
                if count > 1:
                    notes[decide] = f"同じ名前 {count}件のうち上から{index + 1}件目"

        body = flow.read_text(encoding="utf-8")
        need = required_env(body)
        target = "@" + str(flow)
        if need:
            # 撮影先は端末で決まる。決めるのはそれ以外の値
            values = dict(dev.get("inputs") or {}, **dev["picked"], SHOTS=str(dest.resolve()))
            missing = [v for v in need if values.get(v) in (None, "")]
            if missing:
                sys.exit(f"{name} の {flow_name} に値が入らない（{', '.join(missing)}）。"
                         "manifest.py で作り直す")
            target = fill_env(body, need, values, uses)
        run_name = name if last else f"{name}.{k + 1}"
        # 落ちたら、その run をもう一度は走らせない。1回目の出力をそのまま見せる
        if sh(["run", udid, target, run_name, str(dest)], quiet=False) != 0:
            where = "" if last else f"（{k + 1}本目）"
            logline(f"{line} 撮影できず フローが失敗{where}")
            print(f"{line} 失敗{where}", file=sys.stderr)
            return ("failed", f"フローが失敗{where}")
    if replay:
        logline(f"{line} なぞった（撮り直しの前提）")
        return ("done", None)
    sh(["inspect", udid, name, str(shots)])   # 出力は捨てる
    picked = ", ".join(f"{k}={v}" + (f"（{notes[k]}）" if k in notes else "")
                       for k, v in dev["picked"].items() if not k.endswith("_INDEX"))
    logline(f"{line} 撮影済み" + (f"（選んだ: {picked}）" if picked else ""))
    print(line + " 撮影済み" + (f"（選んだ: {picked}）" if picked else ""))
    return ("done", None)


def run_device(manifest, manifest_path, flow_dir, device, udid, resume, only=None, prepare=False, keep=False):
    """1台ぶんのフローを、テストケースごとに組んで走らせる。(止まった位置, 撮った数, 撮れなかった行) を返す。

    入力が未定で止まったら、{"from": 項目の名前, "part": 何本目か, "cursor": そのテストケースを
    組んだ状態} を返す（呼ぶ側が再開位置に書く）。`resume` も同じ形で、同じ状態から
    そのテストケースを組み直し、その項目のその本から走らせる。
    `only` を渡すと、その項目だけ撮り直す（`retake_runs()`）。`prepare` なら撮り直すフローを
    書くだけで走らせず、`keep` なら書いてあるフローを上書きせずに使う。
    """
    out = manifest_path.parent
    shots, log = out / "shots" / device, out / f"progress_{device}.log"
    shots.mkdir(parents=True, exist_ok=True)
    # なぞる項目の撮影先。証跡を上書きしないように、作業用の置き場に逃がす
    scratch = HERE.parent / ".work" / "replay" / out.resolve().name / device
    scratch.mkdir(parents=True, exist_ok=True)
    # フローは端末ごとに置く。落ちたあとの繋ぎ方で、端末ごとに中身が変わる
    fdir = flow_dir / device
    fdir.mkdir(parents=True, exist_ok=True)

    plan = flows_of.plan_of(manifest)
    mp = load_map(plan["repo"])
    cases = flows_of.flow_cases(plan)
    secs = {s["name"]: s for s in manifest["sections"]}
    if only:
        runs = retake_runs(cases, manifest["sections"], only)
    else:
        runs = [(c, [(it, "shot") for it in c["items"]]) for c in cases]
    resume_name, resume_part = (resume["from"], resume["part"]) if resume else (None, 0)
    if resume_name:
        at = next((k for k, (_, its) in enumerate(runs) if any(it["name"] == resume_name for it, _ in its)), None)
        if at is None:
            names = [it["name"] for _, its in runs for it, _ in its]
            sys.exit(f"再開先 {resume_name} が見つからない。ある名前: {', '.join(names)}")
        runs = runs[at:]

    dv = DeviceRun(device, udid, shots, scratch, fdir, log)
    logline = dv.logline

    if prepare:
        pass                                          # 書くだけ。ログには何も足さない
    elif only:
        logline("--- 撮り直し: " + ", ".join(only) + ("（手で直したフローで）" if keep else ""))
    elif resume_name:
        logline(f"--- 再開: {resume_name} の {resume_part + 1}本目から")
    else:
        # 最初から撮るときは、進捗ログと、マニフェストに無い名前の証跡と、前の回のフローを消す
        log.write_text("", encoding="utf-8")
        current = {s["name"] for s in manifest["sections"]}
        for old in list(shots.glob("*.png")) + list(shots.glob("*.txt")):
            if old.stem not in current:
                old.unlink()
        for old in fdir.glob("*.yaml"):
            old.unlink()

    def recover(made, case, name):
        """落ちたあと、次のテストケースをどこから組むか（Cursor）。落ちた地点の画面を読んで決める。"""
        if only or case["next"] is None or case["next"]["restart"]:
            return flows_of.Cursor.fresh(mp)          # 次が無いか、次はどのみち起動し直す
        dump = read_dump(udid, f"{name}.where")
        screen, hits = flows_of.screen_at(mp, shown_ids(dump or ""))
        nxt = case["next"]["name"]
        if screen is None:
            why = ("どの画面の anchor も見えていない" if not hits
                   else "anchor が複数見えている（{}）".format(", ".join(hits)))
            logline(f"{name} のあと: {why}。{nxt} は起動し直して始める")
            return flows_of.Cursor.fresh(mp)
        logline(f"{name} のあと: 画面 {screen} に居る。{nxt} はそこから繋ぐ")
        return flows_of.recovered(mp, made, name, screen)

    def build(case, start):
        """テストケースを組んで、フローを書く。(組んだもの, 項目ごとの行, 次の Cursor, 組んだ状態)。

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
        rows, end = flows_of.render_case(mp, plan["app"], plan["clear_state"], start, got)
        for r in rows:
            for fname, text in r["flows"].items():
                f = fdir / fname
                if keep and f.exists():
                    continue                          # 手で直したフロー。上書きしない
                f.write_text(text, encoding="utf-8")
        return got, {r["name"]: r for r in rows}, end, start

    def unexpected(sec, kind, reason=""):
        """撮れなかったことを、その端末の欄に書く（kind が None なら消す）。判定（`result`）ではない。"""
        dev = sec["devices"][device]
        if kind:
            dev["unexpected"] = {"kind": kind, "reason": reason}
        else:
            dev.pop("unexpected", None)

    print(f"--- {device}（{udid}）")
    cursor = flows_of.Cursor.from_json(resume["cursor"]) if resume else flows_of.Cursor.fresh(mp)
    done, lost = 0, []
    for ci, (case, items) in enumerate(runs):
        if only:
            cursor = flows_of.Cursor.fresh(mp)        # 撮り直しは、テストケースの頭で起動し直す
        for attempt in (0, 1):
            made, rows, end, start = build(case, cursor)
            if prepare:
                if rows is None:
                    print(f"{case['title']}: フローが組めない（{'; '.join(m for _, m in made.problems)}）",
                          file=sys.stderr)
                else:
                    names = [p["flow"] for it, _ in items for p in rows[it["name"]]["parts"]]
                    print("\n".join(str(fdir / f) for f in names))
                break
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
            got, missed, failed_at = 0, [], None
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
                    missed.append(f"{device} {name}")
                    logline(f"{name} 撮影できず 同じテストケースの {failed_at} が落ちたので飛ばした")
                    if not replay:
                        unexpected(sec, "skipped", f"同じテストケースの {failed_at} が落ちた")
                    continue
                status = run_item(dv, sec, rows[name], replay, resume_part if continuing else 0)
                if status[0] == "stopped":
                    rest = len(items) - n + sum(len(its) for _, its in runs[ci + 1:])
                    print(f"{device} のここから先の {rest}件はまだ撮っていない。")
                    return {"from": name, "part": status[1], "cursor": start.to_json()}, done + got, lost
                if status[0] == "failed":
                    failed_at = name
                    missed.append(f"{device} {name}" + ("（なぞる途中で落ちた）" if replay else ""))
                    if not replay:
                        unexpected(sec, "failed", status[1])
                elif not replay:
                    got += 1
            resume_name, resume_part = None, 0
            done += got
            if failed_at == items[0][0]["name"] and start.recovered and attempt == 0:
                # 前が落ちた地点から繋いだ頭の項目が落ちた。繋ぎ方（後始末の reset、落ちた画面からの
                # 経路）が悪かったのかもしれないので、起動し直してこのテストケースをもう1回だけ走らせる
                logline(f"{failed_at} 繋いだ頭で落ちた。起動し直してテストケース「{case['title']}」を走らせ直す")
                cursor = flows_of.Cursor.fresh(mp)
                continue
            lost += missed
            cursor = recover(made, case, failed_at) if failed_at else end
            break
    if not prepare:
        print(f"{done}件を撮った: {shots}")
    return None, done, lost


def main():
    # 標準出力を行ごとに流す。既定のバッファのままだと、即時に出る標準エラーと
    # 混ざったときに順番が入れ替わり、失敗の行が撮影済みの行より前に出る
    sys.stdout.reconfigure(line_buffering=True)

    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        if i + 1 >= len(argv):
            sys.exit("--only には撮り直す項目の名前を渡す（test_15 か test_07,test_15）")
        only = [n for n in argv[i + 1].split(",") if n]
        del argv[i:i + 2]
    prepare, keep = "--prepare" in argv, "--keep" in argv
    argv = [a for a in argv if a not in ("--prepare", "--keep")]
    if (prepare or keep) and not only:
        sys.exit("--prepare / --keep は --only と一緒に使う（撮り直す項目のフローを手で直すとき）")
    if prepare and keep:
        sys.exit("--prepare（書くだけ）と --keep（書いたもので撮る）は別々に叩く")
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

    if not any(s.get("flow") for s in manifest["sections"]):
        sys.exit("flow を持つセクションが無い。全部探索なので sim-driver に渡す。")

    # 撮り直しは再開の状態を使わない。止まったら、同じコマンドでまたテストケースの頭から走らせる
    if only:
        total, lost_all = 0, []
        for d, u in pairs:
            stopped, done, lost = run_device(manifest, manifest_path, flow_dir, d, u, None, only,
                                             prepare, keep)
            if prepare:
                continue
            total += done
            lost_all += lost
            save(manifest_path, manifest)
            if stopped:
                print("値を埋めたら、同じコマンド（--only 付き）をもう一度叩く。テストケースの頭からなぞり直す。")
                sys.exit(1)
        if prepare:
            print("\n撮り直すフローを書いた。直してから --keep を付けて叩く（--prepare を叩き直すと上書きする）")
            return
        print(f"\nこの実行で {total}件を撮り直した（{', '.join(only)}）")
        if lost_all:
            print(f"\n撮れなかった {len(lost_all)}件:", file=sys.stderr)
            for line in lost_all:
                print("  " + line, file=sys.stderr)
            sys.exit(1)
        return

    # 前の実行が未定で止まっていれば、撮り終えた端末は飛ばし、止まった端末の続きから走る
    state = manifest.get("resume") or {}
    finished = list(state.get("done") or [])
    total, lost_all = 0, []
    for d, u in pairs:
        if d in finished:
            print(f"--- {d} は撮り終えている。飛ばす")
            continue
        resume = state if state.get("device") == d and state.get("cursor") else None
        if state.get("device") == d and not state.get("cursor"):
            sys.exit("再開の状態が古い（テストケースを組んだ状態が無い）。manifest.py で作り直してから最初から撮る")
        stopped, done, lost = run_device(manifest, manifest_path, flow_dir, d, u, resume)
        total += done
        lost_all += lost
        if stopped:
            manifest["resume"] = dict(stopped, done=finished, device=d)
            save(manifest_path, manifest)
            sys.exit(1)
        finished.append(d)
    manifest.pop("resume", None)
    save(manifest_path, manifest)

    skipped = [s.get("name") for s in manifest["sections"] if not s.get("flow")]
    print(f"\nこの実行で {total}件を撮った")
    if skipped:
        print(f"飛ばした（フローが無い。探索で撮る）: {', '.join(x or '?' for x in skipped)}")
    if lost_all:
        print(f"\n撮れなかった {len(lost_all)}件:", file=sys.stderr)
        for line in lost_all:
            print("  " + line, file=sys.stderr)
        sys.exit(1)

main()
