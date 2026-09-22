#!/usr/bin/env python3
"""画面マップ（screen-map/）から、動作確認の経路を組み立てる。

  route.py screens                     画面の一覧（id / 呼び名 / できること）
  route.py path  <セグメント...>       人が読む経路。テストケースのレビューに貼る
  route.py flow  <セグメント...>       Maestro のフロー。maestrod.py run に渡す
  route.py check                       マップ全体の自己テスト

  セグメント（**並び順がそのまま実行順**）
    --goto <画面id>    いま居る画面からそこまで、経路を計算して繋ぐ
    --do <操作id>      いま居る画面で操作する。`tap:<id>` `scroll:down` の
                       ように種類を頭に付けて指せる（scroll は必須）
    --shot <パス>      そこまでの直後に撮る。拡張子は Maestro が付ける

  どこに書いてもよい引数
    --input <id>=<値>  text 操作で打つ文字。マップは値を持たないので呼ぶ側が渡す
    --app <bundle id>  flow のときだけ必須
    --clear-state      起動時にアプリのデータも消す
    --timeout <ミリ秒> 画面や要素を待つ上限。既定 10000
    --map <dir>        画面マップの場所。省くとカレントから上へ screen-map/ を探す

  位置引数の画面idは先頭の `--goto` と同じ（`path detail` = `path --goto detail`）。

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
import re
import sys
from pathlib import Path

# ---------- yaml ----------
#
# PyYAML は Xcode 同梱の python3.9 に入っていない。入れさせると、このスキルを
# 使うだけで環境に手を入れることになる。読む相手は screen-map が書く yaml だけ
# なので、その範囲に限った最小のパーサを持つ。**読めない行は例外にする。**
# 黙って None を返すと、経路が組めないのかマップが間違っているのか分からなくなる。

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*:(\s|$)")


def _strip_comment(s):
    """引用符の外にある ` #` から先を落とす。"""
    quote = None
    for i, c in enumerate(s):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i]
    return s


def _scalar(s):
    if s.startswith("[") and s.endswith("]"):
        body = s[1:-1].strip()
        return [_scalar(p.strip()) for p in body.split(",")] if body else []
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if re.match(r"^-?\d+$", s):
        return int(s)
    return s


def _lines(text):
    out = []
    for raw in text.splitlines():
        s = _strip_comment(raw.rstrip())
        if s.strip():
            out.append((len(s) - len(s.lstrip(" ")), s.strip()))
    return out


def _parse(ls, i, indent):
    if ls[i][1] == "-" or ls[i][1].startswith("- "):
        return _seq(ls, i, indent)
    return _map(ls, i, indent)


def _seq(ls, i, indent):
    out = []
    while i < len(ls) and ls[i][0] == indent and (ls[i][1] == "-" or ls[i][1].startswith("- ")):
        head = ls[i][1][1:].strip()
        i += 1
        sub = []
        while i < len(ls) and ls[i][0] > indent:
            sub.append(ls[i])
            i += 1
        if head and _KEY.match(head):
            # `- tap: x` の形。続く行は "- " のぶん右に揃っている
            sub = [(indent + 2, head)] + sub
            out.append(_parse(sub, 0, indent + 2)[0])
        elif head:
            out.append(_scalar(head))
        elif sub:
            out.append(_parse(sub, 0, sub[0][0])[0])
        else:
            out.append(None)
    return out, i


def _map(ls, i, indent):
    out = {}
    while i < len(ls) and ls[i][0] == indent:
        s = ls[i][1]
        if s == "-" or s.startswith("- "):
            break
        if ":" not in s:
            raise ValueError("読めない行: " + s)
        k, _, rest = s.partition(":")
        k, rest = k.strip(), rest.strip()
        i += 1
        if rest:
            out[k] = _scalar(rest)
            continue
        sub = []
        while i < len(ls) and ls[i][0] > indent:
            sub.append(ls[i])
            i += 1
        if sub:
            out[k] = _parse(sub, 0, sub[0][0])[0]
        elif i < len(ls) and ls[i][0] == indent and (ls[i][1] == "-" or ls[i][1].startswith("- ")):
            out[k], i = _seq(ls, i, indent)   # `-` をキーと同じ字下げで書く形
        else:
            out[k] = None
    return out, i


def load_yaml(path):
    ls = _lines(path.read_text(encoding="utf-8"))
    if not ls:
        return {}
    try:
        return _parse(ls, 0, ls[0][0])[0]
    except ValueError as e:
        raise ValueError("{}: {}".format(path, e))


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


def build(mp, segments, inputs):
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

    理由は ("map", ...) と ("call", ...) に分ける。**直す先が違う。**
    前者はマップの穴で screen-map の仕事、後者は呼び方の間違いで、
    値を渡すか行き先を選び直せば済む。
    """
    steps, problems, notes = [], [], []
    at = mp.start
    stack = [mp.start]

    for n, (what, value) in enumerate(segments, 1):
        tag = "[{}] --{} {}".format(n, what, value)

        if what == "shot":
            steps.append({"shot": value})
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
                                 "経路になる。1つの --goto では辿れないので、"
                                 "折り返す画面を --goto で挟んで区間を分ける"
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

    # text は値が要る。マップは値を持たない（テストケース側が決める）
    for st in steps:
        if "shot" in st:
            continue
        op, target = op_of(st["action"])
        if op == "text" and target not in inputs:
            problems.append(("call", "text {} に打つ文字が渡されていない（--input {}=<値>）。"
                             "マップは値を持たない。何を打つかはテストケースが決める"
                             .format(target, target)))
    return steps, problems, notes


# ---------- 出す ----------

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


def emit_flow(mp, steps, inputs, app, clear_state, notes=None, timeout=10000):
    notes = list(notes or [])
    out = ["appId: " + app, "---"]
    # 起点に戻してから始める。launchApp だけでは前の項目の画面に居座ることがある。
    # clearState はログイン状態まで消えるので、要ると言われたときだけ。
    out.append("- stopApp")
    out.append("- launchApp:\n    clearState: true" if clear_state else "- launchApp")

    start_anchor = anchor_of(mp, mp.start, notes)
    out.append("# 起点: " + mp.start)
    if start_anchor:
        out.append(wait_for("id", sel_id(start_anchor), timeout))

    for st in steps:
        if "shot" in st:
            out.append("- takeScreenshot: " + q(st["shot"]))
            continue
        a = st["action"]
        out.append(step_comment(mp, st))
        op, target = op_of(a)
        sel = a.get("select") or {}

        if op == "tap":
            key = "text" if a.get("by") == "label" else "id"
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
            out.append("- inputText: " + q(str(inputs.get(target, ""))))
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


def emit_path(mp, steps, inputs, notes):
    at = mp.start
    chain = [at]
    for st in steps:
        if "shot" in st:
            continue
        if st.get("to"):
            at = st["to"]
            chain.append(at)
    out = [" → ".join(chain), ""]
    w = max([len(st["screen"]) for st in steps if "shot" not in st] + [len(mp.start), 4])
    out.append("  {}  起点 (anchor: {})".format(
        mp.start.ljust(w), (mp.screens.get(mp.start) or {}).get("anchor")))
    for st in steps:
        if "shot" in st:
            out.append("  {}  撮影 {}".format("".ljust(w), st["shot"]))
            continue
        a = st["action"]
        op, target = op_of(a)
        after = ""
        if st.get("to"):
            after = "→ {}".format(st["to"])
        elif a.get("expect"):
            after = "expect {}".format(a["expect"])
        extra = ""
        if op == "text":
            extra = ' "{}"'.format(inputs.get(target, ""))
        out.append("  {}  {}{}  {}".format(st["screen"].ljust(w), label_of(a), extra, after))
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


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    cmd, argv = argv[0], argv[1:]
    if "--help" in argv or "-h" in argv:
        sys.exit(__doc__)

    # --goto / --do / --shot は並び順がそのまま実行順になるので、1つの列に集める。
    # --input と --app はどこに書いてもよい（並びに意味を持たない）
    segments, inputs, app, clear, mapdir, rest = [], {}, None, False, None, []
    timeout = 10000
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--goto", "--do", "--shot"):
            segments.append((a[2:], argv[i + 1])); i += 2
        elif a == "--input":
            k, _, v = argv[i + 1].partition("="); inputs[k] = v; i += 2
        elif a == "--app":
            app = argv[i + 1]; i += 2
        elif a == "--map":
            mapdir = argv[i + 1]; i += 2
        elif a == "--timeout":
            timeout = int(argv[i + 1]); i += 2
        elif a == "--clear-state":
            clear = True; i += 1
        elif a.startswith("--"):
            sys.exit("知らない引数: " + a)
        else:
            rest.append(a); i += 1

    mp = ScreenMap(find_map(mapdir))

    if cmd == "screens":
        return cmd_screens(mp)
    if cmd == "check":
        sys.exit(cmd_check(mp))
    if cmd not in ("path", "flow"):
        sys.exit(__doc__)

    # 位置引数の画面idは、先頭の `--goto` と同じ。よくある1区間の形を短く書けるようにする
    for g in reversed(rest):
        segments.insert(0, ("goto", g))
    if not segments:
        sys.exit("行き先が要る。`route.py path <画面id>` か `--goto <画面id>`。\n"
                 "画面の一覧は `route.py screens`。")
    if cmd == "flow" and not app:
        sys.exit("--app <bundle id> が要る（xcrun simctl listapps <UDID> で調べる）")

    steps, problems, notes = build(mp, segments, inputs)
    if problems:
        print("経路を組めなかった:", file=sys.stderr)
        for _, msg in problems:
            print("  " + msg, file=sys.stderr)
        if any(kind == "map" for kind, _ in problems):
            print("\nマップの穴。埋めるのは screen-map の仕事で、"
                  "ここで推測して繋がない。", file=sys.stderr)
        sys.exit(2)

    if cmd == "path":
        _, all_notes = emit_flow(mp, steps, inputs, "x", clear, notes, timeout)   # 補足だけ取る
        print(emit_path(mp, steps, inputs, all_notes))
        return
    flow, _ = emit_flow(mp, steps, inputs, app, clear, notes, timeout)
    sys.stdout.write(flow)   # 補足はフローの中にコメントで入っている


main()
