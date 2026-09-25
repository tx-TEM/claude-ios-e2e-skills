"""フローの1コマ（ステップ）の型。操作の結果の型は results.py。"""
from dataclasses import dataclass, field
from typing import List, Optional

from .model import Action
from .results import Arrive, Result


# ---------- ステップ ----------

@dataclass
class Act:
    """`screen` で `action` をする。すると起きることが `result`。"""
    screen: str                       # 操作する時点で居る画面
    action: Action                    # する操作（tap / text / scroll と、その対象の要素・summary）
    result: List[Result] = field(default_factory=list)   # action をすると起きること
    value: Optional[str] = None       # パターンの要素を ID まで決め打ちしたときの値（`tap:list.row.牛乳` の 牛乳）
    input: Optional[str] = None       # text で打つ文字（データに依らない値）
    runtime: bool = False             # text で打つ文字を実行時に決める
    pick: Optional[str] = None        # パターンの要素のどれを押すか。"" は画面に見えている1件目、文字は条件
    var: Optional[str] = None         # 実行時に決める値を入れる env の変数名（maestro.py が振る）
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
        """実行時に値を決めるか（打つ文字、パターンの要素のどれを押すか）。"""
        return self.runtime or self.pick is not None


@dataclass
class See:
    """見るだけの要素を、見えるまでスクロールして確かめる。"""
    screen: str
    element: dict
    value: Optional[str] = None       # パターンの要素を ID まで書いたときの値
    item: Optional[str] = None
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
