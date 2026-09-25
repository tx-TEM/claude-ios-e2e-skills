"""ステップ。フローの1コマ分の「何をして、何が起きるか」で、Maestro の書き方はまだ含まない。

積むのは bridge.py（項目と項目の間の経路）と、sim-test-report の testflow/flow.py（項目の do）。
testflow/maestro.py がこれを Maestro のフロー（yaml）に書く。

**種類ごとにクラスを分け、持つものを固定する。** どのステップが何を持つかは、ここの定義を
読めば分かる。`item` はどの項目のステップか（plan の項目の名前。経路を表示するだけのときは None）。

**操作（Act）は「何を押すか」と「押すと何が起きるか（result）」を分けて持つ。** 起きることは
ステップを作る時点で解ききる — 戻る操作は実際に戻る画面に、分かれる結果は選んだ枝に、
`self` は押す要素の ID にする。マップに書いた `expect` をあとから読み直さない。
"""
from dataclasses import dataclass, field
from typing import Any, List, Optional


# ---------- 操作の結果（Act.result の1項目） ----------

@dataclass
class Arrive(object):
    """別の画面に着く。`via` は push / modal / tab / back / dismiss（着いたときの自動表示の確かめ方が変わる）。"""
    screen: str
    via: str


@dataclass
class Visible(object):
    """要素が出る。`own` は押した要素そのもの（パターンの要素なら押した1つ）。"""
    id: str
    own: bool = False


@dataclass
class Value(object):
    """要素の値が変わる。確かめられるのは要素が見えることまでで、値そのものは証跡で見る。"""
    id: str
    own: bool = False


@dataclass
class Selected(object):
    """要素が選択状態になる。"""
    id: str
    own: bool = False


@dataclass
class Hidden(object):
    """要素が消える。"""
    id: str
    own: bool = False


@dataclass
class External(object):
    """アプリの外（Safari、App Store など）に出る。確かめずにアプリに戻す。"""
    name: str


# ---------- ステップ ----------

@dataclass
class Act(object):
    """`screen` で操作する（tap / text / scroll）。起きることは `result`。"""
    screen: str                       # 操作する時点で居る画面
    action: Any                       # 何を押すか（マップの操作 model.Action。要素・種類・summary）
    result: List[Any] = field(default_factory=list)   # 押すと起きること（Arrive / Visible / ...）
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
class See(object):
    """見るだけの要素を、見えるまでスクロールして確かめる。"""
    screen: str
    element: dict
    value: Optional[str] = None       # パターンの要素を ID まで書いたときの値
    item: Optional[str] = None
    to = None


@dataclass
class Await(object):
    """自動表示の画面が出るのを、閉じずに待つ（自動表示そのものを確かめる項目）。"""
    screen: str                       # 被さる先の画面
    to: str                           # 出てくる自動表示の画面
    item: Optional[str] = None


@dataclass
class Shot(object):
    """撮る。`name` は証跡の名前（項目の名前）。"""
    name: str
    item: Optional[str] = None
    to = None


@dataclass
class Restart(object):
    """アプリを起動し直す（`fresh` の項目の前）。起点に戻る。"""
    item: Optional[str] = None
    to = None
