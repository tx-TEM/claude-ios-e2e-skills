"""ステップ。フローの1コマ分の「何をするか」で、Maestro の書き方はまだ含まない。

積むのは bridge.py（項目と項目の間の経路）と、sim-test-report の testflow/flow.py（項目の do）。
testflow/maestro.py がこれを Maestro のフロー（yaml）に書く。

**種類ごとにクラスを分け、持つものを固定する。** どのステップが何を持つかは、ここの定義を
読めば分かる。`to` は着く画面で、画面を移らないステップは None。`item` はどの項目の
ステップか（plan の項目の名前。経路を表示するだけのときは None）。
"""
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Act(object):
    """操作する（tap / text / scroll）。`screen` で `action` を行い、移るなら `to` に着く。"""
    screen: str                       # 操作する時点で居る画面
    action: Any                       # マップの操作（model.Action）
    to: Optional[str] = None          # 着く画面。進むなら expect の screen、戻るなら歩いた履歴で決めた画面
    via: Optional[str] = None         # push / modal / tab / back / dismiss。着いたときの自動表示の確かめ方が変わる
    branch: Optional[int] = None      # 結果が分かれる操作で、項目の when で選んだ枝
    value: Optional[str] = None       # パターンの要素を ID まで決め打ちしたときの値（`tap:list.row.牛乳` の 牛乳）
    input: Optional[str] = None       # text で打つ文字（データに依らない値）
    runtime: bool = False             # text で打つ文字を実行時に決める
    pick: Optional[str] = None        # パターンの要素のどれを押すか。"" は画面に見えている1件目、文字は条件
    var: Optional[str] = None         # 実行時に決める値を入れる env の変数名（maestro.py が振る）
    item: Optional[str] = None

    def outcome(self):
        """確かめる expect の並び。結果が分かれる操作は、選んだ枝だけ。"""
        a = self.action
        if a.branches:
            return [a.branches[self.branch]] if self.branch is not None else []
        return a.expects

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
