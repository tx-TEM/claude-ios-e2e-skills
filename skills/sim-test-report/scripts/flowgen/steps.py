"""フローの1コマ（ステップ）の型。する操作の型は actions.py、起きることの型は results.py。"""
from dataclasses import dataclass, field
from typing import List, Optional

from .actions import Action, InputLater, Tap
from .results import Arrive, External, Result


# ---------- ステップ ----------

@dataclass
class Act:
    """`screen` で `action` をする。すると起きることが `result`。"""
    screen: str                       # 操作する時点で居る画面
    action: Action                    # する操作（Tap / Input / InputLater / Scroll。このステップ専用）
    result: List[Result] = field(default_factory=list)   # action をすると起きること
    item: Optional[str] = None

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
