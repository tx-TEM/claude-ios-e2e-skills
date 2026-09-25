#!/usr/bin/env python3
"""マニフェストに載っているフローを順に走らせ、証跡と同名のダンプを撮る。

  run_flows.py <manifest.json>
  run_flows.py <manifest.json> --only test_15[,test_07]   その項目だけ撮り直す

**どのシミュレーターで撮るかはマニフェストの `devices` に、フローの置き場は `flows` に
入っている**（手順0で manifest.py が書いたもの）。ここで渡し直さない。その UDID が起動していなければ止まる — 別の端末で代用しない。

**1回で全端末を撮る。** マニフェストの順に1台ずつ撮り切ってから次の端末へ移る（Maestro の
デーモンが握れるのは1台で、行き来させると切り替えのたびに約10秒かかる）。フローは
全端末で同じで、撮影先の `${SHOTS}` にここで `<出力先>/shots/<端末>` を入れる。
進捗ログは `<出力先>/progress_<端末>.log`。実行時に決める値は端末ごと — その端末の
画面を見て決める。

**未定で止まったら、叩き直すと続きから走る。** 撮り終えた端末は飛ばし、止まった端末の
止まった項目から走る。状態はマニフェストの `resume` に書き、走り切ったら消す。

`flow` を持つセクションだけを対象にする。持たないセクション（経路が組めず探索で
撮るもの）は飛ばすので、**そちらは sim-driver に任せる。**

**フローを一息に走らせる。** 続きのフローは前のフローが終わった画面から始まるので、
間にアプリの画面を動かすものが入ると次の到達判定が落ちる。ここが最後まで
走り切ってから sim-driver に渡せば、そうならない。マニフェストの並びに探索の
セクションが混ざっていてもよい（実行しないだけ）。

**`--only` はその項目だけ撮り直す**（判定の `RETAKE` を撮り直すとき）。フローは鎖なので、
その項目だけを走らせても前提の状態が無い。**同じ鎖の頭（起動し直すフロー）から走らせ、
手前の項目は撮らずになぞる。** なぞった項目の証跡と判定には触らない（撮影先は作業用の
置き場に逃がす）。撮り直した項目だけ判定を `PENDING` に戻す。手前の項目の実行時の値は
マニフェストに残っているものを使う — そこが空なら走らせられないので止まる。

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

**実行時に決める値があるフローは割れている**（セクションの `parts`）。前の本が目的の
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

**セレクタに入る値だけ正規表現としてエスケープする**（`fill_env()`）。どれがそうかは
マニフェストの `input_use` を見る。inputs に書く側はエスケープをかけない。

**値はフローに書き戻さない。** マニフェストの `devices.<端末>.inputs` を走らせる直前に env へ
入れる。フローに残すと、次の実行で「もう埋まっている」ことになり、データが
変わっても古い値で走る。


**続きから走るとき、前を撮り直さない** — 止まった時点でアプリはその画面に居るので、
続きのフローはそのまま走る。手前からやり直すと、撮れている証跡を捨てて撮り直すことになる。

**入力が未定ならそこで終える。** 落ちたときは次の鎖へ進むが、こちらは進めない
— 先へ走らせると画面が変わり、値を決めるために見ることができなくなる。

**落ちたら、次に起動し直すフローまで飛ばす。** 続きのフロー（`launch` が偽）は
前のフローが終わった画面から始まるので、1本落ちたあとを走らせても意味がない。
**`launch` が真のフローからは走らせる** — 自分で `stopApp` / `launchApp` するので、
前が落ちたことに影響されない。1回で取れる証跡は取っておいたほうが、直して
走らせ直すときの手がかりが増える。落ちた地点の画面は maestrod.py run が出す。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from device import simulators   # UDID から起動しているかを引く

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
    打つ文字そのものなので触らない。どちらに入るかは route.py がマニフェストに書いている。
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


def retake_runs(sections, only):
    """`--only` で走らせるセクションと、その扱い。[(セクション, "shot" / "replay"), ...]。

    撮り直す項目ごとに、**同じ鎖の頭（`launch` が真のフロー）からその項目まで**を走らせる。
    手前の項目は `replay`（なぞるだけで撮らない）。同じ鎖に撮り直す項目が2つあれば、
    鎖は1回だけ走らせる。
    """
    flows = [s for s in sections if s.get("flow")]
    names = [s["name"] for s in flows]
    for n in only:
        if n not in names:
            sec = next((s for s in sections if s.get("name") == n), None)
            sys.exit(f"{n} はフローを持たない（探索で撮る項目）。sim-driver に渡す" if sec
                     else f"{n} というセクションが無い。ある名前: {', '.join(names)}")
    run = set()
    for n in only:
        t = names.index(n)
        head = max(j for j in range(t + 1) if flows[j].get("launch") or j == 0)
        run.update(range(head, t + 1))
    return [(flows[j], "shot" if flows[j]["name"] in only else "replay") for j in sorted(run)]


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


def pattern_rows(dump, pattern, exclude=()):
    """ダンプのうちパターンに当たる行を、Maestro の index と同じ順で [(ID, 画面内か)]。

    ID はアクセシビリティ ID そのもの（ダンプの id の欄）。**順は上端の y、次に x**（Maestro の Filters.index が
    当たった要素を並べる INDEX_COMPARATOR と同じ基準。x は中心で代える — 同じ ID の行は
    幅がそろう）。**画面外の行も返す** — Maestro の index はそれも数える。

    `exclude` はマップで別の要素として定義されている ID（行の中のタイトルなど）。
    パターン（`item_list.cell.*`）の前方一致には当たるが、行ではないので数えない。
    パターンになっているもの（`item_list.cell.badge.*`）は前方一致で外す。
    """
    prefix = pattern[:-1] if pattern.endswith("*") else pattern
    rows = []
    for r in dump_rows(dump):
        rid = r["id"]
        if any(rid == x or (x.endswith("*") and rid.startswith(x[:-1])) for x in exclude):
            continue
        if rid.startswith(prefix) and len(rid) > len(prefix):
            rows.append((r["top"], r["cx"], rid, r["on"]))
    return [(v, on) for _, _, v, on in sorted(rows, key=lambda x: (x[0], x[1]))]


def locate(dump, pattern, exclude=(), value=None):
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
    rows = pattern_rows(dump, pattern, exclude)
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


def read_dump(udid, name):
    """画面を読んで、elements.py の出力を返す。読めなければ None。"""
    if sh(["inspect", udid, name]) != 0:
        return None
    last = HERE.parent / ".work" / "state" / f"last_dump_{udid}.txt"   # maestrod.py が置く
    if not last.exists():
        return None
    return last.read_text(encoding="utf-8")


def parts_of(sec):
    """セクションのフローを、走らせる順に [(フロー, その前に決める値)] で。"""
    parts = sec.get("parts") or [{"flow": sec["flow"], "decide": None}]
    return [(p["flow"], p.get("decide")) for p in parts]


def run_device(manifest, manifest_path, flow_dir, device, udid, resume, only=None):
    """1台ぶんのフローを走らせる。(止まった位置, 撮った数, 撮れなかった行) を返す。

    入力が未定で止まったら、(項目の名前, 何本目か) を返す（呼ぶ側が再開位置に書く）。
    `resume` も同じ形で、その項目のその本から走らせる。
    `only` を渡すと、その項目だけ撮り直す（`retake_runs()`）。
    """
    out = manifest_path.parent
    shots, log = out / "shots" / device, out / f"progress_{device}.log"
    shots.mkdir(parents=True, exist_ok=True)
    # なぞる項目の撮影先。証跡を上書きしないように、作業用の置き場に逃がす
    scratch = HERE.parent / ".work" / "replay" / out.resolve().name / device
    scratch.mkdir(parents=True, exist_ok=True)
    resume_name, resume_part = resume if resume else (None, 0)

    targets = [(i, s) for i, s in enumerate(manifest["sections"], 1) if s.get("flow")]
    mode = {}
    if only:
        runs = retake_runs(manifest["sections"], only)
        mode = {s["name"]: m for s, m in runs}
        targets = [(i, s) for i, s in targets if s["name"] in mode]
    elif resume_name:
        names = [s["name"] for _, s in targets]
        if resume_name not in names:
            sys.exit(f"再開先 {resume_name} が見つからない。ある名前: {', '.join(names)}")
        targets = targets[names.index(resume_name):]

    def logline(text):
        with log.open("a", encoding="utf-8") as f:
            f.write(text + "\n")

    print(f"--- {device}（{udid}）")
    done, lost, broken = 0, [], False
    for i, sec in targets:
        name = sec["name"]
        line = name
        replay = mode.get(name) == "replay"
        dest = scratch if replay else shots
        if sec.get("launch"):
            broken = False          # ここから鎖が切り替わる
        if broken:
            lost.append(f"{device} {line}")
            logline(f"{line} 撮影できず 続きなので、前のフローの失敗で飛ばした")
            continue
        dev = sec["devices"][device]
        dev["picked"] = {} if not (resume_name == name and resume_part) else dev.get("picked") or {}
        picks = sec.get("picks") or {}
        notes = {}   # 同じ名前の行があったときの、何件目を押したか
        parts = parts_of(sec)
        # 再開のときは、止まった本から走らせる（前の本は走っていて、アプリはもうそこに居る）
        first = resume_part if name == resume_name else 0
        failed = False
        for k in range(first, len(parts)):
            flow_name, decide = parts[k]
            last = k == len(parts) - 1
            flow = flow_dir / flow_name
            if not flow.exists():
                sys.exit(f"{name} フローが無い: {flow}")
            if decide:
                pk = picks.get(decide)
                given = (dev.get("inputs") or {}).get(decide)
                if (pk is None or pk.get("pick")) and not given:
                    # **未定ならそこで止まる。** 落ちたときは次の鎖へ進むが、こちらは進めない。
                    # 先へ走らせると画面が変わってしまい、値を決めるために見ることができない。
                    # **止まった時点でアプリはその画面に居る。** 値を埋めて叩き直せば続きから走る。
                    if replay:
                        # なぞる項目の値は、前に撮ったときに決めてあるはず。ここで決めさせると、
                        # 撮り直しのたびに手前の項目の判断をやり直すことになる
                        sys.exit(f"{device} {name} の値が未定（{decide}）。撮り直しは手前の項目を"
                                 f"なぞるので、devices.{device}.inputs に前に撮ったときの値が要る")
                    logline(f"{line} 撮影せず 入力が未定（{decide}）")
                    rest = [n for n, _ in targets[targets.index((i, sec)):]]
                    how = (f"条件「{pk['pick']}」に合う {pk['pattern']} を選んで、その ID（ダンプの id の欄）を"
                           if pk else "打つ文字を")
                    print(f"\n{device} {line} 入力が未定（{decide}）。")
                    print(f"いまこの画面に居る。見て {how} {decide} に決め、"
                          f"devices.{device}.inputs に書き、同じコマンドをもう一度叩けば続きから走る。")
                    if pk:
                        print(f"同じ ID の行が複数あるなら「<ID>#2」のように書く"
                              "（画面に見えている同じ ID の行のうち上から2件目）。")
                    print(f"{device} のここから先の {len(rest)}件はまだ撮っていない。")
                    return (name, k), done, lost
                prefix = pk["pattern"][:-1] if pk and pk["pattern"].endswith("*") else None
                if pk and pk.get("pick") and prefix and not given.startswith(prefix):
                    # 書いた ID がこの操作のパターンに当たらない（別の画面の ID、接頭辞の書き間違い）。
                    # 押すと別物を押すか落ちる。まだ画面は動いていないので、直して叩き直せば続きから走る
                    logline(f"{line} 撮影せず {decide} の {given} が {pk['pattern']} に当たらない")
                    print(f"\n{device} {line} {decide} の値 {given} が {pk['pattern']} に当たらない。"
                          f"ダンプの id の欄（{prefix}…）をそのまま devices.{device}.inputs に書き、"
                          "同じコマンドをもう一度叩く。")
                    return (name, k), done, lost
                if pk is not None:
                    # どの行を押すかを決め、同じ ID の行のうち何番目か（Maestro の index）を数える。
                    # 条件が無ければ画面に見えている1件目（モデルに訊かない）
                    dump = read_dump(udid, f"{name}.pick{k}")
                    found = locate(dump, pk["pattern"], pk.get("exclude") or (),
                                   given if pk.get("pick") else None) if dump else None
                    if found is None:
                        what = f"{given} が" if pk.get("pick") else f"押す {pk['pattern']} が"
                        failed = True
                        logline(f"{line} 撮影できず {what}画面に見えていない")
                        print(f"{line} {what}画面に見えていない。次に起動し直すフローまで飛ばす",
                              file=sys.stderr)
                        break
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
                uses = sec.get("input_use")
                if uses is None and any(v != "SHOTS" for v in need):
                    sys.exit(f"{name} に input_use が無い（古いマニフェスト）。manifest.py で作り直す")
                target = fill_env(body, need, values, uses or {})
            run_name = name if last else f"{name}.{k + 1}"
            # 落ちたら、その run をもう一度は走らせない。1回目の出力をそのまま見せる
            if sh(["run", udid, target, run_name, str(dest)], quiet=False) != 0:
                failed = True
                where = "" if last else f"（{k + 1}本目）"
                logline(f"{line} 撮影できず フローが失敗{where}")
                print(f"{line} 失敗{where}。次に起動し直すフローまで飛ばす", file=sys.stderr)
                break
        if failed:
            broken = True
            lost.append(f"{device} {line}" + ("（なぞる途中で落ちた）" if replay else ""))
            continue
        if replay:
            logline(f"{line} なぞった（撮り直しの前提）")
            continue
        sh(["inspect", udid, name, str(shots)])   # 出力は捨てる
        sec["desc"], sec["note"], sec["result"] = "", "", "PENDING"   # 証跡が入れ替わったので判定も捨てる
        done += 1
        picked = ", ".join(f"{k}={v}" + (f"（{notes[k]}）" if k in notes else "")
                           for k, v in dev["picked"].items() if not k.endswith("_INDEX"))
        logline(f"{line} 撮影済み" + (f"（選んだ: {picked}）" if picked else ""))
        print(line + " 撮影済み" + (f"（選んだ: {picked}）" if picked else ""))
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

    # 撮り直しは再開の状態を使わない。止まったら、同じコマンドでまた鎖の頭から走らせる
    if only:
        total, lost_all = 0, []
        for d, u in pairs:
            stopped, done, lost = run_device(manifest, manifest_path, flow_dir, d, u, None, only)
            total += done
            lost_all += lost
            save(manifest_path, manifest)
            if stopped:
                print("値を埋めたら、同じコマンド（--only 付き）をもう一度叩く。鎖の頭からなぞり直す。")
                sys.exit(1)
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
        resume = (state.get("from"), state.get("part", 0)) if state.get("device") == d else None
        stopped, done, lost = run_device(manifest, manifest_path, flow_dir, d, u, resume)
        total += done
        lost_all += lost
        if stopped:
            manifest["resume"] = {"done": finished, "device": d, "from": stopped[0], "part": stopped[1]}
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
