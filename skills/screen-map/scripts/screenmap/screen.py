"""画面マップ（screen-map/）を読む。画面・要素・操作と、そこから引ける辺。

マップの形は screen-map スキルの reference/schema.md。ここはそれを読んで、経路（bridge.py）・
検査（check.py）・sim-test-report のフローを作る部品（testflow/）が使う形にするだけ。
"""
import heapq
import sys
from pathlib import Path

# PyYAML を入れさせないための最小パーサ（理由は mini_yaml.py）
from .mini_yaml import load_yaml


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


class ActionSpec:
    """マップに書いた1つの操作の定義。要素のアクションか、画面の gestures の1項目。

    マップを読んだときに1つだけ作り、経路の計算と検査が読む。ステップは、ここから値を写した
    自分の Action（steps.py）を持つ（どの行を押すか、何を打つかはステップごとに違うため）。

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


class ScreenMap:
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
                    out.append(ActionSpec(sid, a, el))
            for g in (self.screens.get(sid) or {}).get("gestures") or []:
                out.append(ActionSpec(sid, g))
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


def load_map(repo):
    mp = ScreenMap(find_map(repo))
    old = old_schema(mp)
    if old:
        sys.exit("画面マップが古い形（actions / states）のまま: {}。\n"
                 "migrate_map.py --repo <アプリ> で要素中心の形に移す".format(" ".join(old)))
    return mp


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
