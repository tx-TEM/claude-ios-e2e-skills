"""フローの1コマ（ステップ）の型。する操作の型は actions.py、起きることの型は results.py。"""
from dataclasses import dataclass, field
from typing import List, Optional

from .actions import Action, InputLater, Tap
from .results import Arrive, Result


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
    """見るだけの要素を、見えるまでスクロールして確かめる。"""
    screen: str
    target: str                       # 要素の id（ID まで書いたら、その ID）
    name: Optional[str] = None        # 画面での見え方（フローのコメントになる）
    by_label: bool = False
    item: Optional[str] = None
    up: bool = False                  # 下で見つからなければ上も探すか（maestro.add_reveals が決める）
    to = None


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
