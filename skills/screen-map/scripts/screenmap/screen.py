"""1つの画面（screens/<画面>.yaml）を読む。

画面の要素と操作、その画面から進める先、戻る操作、自動で出ることがある画面を引けるようにする。
マップの書き方は screen-map スキルの reference/schema.md。
"""


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


class Screen:
    """1つの画面。screens/<画面>.yaml を読んだもの。"""

    def __init__(self, sid, raw):
        self.id = sid
        self.raw = raw                      # yaml を読んだまま（check.py が形を確かめる）
        r = raw if isinstance(raw, dict) else {}
        self.anchor = r.get("anchor")       # この画面に居ることを確かめる ID
        self.names = r.get("names") or []   # 呼び名
        self.summary = r.get("summary")     # 何の画面で何ができるか
        self.files = r.get("files") or []   # この画面の主なソース
        self.stub = bool(r.get("stub"))     # まだ書いていない画面（操作は網羅ではない）
        self.ready = r.get("ready")         # 読み込み完了の目印（{any: [...]} / {all: [...]}）
        self.elements = [e for e in r.get("elements") or [] if isinstance(e, dict)]
        # 操作（要素のアクション → gestures の順）
        self.actions = ([ActionSpec(sid, a, el) for el in self.elements for a in el.get("actions") or []]
                        + [ActionSpec(sid, g) for g in r.get("gestures") or []])
        self._auto_shows = r.get("auto_shows") or []

    def element(self, eid):
        """(要素, 実行時の値)。パターンの要素（`list.row.*`）に具体的な ID
        （`list.row.牛乳`）で当たれば、値に `牛乳` を返す。無ければ (None, None)。"""
        for el in self.elements:
            if el.get("id") == eid:
                return el, None
        for el in self.elements:
            p = str(el.get("id") or "")
            if is_pattern(p) and eid.startswith(pattern_prefix(p)) and len(eid) > len(p) - 1:
                return el, eid[len(p) - 1:]
        return None, None

    def edges(self):
        """この画面から進む辺。[(操作, 分岐の番号, 行き先, via, 条件の並び)]。

        条件は要素の `when` と分岐の `when`。**条件つきの辺は、確認項目の前提
        （plan の `when`）に同じ文言があるときだけ往路に使う。** 出るかどうか、
        どちらに着くかが状態で決まるので、前提が無いまま通ると着く先が読めない。
        """
        out = []
        for a in self.actions:
            if a.op not in OPS:
                continue
            for bi, bwhen, exps in a.outcomes():
                for e in exps:
                    if e.get("screen") and e.get("screen") != "back" and e.get("via") in FORWARD:
                        conds = [c for c in (a.when(), bwhen) if c]
                        out.append((a, bi, e["screen"], e["via"], conds))
        return out

    def back_action(self):
        """この画面の「戻る」操作。無ければ None。戻り先は歩いた履歴で決まる。"""
        for a in self.actions:
            if a.op in OPS and a.is_back() and a.in_tree():
                return a
        return None

    def auto_items(self):
        """この画面の `auto_shows` を [(画面id, after の並び)] で。after が空なら入ったときに確かめる。"""
        out = []
        for it in self._auto_shows:
            if isinstance(it, dict):
                after = it.get("after") or []
                out.append((it.get("screen"), after if isinstance(after, list) else [after]))
            else:
                out.append((it, []))
        return out

    def auto_shows(self):
        return [i for i, _ in self.auto_items()]

    def auto_close(self):
        """この画面が自動表示として出たときの (anchor, 閉じる操作)。どちらか無ければ None。

        自動表示（レビュー依頼、お知らせなど、こちらの操作と関係なく被さる画面）も
        **画面として書く**（`screens/<id>.yaml`）。anchor が「出ているか」の目印、
        `screen: back` の操作が閉じ方。命名も ID の振り方も実測も画面と同じルールで効く。
        """
        close = self.back_action()
        if not self.anchor or close is None:
            return None
        return self.anchor, close


class ActionSpec:
    """画面の1つの操作（その画面の要素の tap / text か、画面の gestures の scroll）。

    画面の yaml（screens/<画面>.yaml）を読んだときに1つだけ作る。何をすると何が起きるか
    （expect）は、書いてあるままで持つ。
    `when` つきのリストなら結果が状態で分かれる（branches）。ステップが持つ操作と結果は、
    ここから actions.py と results.py が作る。
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
