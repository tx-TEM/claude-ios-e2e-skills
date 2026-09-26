"""画面から画面への経路。テストケースの項目と項目の間を繋ぐ（橋渡し）。

flow.py が plan の項目を順にフローにするとき、前の項目が終わった画面から次の項目の
`from` まで（最初は起動直後の画面から）をここで引く。項目の中の `do` は flow.py が扱う。

経路はマップの `expect: {screen, via}` を辺にした最短路（screen-map の map.py の `path_from`）。
ここはそれに、**歩いてきた履歴**（戻る操作の戻り先を決める）と、**自動表示の画面への
行き方**（被さる先まで行って、閉じずに待つ）を足す。

**組めなかったことを理由つきで返すのが半分の仕事。** 組めない経路は
マップが持っている穴（`in_tree: false`、未マップの画面、`stub` の先）が
そのまま出たもの。ここで推測して繋がない。埋めるのは screen-map の仕事。
条件つきの要素や枝（マップの `when`）も、前提に同じ文言が無ければ通さず、
「条件つき」と理由にする。
"""
import os
import sys

from .results import External, Hidden, Selected, Value, Visible, resolve_result
from .actions import Input, Tap, resolve_action
from .steps import Act, Await, Enter, Restart, See, Shot, nest, stays_out


class Unroutable(Exception):
    """組めなかった。`problems` は [(種類, 理由)]。

    種類は "map" と "call" に分ける。**直す先が違う。** 前者はマップの穴で screen-map の
    仕事、後者は呼び方の間違いで、値を渡すか行き先を選び直せば済む。
    """

    def __init__(self, problems):
        Exception.__init__(self, problems)
        self.problems = problems


class Route:
    """居る画面（`at`）と、歩いてきた履歴（`stack`）を持って、画面から画面へ繋ぐ。

    **戻る操作（`screen: back`）の戻り先はこの履歴で決める。** どこから来たかで変わる
    （お気に入りから詳細に入ったなら、戻る先は一覧ではなくお気に入り）ので、マップには
    書かない。行き先が履歴に積まれていれば、戻ってから進む経路も同じ探索で比べる。

    flow.py も、項目の `do` で画面を移るときに `forward()` / `back()` を使う。
    居る画面と履歴は1つだけなので、ここに持たせる。
    """

    def __init__(self, mp, start=None):
        self.mp = mp
        self.at = start or mp.start
        self.stack = [self.at]
        self.notes = []

    def fail(self, kind, msg):
        raise Unroutable([(kind, msg)])

    # ---- 居る画面と履歴を動かす ----

    def forward(self, step, dest):
        """進むステップ。dest に移る。"""
        self.stack.append(dest)
        self.at = dest
        return step

    def back(self, spec):
        """戻る操作（マップの定義 spec）のステップ。履歴で1つ前の画面に戻る。"""
        if len(self.stack) < 2:
            # 起点以外の画面には必ず歩いて入っているので、履歴が尽きるのは
            # 起点に居るときだけ。起動直後の画面に戻る先は無い
            self.fail("map", "起点 {} に戻る操作「{}」が書いてある。"
                             "起動直後の画面から戻る先は無い".format(self.at, spec.label()))
        dest = self.stack[-2]
        step = Act(self.at, self.action_of(spec), resolve_result(spec, spec.expects, back_to=dest))
        self.stack.pop()
        self.at = dest
        return step

    def action_of(self, spec):
        """経路の途中でする操作。テストケースは値を添えないので、足りなければ組めない。"""
        action, problems = resolve_action(spec)
        if problems:
            raise Unroutable([("call", m) for m in problems])
        return action

    def restart(self):
        """起動し直す。居る場所も歩いた履歴も捨てて起点に戻る。"""
        self.at = self.mp.start
        self.stack = [self.at]
        return Restart()

    # ---- 繋ぐ ----

    def to(self, goal, given=()):
        """いま居る画面から goal までのステップ列。`given` は項目の前提（when）。

        **自動表示の画面（どこかの `auto_shows` に並んでいる画面）は遷移で入らない**ので、
        `expect` の辺では辿れない。被さる先へ行き、閉じずに出るまで待つ（`await`）。
        `after` つきなら、after の画面へ行って戻ってから待つ。
        """
        mp = self.mp
        if goal not in mp.screens:
            self.fail("map", "画面 {} がマップに無い。screens/{}.yaml を作る必要がある".format(goal, goal))
        hosts = mp.auto_hosts(goal)
        if not hosts:
            return self.walk_to(goal, given)
        if self.at == goal:
            return []               # もう出ている（前の項目がそこで終わった）
        host, after = self.pick_host(goal, hosts, given)
        steps = self.walk_to(host, given)
        if after:
            steps += self.walk_to(after[0], given) + self.walk_to(host, given)
        steps.append(self.forward(Await(self.at, goal), goal))
        return steps

    def pick_host(self, goal, hosts, given):
        """自動表示を待つ画面。入ったときに出るもの（戻る往復が要らない）、次に起点から近いもの。"""
        mp = self.mp

        def dist(h):
            if h == mp.start:
                return 0
            p = mp.path_from(mp.start, h, given)
            return None if p is None else len(p)
        cands = [(0 if not after else 1, dist(h), h, after) for h, after in hosts if dist(h) is not None]
        _, _, host, after = min(cands) if cands else (0, 0, hosts[0][0], hosts[0][1])
        if len(hosts) > 1:
            self.notes.append("{} は {} で自動表示される。{} で待つ".format(
                goal, " / ".join(sorted(set(h for h, _ in hosts))), host))
        return host, after

    def walk_to(self, goal, given):
        """ふつうの画面への経路。必要なら履歴を戻ってから進む。"""
        mp = self.mp
        if goal not in mp.screens:
            self.fail("map", "画面 {} がマップに無い。screens/{}.yaml を作る必要がある".format(goal, goal))
        if goal == self.at:
            return []
        best = self.best_route(goal, given)
        if best is None:
            self.explain_unreachable(goal, given)
        pops, hops = best
        steps = []
        for _ in range(pops):
            a = mp.screens[self.at].back_action()
            if a is None:
                self.fail("map", "{} に戻る操作（screen: back）がマップに無いので、{} へ向かえない"
                                 .format(self.at, goal))
            steps.append(self.back(a))
        for sid, (a, bi, dest, via, _) in hops:
            expects = [a.branches[bi]] if bi is not None else a.expects
            # 子の要素なら、パターンの親は見えている1件目（どれを選ぶかを気にするなら do に書く）
            enters, within, _ = nest(sid, a.parents)
            steps += enters
            steps.append(self.forward(Act(sid, self.action_of(a), resolve_result(a, expects),
                                          within=within), dest))
        return steps

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
                              "一度には繋げないので、折り返す画面を from にした項目を前に置く"
                              .format(self.at, goal))
        out = []
        for sid, e, why in broken:
            kind = "call" if why.startswith("条件つき") else "map"
            hint = "。その前提で確かめるなら、項目の when に同じ文言を書く" if kind == "call" else ""
            out.append((kind, "{} の「{}」で切れる: {}{}".format(sid, e[0].label(), why, hint)))
        raise Unroutable(out)


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
        if isinstance(st, Restart):
            at = mp.start
            chain.append("（起動し直す）")
            chain.append(at)
        elif st.to:
            at = st.to
            chain.append(at)
    out = [" → ".join(chain), ""]

    w = max([len(st.screen) for st in steps if isinstance(st, (Act, See, Await))] + [len(at), 4])
    rows, checked, unchecked = [], 0, 0

    def row(left, right):
        nonlocal checked, unchecked
        rows.append((left, right))
        if right:
            checked += right.startswith("✓")
            unchecked += right.startswith("—")

    start_anchor = mp.anchor(chain[0])
    row("  {}  {}".format(chain[0].ljust(w), "起点" if chain[0] == mp.start else "続き"),
        "✓ {} が出ている".format(start_anchor) if start_anchor else "— anchor が無い")

    for n, st in enumerate(steps):
        if isinstance(st, Restart):
            a = mp.anchor(mp.start)
            row("  {}  アプリを起動し直す".format("".ljust(w)), None)
            row("  {}  起点".format(mp.start.ljust(w)),
                "✓ {} が出ている".format(a) if a else "— anchor が無い")
            continue
        if isinstance(st, Shot):
            # 撮影行は右カラムを持たないので、桁揃えの計算から外す
            row("  {}  撮影 {}".format("".ljust(w), os.path.basename(st.name)), None)
            continue
        if isinstance(st, Await):
            dest = mp.anchor(st.to)
            row("  {}  自動表示 {} を待つ".format(st.screen.ljust(w), st.to),
                "✓ {} が出ている".format(dest) if dest else "— {} に anchor が無い".format(st.to))
            continue
        if isinstance(st, Enter):
            row("  {}  {} のどれの中でするかを決める [{}]".format(
                st.screen.ljust(w), st.target, st.pick.condition or "見えている1件目"), None)
            continue
        if isinstance(st, See) and st.text:
            row("  {}  see text「{}」".format(st.screen.ljust(w), st.target),
                "✓ 「{}」が出ている（文言で待つ）".format(st.target))
            continue
        if isinstance(st, See):
            word = st.contains if st.contains is not None else ("撮るときに決める語" if st.later else None)
            inside = " in " + " > ".join(x.label() for x in st.within) if st.within else ""
            row("  {}  see {}{}{}".format(st.screen.ljust(w), st.target,
                                           " [「{}」を含む行]".format(word) if st.contains is not None
                                           else " [{}を含む行]".format(word) if word else "", inside),
                "✓ {} が見える".format(st.target))
            continue
        a = st.action
        extra = ' "{}"'.format(a.text) if isinstance(a, Input) else ""
        if isinstance(a, Tap) and a.pick is not None:
            extra += " [{}]".format(a.pick.condition or "見えている1件目")
        if st.within:
            extra += " in " + " > ".join(x.label() for x in st.within)
        left = "  {}  {}{}".format(st.screen.ljust(w), a.label(), extra)
        nxt = next((x for x in steps[n + 1:] if not isinstance(x, Shot)), None)
        rights = []
        if st.to and isinstance(nxt, Await):
            rights.append("（{} が被さって隠れるので、次の自動表示で確かめる）".format(st.to))
        elif st.to:
            dest = mp.anchor(st.to)
            rights.append("✓ {} に着いたことを確認".format(st.to) if dest
                          else "— {} に anchor が無い".format(st.to))
        for r in st.result:
            if isinstance(r, (Visible, Value)):
                rights.append("✓ {} が出ている{}".format(r.id, "（値は証跡で見る）" if isinstance(r, Value) else ""))
            elif isinstance(r, Selected):
                rights.append("✓ {} が選択状態".format(r.id))
            elif isinstance(r, Hidden):
                rights.append("✓ {} が消えた".format(r.id))
            elif isinstance(r, External) and stays_out(steps, n):
                rights.append("— アプリの外（{}）に出る。確かめずに、外に居るまま撮る".format(r.name))
            elif isinstance(r, External):
                rights.append("— アプリの外（{}）に出る。確かめずに戻す".format(r.name))
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


def report_problems(problems):
    """組めなかった理由を stderr に出す。"""
    print("経路を組めなかった:", file=sys.stderr)
    for _, msg in problems:
        print("  " + msg, file=sys.stderr)
    if any(kind == "map" for kind, _ in problems):
        print("\nマップの穴。埋めるのは screen-map の仕事で、"
              "ここで推測して繋がない。", file=sys.stderr)
