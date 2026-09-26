"""フローの1コマ（ステップ）の型。する操作の型は actions.py、起きることの型は results.py。"""
from dataclasses import dataclass, field
from typing import List, Optional

from screenmap.screen import is_pattern, pattern_prefix

from .actions import Action, InputLater, Pick, Tap
from .results import Arrive, External, Result


# ---------- 親の中で操作する ----------

@dataclass
class Enter:
    """子の要素を触る前に、パターンの親（`recommend.carousel.*`）のどれの中でするかを、
    走らせるときに決める。

    **親は具体的な ID で指す必要がある。** Maestro の `childOf` に正規表現の親を渡すと、
    最初に当たった親の中しか探さない（実測。画面上端で半分隠れたカルーセルだけを見て、
    その下のカルーセルの中を探さなかった）。条件が無ければ run_flows.py が画面に
    見えている1件目を選ぶ。条件があれば止めて LLM が選ぶ（パターンの要素を押すときと同じ）。
    """
    screen: str
    target: str                       # 親のパターンの id
    pick: Pick = field(default_factory=Pick)
    outer: list = field(default_factory=list)   # この親の外側の親（Within の並び）
    item: Optional[str] = None
    to = None

    def needs_value(self):
        return True


@dataclass
class Within:
    """子の要素を操作・確認するときの、親1つ。子のステップは外から順に並べて持つ。

    親の中の要素は `childOf` で指す。親に `scroll` があれば、子が見えるまでその親を
    送る（`swipe: from: 親`）。
    """
    id: str                           # マップの親の id（パターンなら * で終わる）
    scroll: Optional[str] = None      # horizontal。無ければ範囲を絞るだけ
    value: Optional[str] = None       # 親の具体的な ID（plan の in か、パターンでない親）
    enter: Optional[Enter] = None     # 走らせるときに決めるなら、それを決めるステップ

    def label(self):
        if self.value is not None:
            return self.value
        return "{}（{}）".format(self.id, self.enter.pick.condition or "見えている1件目")


def nest(screen, parents, given=None, label=""):
    """子の要素の親の並び（マップの要素、外から順）を、(Enter の並び, Within の並び, 呼び方の間違い) にする。

    `given` は plan の `in`。パターンの親ごとに具体的な ID を外から順に並べたもの（list）か、
    いちばん内側のパターンの親を選ぶ条件（`{"pick": 条件}`）。無ければ、パターンの親は
    どれも画面に見えている1件目（Enter で走らせるときに決める）。
    """
    patterns = [p for p in parents if is_pattern(str(p.get("id")))]
    problems, values, cond = [], [None] * len(patterns), ""
    if isinstance(given, dict):
        cond = given["pick"]
        if not patterns:
            problems.append("{} はパターンの親の中に無いので、in で選ぶ親が無い".format(label))
    elif given is not None:
        if len(given) != len(patterns):
            problems.append("{} の in はパターンの親（{}）ごとに外から順に1つずつ書く".format(
                label, " / ".join(str(p.get("id")) for p in patterns) or "無し"))
        else:
            for k, (p, v) in enumerate(zip(patterns, given)):
                pre = pattern_prefix(str(p.get("id")))
                if not (str(v).startswith(pre) and len(str(v)) > len(pre)):
                    problems.append("{} の in の {} が親 {} に当たらない".format(label, v, p.get("id")))
                values[k] = str(v)
    enters, within, k = [], [], 0
    for p in parents:
        pid = str(p.get("id"))
        w = Within(pid, p.get("scroll"))
        if is_pattern(pid):
            w.value = values[k]
            last = k == len(patterns) - 1
            k += 1
            if w.value is None:
                w.enter = Enter(screen, pid, Pick(cond if last else ""), list(within))
                enters.append(w.enter)
        else:
            w.value = pid
        within.append(w)
    return enters, within, problems


# ---------- ステップ ----------

@dataclass
class Act:
    """`screen` で `action` をする。すると起きることが `result`。"""
    screen: str                       # 操作する時点で居る画面
    action: Action                    # する操作（Tap / Input / InputLater / Scroll。このステップ専用）
    result: List[Result] = field(default_factory=list)   # action をすると起きること
    item: Optional[str] = None
    within: List[Within] = field(default_factory=list)   # 子の要素なら、その親（外から順）

    @property
    def arrive(self):
        """別の画面に着くなら、その Arrive。着かなければ None。"""
        return next((r for r in self.result if isinstance(r, Arrive)), None)

    @property
    def to(self):
        """着く画面。着かなければ None（ほかのステップの `to` と同じ意味）。"""
        return self.arrive.screen if self.arrive else None

    def needs_value(self):
        """走らせるときに値を決めるか（パターンの要素のどれに操作するか、打つ文字）。"""
        a = self.action
        return (isinstance(a, Tap) and a.pick is not None) or isinstance(a, InputLater)


@dataclass
class See:
    """見るだけの要素を、見えるまでスクロールして確かめる。

    `text` なら、マップの要素ではなく画面の文言（`see:text:<文言>`）を、スクロールせずに待つ。
    アプリの外（Safari など）はマップに無いので、ID の代わりに文言で待つしかない。

    パターンの要素（`list.row.*`）には、その語を含む行を待つ語を添えられる。`contains` は
    plan に書いた語（`input`）、`later` は撮るときに決める語（`runtime`）。
    """
    screen: str
    target: str                       # 要素の id（ID まで書いたら、その ID）。text なら文言
    name: Optional[str] = None        # 画面での見え方（フローのコメントになる）
    by_label: bool = False
    item: Optional[str] = None
    up: bool = False                  # 下で見つからなければ上も探すか（maestro.add_reveals が決める）
    text: bool = False                # マップに無い文言を待つ（see:text:<文言>）
    contains: Optional[str] = None    # この語を含む行を待つ（input）
    later: bool = False               # 含む語を撮るときに決める（runtime）
    within: List[Within] = field(default_factory=list)   # 子の要素なら、その親（外から順）
    back: bool = False                # 親を送って見つからなければ戻る向きも探すか（maestro.add_reveals が決める）
    to = None

    def needs_value(self):
        return self.later


@dataclass
class Await:
    """自動表示の画面が出るのを、閉じずに待つ（自動表示そのものを確かめる項目）。"""
    screen: str                       # 被さる先の画面
    to: str                           # 出てくる自動表示の画面
    item: Optional[str] = None


@dataclass
class Shot:
    """撮る。`name` は証跡の名前（項目の名前）。"""
    name: str
    item: Optional[str] = None
    to = None


@dataclass
class Restart:
    """アプリを起動し直す（`fresh` の項目の前）。起点に戻る。"""
    item: Optional[str] = None
    to = None


def goes_out(st):
    """アプリの外に出る操作か。"""
    return isinstance(st, Act) and any(isinstance(r, External) for r in st.result)


def waits_text(st):
    """マップに無い文言を待つステップか（see:text:<文言>）。アプリの外でも使える。"""
    return isinstance(st, See) and st.text


def stays_out(steps, i, skip=()):
    """steps[i]（アプリの外に出る操作）のあと、外に居るまま撮るか。

    撮るまでの間が、文言を待つステップ（外のページの見出しなど）だけならそう。
    `skip` は間にあっても構わないステップの型（maestro.py の Reveal）。
    """
    j = i + 1
    while j < len(steps) and (waits_text(steps[j]) or isinstance(steps[j], skip)):
        j += 1
    return j < len(steps) and isinstance(steps[j], Shot)
