#!/usr/bin/env python3
"""画面マップ（screen-map/）から、動作確認の経路を組み立てる。

  route.py screens                     画面の一覧（id / 呼び名 / できること）
  route.py path  <画面id>...           そこまでの経路を人が読む形で出す。並べると順にたどる
  route.py which <パス...>             変更したファイルから対象画面を引く（`-` で標準入力）
  route.py check                       マップ全体の自己テスト

  --repo <dir>       アプリのリポジトリ。画面マップはその下の screen-map/。必ず渡す

**フローはここでは書かない。** plan.json から項目ごとのフローを書くのは `manifest.py` で、
中でこのモジュールの `write_flows()` を呼ぶ。plan の形は `manifest.py --help`。

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
        if action.get("kind") in ("back", "dismiss"):
            return "復路"
        if not action.get("to"):
            return "遷移しない"
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

    def auto_shows(self, sid):
        """その画面に着くと、自動で出ることがある画面（マップの `auto_shows`）。"""
        return [i for i in (self.screens.get(sid) or {}).get("auto_shows") or []]

    def auto_hosts(self, sid):
        """sid を自動で出すことがある画面（被さる先）。"""
        return sorted(h for h in self.screens if sid in self.auto_shows(h))

    def back_action(self, sid):
        """その画面の「戻る」操作。無ければ None。**`to` は見ない** — 戻り先は歩いた履歴で決まる。"""
        for a in self.actions(sid):
            if a.get("kind") in ("back", "dismiss"):
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


def find_map(repo):
    """アプリのリポジトリから画面マップ（screen-map/）を引く。**呼ぶ側が必ず渡す**
    （カレントからは探さない — どこで叩いたかで結果が変わらないように）。"""
    if not repo:
        sys.exit("--repo <アプリのリポジトリ> が要る")
    p = Path(repo).expanduser().resolve() / "screen-map"
    if (p / "config.yaml").exists():
        return p
    sys.exit("{} に画面マップが無い（config.yaml を探した）。\n"
             "このアプリにはまだ画面マップが無いのかもしれない（screen-map スキルで作る）".format(p))


# ---------- 経路を組む ----------

def stale_back_to(at, action, dest):
    """戻る操作に書かれた `to` が、歩いた履歴と食い違ったときの補足。"""
    return ("{} の「{}」に to: {} と書いてあるが、歩いてきた履歴では {} に戻る。"
            "戻り先は履歴で決まるので、戻る操作の to は消してよい"
            .format(at, label_of(action), action["to"], dest))


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
    積まれていれば、往路を探さずに戻る。**戻る操作（`kind: back` / `dismiss`）の
    戻り先はこの履歴で決める。** どこから来たかで変わるので、マップには書かない
    （複数の入口を持つ画面では、どれを書いても嘘になる）。古いマップに `to` が
    書いてあって履歴と食い違えば、補足として出す。

    **自動表示の画面（どこかの `auto_shows` に並んでいる画面）への goto は、被さる先へ
    行って、閉じずに出るまで待つ**（`await` のステップ）。遷移で入る画面ではないので、
    `to` の辺では辿れない。被さる先が複数あれば、起点から近いほうを採って補足に出す。

    `start` を渡すとそこから歩き始める。**フローを途中で切って続きを出す
    ため**で、切った地点でダンプを取っても歩き直しにならない。

    理由は ("map", ...) と ("call", ...) に分ける。**直す先が違う。**
    前者はマップの穴で screen-map の仕事、後者は呼び方の間違いで、
    値を渡すか行き先を選び直せば済む。
    """
    steps, problems, notes = [], [], []
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

    # 自動表示の画面への goto を、被さる先への goto と、そこで待つ await に割る
    split = []
    for seg in segments:
        hosts = mp.auto_hosts(seg[1]) if seg[0] == "goto" else []
        if not hosts:
            split.append(seg)
            continue
        dist = {h: len(mp.path_from(mp.start, h) or []) if h != mp.start else 0
                for h in hosts if h == mp.start or mp.path_from(mp.start, h) is not None}
        host = min(dist, key=lambda h: (dist[h], h)) if dist else hosts[0]
        if len(hosts) > 1:
            notes.append("{} は {} で自動表示される。{} で待つ".format(
                seg[1], " / ".join(hosts), host))
        ctx = seg[2] if len(seg) > 2 else {}
        split.append(("goto", host, dict(ctx, for_await=seg[1])))
        split.append(("await", seg[1], ctx))
    segments = split

    marked, ctx = 0, {}
    for n, seg in enumerate(segments, 1):
        mark(marked, ctx)
        marked = len(steps)
        what, value = seg[0], seg[1]
        ctx = seg[2] if len(seg) > 2 else {}
        tag = "[{}] {} {}".format(n, what, value).rstrip()

        if what == "await":
            if at == value:
                continue            # もう出ている（前の項目がそこで終わった）
            # 閉じずに出るまで待つ。閉じる操作（kind: dismiss）は履歴で被さる先に戻る
            steps.append({"screen": at, "await": True, "to": value})
            stack.append(value)
            at = value
            continue
        if what == "goto" and ctx.get("for_await") and at == ctx["for_await"]:
            continue                # 待つ画面にもう居る。被さる先へ戻ると閉じてしまう

        if what == "restart":
            # 起動し直すので、居る場所も歩いた履歴も捨てて起点に戻る
            at = mp.start
            stack = [at]
            steps.append({"restart": True})
            continue

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
            if found.get("kind") in ("back", "dismiss"):
                # goto の途中の戻ると同じく、戻り先は歩いた履歴で決める。どこから
                # 来たかで変わる（お気に入りから詳細に入ったなら、戻る先は一覧では
                # なくお気に入りの手前）ので、マップには書かない
                if len(stack) < 2:
                    # 起点以外の画面には必ず歩いて入っているので、履歴が尽きるのは
                    # 起点に居るときだけ。起動直後の画面に戻る先は無い
                    problems.append(("map", "{}: 起点 {} に戻る操作「{}」が書いてある。"
                                     "起動直後の画面から戻る先は無い"
                                     .format(tag, at, label_of(found))))
                    break
                dest = stack[-2]
                st = {"screen": at, "action": found, "to": dest}
                if found.get("to") and found["to"] != dest:
                    st["note"] = stale_back_to(at, found, dest)
                    notes.append(st["note"])
                steps.append(st)
                stack.pop()
                at = dest
                continue
            steps.append({"screen": at, "action": found, "to": found.get("to")})
            if found.get("to"):
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
            # 戻り先は歩いた履歴で決める（どこから来たかで変わるので、マップには書かない）
            dest = stack[-2]
            st = {"screen": at, "action": a, "to": dest}
            if a.get("to") and a["to"] != dest:
                st["note"] = stale_back_to(at, a, dest)
                notes.append(st["note"])
            steps.append(st)
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
        if "shot" in st or st.get("restart") or st.get("await"):
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


def runtime_uses(steps):
    """実行時に決める値の、変数名 → 入る先。`selector`（tapOn の id / text。正規表現）か
    `text`（inputText。文字そのまま）。

    **走らせる側がこれを見てエスケープを分ける。** セレクタは正規表現なので、
    `牛乳(1L)` や `C++入門` をそのまま入れると別物として解釈され、狙った行に
    当たらない（`a.b` なら `aXb` にも当たる）。逆に inputText をエスケープすると
    `\\` ごと打たれる。どちらに入るかはフローを書くここでしか分からない。
    """
    uses = {}
    for st in steps:
        if "shot" in st or st.get("restart") or not st.get("runtime"):
            continue
        op, target = op_of(st["action"])
        uses[var_name(target)] = "selector" if op == "tap" else "text"
    return uses


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


SCROLL_TIMEOUT = 60000   # scrollUntilVisible の上限。理由は emit_flow の scroll の箇所

def auto_of(mp, iid):
    """自動表示の画面から (anchor, 閉じる操作) を引く。どちらか無ければ None。

    自動表示（レビュー依頼、お知らせなど、こちらの操作と関係なく被さる画面）も
    **画面として書く**（`screens/<id>.yaml`）。anchor が「出ているか」の目印、
    `kind: dismiss`（か `back`）の操作が閉じ方。命名も ID の振り方も実測も
    画面と同じルールで効くので、専用の決まりを持たない。
    """
    scr = mp.screens.get(iid) or {}
    close = next((a for a in mp.actions(iid) if a.get("kind") in ("dismiss", "back")), None)
    if not scr.get("anchor") or close is None:
        return None
    return scr["anchor"], close


def auto_checks(mp, sid, keep=None):
    """その画面の `auto_shows`（自動で出ることがある画面）が出ていたら閉じる。

    **閉じるのは既定の扱い。** マップは「出ることがある」という事実だけを持ち、
    邪魔か確かめたいかはテストケースが決める。確かめたい項目（`from` にその画面）
    では、`keep` に渡した画面は閉じずに待つ（`build()` の `await`）。


    **出ていないときに1つあたり約7秒かかる**（Maestro が「無い」と決めるまで
    `optionalLookupTimeoutMs` の既定7秒を待つ。下げる設定は無い）。だから
    `auto_shows` を書いた画面に着いたときだけ積む。書いていない画面はコストゼロ。

    **閉じたことは残さない。** 閉じる項目にとって、それは確かめたいアプリの
    挙動と関係が無い。
    """
    out = []
    for iid in mp.auto_shows(sid):
        if iid == keep:
            continue
        found = auto_of(mp, iid)
        if found is None:
            continue            # 書き間違いは check が出す
        anchor, close = found
        op, target = op_of(close)
        key = "text" if close.get("by") == "label" else "id"
        dismiss = sel_text(target) if key == "text" else sel_id(target)
        summary = (mp.screens.get(iid) or {}).get("summary")
        out.append("# 自動表示: {}{}（出ていたら閉じる）".format(iid, " — " + summary if summary else ""))
        out.append("- runFlow:\n    when:\n      visible:\n        id: {}\n    commands:\n"
                   "      - tapOn:\n          {}: {}".format(q(sel_id(anchor)), key, q(dismiss)))
    return out


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
    """`launch=False` はアプリを起動し直さない。続きのフローを出すため。

    **補足はこのフローのステップに関係するものだけ。** 経路全体の補足（build の
    notes）を渡すと、どのフローにも全項目ぶんが付き、どれがこの項目の話か読めない。
    build の補足はそのステップの `note` に付いているので、ここで拾う。
    """
    notes = list(notes or [])
    start = start or mp.start
    # **実行時に決める値は env に未定のまま置く。** 値を焼き込むと、
    # データが変わったときに黙って古い値で走る。未定のままなら、埋まっていない
    # ことが走らせる前に分かる。
    used = [var_name(op_of(st["action"])[1]) for st in steps
            if "shot" not in st and not st.get("restart")
            and st.get("runtime")]
    out = ["appId: " + app]
    # 撮影先は端末で変わるので焼き込まない。走らせる側が端末に合わせて埋める
    if any("shot" in st for st in steps):
        used.insert(0, SHOTS_VAR)
    if used:
        out.append("env:")
        for v in dict.fromkeys(used):
            out.append("  {}: ''".format(v))
    out.append("---")

    def awaited_after(i):
        """steps[i] の次が自動表示を待つステップなら、その画面（着いた画面で閉じない）。"""
        nxt = next((st for st in steps[i:] if "shot" not in st), None)
        return nxt["to"] if nxt and nxt.get("await") else None

    def relaunch(at, i=0):
        # 起点に戻してから始める。launchApp だけでは前の項目の画面に居座ることがある。
        # clearState はログイン状態まで消えるので、要ると言われたときだけ。
        out.append("- stopApp")
        out.append("- launchApp:\n    clearState: true" if clear_state else "- launchApp")
        out.append("# 起点: " + at)
        a = anchor_of(mp, at, notes)
        if a:
            out.append(wait_for("id", sel_id(a), timeout))
        out.extend(auto_checks(mp, at, awaited_after(i)))

    if launch:
        relaunch(start)
    else:
        out.append("# 続き: " + start + " から")
        start_anchor = anchor_of(mp, start, notes)
        if start_anchor:
            out.append(wait_for("id", sel_id(start_anchor), timeout))

    for i, st in enumerate(steps):
        if st.get("restart"):
            out.append("# ここで起動し直す（前の状態から次の前提に行けないため）")
            relaunch(mp.start, i + 1)
            continue
        if st.get("await"):
            # 自動表示を確かめる項目。閉じずに、出るまで待つ（出なければ落ちる）
            summary = (mp.screens.get(st["to"]) or {}).get("summary")
            out.append("# {}: 自動表示 {} を待つ{}".format(
                st["screen"], st["to"], " — " + summary if summary else ""))
            dest = anchor_of(mp, st["to"], notes)
            if dest:
                out.append(wait_for("id", sel_id(dest), timeout))
            continue
        if "shot" in st:
            out.append("- takeScreenshot: " + q("${" + SHOTS_VAR + "}/" + st["shot"]))
            continue
        a = st["action"]
        if st.get("note"):
            notes.append(st["note"])
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
                # **ここだけ要素を待つ時間（wait_for）より長い。** wait_for はその場に出るのを
                # 待つだけだが、scrollUntilVisible はその間スクロールを繰り返す。スクロール
                # 1回は実測5〜8秒（maestrod.py の実測）で、60秒でも8〜12回ぶんにしかならない。
                # 短くすると、一覧の下の方にある要素が見つかる前に落ちる。見つからない要素で
                # 1分持っていかれるのは、スクロールを伴う項目だけの代償として受け入れる
                out.append("- scrollUntilVisible:\n    element:\n      id: {}\n"
                           "    direction: DOWN\n    timeout: {}".format(q(sel_id(target)), SCROLL_TIMEOUT))
        else:
            notes.append("種類の分からない操作を飛ばした: {}".format(a))
            continue

        if st.get("to"):
            dest = anchor_of(mp, st["to"], notes)
            if dest:
                out.append(wait_for("id", sel_id(dest), timeout))
            # 戻る操作で戻った画面では確かめない。自動表示は画面に入ったときに出るもので、
            # 戻るたびに確かめると、出ていないときの約7秒を往復ぶん払うことになる
            if a.get("kind") not in ("back", "dismiss"):
                out.extend(auto_checks(mp, st["to"], awaited_after(i + 1)))
        elif a.get("expect"):
            out.append(wait_for("id", sel_id(a["expect"]), timeout))
        else:
            notes.append("「{}」の結果を確かめる expect がマップに無い".format(label_of(a)))

    notes = list(dict.fromkeys(notes))   # 同じ画面の anchor 無しなどが重ならないように
    if notes:
        out.append("")
        out.extend("# 補足: " + n for n in notes)
    return "\n".join(out) + "\n", notes


def shot_context(mp, seg_start, seg_steps):
    """そのフローが終わる画面と、最後に確かめたID。

    `write_flows()` が返す行に載せるためのもの。**呼ぶ側が経路を読み直して導出せずに済ませる。**
    確かめたIDが無い（`expect` を持たない操作で終わった）なら None で、
    その証跡は自動確認なし＝画像だけが根拠になる。
    """
    at, checked = seg_start, (mp.screens.get(seg_start) or {}).get("anchor")
    for st in seg_steps:
        if st.get("restart"):
            at = mp.start
            checked = (mp.screens.get(at) or {}).get("anchor")
            continue
        if "shot" in st:
            continue
        if st.get("to"):
            at = st["to"]
            checked = (mp.screens.get(at) or {}).get("anchor")
        else:
            checked = st["action"].get("expect")
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
    """人が読む経路。**どこに自動確認があり、どこが証跡頼みかを明示する。**

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
        if st.get("await"):
            dest = (mp.screens.get(st["to"]) or {}).get("anchor")
            rows.append(("  {}  自動表示 {} を待つ".format(st["screen"].ljust(w), st["to"]),
                         "✓ {} が出ている".format(dest) if dest else "— {} に anchor が無い".format(st["to"])))
            checked += 1 if dest else 0
            unchecked += 0 if dest else 1
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
            right = "— 自動確認なし。証跡で見る"
        checked += right.startswith("✓")
        unchecked += right.startswith("—")
        rows.append((left, right))

    pad = max(len(l) for l, r in rows if r is not None)
    for left, right in rows:
        out.append(left if not right else "{}  {}".format(left.ljust(pad), right))

    out.append("")
    out.append("  自動確認あり {}件 / 証跡だけ {}件".format(checked, unchecked))
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
    # 割り込みの画面は遷移で入らない（こちらの操作と関係なく被さる）。被さる先の画面に
    # 着けるなら、出会いうる画面として数える
    reach |= {i for sid in reach for i in mp.auto_shows(sid) if i in mp.screens}
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
        for iid in mp.auto_shows(sid):
            # 欠けていると、確かめも閉じもせずに黙って飛ばす（auto_checks）
            if iid not in mp.screens:
                bad.append("{}: 自動表示 {} のファイルが無い（screens/{}.yaml）".format(sid, iid, iid))
            elif auto_of(mp, iid) is None:
                bad.append("{}: 自動表示 {} に anchor と閉じる操作（kind: dismiss）の両方が要る"
                           .format(sid, iid))
            else:
                breaks.append("{}: 自動表示 {} を着くたびに確かめる（出ていないとき約7秒）"
                              .format(sid, iid))

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


PLAN_KEYS = {"app", "clear_state", "items", "explore"}
ITEM_KEYS = {"from", "do", "fresh", "title", "expect"}


def shot_name(n):
    """項目の名前。並び順から振る（explore は items の続きの番号）。証跡・ダンプ・
    フローのファイル名になる。端末はディレクトリで分けるので、名前には入れない。"""
    return "test_{:02d}".format(n)
DO_KEYS = {"op", "runtime", "input"}


EXPLORE_KEYS = {"from", "title", "expect", "reason"}


def load_plan(path):
    """plan.json を読み、鍵を確かめて返す。**知らない鍵は綴り違い** — 黙って無視すると、
    その指定が効かないまま走る。"""
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit("plan を読めない: {}: {}".format(path, e))
    unknown = set(plan) - PLAN_KEYS
    if unknown:
        hint = ""
        if unknown & {"runtime", "inputs"}:
            hint = "。値の決め方は do の操作に書く（{\"op\": …, \"runtime\": true}）"
        elif "shots_dir" in unknown:
            hint = "。証跡の出力先は manifest.py の引数で決まる"
        sys.exit("plan に知らない鍵: {}（使えるのは {}）{}".format(
            ", ".join(sorted(unknown)), ", ".join(sorted(PLAN_KEYS)), hint))
    for n, it in enumerate(plan.get("explore") or [], 1):
        unknown = set(it) - EXPLORE_KEYS
        if unknown:
            sys.exit("plan の explore[{}] に知らない鍵: {}（使えるのは {}）".format(
                n, ", ".join(sorted(unknown)), ", ".join(sorted(EXPLORE_KEYS))))
    return plan


SHOTS_VAR = "SHOTS"   # 撮影先のディレクトリ。run_flows.py が端末ごとに埋める


def read_plan(plan):
    """plan.json をセグメントの列に開く。経路の計算も検査も build() に任せる。

    項目1つ＝「`from` から `do` を順に叩いて、1枚撮る」。**項目は経路を持たない。**
    `from` へどう着くかはここで `goto` を挟んで計算させる（すでに居れば何もしない）。
    テストケースが持つのは、どこから何を確かめるかだけ。

    `do` の要素は操作id（`"tap:Search"`）か、入れる値の決め方を添えた
    `{"op": 操作id, "runtime": true}` / `{"op": 操作id, "input": 値}`。

    `fresh` が真の項目は、アプリを起動し直した直後から始める（その項目の前提）。
    前の項目を撮った直後で起動し直すので、前の項目の状態は引き継がない。
    撮影は並び順から振った名前（`shot_name()`）で、置き場は `${SHOTS}` のまま残す。`title` / `expect` は読まない
    （manifest.py が読む）。`explore` も見ない — 経路が組めなかった項目で、フローを持たない。
    """
    items = plan.get("items") or []
    segments, owners = [], []   # owners: セグメントごとの項目名。エラーをどの項目か読めるように
    for n, item in enumerate(items, 1):
        shot = shot_name(n)
        unknown = set(item) - ITEM_KEYS
        if unknown:
            hint = ""
            if "shot" in unknown:
                hint = "。証跡の名前は並び順から振る"
            elif unknown & {"steps", "goto"}:
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
        add("shot", shot)
    return segments, plan.get("app"), bool(plan.get("clear_state")), owners


def report_problems(problems, owners=None):
    """組めなかった理由を stderr に出す。plan から来たなら、区間の番号に項目の名前を添える。"""
    print("経路を組めなかった:", file=sys.stderr)
    for _, msg in problems:
        m = re.match(r"\[(\d+)\]", msg) if owners else None
        if m and 0 < int(m.group(1)) <= len(owners):
            msg = "{} ({})".format(m.group(0), owners[int(m.group(1)) - 1]) + msg[m.end():]
        print("  " + msg, file=sys.stderr)
    if any(kind == "map" for kind, _ in problems):
        print("\nマップの穴。埋めるのは screen-map の仕事で、"
              "ここで推測して繋がない。", file=sys.stderr)


def write_flows(plan, out_dir, repo, timeout=10000):
    """plan.json の項目ごとに Maestro のフローを out_dir に書き、項目ごとの行を返す。

    **項目（撮影）ごとに1本ずつ。** 走らせる側は順に run して inspect するだけで、
    証跡と同名のダンプが揃う。2本目以降は前の続き（起動し直さない）。
    標準出力には読める経路を出す（レビューの2段目）。組めなければ理由を出して
    終了コード 2 で終わり、何も書かない。

    返す行は、plan の項目の欄（`title` / `expect` / `from`）と、ここで計算した欄
    （撮った画面・自動確認のID・フローのファイル名・起動し直すか・実行時に決める値）を
    1つにしたもの。呼ぶ側が plan と突き合わせずに済むように。

    **フローは端末によらず1組。** 撮影先は `${SHOTS}` のまま書き、走らせる側
    （run_flows.py）が端末に合わせて埋める。経路も操作も端末で変わらない。
    """
    segments, app, clear, owners = read_plan(plan)
    items = plan.get("items") or []
    if not segments:
        sys.exit("plan の items が空（経路が組めた項目が無いならフローは要らない）")
    if not app:
        sys.exit("plan に app（bundle id）が要る（xcrun simctl listapps <UDID> で調べる）")
    mp = ScreenMap(find_map(repo))
    steps, problems, notes = build(mp, segments)
    if problems:
        report_problems(problems, owners)
        sys.exit(2)

    by_shot = {shot_name(n): it for n, it in enumerate(items, 1)}
    segs = split_at_shots(mp, steps, None)
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for seg_start, seg_steps, shot, lch in segs:
        # 実行時に決める操作があれば手前で切る。前半を先に流し、着いてから値を決める
        pre_steps, resume_at, main_steps = split_before_runtime(mp, seg_start, seg_steps)
        # 起動も前半に出す。値を決める前に止めたとき、起動していなければ画面は見えない
        pre_name = None
        if pre_steps or (lch and main_steps is not seg_steps):
            pre, _ = emit_flow(mp, pre_steps, app, clear, None, timeout,
                               seg_start, launch=lch)
            pre_name = shot + ".pre.yaml"
            (d / pre_name).write_text(pre, encoding="utf-8")

        # 自分で起動するのは1本目と fresh の項目。他は居る場所から続ける
        flow, _ = emit_flow(mp, main_steps, app, clear, None, timeout,
                            resume_at, launch=lch and pre_name is None)
        name = shot + ".yaml"
        (d / name).write_text(flow, encoding="utf-8")
        screen, checked = shot_context(mp, seg_start, seg_steps)
        uses = runtime_uses(seg_steps)
        runtime_inputs = {v: "" for v in uses}
        it = by_shot.get(shot, {})
        written.append({"name": shot, "title": it.get("title", ""),
                        "from": it.get("from"), "fresh": bool(it.get("fresh")),
                        "do": it.get("do") or [], "expect": it.get("expect", ""),
                        "screen": screen, "checked": checked, "launch": lch,
                        "inputs": runtime_inputs, "input_use": uses,
                        "pre_flow": pre_name, "flow": name})
    print(emit_path(mp, steps, notes))
    print("\n  フロー（{}本）: {}".format(len(written), out_dir))
    for w in written:
        print("    {}{}  →  ダンプ名 {}  自動確認 {}".format(
            w["flow"], " ★起動し直す" if w["launch"] else "",
            w["name"], w["checked"] or "なし"))
    return written


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

    repo, rest = None, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--repo":
            repo = argv[i + 1]; i += 2
        elif a.startswith("--"):
            sys.exit("知らない引数: " + a + "（--help）")
        else:
            rest.append(a); i += 1
    mp = ScreenMap(find_map(repo))

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
    if cmd != "path":
        sys.exit(__doc__)

    # 画面idを並べると、順にたどる。どう行くかを見るだけなので操作は挟まない
    if not rest:
        sys.exit("行き先が要る。`route.py path <画面id>`。\n"
                 "画面の一覧は `route.py screens`。")
    steps, problems, notes = build(mp, [("goto", g) for g in rest])
    if problems:
        report_problems(problems)
        sys.exit(2)
    _, all_notes = emit_flow(mp, steps, "x", False, notes)   # 補足だけ取る
    print(emit_path(mp, steps, all_notes))


if __name__ == "__main__":
    main()
