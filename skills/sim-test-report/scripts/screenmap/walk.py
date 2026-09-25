"""画面マップの上を歩いて、セグメント（goto / do / shot / restart）を1本のステップ列にする。

**セグメントに割るのは、経路の計算を1回に縛らないため。** 「A の機能を使って
から、離れた B を確認する」のような項目は、計算が1回きりだと2つ目以降の遷移を
全部手で綴ることになり、マップから計算している意味が消える。区間に割れば、
**どの区間が切れているか**もそこで言える。

**組めなかったことを理由つきで返すのが半分の仕事。** 組めない経路は
マップが持っている穴（`in_tree: false`、未マップの画面、`stub` の先）が
そのまま出たもの。ここで推測して繋がない。埋めるのは screen-map の仕事。
条件つきの要素や枝（マップの `when`）も、前提に同じ文言が無ければ通さず、
「条件つき」と理由にする。
"""
from .model import DO_OPS, FORWARD, GESTURES, is_pattern, pattern_prefix


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
    """セグメントを順に繋いで1本にする。(ステップ列, 組めなかった理由, 補足) を返す。

    セグメントは `goto`（経路を計算して繋ぐ）/ `do`（その場で操作する）/
    `shot`（撮る）/ `restart`（起動し直す）で、並び順がそのまま実行順になる。
    3つ目の要素は項目の文脈（`item` / `when` / 値の決め方）。

    `start` を渡すとそこから歩き始める。

    理由は ("map", ...) と ("call", ...) に分ける。**直す先が違う。**
    前者はマップの穴で screen-map の仕事、後者は呼び方の間違いで、
    値を渡すか行き先を選び直せば済む。
    """
    walk = Walk(mp, start)
    walk.run(expand_auto_shows(mp, segments, walk.notes))
    check_values(walk.steps, walk.problems)
    return walk.steps, walk.problems, walk.notes


def expand_auto_shows(mp, segments, notes):
    """自動表示の画面への goto を、被さる先への goto と、そこで待つ await に割る。

    **自動表示の画面（どこかの `auto_shows` に並んでいる画面）は遷移で入らない**ので、
    `expect` の辺では辿れない。被さる先へ行き、閉じずに出るまで待つ。`after` つきなら、
    after の画面へ行って戻ってから待つ。被さる先が複数あれば、入ったときに出るもの
    （戻る往復が要らない）、次に起点から近いものを採って補足に出す。
    """
    out = []
    for seg in segments:
        hosts = mp.auto_hosts(seg[1]) if seg[0] == "goto" else []
        if not hosts:
            out.append(seg)
            continue
        ctx = seg[2] if len(seg) > 2 else {}
        given = ctx.get("when") or ()

        def dist(h):
            if h == mp.start:
                return 0
            p = mp.path_from(mp.start, h, given)
            return None if p is None else len(p)
        cands = [(0 if not after else 1, dist(h), h, after) for h, after in hosts if dist(h) is not None]
        _, _, host, after = min(cands) if cands else (0, 0, hosts[0][0], hosts[0][1])
        if len(hosts) > 1:
            notes.append("{} は {} で自動表示される。{} で待つ".format(
                seg[1], " / ".join(sorted(set(h for h, _ in hosts))), host))
        out.append(("goto", host, dict(ctx, for_await=seg[1] if not after else None)))
        if after:
            out.append(("goto", after[0], dict(ctx)))
            out.append(("goto", host, dict(ctx, for_await=seg[1])))
        out.append(("await", seg[1], ctx))
    return out


class Stop(Exception):
    """組めなかった。理由は Walk.problems に積んである。"""


class Walk(object):
    """マップの上を歩く。居る画面（`at`）と、歩いてきた履歴（`stack`）を持つ。

    **戻る操作（`screen: back`）の戻り先はこの履歴で決める。** どこから来たかで変わる
    （お気に入りから詳細に入ったなら、戻る先は一覧ではなくお気に入り）ので、マップには
    書かない。`goto` の行き先が履歴に積まれていれば、戻ってから進む経路も同じ探索で比べる。
    """

    def __init__(self, mp, start=None):
        self.mp = mp
        self.at = start or mp.start
        self.stack = [self.at]
        self.steps, self.problems, self.notes = [], [], []
        self.item, self.tag = None, ""

    def run(self, segments):
        try:
            for n, seg in enumerate(segments, 1):
                what, value = seg[0], seg[1]
                ctx = seg[2] if len(seg) > 2 else {}
                self.item = ctx.get("item")
                self.tag = "[{}] {} {}".format(n, what, value).rstrip()
                getattr(self, "seg_" + what)(value, ctx)
        except Stop:
            pass

    def fail(self, kind, msg):
        self.problems.append((kind, "{}: {}".format(self.tag, msg)))
        raise Stop

    # ---- 居る画面と履歴を動かす ----

    def forward(self, step, dest):
        """進むステップを積み、dest に移る。"""
        self.steps.append(dict(step, item=self.item))
        self.stack.append(dest)
        self.at = dest

    def back(self, action):
        """戻る操作を積み、履歴で1つ前の画面に戻る。"""
        if len(self.stack) < 2:
            # 起点以外の画面には必ず歩いて入っているので、履歴が尽きるのは
            # 起点に居るときだけ。起動直後の画面に戻る先は無い
            self.fail("map", "起点 {} に戻る操作「{}」が書いてある。"
                             "起動直後の画面から戻る先は無い".format(self.at, action.label()))
        dest = self.stack[-2]
        via = next(e.get("via") for e in action.expects if e.get("screen") == "back")
        self.steps.append({"screen": self.at, "action": action, "to": dest, "via": via, "item": self.item})
        self.stack.pop()
        self.at = dest
        return self.steps[-1]

    # ---- セグメントの種類ごと ----

    def seg_shot(self, value, ctx):
        self.steps.append({"shot": value, "item": self.item})

    def seg_restart(self, value, ctx):
        # 起動し直すので、居る場所も歩いた履歴も捨てて起点に戻る
        self.at = self.mp.start
        self.stack = [self.at]
        self.steps.append({"restart": True})

    def seg_await(self, value, ctx):
        if self.at == value:
            return                  # もう出ている（前の項目がそこで終わった）
        # 閉じずに出るまで待つ。閉じる操作（screen: back）は履歴で被さる先に戻る
        self.forward({"screen": self.at, "await": True, "to": value}, value)

    def seg_do(self, value, ctx):
        mp, at = self.mp, self.at
        found, el, val = resolve(mp, at, value)
        op = value.partition(":")[0]
        if op == "see" and el is not None:
            self.steps.append({"screen": at, "see": el, "value": val, "item": self.item})
            return
        if found is None:
            self.fail("call", "{} に「{}」という操作がマップに無い。この画面にあるのは {}"
                              .format(at, value, known_ops(mp, at) or "（無し）"))
        if not found.in_tree():
            self.fail("map", "{} の「{}」は in_tree: false（座標が要る）。フローでは押せない"
                             .format(at, found.label()))
        # 値の決め方（打つ文字、どの行を押すか）はテストケースが do に添えたもの
        extra = {k: ctx[k] for k in ("input", "runtime", "pick") if k in ctx}
        extra["value"] = val
        if found.is_back():
            self.back(found).update(extra)
            return
        st = dict({"screen": at, "action": found}, **extra)
        outcome = found.expects
        if found.branches:
            st["branch"] = self.branch_of(found, ctx.get("when") or ())
            outcome = [found.branches[st["branch"]]]
        dest = next((e for e in outcome if e.get("screen") and e.get("via") in FORWARD), None)
        if dest is None:
            self.steps.append(dict(st, item=self.item))
            return
        if dest["screen"] not in mp.screens:
            self.fail("map", "{} の「{}」の遷移先 {} のファイルが無い"
                             .format(at, found.label(), dest["screen"]))
        self.forward(dict(st, to=dest["screen"], via=dest["via"]), dest["screen"])

    def branch_of(self, action, given):
        """結果が状態で分かれる操作の、確認項目の前提（when）に合う枝の番号。"""
        hit = [i for i, b in enumerate(action.branches) if b.get("when") in given]
        if len(hit) != 1:
            self.fail("call", "{} の「{}」は結果が分かれる（{}）。項目の when にどちらかを書く"
                              .format(self.at, action.label(),
                                      " / ".join(b.get("when") for b in action.branches)))
        return hit[0]

    def seg_goto(self, value, ctx):
        mp = self.mp
        if ctx.get("for_await") and self.at == ctx["for_await"]:
            return                  # 待つ画面にもう居る。被さる先へ戻ると閉じてしまう
        if value not in mp.screens:
            self.fail("map", "画面 {} がマップに無い。screens/{}.yaml を作る必要がある".format(value, value))
        if value == self.at:
            return
        given = ctx.get("when") or ()
        best = self.best_route(value, given)
        if best is None:
            self.explain_unreachable(value, given)
        pops, hops = best
        for _ in range(pops):
            a = mp.back_action(self.at)
            if a is None:
                self.fail("map", "{} に戻る操作（screen: back）がマップに無いので、{} へ向かえない"
                                 .format(self.at, value))
            self.back(a)
        for sid, (a, bi, dest, via, _) in hops:
            st = {"screen": sid, "action": a, "to": dest, "via": via}
            if bi is not None:
                st["branch"] = bi
            self.forward(st, dest)

    def best_route(self, goal, given):
        """(戻る回数, そこから進む辺の並び)。いちばん短いもの。届かなければ None。

        いま居る画面から前へ辿れるか、履歴を一段ずつ戻りながら探す。戻る回数と進む回数の
        合計がいちばん小さいものを採る（同点なら戻らない方）。**「行き先が積まれているか」で
        場合分けしない。** 戻ってから進む（一覧へ戻って別のタブへ、など）が普通に要るので、
        同じ探索に収める。
        """
        best = None
        for pops in range(len(self.stack)):
            hops = self.mp.path_from(self.stack[len(self.stack) - 1 - pops], goal, given)
            if hops is not None and (best is None or pops + len(hops) < best[0] + len(best[1])):
                best = (pops, hops)
        return best

    def explain_unreachable(self, goal, given):
        """届かない理由を言って止まる。

        **戻る辺は「切れている」に数えない** — 戻るのはここが自分でやることなので、
        それを欠陥として報告すると、直しようのないものをマップの穴として挙げてしまう。
        """
        mp, relaxed = self.mp, None
        for pops in range(len(self.stack)):
            relaxed = mp.path_from(self.stack[len(self.stack) - 1 - pops], goal, given, relaxed=True)
            if relaxed is not None:
                break
        if relaxed is None:
            self.fail("map", "{} から {} へ行ける操作がマップに無い".format(self.at, goal))
        broken = [(sid, e, mp.blocked(e, given)) for sid, e in relaxed if mp.blocked(e, given)]
        if not broken:
            self.fail("call", "{} から {} へは、途中で戻ってからまた進む経路になる。"
                              "1つの goto では辿れないので、折り返す画面への goto を挟んで区間を分ける"
                              .format(self.at, goal))
        for sid, e, why in broken:
            kind = "call" if why.startswith("条件つき") else "map"
            hint = "。その前提で確かめるなら、項目の when に同じ文言を書く" if kind == "call" else ""
            self.problems.append((kind, "{}: {} の「{}」で切れる: {}{}"
                                  .format(self.tag, sid, e[0].label(), why, hint)))
        raise Stop


def check_values(steps, problems):
    """値の決め方（打つ文字、どの行を押すか）を確かめる。**呼び方の間違いは走らせる前に止める。**

    パターンの要素（ID の末尾が *）を押すステップには `pick` を付ける（空なら、画面に
    見えている1件目）。どれを押すかは走らせるときに決まる。
    """
    for st in steps:
        a = st.get("action")
        if a is None:
            continue
        where = "({}) ".format(st["item"]) if st.get("item") else ""
        pattern = a.element is not None and is_pattern(a.element.get("id")) and st.get("value") is None
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
        elif pattern:
            st["pick"] = st.get("pick") or ""
