#!/usr/bin/env python3
"""画面マップ（screen-map/）から、動作確認の経路を組み立てる。

  route.py screens                     画面の一覧（id / 呼び名 / できること）
  route.py path  <画面id>...           そこまでの経路を人が読む形で出す。並べると順にたどる
  route.py which <パス...>             変更したファイルから対象画面を引く（`-` で標準入力）
  route.py check                       マップ全体の自己テスト

  --repo <dir>       アプリのリポジトリ。画面マップはその下の screen-map/。必ず渡す
  --when <文言>      path の前提（マップの when の文言そのまま）。条件つきの辺を通す。並べてよい

**フローはここでは書かない。** plan.json から項目ごとのフローを書くのは `manifest.py` で、
中でこのモジュールの `write_flows()` を呼ぶ。plan の形は `manifest.py --help`。

**セグメントに割るのは、経路の計算を1回に縛らないため。** 「A の機能を使って
から、離れた B を確認する」のような項目は、計算が1回きりだと2つ目以降の遷移を
全部手で綴ることになり、マップから計算している意味が消える。区間に割れば、
**どの区間が切れているか**もそこで言える。

**組めなかったことを理由つきで返すのが半分の仕事。** 組めない経路は
マップが持っている穴（`in_tree: false`、未マップの画面、`stub` の先）が
そのまま出たもの。ここで推測して繋がない。埋めるのは screen-map の仕事。
条件つきの要素や枝（マップの `when`）も、前提に同じ文言が無ければ通さず、
「条件つき」と理由にする。

**どの画面が目標かを決めるのはここの仕事ではない。** `screens` が出すのは
一覧で、絞るのは読む側。ここにキーワード一致を足さない。**文字列の一致は
`summary` を読む判断より粗いのに、決定的なツールの見た目をまとう。**
柔らかい判断は柔らかいまま人のレビューに出す（sim-test-report の手順0）。
"""
import heapq
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# PyYAML を入れさせないための最小パーサ（理由は mini_yaml.py）
from mini_yaml import load_yaml

# ---------- マップ ----------

OPS = ("tap", "text")                    # 要素に対する操作。マップのアクションはこのどれか1つを持つ
GESTURES = ("scroll",)                   # 要素に紐づかない操作（マップの gestures）
DO_OPS = OPS + GESTURES + ("see",)       # plan の do で指せる種類。see は「その要素を見る」
FORWARD = ("push", "modal", "tab")       # 別の画面に進む
BACKWARD = ("back", "dismiss")           # 来た画面に戻る。戻り先は歩いた履歴で決まる
KINDS = ("screen", "visible", "hidden", "selected", "value", "external")

SCREEN_KEYS = {"anchor", "names", "summary", "files", "stub", "ready", "elements",
               "gestures", "auto_shows"}
ELEMENT_KEYS = {"id", "name", "summary", "when", "by", "in_tree", "actions"}
ACTION_KEYS = {"summary", "note", "expect"} | set(OPS)
GESTURE_KEYS = {"summary", "note", "expect"} | set(GESTURES)
EXPECT_KEYS = set(KINDS) | {"via", "when"}


def is_pattern(eid):
    """ID の末尾が `*` の要素は、同じ種類のものが複数並ぶ（一覧の行など）。"""
    return str(eid).endswith("*")


def pattern_prefix(eid):
    return str(eid)[:-1]


def expect_kind(e):
    """expect の1項目の種類（KINDS のどれか）。無ければ None。"""
    found = [k for k in KINDS if k in e]
    return found[0] if len(found) == 1 else None


class Action(object):
    """マップの1つの操作。要素のアクションか、画面の gestures の1項目。

    **結果は `expect` だけで持つ。** 遷移も「移った先の anchor が見える」という結果の
    1つで、`screen` を持つ expect が経路の辺になる。`when` つきのリストなら結果が
    状態で分かれる（`branches`）。
    """

    def __init__(self, sid, raw, element=None):
        self.sid = sid
        self.raw = raw or {}
        self.element = element
        self.op = next((k for k in (OPS if element is not None else GESTURES) if k in self.raw), None)
        self.target = element.get("id") if element is not None else self.raw.get(self.op)
        self.summary = self.raw.get("summary")
        exp = self.raw.get("expect")
        items = exp if isinstance(exp, list) else ([exp] if isinstance(exp, dict) else [])
        # 全部に when があれば分岐。1つも無ければ全部を確かめる。混ざっていれば check が出す
        if items and all(isinstance(e, dict) and e.get("when") for e in items):
            self.expects, self.branches = [], items
        else:
            self.expects, self.branches = [e for e in items if isinstance(e, dict)], None

    def label(self):
        if self.op is None:
            return "(操作が無い)"
        s = "{} {}".format(self.op, self.target)
        if self.element is not None and self.element.get("by") == "label":
            s += " [ラベル]"
        return s

    def when(self):
        """要素の出る条件。無ければ None。"""
        return self.element.get("when") if self.element is not None else None

    def outcomes(self):
        """(分岐の番号, 分岐の when, expect の並び)。分岐が無ければ1つだけで番号は None。"""
        if self.branches:
            return [(i, b.get("when"), [b]) for i, b in enumerate(self.branches)]
        return [(None, None, self.expects)]

    def is_back(self):
        return any(e.get("screen") == "back" for e in self.expects)

    def in_tree(self):
        return not (self.element is not None and self.element.get("in_tree") is False)


class ScreenMap(object):
    def __init__(self, root):
        self.root = root
        cfg = load_yaml(root / "config.yaml") or {}
        self.start = cfg.get("start")
        self.screens = {}
        for f in sorted((root / "screens").glob("*.yaml")):
            self.screens[f.stem] = load_yaml(f) or {}
        self._actions = {}

    def elements(self, sid):
        return [e for e in (self.screens.get(sid) or {}).get("elements") or [] if isinstance(e, dict)]

    def actions(self, sid):
        """その画面の操作（要素のアクション → gestures の順）。"""
        if sid not in self._actions:
            out = []
            for el in self.elements(sid):
                for a in el.get("actions") or []:
                    out.append(Action(sid, a, el))
            for g in (self.screens.get(sid) or {}).get("gestures") or []:
                out.append(Action(sid, g))
            self._actions[sid] = out
        return self._actions[sid]

    def element(self, sid, eid):
        """(要素, 実行時の値)。パターンの要素（`list.row.*`）に具体的な ID
        （`list.row.牛乳`）で当たれば、値に `牛乳` を返す。無ければ (None, None)。"""
        els = self.elements(sid)
        for el in els:
            if el.get("id") == eid:
                return el, None
        for el in els:
            p = str(el.get("id") or "")
            if is_pattern(p) and eid.startswith(pattern_prefix(p)) and len(eid) > len(p) - 1:
                return el, eid[len(p) - 1:]
        return None, None

    def edges(self, sid):
        """その画面から進む辺。[(操作, 分岐の番号, 行き先, via, 条件の並び)]。

        条件は要素の `when` と分岐の `when`。**条件つきの辺は、確認項目の前提
        （plan の `when`）に同じ文言があるときだけ往路に使う。** 出るかどうか、
        どちらに着くかが状態で決まるので、前提が無いまま通ると着く先が読めない。
        """
        out = []
        for a in self.actions(sid):
            if a.op not in OPS:
                continue
            for bi, bwhen, exps in a.outcomes():
                for e in exps:
                    if e.get("screen") and e.get("screen") != "back" and e.get("via") in FORWARD:
                        conds = [c for c in (a.when(), bwhen) if c]
                        out.append((a, bi, e["screen"], e["via"], conds))
        return out

    def blocked(self, edge, given=()):
        """往路の辺として使えない理由。使えるなら None。"""
        a, _, dest, _, conds = edge
        if not a.in_tree():
            return "in_tree: false（座標が要る）"
        if dest not in self.screens:
            return "遷移先 {} のファイルが無い".format(dest)
        missing = [c for c in conds if c not in given]
        if missing:
            return "条件つき（when: {}）".format(" / ".join(missing))
        return None

    def path_from(self, src, goal, given=(), relaxed=False):
        """src から goal までの最短経路。[(画面id, 辺), ...] か None。

        **同じ長さなら、タブの切り替え（`via: tab`）を多く通るほうを採る。** タブバーは
        いつも同じ位置にあり、画面の下の方のカードを押すより確実。

        **起点固定にしない。** セグメントを繋ぐとき、2本目以降は
        いま居る画面から引くことになる。

        relaxed=True では使えない辺も通す。**経路が無いのか、切れた辺の
        先にあるのかを言い分けるため**だけに使う。
        """
        if src == goal:
            return []
        best = {src: (0, 0)}
        prev = {src: None}
        heap = [(0, 0, 0, src)]
        n = 0
        while heap:
            hops, other, _, cur = heapq.heappop(heap)
            if (hops, other) > best.get(cur, (hops, other)):
                continue
            if cur == goal:
                out, at = [], goal
                while prev[at]:
                    out.append(prev[at])
                    at = prev[at][0]
                return list(reversed(out))
            for edge in self.edges(cur):
                if self.blocked(edge, given) and not (relaxed and edge[2] in self.screens):
                    continue
                nxt = edge[2]
                cost = (hops + 1, other + (0 if edge[3] == "tab" else 1))
                if nxt not in best or cost < best[nxt]:
                    best[nxt] = cost
                    prev[nxt] = (cur, edge)
                    n += 1
                    heapq.heappush(heap, (cost[0], cost[1], n, nxt))
        return None

    def auto_items(self, sid):
        """その画面の `auto_shows` を [(画面id, after の並び)] で。after が空なら入ったときに確かめる。"""
        out = []
        for it in (self.screens.get(sid) or {}).get("auto_shows") or []:
            if isinstance(it, dict):
                after = it.get("after") or []
                out.append((it.get("screen"), after if isinstance(after, list) else [after]))
            else:
                out.append((it, []))
        return out

    def auto_shows(self, sid):
        return [i for i, _ in self.auto_items(sid)]

    def auto_hosts(self, sid):
        """sid を自動で出すことがある画面（被さる先）と、その after。[(画面, after)]。"""
        return sorted((h, after) for h in self.screens
                      for i, after in self.auto_items(h) if i == sid)

    def back_action(self, sid):
        """その画面の「戻る」操作。無ければ None。戻り先は歩いた履歴で決まる。"""
        for a in self.actions(sid):
            if a.op in OPS and a.is_back() and a.in_tree():
                return a
        return None

    def reachable(self):
        """起点から、何かの条件の下で辿れる画面。"""
        out = {self.start}
        queue = [self.start]
        while queue:
            cur = queue.pop(0)
            for edge in self.edges(cur):
                if edge[0].in_tree() and edge[2] in self.screens and edge[2] not in out:
                    out.add(edge[2])
                    queue.append(edge[2])
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


def old_schema(mp):
    """古い形（`actions` / `states` を画面に持つ）の画面。移行前のマップを黙って空として読まない。"""
    return sorted(sid for sid, s in mp.screens.items()
                  if isinstance(s, dict) and ("actions" in s or "states" in s))


# ---------- 経路を組む ----------

def resolve(mp, at, wanted):
    """その画面の操作を1つ選ぶ。(操作, 要素, 実行時の値)。無ければ (None, None, None)。

    `tap:<id>` のように種類を頭に付けて指す。`see:<id>` は操作ではなく、要素を
    見る（見えるまでスクロールして確かめる）もので、操作は None で要素だけ返す。
    パターンの要素は具体的な ID でも指せる（`tap:list.row.牛乳`）。
    """
    want_op, _, want_target = wanted.partition(":")
    if want_op not in DO_OPS:
        want_op, want_target = None, wanted
    if want_op in GESTURES:
        for a in mp.actions(at):
            if a.op == want_op and a.target == want_target:
                return a, None, None
        return None, None, None
    el, value = mp.element(at, want_target)
    if el is None:
        return None, None, None
    if want_op == "see":
        return None, el, value
    for a in mp.actions(at):
        if a.element is el and (want_op is None or a.op == want_op):
            return a, el, value
    return None, el, value


def known_ops(mp, at):
    out = ["{}:{}".format(a.op, a.target) for a in mp.actions(at)]
    out += ["see:{}".format(el.get("id")) for el in mp.elements(at) if not el.get("actions")]
    return ", ".join(out)


def build(mp, segments, start=None):
    """セグメントを順に繋いで1本にする。

    セグメントは `goto`（経路を計算して繋ぐ）/ `do`（その場で操作する）/
    `shot`（撮る）/ `restart`（起動し直す）で、並び順がそのまま実行順になる。

    **分割の効き目は、区間ごとに組めたか言えること。** 「A の機能を使って
    から離れた B を確認する」のような項目は、経路計算が1回きりだと2つ目
    以降の遷移を全部手で綴ることになり、マップから計算している意味が消える。
    区間に割れば、どの区間が切れているかもそこで言える。

    歩きながら**ナビゲーションのスタックを持つ**。`goto` の行き先が既に
    積まれていれば、往路を探さずに戻る。**戻る操作（`screen: back`）の
    戻り先はこの履歴で決める。** どこから来たかで変わるので、マップには書かない。

    **自動表示の画面（どこかの `auto_shows` に並んでいる画面）への goto は、被さる先へ
    行って、閉じずに出るまで待つ**（`await` のステップ）。`after` つきなら、after の
    画面へ行って戻ってから待つ。被さる先が複数あれば、起点から近いほうを採って補足に出す。

    `start` を渡すとそこから歩き始める。

    理由は ("map", ...) と ("call", ...) に分ける。**直す先が違う。**
    前者はマップの穴で screen-map の仕事、後者は呼び方の間違いで、
    値を渡すか行き先を選び直せば済む。
    """
    steps, problems, notes = [], [], []
    at = start or mp.start
    stack = [at]

    # 自動表示の画面への goto を、被さる先への goto と、そこで待つ await に割る
    split = []
    for seg in segments:
        hosts = mp.auto_hosts(seg[1]) if seg[0] == "goto" else []
        if not hosts:
            split.append(seg)
            continue
        ctx = seg[2] if len(seg) > 2 else {}
        given = ctx.get("when") or ()

        def dist(h):
            if h == mp.start:
                return 0
            p = mp.path_from(mp.start, h, given)
            return None if p is None else len(p)
        # 入ったときに出るもの（after 無し）を先に。戻る往復が要らない
        cands = [(0 if not after else 1, dist(h), h, after) for h, after in hosts if dist(h) is not None]
        _, _, host, after = min(cands) if cands else (0, 0, hosts[0][0], hosts[0][1])
        if len(hosts) > 1:
            notes.append("{} は {} で自動表示される。{} で待つ".format(
                seg[1], " / ".join(sorted(set(h for h, _ in hosts))), host))
        split.append(("goto", host, dict(ctx, for_await=seg[1] if not after else None)))
        if after:
            split.append(("goto", after[0], dict(ctx)))
            split.append(("goto", host, dict(ctx, for_await=seg[1])))
        split.append(("await", seg[1], ctx))
    segments = split

    def back_step(a, tag):
        """戻る操作のステップ。戻り先は歩いた履歴で決める。"""
        nonlocal at
        if len(stack) < 2:
            # 起点以外の画面には必ず歩いて入っているので、履歴が尽きるのは
            # 起点に居るときだけ。起動直後の画面に戻る先は無い
            problems.append(("map", "{}: 起点 {} に戻る操作「{}」が書いてある。"
                             "起動直後の画面から戻る先は無い".format(tag, at, a.label())))
            return False
        dest = stack[-2]
        via = next(e.get("via") for e in a.expects if e.get("screen") == "back")
        steps.append({"screen": at, "action": a, "to": dest, "via": via, "item": item})
        stack.pop()
        at = dest
        return True

    item = None
    for n, seg in enumerate(segments, 1):
        what, value = seg[0], seg[1]
        ctx = seg[2] if len(seg) > 2 else {}
        item = ctx.get("item")
        given = ctx.get("when") or ()
        tag = "[{}] {} {}".format(n, what, value).rstrip()

        if what == "await":
            if at == value:
                continue            # もう出ている（前の項目がそこで終わった）
            # 閉じずに出るまで待つ。閉じる操作（screen: back）は履歴で被さる先に戻る
            steps.append({"screen": at, "await": True, "to": value, "item": item})
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
            steps.append({"shot": value, "item": item})
            continue

        if what == "do":
            found, el, val = resolve(mp, at, value)
            op = value.partition(":")[0]
            if found is None and not (op == "see" and el is not None):
                problems.append(("call", "{}: {} に「{}」という操作がマップに無い。"
                                 "この画面にあるのは {}".format(tag, at, value, known_ops(mp, at) or "（無し）")))
                break
            if op == "see":
                steps.append({"screen": at, "see": el, "value": val, "item": item})
                continue
            if not found.in_tree():
                problems.append(("map", "{}: {} の「{}」は in_tree: false（座標が要る）。"
                                 "フローでは押せない".format(tag, at, found.label())))
                break
            st = {"screen": at, "action": found, "value": val, "item": item}
            for k in ("input", "runtime", "pick"):
                if k in ctx:
                    st[k] = ctx[k]
            # 結果が状態で分かれるなら、確認項目の前提（when）でどちらかに決める
            if found.branches:
                hit = [(i, b) for i, b in enumerate(found.branches) if b.get("when") in given]
                if len(hit) != 1:
                    problems.append(("call", "{}: {} の「{}」は結果が分かれる（{}）。"
                                     "項目の when にどちらかを書く"
                                     .format(tag, at, found.label(),
                                             " / ".join(b.get("when") for b in found.branches))))
                    break
                st["branch"] = hit[0][0]
            outcome = found.branches[st["branch"]:st["branch"] + 1] if found.branches else found.expects
            if found.is_back():
                if not back_step(found, tag):
                    break
                steps[-1].update({k: v for k, v in st.items() if k in ("input", "runtime", "pick", "value")})
                continue
            dest = next((e for e in outcome if e.get("screen") and e.get("via") in FORWARD), None)
            if dest:
                st.update(to=dest["screen"], via=dest["via"])
            steps.append(st)
            if dest:
                if dest["screen"] not in mp.screens:
                    problems.append(("map", "{}: {} の「{}」の遷移先 {} のファイルが無い"
                                     .format(tag, at, found.label(), dest["screen"])))
                    break
                at = dest["screen"]
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
            hops = mp.path_from(stack[len(stack) - 1 - pops], value, given)
            if hops is not None and (best is None or pops + len(hops) < best[0] + len(best[1])):
                best = (pops, hops)

        if best is None:
            # なぜ届かないかを言う。**戻る辺は「切れている」に数えない** —
            # 戻るのはこのループが自分でやることなので、それを欠陥として
            # 報告すると、直しようのないものをマップの穴として挙げてしまう
            relaxed = None
            for pops in range(len(stack)):
                relaxed = mp.path_from(stack[len(stack) - 1 - pops], value, given, relaxed=True)
                if relaxed is not None:
                    break
            broken = [(sid, e, mp.blocked(e, given)) for sid, e in (relaxed or []) if mp.blocked(e, given)]
            if relaxed is None:
                problems.append(("map", "{}: {} から {} へ行ける操作がマップに無い"
                                 .format(tag, at, value)))
            elif broken:
                for sid, e, why in broken:
                    kind = "call" if why.startswith("条件つき") else "map"
                    hint = "。その前提で確かめるなら、項目の when に同じ文言を書く" if kind == "call" else ""
                    problems.append((kind, "{}: {} の「{}」で切れる: {}{}"
                                     .format(tag, sid, e[0].label(), why, hint)))
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
                problems.append(("map", "{}: {} に戻る操作（screen: back）が"
                                 "マップに無いので、{} へ向かえない".format(tag, at, value)))
                broke = True
                break
            if not back_step(a, tag):
                broke = True
                break
        if broke:
            break

        for sid, (a, bi, dest, via, _) in hops:
            st = {"screen": sid, "action": a, "to": dest, "via": via, "item": item}
            if bi is not None:
                st["branch"] = bi
            steps.append(st)
            stack.append(dest)
            at = dest

    for st in steps:
        a = st.get("action")
        if a is None:
            continue
        where = "({}) ".format(st["item"]) if st.get("item") else ""
        el = a.element
        pattern = el is not None and is_pattern(el.get("id")) and st.get("value") is None
        # 値の決め方の検査。**呼び方の間違いは走らせる前に止める**
        if st.get("runtime") and a.op != "text":
            problems.append(("call", "{}{} に runtime は付けられない。どの行を押すかは"
                             "スクリプトが決める（条件があるなら pick に書く）".format(where, a.label())))
        if "pick" in st and not pattern:
            problems.append(("call", "{}{} はパターンの要素（ID の末尾が *）ではないので、"
                             "実行時に選べない（pick を外す）".format(where, a.label())))
        if a.op == "text" and "input" not in st and not st.get("runtime"):
            problems.append(("call", "{}text {} に打つ文字が渡されていない（do に {{\"op\": …, \"input\": 値}} か "
                             "{{\"op\": …, \"runtime\": true}} で書く）。"
                             "マップは値を持たない。何を打つかはテストケースが決める"
                             .format(where, a.target)))
        if pattern and a.op == "text":
            # どれに打つかと何を打つかの2つを実行時に決めることになる。変数が1つしか持てない
            problems.append(("call", "{}text {} はパターンの要素なので、どの欄に打つかを ID まで書く"
                             "（text:{}<表示中の名前>）".format(where, a.target, pattern_prefix(a.target))))
            continue
        if pattern:
            st["pick"] = st.get("pick") or ""     # 空なら、画面に見えている1件目
    return steps, problems, notes


# ---------- 出す ----------

def var_name(target):
    """操作のidから env の変数名を作る。`browse.search_field` → `BROWSE_SEARCH_FIELD`。
    末尾のパターン記号（`browse.book_row.*`）は落とす。

    **呼ぶ側に名前を決めさせない。** idから決まるので、フローと索引と
    マニフェストで同じ名前になり、突き合わせに手が要らない。
    """
    return re.sub(r"[^A-Za-z0-9]", "_", target.rstrip(".*")).upper()


def needs_value(st):
    """実行時に値を決めるステップか。打つ文字（runtime）か、パターンの要素のどれを押すか（pick）。"""
    return "action" in st and (bool(st.get("runtime")) or "pick" in st)


def var_of(st):
    return st.get("var") or var_name(st["action"].target)


def index_var(var):
    """パターンの要素のうち何番目を押すか（Maestro の `index`）の変数名。"""
    return var + "_INDEX"


def picks_pattern(st):
    """パターンの要素のどれを押すかを実行時に決めるステップか（打つ文字ではなく）。"""
    return "pick" in st and "action" in st


def assign_vars(steps):
    """実行時に決める値に変数名を振る。**同じ区間で同じ名前が2回要れば `_2` を付ける**
    （同じ一覧を2回通るなど）。ここで振れば、フローとマニフェストで名前がずれない。"""
    used = {}
    for st in steps:
        if not needs_value(st):
            continue
        base = var_name(st["action"].target)
        used[base] = used.get(base, 0) + 1
        st["var"] = base if used[base] == 1 else "{}_{}".format(base, used[base])


def runtime_uses(steps):
    """実行時に決める値の、変数名 → 入る先。`selector`（tapOn の id。正規表現）か
    `text`（inputText。文字そのまま）。

    **走らせる側がこれを見てエスケープを分ける。** セレクタは正規表現なので、
    `牛乳(1L)` や `C++入門` をそのまま入れると別物として解釈され、狙った行に
    当たらない（`a.b` なら `aXb` にも当たる）。逆に inputText をエスケープすると
    `\\` ごと打たれる。どちらに入るかはフローを書くここでしか分からない。
    """
    uses = {}
    for st in steps:
        if needs_value(st):
            uses[var_of(st)] = "text" if st.get("runtime") else "selector"
        if picks_pattern(st):
            uses[index_var(var_of(st))] = "index"   # 数字そのもの。エスケープしない
    return uses


def runtime_picks(mp, steps):
    """パターンの要素を押すときの選び方。変数名 → {"pattern": ID, "pick": 条件, "exclude": [ID]}。

    条件が空なら、走らせる側（run_flows.py）が**画面に見えている1件目**を機械的に選ぶ。
    条件があれば止めて、ダンプと条件から選ばせる。

    `exclude` は、同じ画面で別の要素として定義されている、パターンの前方一致に当たる ID。
    **行の中の要素（`item_list.cell.title`）は行のパターン（`item_list.cell.*`）にも当たる。**
    外さないと、見えている1件目として行ではなくタイトルを選ぶ。
    """
    out = {}
    for st in steps:
        if "pick" not in st or "action" not in st:
            continue
        a = st["action"]
        prefix = pattern_prefix(a.target)
        others = sorted(str(el.get("id")) for el in mp.elements(a.sid)
                        if el is not a.element and str(el.get("id") or "").startswith(prefix))
        out[var_of(st)] = {"pattern": a.target, "pick": st.get("pick") or "", "exclude": others}
    return out


def sel_id(value):
    """マップの id を Maestro のセレクタにする。

    Maestro の id は正規表現なので、そのまま渡すと `.` が任意の1文字になり、
    `browse` が `browsex` にも当たる。**当たってほしいものにだけ当てる。**
    末尾 `*` はマップのパターン記法（`browse.book_row.*`）で、前方一致の意味。
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


def element_sel(el, value=None, var=None):
    """要素のセレクタ。(キー, 値)。パターンの要素は、決まった値か実行時の変数で1つに絞る。"""
    eid = str(el.get("id"))
    if el.get("by") == "label":
        return "text", sel_text(eid)
    if is_pattern(eid) and value is not None:
        return "id", "^" + re.escape(pattern_prefix(eid) + value) + "$"
    if is_pattern(eid) and var:
        # 値はアクセシビリティ ID そのもの（`list.row.牛乳`）。ダンプの id の欄を写せば済む
        return "id", "^${" + var + "}$"
    return "id", sel_id(eid)


def step_sel(st):
    a = st.get("action")
    el = st.get("see") if a is None else a.element
    var = var_of(st) if a is not None and needs_value(st) and a.element is not None \
        and is_pattern(a.element.get("id")) else None
    return element_sel(el, st.get("value"), var)


SCROLL_TIMEOUT = 60000   # scrollUntilVisible の上限。理由は reveal()
SETTLE_TIMEOUT = 3000    # 着いたあとの落ち着き待ちの上限


def reveal(key, value):
    """要素が全部見えるまでスクロールする。**マップに載っている要素を操作・確認する前に必ず入れる。**

    画面外の要素は、フローからは見つからずに落ちる。さらに悪いことに、ツリーには
    あるが画面外にある要素は、Maestro がその位置を叩いて**別の要素を押す**
    （mobile-dev-inc/Maestro#1275 と同じ症状）。見えている要素ならすぐ抜ける。

    **ここだけ要素を待つ時間（wait_for）より長い。** scrollUntilVisible はその間
    スクロールを繰り返す。スクロール1回は実測5〜8秒（maestrod.py の実測）で、
    60秒でも8〜12回ぶんにしかならない。
    """
    return ("- scrollUntilVisible:\n    element:\n      {}: {}\n"
            "    direction: DOWN\n    timeout: {}".format(key, q(value), SCROLL_TIMEOUT))


def wait_for(selector, value, timeout, extra=""):
    """要素が出るまで待つ。出なければ落ちる。

    `assertVisible` に `timeout` は渡せない（実測で `Unknown Property`）。
    既定の待ち時間は実測18秒で、通るぶんには足りるが、**本当に出ない要素で
    1つあたり18秒持っていかれる。** フローが唯一の検証手段になった以上、
    壊れたフローは早く落ちてほしいので、明示できる形にする。
    """
    return ("- extendedWaitUntil:\n    visible:\n      {}: {}{}\n    timeout: {}"
            .format(selector, q(value), extra, timeout))


def wait_gone(selector, value, timeout):
    return ("- extendedWaitUntil:\n    notVisible:\n      {}: {}\n    timeout: {}"
            .format(selector, q(value), timeout))


def auto_of(mp, iid):
    """自動表示の画面から (anchor, 閉じる操作) を引く。どちらか無ければ None。

    自動表示（レビュー依頼、お知らせなど、こちらの操作と関係なく被さる画面）も
    **画面として書く**（`screens/<id>.yaml`）。anchor が「出ているか」の目印、
    `screen: back` の操作が閉じ方。命名も ID の振り方も実測も画面と同じルールで効く。
    """
    scr = mp.screens.get(iid) or {}
    close = mp.back_action(iid)
    if not scr.get("anchor") or close is None:
        return None
    return scr["anchor"], close


def auto_checks(mp, sid, keep=None, via=None, came=None):
    """その画面の `auto_shows`（自動で出ることがある画面）が出ていたら閉じる。

    **入ったときは after の無い項目を、戻ってきたときは after に来た画面がある項目を**
    確かめる。ビューアを閉じた直後に出るレビュー依頼などは後者。

    **閉じるのは既定の扱い。** マップは「出ることがある」という事実だけを持ち、
    邪魔か確かめたいかはテストケースが決める。確かめたい項目（`from` にその画面）
    では、`keep` に渡した画面は閉じずに待つ（`build()` の `await`）。

    **出ていないときに1つあたり約7秒かかる**（Maestro が「無い」と決めるまで
    `optionalLookupTimeoutMs` の既定7秒を待つ。下げる設定は無い）。だから
    `auto_shows` を書いた画面に着いたときだけ積み、after で絞れるものは絞る。
    """
    out = []
    back = via in BACKWARD
    for iid, after in mp.auto_items(sid):
        if iid == keep:
            continue
        if (back and came not in after) or (not back and after):
            continue
        found = auto_of(mp, iid)
        if found is None:
            continue            # 書き間違いは check が出す
        anchor, close = found
        key, dismiss = element_sel(close.element)
        summary = (mp.screens.get(iid) or {}).get("summary")
        when = "（{} から戻ったとき）".format(came) if back else ""
        out.append("# 自動表示: {}{}{}（出ていたら閉じる）".format(iid, when, " — " + summary if summary else ""))
        out.append("- runFlow:\n    when:\n      visible:\n        id: {}\n    commands:\n"
                   "      - tapOn:\n          {}: {}".format(q(sel_id(anchor)), key, q(dismiss)))
    return out


def ready_lines(mp, sid, timeout):
    """読み込み完了の目印（`ready`）を待つ。any はどれか1つ、all は全部。"""
    r = (mp.screens.get(sid) or {}).get("ready") or {}
    out = []
    if r.get("any"):
        alts = [sel_id(str(x)) for x in r["any"]]
        out.append("# {}: 読み込み完了（どれか1つ）".format(sid))
        out.append(wait_for("id", alts[0] if len(alts) == 1 else "(" + "|".join(alts) + ")", timeout))
    if r.get("all"):
        out.append("# {}: 読み込み完了（全部）".format(sid))
        out.extend(wait_for("id", sel_id(str(x)), timeout) for x in r["all"])
    return out


def settle():
    """最後の落ち着き待ち。要素が出ても、ほかのセクションや画像がまだ読み込み中のことがある。"""
    return "- waitForAnimationToEnd:\n    timeout: {}".format(SETTLE_TIMEOUT)


def anchor_of(mp, sid, notes):
    a = (mp.screens.get(sid) or {}).get("anchor")
    if not a:
        notes.append("{} に anchor が無いので、着いたことを確かめられない".format(sid))
    return a


def outcome(st):
    """そのステップで確かめる expect の並び（分岐なら選んだ枝だけ）。"""
    a = st["action"]
    if a.branches:
        return [a.branches[st["branch"]]] if "branch" in st else []
    return a.expects


def ref_sel(st, ref):
    """expect が指す要素のセレクタ。`self` はその操作の要素（パターンなら押した1つ）。"""
    if ref == "self":
        return step_sel(st)
    return "id", sel_id(str(ref))


def step_comment(st):
    """そのステップが何をしているかの1行。**フローの中にコメントとして残す。**

    要約を別に作って見せると、レビューしたものと実際に走るものが別になる。
    同じ1つを読めるようにする。
    """
    a = st["action"]
    head = "# {}: {}".format(st["screen"], a.label())
    if st.get("value") is not None:
        head += " [{}]".format(st["value"])
    elif "pick" in st:
        head += " [{}]".format(st["pick"] or "見えている1件目")
    if a.summary:
        head += " — " + str(a.summary)
    if st.get("to"):
        head += " → " + st["to"]
    return head


def emit_flow(mp, steps, app, clear_state, notes=None, timeout=10000,
              start=None, launch=True, tail=None):
    """`launch=False` はアプリを起動し直さない。続きのフローを出すため。

    `tail` は、次のフローの頭で値を決めて押すステップ。**その要素までのスクロールを
    このフローの最後に置く** — 値を決めるときに、選ぶ対象が画面に見えていないと
    選べない（見えている1件目を選ぶのも、条件で選ぶのも同じ）。

    **補足はこのフローのステップに関係するものだけ。**
    """
    notes = list(notes or [])
    start = start or mp.start
    # **実行時に決める値は env に未定のまま置く。** 値を焼き込むと、
    # データが変わったときに黙って古い値で走る。未定のままなら、埋まっていない
    # ことが走らせる前に分かる。
    used = []
    for st in steps:
        if needs_value(st):
            used.append(var_of(st))
        if picks_pattern(st):
            used.append(index_var(var_of(st)))
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
        arrive(at, i)

    def arrive(sid, i, via=None, came=None):
        """sid に着いたときの確認。自動表示を閉じる → anchor → ready → 落ち着き待ち。

        **自動表示を先に閉じる。** シートやダイアログがモーダルで出ている間、下の画面は
        アクセシビリティのツリーから隠れる（実測）。先に anchor を待つと、自動表示が
        出た回は anchor が見えないまま時間切れになる。

        次のステップが自動表示を待つなら、anchor は待たない（見えないので）。
        """
        keep = awaited_after(i)
        out.extend(auto_checks(mp, sid, keep, via, came))
        if keep:
            return
        a = anchor_of(mp, sid, notes)
        if a:
            out.append(wait_for("id", sel_id(a), timeout))
        out.extend(ready_lines(mp, sid, timeout))
        out.append(settle())

    if launch:
        relaunch(start)
    else:
        out.append("# 続き: " + start + " から")
        start_anchor = anchor_of(mp, start, notes)
        if start_anchor:
            out.append(wait_for("id", sel_id(start_anchor), timeout))

    at = start
    for i, st in enumerate(steps):
        if st.get("restart"):
            out.append("# ここで起動し直す（前の状態から次の前提に行けないため）")
            relaunch(mp.start, i + 1)
            at = mp.start
            continue
        if st.get("await"):
            # 自動表示を確かめる項目。閉じずに、出るまで待つ（出なければ落ちる）
            summary = (mp.screens.get(st["to"]) or {}).get("summary")
            out.append("# {}: 自動表示 {} を待つ{}".format(
                st["screen"], st["to"], " — " + summary if summary else ""))
            dest = anchor_of(mp, st["to"], notes)
            if dest:
                out.append(wait_for("id", sel_id(dest), timeout))
            at = st["to"]
            continue
        if "shot" in st:
            out.append("- takeScreenshot: " + q("${" + SHOTS_VAR + "}/" + st["shot"]))
            continue
        if "see" in st:
            el = st["see"]
            name = el.get("name")
            out.append("# {}: see {}{}".format(st["screen"], el.get("id"), " — " + name if name else ""))
            out.append(reveal(*step_sel(st)))
            continue

        a = st["action"]
        out.append(step_comment(st))
        if a.element is not None and not st.get("revealed"):
            out.append(reveal(*step_sel(st)))
        key, val = step_sel(st) if a.element is not None else (None, None)

        if a.op == "tap":
            line = "- tapOn:\n    {}: {}".format(key, q(val))
            if picks_pattern(st):
                # 同じ名前の行が複数あると ID も同じになる。どれを押すかを index で1つに絞る。
                # Maestro の index は、当たった要素を画面上の位置順（上端の y、次に x）に並べた
                # 番号で、画面外の要素も数える（Filters.index / INDEX_COMPARATOR）。index を
                # 付けないとツリー順の先頭（押せるもの優先）になり、画面に見えているとは限らない。
                # ドキュメントには書かれていない挙動なので、Maestro を上げたら確かめ直す
                line += "\n    index: ${" + index_var(var_of(st)) + "}"
            out.append(line)
        elif a.op == "text":
            out.append("- tapOn:\n    {}: {}".format(key, q(val)))
            out.append("- eraseText")       # 前の項目の文字が残ったまま打たない
            if st.get("runtime"):
                out.append("- inputText: ${" + var_of(st) + "}")
            else:
                out.append("- inputText: " + q(str(st.get("input", ""))))
        elif a.op == "scroll":
            if a.target == "up":
                out.append("- swipe:\n    direction: DOWN")   # 内容を下へ＝上へ戻る
            else:
                out.append("- scroll")
        else:
            notes.append("種類の分からない操作を飛ばした: {}".format(a.label()))
            continue

        exps = outcome(st)
        if st.get("to"):
            arrive(st["to"], i + 1, st.get("via"), st["screen"])
            at = st["to"]
        for e in exps:
            kind = expect_kind(e)
            if kind in ("visible", "value"):
                out.append(wait_for(*ref_sel(st, e[kind]), timeout=timeout))
            elif kind == "selected":
                out.append(wait_for(*ref_sel(st, e[kind]), timeout=timeout, extra="\n      selected: true"))
            elif kind == "hidden":
                out.append(wait_gone(*ref_sel(st, e[kind]), timeout=timeout))
            elif kind == "external":
                # アプリの外に出た。確かめずに、落とさずに前に戻す
                out.append("# アプリの外（{}）に出る。確かめずにアプリに戻す".format(e[kind]))
                out.append("- launchApp:\n    stopApp: false")
                a2 = anchor_of(mp, at, notes)
                if a2:
                    out.append(wait_for("id", sel_id(a2), timeout))
        if not exps:
            notes.append("「{}」の結果を確かめる expect がマップに無い".format(a.label()))

    if tail is not None:
        what = "打つ文字" if tail.get("runtime") else "押すもの"
        out.append("# 次で使う {} が見えるまでスクロールして止める（ここで{}を決める）"
                   .format(tail["action"].target, what))
        out.append(reveal(*element_sel(tail["action"].element)))

    notes = list(dict.fromkeys(notes))   # 同じ画面の anchor 無しなどが重ならないように
    if notes:
        out.append("")
        out.extend("# 補足: " + n for n in notes)
    return "\n".join(out) + "\n", notes


def check_of(mp, st):
    """そのステップで最後に自動で確かめる ID。確かめないなら None。"""
    if st.get("await") or st.get("restart"):
        sid = st["to"] if st.get("await") else mp.start
        return (mp.screens.get(sid) or {}).get("anchor")
    if "see" in st:
        return st["see"].get("id")
    checked = None
    if st.get("to"):
        checked = (mp.screens.get(st["to"]) or {}).get("anchor")
    for e in outcome(st):
        kind = expect_kind(e)
        if kind in ("visible", "value", "selected", "hidden"):
            ref = e[kind]
            checked = st["action"].target if ref == "self" else ref
        elif kind == "external":
            checked = None
    return checked


def shot_context(mp, seg_start, seg_steps):
    """そのフローが終わる画面と、最後に確かめたID。

    `write_flows()` が返す行に載せるためのもの。**呼ぶ側が経路を読み直して導出せずに済ませる。**
    確かめたIDが無い（`expect` を持たない操作で終わった）なら None で、
    その証跡は自動確認なし＝画像だけが根拠になる。
    """
    at, checked = seg_start, (mp.screens.get(seg_start) or {}).get("anchor")
    for st in seg_steps:
        if "shot" in st:
            continue
        if st.get("restart"):
            at = mp.start
        elif st.get("to"):
            at = st["to"]
        checked = check_of(mp, st)
    return at, checked


def split_at_shots(mp, steps, start, launch_first=True):
    """撮影ごとにステップを切り、(そのフローの起点, ステップ列, 撮る名前, 起動するか) で返す。

    **1本＝1枚＝1ダンプにするため。** ダンプはフローの途中では取れないので、
    証跡1枚ごとに構造を残すには、撮る地点でフローを終わらせるしかない。
    2本目以降は前のフローの続きになるので、歩き直しは起きない。

    返す4つ目は**そのフローが自分で起動するか。** 1本目と、`fresh` の項目が
    そう。**ここが鎖の切れ目**で、走らせる側は落ちたときにどこまで諦めるかを
    これで決める（次に起動するフローからは、前が落ちていても走る）。
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


def split_parts(seg_start, seg_steps):
    """実行時に値を決めるステップの手前で切る。[(起点, ステップ列), ...]。

    **値を決めるには、目的の画面に着いていて、選ぶ対象が見えている必要がある。**
    手前までを1本にして先に走らせ、止まったところで画面を見て決める。
    切る箇所ごとに1本増える（途中の一覧で1件選び、着いた先でまた選ぶ、など）。
    2本目以降の頭のステップは、前の本の最後でスクロール済み（`revealed`）。
    """
    parts, cur, at, start = [], [], seg_start, seg_start
    for st in seg_steps:
        if needs_value(st) and (cur or parts):
            parts.append((start, cur))
            cur, start = [], at
            st = dict(st, revealed=True)
        elif needs_value(st):
            # 区間の頭で値が要る。スクロールだけの本を先に置く
            parts.append((start, []))
            st = dict(st, revealed=True)
        cur.append(st)
        if st.get("to"):
            at = st["to"]
    parts.append((start, cur))
    return parts


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

    def row(left, right):
        nonlocal checked, unchecked
        rows.append((left, right))
        if right:
            checked += right.startswith("✓")
            unchecked += right.startswith("—")

    start_anchor = (mp.screens.get(chain[0]) or {}).get("anchor")
    row("  {}  {}".format(chain[0].ljust(w), "起点" if chain[0] == mp.start else "続き"),
        "✓ {} が出ている".format(start_anchor) if start_anchor else "— anchor が無い")

    for n, st in enumerate(steps):
        if st.get("restart"):
            a = (mp.screens.get(mp.start) or {}).get("anchor")
            row("  {}  アプリを起動し直す".format("".ljust(w)), None)
            row("  {}  起点".format(mp.start.ljust(w)),
                "✓ {} が出ている".format(a) if a else "— anchor が無い")
            continue
        if "shot" in st:
            # 撮影行は右カラムを持たないので、桁揃えの計算から外す
            row("  {}  撮影 {}".format("".ljust(w), os.path.basename(st["shot"])), None)
            continue
        if st.get("await"):
            dest = (mp.screens.get(st["to"]) or {}).get("anchor")
            row("  {}  自動表示 {} を待つ".format(st["screen"].ljust(w), st["to"]),
                "✓ {} が出ている".format(dest) if dest else "— {} に anchor が無い".format(st["to"]))
            continue
        if "see" in st:
            row("  {}  see {}".format(st["screen"].ljust(w), st["see"].get("id")),
                "✓ {} が見える".format(st["see"].get("id")))
            continue
        a = st["action"]
        extra = ' "{}"'.format(st.get("input", "")) if a.op == "text" and "input" in st else ""
        if st.get("value") is not None:
            extra += " [{}]".format(st["value"])
        elif "pick" in st:
            extra += " [{}]".format(st["pick"] or "見えている1件目")
        left = "  {}  {}{}".format(st["screen"].ljust(w), a.label(), extra)
        nxt = next((x for x in steps[n + 1:] if "shot" not in x), None)
        rights = []
        if st.get("to") and nxt and nxt.get("await"):
            rights.append("（{} が被さって隠れるので、次の自動表示で確かめる）".format(st["to"]))
        elif st.get("to"):
            dest = (mp.screens.get(st["to"]) or {}).get("anchor")
            rights.append("✓ {} に着いたことを確認".format(st["to"]) if dest
                          else "— {} に anchor が無い".format(st["to"]))
        for e in outcome(st):
            kind = expect_kind(e)
            ref = e.get(kind)
            ref = a.target if ref == "self" else ref
            if kind in ("visible", "value"):
                rights.append("✓ {} が出ている{}".format(ref, "（値は証跡で見る）" if kind == "value" else ""))
            elif kind == "selected":
                rights.append("✓ {} が選択状態".format(ref))
            elif kind == "hidden":
                rights.append("✓ {} が消えた".format(ref))
            elif kind == "external":
                rights.append("— アプリの外（{}）に出る。確かめずに戻す".format(ref))
        if not rights:
            rights.append("— 自動確認なし。証跡で見る")
        row(left, rights[0])
        for r in rights[1:]:
            row("  {}".format("".ljust(w)), r)

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


def defined_ids(mp):
    """どこかの画面に要素として定義されている ID と、画面の anchor。"""
    ids = set()
    for sid in mp.screens:
        a = (mp.screens[sid] or {}).get("anchor")
        if a:
            ids.add(str(a))
        ids.update(str(el.get("id")) for el in mp.elements(sid) if el.get("id"))
    return ids


def is_defined(ids, ref):
    """ref が定義済みの ID か。パターンの要素（`list.row.*`）の具体的な1つも定義済みとみなす。"""
    ref = str(ref)
    return ref in ids or any(is_pattern(i) and ref.startswith(pattern_prefix(i)) for i in ids)


def check_screen(mp, sid, ids):
    """1画面ぶんの不整合（bad）と、経路が切れる／弱い箇所（breaks）と、書き足すもの（todo）。"""
    s = mp.screens[sid] or {}
    bad, breaks, todo = [], [], []
    if not isinstance(s, dict):
        return ["{}: 画面の中身が辞書になっていない".format(sid)], breaks, todo
    if s.get("stub"):
        breaks.append("{}: stub（操作は網羅ではない）".format(sid))
    if not s.get("anchor"):
        bad.append("{}: anchor が無い".format(sid))
    unknown = set(s) - SCREEN_KEYS
    if unknown:
        bad.append("{}: 知らない鍵 {}（使えるのは {}）".format(
            sid, ", ".join(sorted(unknown)), ", ".join(sorted(SCREEN_KEYS))))

    r = s.get("ready")
    if r is not None:
        if not isinstance(r, dict) or not (set(r) <= {"any", "all"}) or not r:
            bad.append("{}: ready は any か all に ID の並びを書く".format(sid))
        else:
            for k in r:
                for ref in r[k] or []:
                    if not is_defined(ids, ref):
                        bad.append("{}: ready の {} がどの画面の要素にも無い".format(sid, ref))

    seen = set()
    for el in s.get("elements") or []:
        if not isinstance(el, dict) or not el.get("id"):
            bad.append("{}: id の無い要素がある".format(sid))
            continue
        eid = str(el["id"])
        if eid in seen:
            bad.append("{}: 要素 {} が2回ある（アクションは1つの要素にまとめる）".format(sid, eid))
        seen.add(eid)
        if "*" in eid[:-1]:
            bad.append("{}: {} の * は末尾にだけ書ける".format(sid, eid))
        unknown = set(el) - ELEMENT_KEYS
        if unknown:
            bad.append("{}: 要素 {} に知らない鍵 {}（使えるのは {}）".format(
                sid, eid, ", ".join(sorted(unknown)), ", ".join(sorted(ELEMENT_KEYS))))
        if not el.get("name"):
            todo.append("{}: 要素 {} に name が無い".format(sid, eid))
        if el.get("in_tree") is False:
            breaks.append("{}: {} は in_tree: false（座標が要る）".format(sid, eid))
        if el.get("by") == "label":
            breaks.append("{}: {} はラベル指定（ローカライズで壊れる）".format(sid, eid))
            if is_pattern(eid):
                bad.append("{}: {} はラベル指定なのでパターンにできない".format(sid, eid))
        if el.get("when") and el.get("actions"):
            breaks.append("{}: {} は条件つき（when: {}）。前提に書かない限り経路に使わない"
                          .format(sid, eid, el["when"]))

    for a in mp.actions(sid):
        where = "{}: 「{}」".format(sid, a.label())
        keys = ACTION_KEYS if a.element is not None else GESTURE_KEYS
        ops = [k for k in (OPS if a.element is not None else GESTURES) if k in a.raw]
        if len(ops) != 1:
            bad.append("{}: {} のどれか1つだけを持つ操作にする（{}）".format(
                sid, " / ".join(OPS if a.element is not None else GESTURES),
                a.element.get("id") if a.element is not None else a.raw))
            continue
        if a.element is not None and a.raw.get(a.op) is not None:
            bad.append("{} は値を持たない（`- {}:` だけ書く。対象は要素の id）".format(where, a.op))
        if a.element is None and a.target not in ("down", "up"):
            bad.append("{} の向きは down / up".format(where))
        unknown = set(a.raw) - keys
        if unknown:
            bad.append("{} に知らない鍵 {}（使えるのは {}）".format(
                where, ", ".join(sorted(unknown)), ", ".join(sorted(keys))))
        if not a.summary:
            todo.append("{} に summary が無い".format(where))
        exp = a.raw.get("expect")
        items = exp if isinstance(exp, list) else ([exp] if exp is not None else [])
        if not items:
            todo.append("{} に expect が無い（結果を自動で確かめられない）".format(where))
        whens = [isinstance(e, dict) and bool(e.get("when")) for e in items]
        if any(whens) and not all(whens):
            bad.append("{} の expect で when のある項目と無い項目が混ざっている"
                       "（分かれるなら全部に when を書く）".format(where))
        screens = 0
        for e in items:
            if not isinstance(e, dict):
                bad.append("{} の expect が読めない: {}".format(where, e))
                continue
            unknown = set(e) - EXPECT_KEYS
            if unknown:
                bad.append("{} の expect に知らない鍵 {}".format(where, ", ".join(sorted(unknown))))
            kind = expect_kind(e)
            if kind is None:
                bad.append("{} の expect は {} のどれか1つを持つ".format(where, " / ".join(KINDS)))
                continue
            if kind == "screen":
                screens += 1
                dest, via = e.get("screen"), e.get("via")
                if dest == "back":
                    if via not in BACKWARD:
                        bad.append("{} は screen: back なので via は {}".format(where, " / ".join(BACKWARD)))
                else:
                    if via not in FORWARD:
                        bad.append("{} の via は {}（戻る操作は screen: back）".format(where, " / ".join(FORWARD)))
                    if dest not in mp.screens:
                        bad.append("{} の遷移先 {} のファイルが無い".format(where, dest))
                if a.element is None:
                    bad.append("{} は要素に紐づかないので画面を移れない".format(where))
            elif e.get("via"):
                bad.append("{} の via は screen と一緒にだけ書く".format(where))
            elif kind != "external":
                ref = e[kind]
                if ref == "self" and a.element is None:
                    bad.append("{} の self は要素のアクションでだけ使える".format(where))
                elif ref != "self" and not is_defined(ids, ref):
                    bad.append("{} の expect が指す {} がどの画面の要素にも無い".format(where, ref))
        if screens > 1 and not any(whens):
            bad.append("{} に移る先が2つある（状態で分かれるなら when を書く）".format(where))

    for iid, after in mp.auto_items(sid):
        # 欠けていると、確かめも閉じもせずに黙って飛ばす（auto_checks）
        if iid not in mp.screens:
            bad.append("{}: 自動表示 {} のファイルが無い（screens/{}.yaml）".format(sid, iid, iid))
        elif auto_of(mp, iid) is None:
            bad.append("{}: 自動表示 {} に anchor と閉じる操作（screen: back）の両方が要る"
                       .format(sid, iid))
        for x in after:
            if x not in mp.screens:
                bad.append("{}: 自動表示 {} の after {} の画面が無い".format(sid, iid, x))
        if after:
            breaks.append("{}: 自動表示 {} を {} から戻るたびに確かめる（出ていないとき約7秒）"
                          .format(sid, iid, " / ".join(after)))
        else:
            breaks.append("{}: 自動表示 {} を着くたびに確かめる（出ていないとき約7秒）"
                          .format(sid, iid))
    return bad, breaks, todo


def cmd_check(mp):
    bad = []
    old = old_schema(mp)
    if old:
        print("古い形（actions / states を画面に持つ）のマップ: {}".format(" ".join(old)))
        print("migrate_map.py で要素中心の形に移す（screen-map スキルの reference/schema.md）")
        return 1
    if mp.start not in mp.screens:
        bad.append("起点 {} のファイルが無い".format(mp.start))
    reach = mp.reachable()
    # 自動表示の画面は遷移で入らない（こちらの操作と関係なく被さる）。被さる先の画面に
    # 着けるなら、出会いうる画面として数える
    reach |= {i for sid in reach for i in mp.auto_shows(sid) if i in mp.screens}
    print("起点: {}".format(mp.start))
    print("到達できる: {}".format(" ".join(sorted(reach))))
    lost = sorted(set(mp.screens) - reach)
    print("到達できない: {}".format(" ".join(lost) if lost else "なし"))

    ids = defined_ids(mp)
    breaks, todo = [], []
    for sid in sorted(mp.screens):
        b, br, td = check_screen(mp, sid, ids)
        bad += b
        breaks += br
        todo += td

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
            print("  **この画面の summary と expect は実装とずれている可能性がある。**"
                  "期待値を立てる前に files を読む。\n"
                  "  リファクタやコメントの修正でもここに出るので、不整合ではなく警告。")

    print("\n経路が切れる／弱い箇所:")
    for b in breaks:
        print("  " + b)
    if not breaks:
        print("  （なし）")
    if todo:
        print("\n書き足すもの（{}件）:".format(len(todo)))
        for t in todo:
            print("  " + t)
    if bad:
        print("\nマップの不整合:")
        for b in bad:
            print("  " + b)
        return 1
    return 0


PLAN_KEYS = {"app", "clear_state", "items", "explore"}
ITEM_KEYS = {"from", "do", "fresh", "title", "expect", "when"}


def shot_name(n):
    """項目の名前。並び順から振る（explore は items の続きの番号）。証跡・ダンプ・
    フローのファイル名になる。端末はディレクトリで分けるので、名前には入れない。"""
    return "test_{:02d}".format(n)
DO_KEYS = {"op", "runtime", "input", "pick"}


EXPLORE_KEYS = {"from", "title", "expect", "reason", "when"}


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
            hint = "。打つ文字の決め方は do の操作に書く（{\"op\": …, \"runtime\": true}）"
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

    `do` の要素は操作id（`"tap:Search"` / `"see:list.footer"`）か、値の決め方を添えた
    `{"op": 操作id, "runtime": true}` / `{"op": 操作id, "input": 値}`（打つ文字）/
    `{"op": 操作id, "pick": 条件}`（パターンの要素から条件に合うものを選ぶ）。

    `when` は項目の前提（マップの `when` の文言をそのまま写す）。条件つきの辺と、
    結果が分かれる操作の枝は、ここに同じ文言があるときだけ使う。

    `fresh` が真の項目は、アプリを起動し直した直後から始める（その項目の前提）。
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
        when = item.get("when") or []
        if isinstance(when, str):
            when = [when]

        def add(what, value="", **ctx):
            ctx["item"] = shot
            ctx["when"] = tuple(when)
            segments.append((what, value, ctx)); owners.append(shot)
        if item.get("fresh") and segments:
            add("restart")
        add("goto", start)
        for d in do:
            if isinstance(d, str):
                add("do", d)
                continue
            modes = [k for k in ("runtime", "input", "pick") if k in d] if isinstance(d, dict) else []
            bad = not isinstance(d, dict) or not d.get("op") or set(d) - DO_KEYS \
                or len(modes) != 1 or d.get("runtime") not in (None, True) \
                or ("pick" in d and not (isinstance(d["pick"], str) and d["pick"].strip()))
            if bad:
                sys.exit("plan の {} の do の要素は操作id か {{\"op\": 操作id, \"input\": 値}} か "
                         "{{\"op\": 操作id, \"runtime\": true}} か {{\"op\": 操作id, \"pick\": 条件}}: {}".format(
                             shot, json.dumps(d, ensure_ascii=False)))
            add("do", d["op"], **{modes[0]: d[modes[0]]})
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


def load_map(repo):
    mp = ScreenMap(find_map(repo))
    old = old_schema(mp)
    if old:
        sys.exit("画面マップが古い形（actions / states）のまま: {}。\n"
                 "migrate_map.py --repo <アプリ> で要素中心の形に移す".format(" ".join(old)))
    return mp


def write_flows(plan, out_dir, repo, timeout=10000):
    """plan.json の項目ごとに Maestro のフローを out_dir に書き、項目ごとの行を返す。

    **項目（撮影）ごとに1本ずつ。** 走らせる側は順に run して inspect するだけで、
    証跡と同名のダンプが揃う。2本目以降は前の続き（起動し直さない）。
    標準出力には読める経路を出す（レビューの2段目）。組めなければ理由を出して
    終了コード 2 で終わり、何も書かない。

    **実行時に値を決める操作があれば、項目のフローをその手前で割る**（`parts`）。
    打つ文字、パターンの要素のどれを押すか。前の本で目的の画面に着き、対象が
    見えるまでスクロールして止まり、走らせる側が値を決めてから次の本を走らせる。

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
    mp = load_map(repo)
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
        assign_vars(seg_steps)
        parts = split_parts(seg_start, seg_steps)
        files = []
        for k, (p_start, p_steps) in enumerate(parts):
            last = k == len(parts) - 1
            tail = None if last else parts[k + 1][1][0]
            # 自分で起動するのは1本目と fresh の項目の、最初の本だけ。他は居る場所から続ける
            flow, _ = emit_flow(mp, p_steps, app, clear, None, timeout,
                                p_start, launch=lch and k == 0, tail=tail)
            name = shot + ".yaml" if last else "{}.{}.yaml".format(shot, k + 1)
            (d / name).write_text(flow, encoding="utf-8")
            decide = var_of(p_steps[0]) if k > 0 else None
            files.append({"flow": name, "decide": decide})
        screen, checked = shot_context(mp, seg_start, seg_steps)
        uses = runtime_uses(seg_steps)
        picks = runtime_picks(mp, seg_steps)
        # 走らせる側（や LLM）が埋める値。見えている1件目を選ぶものは run_flows.py が埋めるので入れない
        # 何番目か（_INDEX）は run_flows.py が数えるので入れない
        inputs = {v: "" for v, use in uses.items()
                  if use != "index" and not (v in picks and not picks[v]["pick"])}
        it = by_shot.get(shot, {})
        written.append({"name": shot, "title": it.get("title", ""),
                        "from": it.get("from"), "fresh": bool(it.get("fresh")),
                        "when": it.get("when") or [],
                        "do": it.get("do") or [], "expect": it.get("expect", ""),
                        "screen": screen, "checked": checked, "launch": lch,
                        "inputs": inputs, "input_use": uses, "picks": picks,
                        "parts": files, "flow": files[-1]["flow"]})
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

    repo, rest, when = None, [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--repo":
            repo = argv[i + 1]; i += 2
        elif a == "--when":
            when.append(argv[i + 1]); i += 2
        elif a.startswith("--"):
            sys.exit("知らない引数: " + a + "（--help）")
        else:
            rest.append(a); i += 1
    mp = load_map(repo)

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
    steps, problems, notes = build(mp, [("goto", g, {"when": tuple(when)}) for g in rest])
    if problems:
        report_problems(problems)
        sys.exit(2)
    _, all_notes = emit_flow(mp, steps, "x", False, notes)   # 補足だけ取る
    print(emit_path(mp, steps, all_notes))


if __name__ == "__main__":
    main()
