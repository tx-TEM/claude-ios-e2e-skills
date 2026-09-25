"""操作をすると起きることを、マップから読み解く。

マップの expect を、Arrive / Visible などの型に変換する（resolve_result）。
"""
from dataclasses import dataclass
from typing import Union

from .screen import expect_kind


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

OWN_RESULTS = {"visible": Visible, "value": Value, "selected": Selected, "hidden": Hidden}


def resolve_result(action, expects, back_to=None):
    """マップに書いた `expect` の並びを、結果の型の並びに解く。

    `screen: back` は `back_to`（歩いた履歴で決めた、実際に戻る画面）に、`self` は操作した
    要素の ID にする。分かれる結果は、呼ぶ側が選んだ枝の expect だけを渡す。
    """
    out = []
    for e in expects:
        kind = expect_kind(e)
        if kind == "screen":
            out.append(Arrive(back_to if e["screen"] == "back" else e["screen"], e.get("via")))
        elif kind == "external":
            out.append(External(e[kind]))
        elif kind in OWN_RESULTS:
            ref = e[kind]
            own = ref == "self"
            out.append(OWN_RESULTS[kind](action.target if own else ref, own))
    return out
