"""フローの1コマ（ステップ）と、操作の結果の型。"""
from dataclasses import dataclass, field
from typing import List, Optional, Union

from .model import Action


# ---------- 操作の結果（Act.result の1項目） ----------

@dataclass
class Arrive:
    """別の画面に着く。`via` は push / modal / tab / back / dismiss（着いたときの自動表示の確かめ方が変わる）。"""
    screen: str
    via: str


@dataclass
class Visible:
    """要素が出る。`own` は操作した要素そのもの（パターンの要素なら操作した1つ）。"""
    id: str
    own: bool = False


@dataclass
class Value:
    """要素の値が変わる。確かめられるのは要素が見えることまでで、値そのものは証跡で見る。"""
    id: str
    own: bool = False


@dataclass
class Selected:
    """要素が選択状態になる。"""
    id: str
    own: bool = False


@dataclass
class Hidden:
    """要素が消える。"""
    id: str
    own: bool = False


@dataclass
class External:
    """アプリの外（Safari、App Store など）に出る。確かめずにアプリに戻す。"""
    name: str


Result = Union[Arrive, Visible, Value, Selected, Hidden, External]


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
