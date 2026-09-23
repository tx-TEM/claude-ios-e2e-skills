#!/usr/bin/env python3
"""画面マップ（screen-map/）から、動作確認の経路を組み立てる。

  route.py screens                     画面の一覧（id / 呼び名 / できること）
  route.py path  <画面id>...           そこまでの経路を人が読む形で出す。並べると順にたどる
  route.py flow  --plan <json> --out-dir <dir>
                                       plan の項目ごとに Maestro のフローを書く
  route.py which <パス...>             変更したファイルから対象画面を引く（`-` で標準入力）
  route.py check                       マップ全体の自己テスト

  flow の引数
    --plan <json>      項目の一覧。**項目1つ＝ from から do を順に叩いて、1枚撮る。**
                         {"app": "<bundle id>", "shots_dir": "<絶対パス>",
                          "clear_state": false,
                          "items": [
                            {"shot": "iphone_01_list", "from": "browse"},
                            {"shot": "iphone_02_filter", "from": "browse",
                             "do": [{"op": "text:browse.searchField", "runtime": true}]},
                            {"shot": "iphone_03_nohit", "from": "browse",
                             "do": [{"op": "text:browse.searchField", "input": "zzzz"}]},
                            {"shot": "iphone_04_detail", "from": "browse", "fresh": true,
                             "do": ["tap:browse.bookRow.*"]}]}
                       from      その項目の操作を始める画面。**項目は経路を持たない** —
                                 前の項目が終わった画面から from までは、ここで計算して
                                 繋ぐ（すでに居れば何もしない）
                       do        確かめる操作。`tap:<id>` `scroll:down` のように種類を頭に
                                 付けて指せる（scroll は必須）。**並び順がそのまま実行順。**
                                 遷移する操作も書いてよく、行き先はマップの `to` で追う。
                                 着いた状態を見るだけの項目は空。入れる値の要る操作は
                                 {"op": 操作id, …} にして、次のどちらかを添える
                                   "runtime": true  **値を実行時に決める。** フローには値を
                                        焼き込まず、`env` の未定のまま残す。着いた画面を
                                        見ないと決まらないときに（打つ文字、どの行を叩くか）。
                                        焼き込むと、データが変わっても古い値で黙って走る
                                   "input": 値      データに依らない値（一致しない語など）
                                 from までの経路の途中で叩く操作は位置で選ぶ。どれを選ぶかを
                                 気にするなら、それは確かめる操作なので do に書く
                       fresh     その項目はアプリを起動し直した直後から始める。**項目の前提で
                                 あって、フローの切り方ではない** — 前の項目の状態（絞り込み、
                                 変えたデータ）が残ると前提が崩れるときだけ付ける
                       shot      証跡の名前。`shots_dir` の下に撮る（Maestro はデーモンの
                                 作業ディレクトリ基準で書くので、shots_dir は絶対パス）
                       `title` / `expect` / `explore` は読まない
                       （sim-test-report の manifest.py が読む）
    --out-dir <dir>    **項目ごとにフローを分けて**そのディレクトリに書く。
                       あわせて `index.json`（撮影の名前・画面・機械判定のID・
                       フローのファイル名）も置く。**呼ぶ側が写さずに済ませるため**
                       1本＝1枚＝1ダンプになるので、証跡と同名でダンプが取れる。
                       2本目以降は前の続き（起動し直さない）。標準出力には
                       読める経路が出る。組めたときだけ書く

  どこでも使える引数
    --timeout <ミリ秒> 画面や要素を待つ上限。既定 10000
    --map <dir>        画面マップの場所。省くとカレントから上へ screen-map/ を探す

**セグメントに割るのは、経路の計算を1回に縛らないため。** 「A の機能を使って
から、離れた B を確認する」のような項目は、計算が1回きりだと2つ目以降の遷移を
全部手で綴ることになり、マップから計算している意味が消える。区間に割れば、
**どの区間が切れているか**もそこで言える。

**組めなかったことを理由つきで返すのが半分の仕事。** 組めない経路は
マップが持っている穴（`in_tree: false`、未マップの画面、`stub` の先）が
そのまま出たもの。ここで推測して繋がない。埋めるのは screen-map の仕事。

**どの画面が目標かを決めるのはここの仕事ではない。** `screens` が出すのは
一覧で、絞るのは読む側。ここにキーワード一致を足さない。**文字列の一致は
`summary` を読む判断より粗いのに、決定的なツールの見た目をまとう。**
柔らかい判断は柔らかいまま人のレビューに出す（sim-test-report の手順0）。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# PyYAML を入れさせないための最小パーサ（理由は mini_yaml.py）
from mini_yaml import load_yaml

# ---------- マップ ----------

OPS = ("tap", "text", "scroll")


def op_of(action):
    """(操作の種類, 対象) を返す。tap / text / scroll のどれか1つを持つ前提。"""
    for k in OPS:
        if k in action:
            return k, action[k]
    return None, None


def label_of(action):
    op, target = op_of(action)
    if op is None:
        return "(操作が無い)"
    s = "{} {}".format(op, target)
    sel = action.get("select") or {}
    if "index" in sel:
        s += " [index {}]".format(sel["index"])
    if action.get("by") == "label":
        s += " [ラベル]"
    return s


class ScreenMap(object):
    def __init__(self, root):
        self.root = root
        cfg = load_yaml(root / "config.yaml") or {}
        self.start = cfg.get("start")
        self.screens = {}
        for f in sorted((root / "screens").glob("*.yaml")):
            self.screens[f.stem] = load_yaml(f) or {}

    def actions(self, sid):
        return (self.screens.get(sid) or {}).get("actions") or []

    def blocked(self, action):
        """往路の辺として使えない理由。使えるなら None。"""
        if not action.get("to"):
            return "遷移しない"
        if action.get("kind") in ("back", "dismiss"):
            return "復路"
        if action.get("in_tree") is False:
            return "in_tree: false（座標が要る）"
        if action.get("to") not in self.screens:
            return "遷移先 {} のファイルが無い".format(action.get("to"))
        return None

    def path_from(self, src, goal, relaxed=False):
        """src から goal までの最短経路。[(画面id, action), ...] か None。

        **起点固定にしない。** セグメントを繋ぐとき、2本目以降は
        いま居る画面から引くことになる。

        relaxed=True では使えない辺も通す。**経路が無いのか、切れた辺の
        先にあるのかを言い分けるため**だけに使う。
        """
        if src == goal:
            return []
        seen = {src: None}
        queue = [src]
        while queue:
            cur = queue.pop(0)
            for a in self.actions(cur):
                why = self.blocked(a)
                if why and not (relaxed and a.get("to") in self.screens):
                    continue
                nxt = a["to"]
                if nxt in seen:
                    continue
                seen[nxt] = (cur, a)
                if nxt == goal:
                    hops = []
                    at = goal
                    while seen[at]:
                        hops.append(seen[at])
                        at = seen[at][0]
                    return list(reversed(hops))
                queue.append(nxt)
        return None

    def back_action(self, sid):
        """その画面の「戻る」操作。無ければ None。"""
        for a in self.actions(sid):
            if a.get("kind") in ("back", "dismiss") and a.get("to"):
                return a
        return None

    def reachable(self):
        out = {self.start}
        queue = [self.start]
        while queue:
            cur = queue.pop(0)
            for a in self.actions(cur):
                if self.blocked(a) is None and a["to"] not in out:
                    out.add(a["to"])
                    queue.append(a["to"])
        return out


def find_map(arg):
    if arg:
        p = Path(arg).expanduser().resolve()
        for cand in (p, p / "screen-map"):
            if (cand / "config.yaml").exists():
                return cand
        sys.exit("{} に画面マップが無い（config.yaml を探した）".format(p))
    here = Path.cwd().resolve()
    for d in [here] + list(here.parents):
        if (d / "screen-map" / "config.yaml").exists():
            return d / "screen-map"
    sys.exit("screen-map/ が見つからない。--map で場所を渡す。\n"
             "このアプリにはまだ画面マップが無いのかもしれない（screen-map スキルで作る）")


# ---------- 経路を組む ----------

def resolve(mp, at, wanted):
    """その画面の操作を1つ選ぶ。`tap:<id>` のように種類を頭に付けて指せる。

    scroll は対象が `down` のような向きなので、種類を書かないと指せない。
    """
    want_op, _, want_target = wanted.partition(":")
    if want_op not in OPS:
        want_op, want_target = None, wanted
    for a in mp.actions(at):
        op, target = op_of(a)
        if target == want_target and (want_op is None or op == want_op):
            return a
    return None


def build(mp, segments, start=None):
    """セグメントを順に繋いで1本にする。

    セグメントは `goto`（経路を計算して繋ぐ）/ `do`（その場で操作する）/
    `shot`（撮る）の3つで、並び順がそのまま実行順になる。

    **分割の効き目は、区間ごとに組めたか言えること。** 「A の機能を使って
    から離れた B を確認する」のような項目は、経路計算が1回きりだと2つ目
    以降の遷移を全部手で綴ることになり、マップから計算している意味が消える。
    区間に割れば、どの区間が切れているかもそこで言える。

    歩きながら**ナビゲーションのスタックを持つ**。`goto` の行き先が既に
    積まれていれば、往路を探さずに戻る。マップの `kind: back` は `to` を
    静的に宣言しているが、**複数の入口を持つ画面ではその宣言が嘘になる**
    （どこから来たかで戻り先が変わる）。歩いた履歴の方が正しいので、
    そちらを使い、食い違いは補足として出す。

    `start` を渡すとそこから歩き始める。**フローを途中で切って続きを出す
    ため**で、切った地点でダンプを取っても歩き直しにならない。

    理由は ("map", ...) と ("call", ...) に分ける。**直す先が違う。**
    前者はマップの穴で screen-map の仕事、後者は呼び方の間違いで、
    値を渡すか行き先を選び直せば済む。
    """
    steps, problems, notes, shot_names = [], [], [], set()
    at = start or mp.start
    stack = [at]

    # セグメントの3つ目は、どの項目のものかと、do なら入れる値の決め方
    # （runtime / input）。**値の決め方は do の操作だけが持つ** — 計算した経路の
    # 途中で叩く操作はテストケースが選んだものではないので、位置で選ぶ
    def mark(frm, ctx):
        for st in steps[frm:]:
            if "action" not in st:
                continue
            if ctx.get("item"):
                st["item"] = ctx["item"]
            if ctx.get("runtime"):
                st["runtime"] = True
            elif "input" in ctx:
                st["input"] = ctx["input"]

    marked, ctx = 0, {}
    for n, seg in enumerate(segments, 1):
        mark(marked, ctx)
        marked = len(steps)
        what, value = seg[0], seg[1]
        ctx = seg[2] if len(seg) > 2 else {}
        tag = "[{}] {} {}".format(n, what, value).rstrip()

        if what == "restart":
            # 撮る前に起動し直すと、その操作の結果はどこにも残らない。
            # フローも撮影の地点でしか切れないので、間の操作は走らないまま消える
            if steps and "shot" not in steps[-1]:
                problems.append(("call", "{}: 直前が撮影ではない。起動し直すと"
                                 "その間の操作の結果はどこにも残らないので、"
                                 "撮ってから切るか、操作の方を消す".format(tag)))
                break
            # 起動し直すので、居る場所も歩いた履歴も捨てて起点に戻る
            at = mp.start
            stack = [at]
            steps.append({"restart": True})
            continue

        if what == "shot":
            # 相対パスは弾く。Maestro はデーモンの作業ディレクトリ基準で書くので、
            # どこに落ちたか分からないまま「撮れた」になる。
            if not os.path.isabs(os.path.expanduser(value)):
                problems.append(("call", "{}: shots_dir は絶対パスで書く。"
                                 "相対だと Maestro がどこに書くか決まらない"
                                 .format(tag)))
                break
            # 名前が証跡・ダンプ・項目を結ぶ唯一の手がかりなので、重複させない。
            # 同じ名前だと後の1枚が前を上書きし、項目が同じ画像を指したまま通る。
            name = os.path.basename(os.path.expanduser(value))
            if name in shot_names:
                problems.append(("call", "{}: 証跡の名前 {} が重複している。"
                                 "後から撮ったほうが上書きするので、項目ごとに別の名前にする"
                                 .format(tag, name)))
                break
            shot_names.add(name)
            steps.append({"shot": os.path.expanduser(value)})
            continue

        if what == "do":
            found = resolve(mp, at, value)
            if found is None:
                known = ", ".join("{}:{}".format(*op_of(a)) for a in mp.actions(at))
                problems.append(("call", "{}: {} に「{}」という操作がマップに無い。"
                                 "この画面にあるのは {}"
                                 .format(tag, at, value, known or "（無し）")))
                break
            steps.append({"screen": at, "action": found, "to": found.get("to")})
            if found.get("to"):
                if found.get("kind") in ("back", "dismiss"):
                    if len(stack) > 1:
                        stack.pop()
                    at = found["to"]
                else:
                    at = found["to"]
                    stack.append(at)
            continue

        # goto
        if value not in mp.screens:
            problems.append(("map", "{}: 画面 {} がマップに無い。"
                             "screens/{}.yaml を作る必要がある".format(tag, value, value)))
            break
        if value == at:
            continue

        # いま居る画面から前へ辿れるか、スタックを一段ずつ戻りながら探す。
        # 戻る回数と進む回数の合計がいちばん小さいものを採る（同点なら戻らない方）。
        # **「行き先が積まれているか」で場合分けしない。** 戻ってから進む
        # （一覧へ戻って別のタブへ、など）が普通に要るので、同じ探索に収める。
        best = None
        for pops in range(len(stack)):
            hops = mp.path_from(stack[len(stack) - 1 - pops], value)
            if hops is not None and (best is None or pops + len(hops) < best[0] + len(best[1])):
                best = (pops, hops)

        if best is None:
            # なぜ届かないかを言う。**戻る辺は「切れている」に数えない** —
            # 戻るのはこのループが自分でやることなので、それを欠陥として
            # 報告すると、直しようのないものをマップの穴として挙げてしまう
            relaxed = None
            for pops in range(len(stack)):
                relaxed = mp.path_from(stack[len(stack) - 1 - pops], value, relaxed=True)
                if relaxed is not None:
                    break
            broken = [(sid, a, mp.blocked(a)) for sid, a in (relaxed or [])
                      if mp.blocked(a) and mp.blocked(a) != "復路"]
            if relaxed is None:
                problems.append(("map", "{}: {} から {} へ行ける操作がマップに無い"
                                 .format(tag, at, value)))
            elif broken:
                for sid, a, why in broken:
                    problems.append(("map", "{}: {} の「{}」で切れる: {}"
                                     .format(tag, sid, label_of(a), why)))
            else:
                problems.append(("call", "{}: {} から {} へは、途中で戻ってからまた進む"
                                 "経路になる。1つの goto では辿れないので、"
                                 "折り返す画面への goto を挟んで区間を分ける"
                                 .format(tag, at, value)))
            break

        pops, hops = best
        broke = False
        for _ in range(pops):
            a = mp.back_action(at)
            if a is None:
                problems.append(("map", "{}: {} に戻る操作（kind: back / dismiss）が"
                                 "マップに無いので、{} へ向かえない".format(tag, at, value)))
                broke = True
                break
            # 戻り先はマップの `to` ではなく歩いた履歴を採る。`kind: back` の `to` は
            # 静的な宣言で、**複数の入口を持つ画面では嘘になる**
            dest = stack[-2]
            if a["to"] != dest:
                notes.append("{} の「{}」は to: {} と書いてあるが、"
                             "歩いてきた履歴では {} に戻る。履歴を採った"
                             .format(at, label_of(a), a["to"], dest))
            steps.append({"screen": at, "action": a, "to": dest})
            stack.pop()
            at = dest
        if broke:
            break

        for sid, a in hops:
            steps.append({"screen": sid, "action": a, "to": a["to"]})
            stack.append(a["to"])
            at = a["to"]

    mark(marked, ctx)

    # text は値が要る。マップは値を持たない（テストケース側が決める）
    for st in steps:
        if "shot" in st or st.get("restart"):
            continue
        op, target = op_of(st["action"])
        if op == "text" and "input" not in st and not st.get("runtime"):
            where = "({}) ".format(st["item"]) if st.get("item") else ""
            problems.append(("call", "{}text {} に打つ文字が渡されていない（do に {{\"op\": …, \"input\": 値}} か "
                             "{{\"op\": …, \"runtime\": true}} で書く）。"
                             "マップは値を持たない。何を打つかはテストケースが決める"
                             .format(where, target)))
    return steps, problems, notes


# ---------- 出す ----------

def var_name(target):
    """操作のidから env の変数名を作る。`browse.searchField` → `BROWSE_SEARCHFIELD`。
    末尾のパターン記号（`browse.bookRow.*`）は落とす。

    **呼ぶ側に名前を決めさせない。** idから決まるので、フローと索引と
    マニフェストで同じ名前になり、突き合わせに手が要らない。
    """
    return re.sub(r"[^A-Za-z0-9]", "_", target.rstrip(".*")).upper()


def sel_id(value):
    """マップの id を Maestro のセレクタにする。

    Maestro の id は正規表現なので、そのまま渡すと `.` が任意の1文字になり、
    `browse` が `browsex` にも当たる。**当たってほしいものにだけ当てる。**
    末尾 `*` はマップのパターン記法（`browse.bookRow.*`）で、前方一致の意味。
    """
    if value.endswith("*"):
        return "^" + re.escape(value[:-1]) + ".*"
    return "^" + re.escape(value) + "$"


def sel_text(value):
    # 表示文言には不可視文字が混ざることがあるので前後を緩める（sim-driver と同じ理由）
    return ".*" + re.escape(value) + ".*"


def q(s):
    """yaml のシングルクォート。正規表現の `\\` を素通しするためダブルにしない。"""
    return "'" + s.replace("'", "''") + "'"


def wait_for(selector, value, timeout):
    """要素が出るまで待つ。出なければ落ちる。

    `assertVisible` に `timeout` は渡せない（実測で `Unknown Property`）。
    既定の待ち時間は実測18秒で、通るぶんには足りるが、**本当に出ない要素で
    1つあたり18秒持っていかれる。** フローが唯一の検証手段になった以上、
    壊れたフローは早く落ちてほしいので、明示できる形にする。
    """
    return ("- extendedWaitUntil:\n    visible:\n      {}: {}\n    timeout: {}"
            .format(selector, q(value), timeout))


def anchor_of(mp, sid, notes):
    a = (mp.screens.get(sid) or {}).get("anchor")
    if not a:
        notes.append("{} に anchor が無いので、着いたことを確かめられない".format(sid))
    return a


def step_comment(mp, st):
    """そのステップが何をしているかの1行。**フローの中にコメントとして残す。**

    要約を別に作って見せると、レビューしたものと実際に走るものが別になる。
    同じ1つを読めるようにする。
    """
    a = st["action"]
    head = "# {}: {}".format(st["screen"], label_of(a))
    if st.get("to"):
        return head + " → " + st["to"]
    if a.get("result"):
        return head + " — " + str(a["result"])
    return head


def emit_flow(mp, steps, app, clear_state, notes=None, timeout=10000,
              start=None, launch=True):
    """`launch=False` はアプリを起動し直さない。続きのフローを出すため。"""
    notes = list(notes or [])
    start = start or mp.start
    # **実行時に決める値は env に未定のまま置く。** 値を焼き込むと、
    # データが変わったときに黙って古い値で走る。未定のままなら、埋まっていない
    # ことが走らせる前に分かる。
    used = [var_name(op_of(st["action"])[1]) for st in steps
            if "shot" not in st and not st.get("restart")
            and st.get("runtime")]
    out = ["appId: " + app]
    if used:
        out.append("env:")
        for v in dict.fromkeys(used):
            out.append("  {}: ''".format(v))
    out.append("---")

    def relaunch(at):
        # 起点に戻してから始める。launchApp だけでは前の項目の画面に居座ることがある。
        # clearState はログイン状態まで消えるので、要ると言われたときだけ。
        out.append("- stopApp")
        out.append("- launchApp:\n    clearState: true" if clear_state else "- launchApp")
        out.append("# 起点: " + at)
        a = anchor_of(mp, at, notes)
        if a:
            out.append(wait_for("id", sel_id(a), timeout))

    if launch:
        relaunch(start)
    else:
        out.append("# 続き: " + start + " から")
        start_anchor = anchor_of(mp, start, notes)
        if start_anchor:
            out.append(wait_for("id", sel_id(start_anchor), timeout))

    for st in steps:
        if st.get("restart"):
            out.append("# ここで起動し直す（前の状態から次の前提に行けないため）")
            relaunch(mp.start)
            continue
        if "shot" in st:
            out.append("- takeScreenshot: " + q(st["shot"]))
            continue
        a = st["action"]
        out.append(step_comment(mp, st))
        op, target = op_of(a)
        sel = a.get("select") or {}

        if op == "tap":
            key = "text" if a.get("by") == "label" else "id"
            if st.get("runtime"):
                # パターンの `*` を変数に置き換える。どれを叩くかは走らせるときに決まる
                val = "^" + re.escape(target.rstrip(".*")) + r"\." + "${" + var_name(target) + "}$"
            else:
                val = sel_text(target) if key == "text" else sel_id(target)
            line = "- tapOn:\n    {}: {}".format(key, q(val))
            if "index" in sel:
                line += "\n    index: {}".format(sel["index"])
            out.append(line)
            if "capture" in sel:
                notes.append("{} は {} を控える操作。どれを選んだかは着いた先の"
                             "ダンプ／証跡で拾う（フローには積まない）"
                             .format(target, sel["capture"]))
        elif op == "text":
            out.append("- tapOn:\n    id: " + q(sel_id(target)))
            out.append("- eraseText")       # 前の項目の文字が残ったまま打たない
            if st.get("runtime"):
                out.append("- inputText: ${" + var_name(target) + "}")
            else:
                out.append("- inputText: " + q(str(st.get("input", ""))))
        elif op == "scroll":
            if target == "down":
                out.append("- scroll")
            elif target == "up":
                out.append("- swipe:\n    direction: DOWN")   # 内容を下へ＝上へ戻る
            else:
                out.append("- scrollUntilVisible:\n    element:\n      id: {}\n"
                           "    direction: DOWN\n    timeout: 60000".format(q(sel_id(target))))
        else:
            notes.append("種類の分からない操作を飛ばした: {}".format(a))
            continue

        if st.get("to"):
            dest = anchor_of(mp, st["to"], notes)
            if dest:
                out.append(wait_for("id", sel_id(dest), timeout))
        elif a.get("expect"):
            out.append(wait_for("id", sel_id(a["expect"]), timeout))
        else:
            notes.append("「{}」の結果を確かめる expect がマップに無い".format(label_of(a)))

    if notes:
        out.append("")
        out.extend("# 補足: " + n for n in notes)
    return "\n".join(out) + "\n", notes


def shot_context(mp, seg_start, seg_steps):
    """そのフローが終わる画面と、最後に確かめたID。

    `index.json` に出すためのもの。**呼ぶ側が経路を読み直して導出せずに済ませる。**
    確かめたIDが無い（`expect` を持たない操作で終わった）なら None で、
    その証跡は機械判定なし＝画像だけが根拠になる。
    """
    at, checked = seg_start, (mp.screens.get(seg_start) or {}).get("anchor")
    for st in seg_steps:
        if st.get("restart"):
            at = mp.start
            checked = (mp.screens.get(at) or {}).get("anchor")
            continue
        if "shot" in st:
            continue
        a = st["action"]
        if st.get("to"):
            at = st["to"]
            checked = (mp.screens.get(at) or {}).get("anchor")
        else:
            checked = a.get("expect")
    return at, checked


def split_at_shots(mp, steps, start, launch_first=True):
    """撮影ごとにステップを切り、(そのフローの起点, ステップ列, 撮る名前) で返す。

    **1本＝1枚＝1ダンプにするため。** ダンプはフローの途中では取れないので、
    証跡1枚ごとに構造を残すには、撮る地点でフローを終わらせるしかない。
    2本目以降は前のフローの続きになるので、歩き直しは起きない。

    返す4つ目は**そのフローが自分で起動するか。** 1本目と、`fresh` の項目が
    そう。**ここが鎖の切れ目**で、走らせる側は落ちたときにどこまで諦めるかを
    これで決める（次に起動するフローからは、前が落ちていても走る）。

    5つ目は**値を決める操作の手前までを、別に走らせるためのステップ列**
    （無ければ空）。実行時に決める値があるフローは、走らせる側が画面を見て値を
    決める。**そのとき目的の画面に着いていないと決めようがない。** 遷移や起動を
    含んだまま止めると、まだ着いていない。手前で切って先に走らせる。
    """
    out, cur, at = [], [], start or mp.start
    seg_start, launch = at, launch_first
    for st in steps:
        if st.get("restart"):
            # build が直前の撮影を保証しているので、cur は空
            at = seg_start = mp.start
            launch = True
            continue
        cur.append(st)
        if "shot" in st:
            out.append((seg_start, cur, os.path.basename(str(st["shot"])), launch))
            cur, seg_start, launch = [], at, False
        elif st.get("to"):
            at = st["to"]
    if cur:
        out.append((seg_start, cur, None, launch))
    return out


def split_before_runtime(mp, seg_start, seg_steps):
    """実行時に決める操作の手前で切る。(前半, 前半が着く画面, 後半) を返す。"""
    for n, st in enumerate(seg_steps):
        if "shot" in st or st.get("restart"):
            continue
        if st.get("runtime"):
            at = seg_start
            for prev in seg_steps[:n]:
                if prev.get("to"):
                    at = prev["to"]
            return seg_steps[:n], at, seg_steps[n:]
    return [], seg_start, seg_steps   # 無し。`main_steps is seg_steps` で判る


def emit_path(mp, steps, notes, start=None):
    """人が読む経路。**どこが機械判定でどこが証跡頼みかを明示する。**

    フローを見せてレビューを受けるとき、いちばん知りたいのは「この確認は
    何によって裏付けられるのか」。遷移は `anchor`、遷移しない操作は `expect`
    が assert になるが、**`expect` を持たない操作は何も確かめていない**。
    そこは証跡のPNGだけが根拠になるので、黙って並べない。
    """
    at = start or mp.start
    chain = [at]
    for st in steps:
        if st.get("restart"):
            at = mp.start
            chain.append("（起動し直す）")
            chain.append(at)
            continue
        if "shot" in st:
            continue
        if st.get("to"):
            at = st["to"]
            chain.append(at)
    out = [" → ".join(chain), ""]

    w = max([len(st["screen"]) for st in steps if "screen" in st] + [len(at), 4])
    rows, checked, unchecked = [], 0, 0

    start_anchor = (mp.screens.get(chain[0]) or {}).get("anchor")
    rows.append(("  {}  {}".format(chain[0].ljust(w), "起点" if chain[0] == mp.start else "続き"),
                 "✓ {} が出ている".format(start_anchor) if start_anchor else "— anchor が無い"))
    if start_anchor:
        checked += 1
    else:
        unchecked += 1

    for st in steps:
        if st.get("restart"):
            a = (mp.screens.get(mp.start) or {}).get("anchor")
            rows.append(("  {}  アプリを起動し直す".format("".ljust(w)), None))
            rows.append(("  {}  起点".format(mp.start.ljust(w)),
                         "✓ {} が出ている".format(a) if a else "— anchor が無い"))
            checked += 1 if a else 0
            unchecked += 0 if a else 1
            continue
        if "shot" in st:
            # 撮影行は絶対パスで長い。右カラムを持たないので、桁揃えの計算から外す
            rows.append(("  {}  撮影 {}".format("".ljust(w), os.path.basename(st["shot"])), None))
            continue
        a = st["action"]
        op, target = op_of(a)
        extra = ' "{}"'.format(st.get("input", "")) if op == "text" else ""
        left = "  {}  {}{}".format(st["screen"].ljust(w), label_of(a), extra)
        if st.get("to"):
            dest = (mp.screens.get(st["to"]) or {}).get("anchor")
            right = "✓ {} に着いたことを確認".format(st["to"]) if dest else "— {} に anchor が無い".format(st["to"])
        elif a.get("expect"):
            right = "✓ {} が出ている".format(a["expect"])
        else:
            right = "— 機械判定なし。証跡で見る"
        checked += right.startswith("✓")
        unchecked += right.startswith("—")
        rows.append((left, right))

    pad = max(len(l) for l, r in rows if r is not None)
    for left, right in rows:
        out.append(left if not right else "{}  {}".format(left.ljust(pad), right))

    out.append("")
    out.append("  機械判定 {}件 / 証跡でしか見られない {}件".format(checked, unchecked))
    for n in notes:
        out.append("  補足: " + n)
    return "\n".join(out)


# ---------- サブコマンド ----------

def cmd_screens(mp):
    for sid in sorted(mp.screens):
        s = mp.screens[sid]
        mark = " [stub]" if s.get("stub") else ""
        names = s.get("names") or []
        print("{}{}".format(sid, mark))
        if names:
            print("    呼び名: {}".format(" / ".join(str(n) for n in names)))
        print("    {}".format(s.get("summary") or "(summary 無し)"))


def last_commit(repo, paths):
    """そのパス群を最後に触ったコミットの時刻（epoch秒）。無ければ None。

    複数渡すと**いちばん新しいもの**が返る（git log -1 の仕様）。ファイルごとに
    呼ばずに済む。
    """
    if not paths:
        return None
    try:
        r = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ct", "--"] + list(paths),
                           capture_output=True, text=True)
    except OSError:
        return None
    out = r.stdout.strip()
    return int(out) if r.returncode == 0 and out.isdigit() else None


def staleness(mp):
    """マップが実装に追いついていないかを、gitのコミット日時で見る。

    **判定はファイルシステムの mtime ではなく git のコミット日時で行う。**
    mtime は clone や checkout で全ファイルが同じ時刻になるので、リポジトリでは
    意味を持たない。

    `screens/<id>.yaml` より新しく `files` が触られていたら、その画面の
    `actions` と `result` は実装とずれている可能性がある。**ずれていても
    フローは通る**（到達判定は anchor しか見ない）ので、古い `result` を
    信じて期待値を立てると、違う期待で撮った証跡がそのまま通ってしまう。

    **リファクタやコメントの修正でも「新しい」と出る。** 警告であって
    不整合ではない。読む側が `files` を確かめる合図として使う。
    """
    repo = mp.root.parent
    if not (repo / ".git").exists():
        return None
    out = []
    for sid in sorted(mp.screens):
        files = (mp.screens[sid] or {}).get("files") or []
        y = last_commit(repo, [str(mp.root / "screens" / (sid + ".yaml"))])
        f = last_commit(repo, [str(repo / str(x)) for x in files])
        if y is None or f is None or not files:
            continue
        if f > y:
            out.append((sid, y, f))
    return out


def cmd_which(mp, paths):
    """変更したファイルが、どの画面のものかを引く。

    **コードを開かずに対象画面を決めるための入り口。** 画面が決まれば
    `actions` と `result` に期待動作が書いてあるので、そこから確認項目を
    立てられる。マップを作った目的の半分はこれ。

    **当たらなかったファイルを出すのが、もう半分の仕事。** `files` は
    網羅ではない（共有コンポーネントは載せない決まりだし、新しく足した
    画面はまだマップに無い）。当たらなかったことを「無関係」と読むと
    確認が漏れるので、**そこはコードを読む必要がある**と名指しする。
    """
    owner = {}
    for sid in sorted(mp.screens):
        for f in (mp.screens[sid] or {}).get("files") or []:
            owner.setdefault(str(f), []).append(sid)
    # 完全一致で引けなければファイル名で引く。リポジトリ相対かどうかの
    # 食い違いで黙って0件になる方が怖い
    by_base = {}
    for f, sids in owner.items():
        by_base.setdefault(f.rsplit("/", 1)[-1], []).append((f, sids))

    hit, miss = {}, []
    for path in paths:
        sids = owner.get(path)
        how = ""
        if sids is None:
            cands = by_base.get(path.rsplit("/", 1)[-1] or path)
            if cands and len(cands) == 1:
                sids, how = cands[0][1], "（ファイル名で一致）"
        if sids:
            for sid in sids:
                hit.setdefault(sid, []).append(path + how)
        else:
            miss.append(path)

    for sid in sorted(hit):
        print("{}  — {}".format(sid, (mp.screens[sid] or {}).get("summary") or ""))
        for f in hit[sid]:
            print("    " + f)
    if not hit:
        print("どの画面にも当たらなかった")
    if miss:
        print("\nどの画面にも載っていない（マップでは決められない）")
        for f in miss:
            print("    " + f)
        print("\n共有コンポーネント、モデル、API層は `files` に載せない決まりなので、"
              "ここに出る。\nまだマップに無い画面のファイルもここに出る。"
              "**当たらなかったことを「無関係」と読まない。**")
    return 0


def cmd_check(mp):
    bad = []
    if mp.start not in mp.screens:
        bad.append("起点 {} のファイルが無い".format(mp.start))
    reach = mp.reachable()
    print("起点: {}".format(mp.start))
    print("到達できる: {}".format(" ".join(sorted(reach))))
    lost = sorted(set(mp.screens) - reach)
    print("到達できない: {}".format(" ".join(lost) if lost else "なし"))

    breaks = []
    for sid in sorted(mp.screens):
        if (mp.screens[sid] or {}).get("stub"):
            breaks.append("{}: stub（操作は網羅ではない）".format(sid))
        if not (mp.screens[sid] or {}).get("anchor"):
            bad.append("{}: anchor が無い".format(sid))
        for a in mp.actions(sid):
            op, target = op_of(a)
            if op is None:
                bad.append("{}: tap / text / scroll のどれも持たない操作がある".format(sid))
                continue
            if a.get("to") and a["to"] not in mp.screens:
                bad.append("{}: 「{}」の遷移先 {} のファイルが無い".format(sid, label_of(a), a["to"]))
            if a.get("in_tree") is False:
                breaks.append("{}: 「{}」は in_tree: false（座標が要る）".format(sid, label_of(a)))
            if a.get("by") == "label":
                breaks.append("{}: 「{}」はラベル指定（ローカライズで壊れる）".format(sid, label_of(a)))

    stale = staleness(mp)
    if stale is None:
        print("\nマップの鮮度: git が無いので測れない")
    else:
        print("\nマップの鮮度（git のコミット日時。mtime は clone で揃うので使わない）:")
        import time as _t
        for sid, y, f in stale:
            print("  {}: 実装のほうが新しい（files {} > マップ {}）"
                  .format(sid, _t.strftime("%m/%d %H:%M", _t.localtime(f)),
                          _t.strftime("%m/%d %H:%M", _t.localtime(y))))
        if not stale:
            print("  全画面、マップが実装に追いついている")
        else:
            print("  **この画面の result は実装とずれている可能性がある。**"
                  "期待値を立てる前に files を読む。\n"
                  "  リファクタやコメントの修正でもここに出るので、不整合ではなく警告。")

    print("\n経路が切れる／弱い箇所:")
    for b in breaks:
        print("  " + b)
    if not breaks:
        print("  （なし）")
    if bad:
        print("\nマップの不整合:")
        for b in bad:
            print("  " + b)
        return 1
    return 0


PLAN_KEYS = {"app", "shots_dir", "clear_state", "items", "explore"}
ITEM_KEYS = {"shot", "from", "do", "fresh", "title", "expect"}
DO_KEYS = {"op", "runtime", "input"}


def read_plan(path):
    """plan.json をセグメントの列に開く。経路の計算も検査も build() に任せる。

    項目1つ＝「`from` から `do` を順に叩いて、1枚撮る」。**項目は経路を持たない。**
    `from` へどう着くかはここで `goto` を挟んで計算させる（すでに居れば何もしない）。
    テストケースが持つのは、どこから何を確かめるかだけ。

    `do` の要素は操作id（`"tap:Search"`）か、入れる値の決め方を添えた
    `{"op": 操作id, "runtime": true}` / `{"op": 操作id, "input": 値}`。

    `fresh` が真の項目は、アプリを起動し直した直後から始める（その項目の前提）。
    前の項目を撮った直後で起動し直すので、前の項目の状態は引き継がない。
    撮影のパスは `shots_dir` と `shot` から組む。`title` / `expect` は読まない
    （manifest.py が読む）。`explore` も見ない — 経路が組めなかった項目で、フローを持たない。
    """
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit("plan を読めない: {}: {}".format(path, e))
    # 知らない鍵は綴り違い。黙って無視すると、その指定が効かないまま走る
    unknown = set(plan) - PLAN_KEYS
    if unknown:
        hint = ("。値の決め方は do の操作に書く（{\"op\": …, \"runtime\": true}）"
                if unknown & {"runtime", "inputs"} else "")
        sys.exit("plan に知らない鍵: {}（使えるのは {}）{}".format(
            ", ".join(sorted(unknown)), ", ".join(sorted(PLAN_KEYS)), hint))
    shots_dir = plan.get("shots_dir")
    items = plan.get("items") or []
    if items and not shots_dir:
        sys.exit("plan に shots_dir が要る（証跡の出力先。絶対パス）")
    segments, owners = [], []   # owners: セグメントごとの項目名。エラーをどの項目か読めるように
    for n, item in enumerate(items, 1):
        shot = item.get("shot")
        if not shot:
            sys.exit("plan の items[{}] に shot（証跡の名前）が無い".format(n))
        unknown = set(item) - ITEM_KEYS
        if unknown:
            hint = ""
            if unknown & {"steps", "goto"}:
                hint = "。経路は書かない — from に着くまでは route.py が計算する"
            elif unknown & {"runtime", "inputs"}:
                hint = "。値の決め方は do の操作に書く（{\"op\": …, \"runtime\": true}）"
            elif "screen" in unknown:
                hint = "。操作を始める画面は from"
            sys.exit("plan の {} に知らない鍵: {}（使えるのは {}）{}".format(
                shot, ", ".join(sorted(unknown)), ", ".join(sorted(ITEM_KEYS)), hint))
        start = item.get("from")
        if not start:
            sys.exit("plan の {} に from（操作を始める画面）が無い".format(shot))
        do = item.get("do") or []
        if isinstance(do, (str, dict)):
            sys.exit("plan の {} の do は配列で書く: {}".format(
                shot, json.dumps(do, ensure_ascii=False)))

        def add(what, value="", **ctx):
            ctx["item"] = shot
            segments.append((what, value, ctx)); owners.append(shot)
        if item.get("fresh") and segments:
            add("restart")
        add("goto", start)
        for d in do:
            if isinstance(d, str):
                add("do", d)
                continue
            bad = not isinstance(d, dict) or not d.get("op") or set(d) - DO_KEYS \
                or ("runtime" in d) == ("input" in d) or d.get("runtime") not in (None, True)
            if bad:
                sys.exit("plan の {} の do の要素は操作id か {{\"op\": 操作id, \"runtime\": true}} か "
                         "{{\"op\": 操作id, \"input\": 値}}: {}".format(
                             shot, json.dumps(d, ensure_ascii=False)))
            if d.get("runtime"):
                add("do", d["op"], runtime=True)
            else:
                add("do", d["op"], input=d["input"])
        add("shot", str(Path(os.path.expanduser(shots_dir)) / shot))
    return segments, plan.get("app"), bool(plan.get("clear_state")), owners


def main():
    argv = sys.argv[1:]
    # 使い方を訊かれたのは失敗ではない。標準出力に出して 0 で終わる
    # （`&&` で繋いだ先が続けられるように）。引数が無いのは失敗なので 1
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    if not argv:
        sys.exit(__doc__)
    cmd, argv = argv[0], argv[1:]

    mapdir, rest, timeout, out_dir, plan_path = None, [], 10000, None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out-dir":
            out_dir = argv[i + 1]; i += 2
        elif a == "--map":
            mapdir = argv[i + 1]; i += 2
        elif a == "--timeout":
            timeout = int(argv[i + 1]); i += 2
        elif a == "--plan":
            plan_path = argv[i + 1]; i += 2
        elif a.startswith("--"):
            sys.exit("知らない引数: " + a + "（経路は plan に書いて --plan で渡す。--help）")
        else:
            rest.append(a); i += 1

    mp = ScreenMap(find_map(mapdir))

    if cmd == "screens":
        return cmd_screens(mp)
    if cmd == "which":
        if not rest:
            sys.exit("ファイルのパスが要る（`git diff --name-only ...` の出力、"
                     "または `-` で標準入力）")
        paths = rest
        if rest == ["-"]:
            paths = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
        sys.exit(cmd_which(mp, paths))
    if cmd == "check":
        sys.exit(cmd_check(mp))

    app, clear, owners = None, False, None
    if cmd == "path":
        # 画面idを並べると、順にたどる。どう行くかを見るだけなので操作は挟まない
        if not rest:
            sys.exit("行き先が要る。`route.py path <画面id>`。\n"
                     "画面の一覧は `route.py screens`。")
        segments = [("goto", g) for g in rest]
    elif cmd == "flow":
        if not plan_path or not out_dir or rest:
            sys.exit("flow は `--plan <json> --out-dir <dir>` で呼ぶ（--help）")
        segments, app, clear, owners = read_plan(plan_path)
        if not segments:
            sys.exit("plan の items が空（経路が組めた項目が無いなら route.py は要らない）")
        if not app:
            sys.exit("plan に app（bundle id）が要る（xcrun simctl listapps <UDID> で調べる）")
    else:
        sys.exit(__doc__)

    steps, problems, notes = build(mp, segments)
    if problems:
        print("経路を組めなかった:", file=sys.stderr)
        for _, msg in problems:
            # plan から来たなら、区間の番号にその項目の名前を添える
            m = re.match(r"\[(\d+)\]", msg) if owners else None
            if m and 0 < int(m.group(1)) <= len(owners):
                msg = "{} ({})".format(m.group(0), owners[int(m.group(1)) - 1]) + msg[m.end():]
            print("  " + msg, file=sys.stderr)
        if any(kind == "map" for kind, _ in problems):
            print("\nマップの穴。埋めるのは screen-map の仕事で、"
                  "ここで推測して繋がない。", file=sys.stderr)
        sys.exit(2)

    if cmd == "path":
        _, all_notes = emit_flow(mp, steps, "x", clear, notes, timeout)   # 補足だけ取る
        print(emit_path(mp, steps, all_notes))
        return
    # **項目（撮影）ごとに1本ずつ。** 走らせる側は順に run して inspect するだけで、
    # 証跡と同名のダンプが揃う。
    segs = split_at_shots(mp, steps, None)
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for n, (seg_start, seg_steps, shot_name, lch) in enumerate(segs, 1):
        # 実行時に決める操作があれば手前で切る。前半を先に流し、着いてから値を決める
        pre_steps, resume_at, main_steps = split_before_runtime(mp, seg_start, seg_steps)
        base = "{:02d}_{}".format(n, shot_name or "tail")
        # 起動も前半に出す。値を決める前に止めたとき、起動していなければ画面は見えない
        pre_name = None
        if pre_steps or (lch and main_steps is not seg_steps):
            pre, _ = emit_flow(mp, pre_steps, app, clear, notes, timeout,
                               seg_start, launch=lch)
            pre_name = base + ".pre.yaml"
            (d / pre_name).write_text(pre, encoding="utf-8")

        # 自分で起動するのは1本目と fresh の項目。他は居る場所から続ける
        flow, _ = emit_flow(mp, main_steps, app, clear, notes, timeout,
                            resume_at, launch=lch and pre_name is None)
        name = base + ".yaml"
        (d / name).write_text(flow, encoding="utf-8")
        screen, checked = shot_context(mp, seg_start, seg_steps)
        shot_path = next((st["shot"] for st in seg_steps if "shot" in st), None)
        runtime_inputs = {var_name(op_of(st["action"])[1]): "" for st in seg_steps
                 if "shot" not in st and not st.get("restart")
                 and st.get("runtime")}
        written.append({"name": shot_name, "screen": screen, "checked": checked,
                        "pre_flow": pre_name, "flow": name, "shot": shot_path,
                        "launch": lch, "inputs": runtime_inputs})
    # 一覧は必ず書く。`--out-dir` を使う時点で1実行ぶんのフロー一式なので、
    # 出すか出さないかを選ばせる意味が無い（付け忘れる余地になるだけ）。
    (d / "index.json").write_text(
        json.dumps([w for w in written if w["name"]], ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(emit_path(mp, steps, notes))
    print("\n  フロー（{}本）: {}".format(len(written), out_dir))
    for w in written:
        print("    {}{}  →  ダンプ名 {}  機械判定 {}".format(
            w["flow"], " ★起動し直す" if w["launch"] else "",
            w["name"] or "（撮影なし）", w["checked"] or "なし"))
    print("  一覧: {}/index.json".format(out_dir))
    return


main()
