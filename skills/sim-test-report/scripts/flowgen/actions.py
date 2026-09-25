"""ステップでする操作（Tap / Input / InputLater / Scroll）。"""
from dataclasses import dataclass
from typing import Optional, Union

from screenmap.screen import ActionSpec, is_pattern, pattern_prefix


@dataclass
class Pick:
    """パターンの要素（`list.row.*`）のどれを押すかを、走らせるときに選ぶ。

    `condition` が空なら画面に見えている1件目（run_flows.py が選ぶ）。条件があれば、止めて
    LLM がダンプと条件から選ぶ。
    """
    condition: str = ""


@dataclass
class Tap:
    """要素を押す。"""
    target: str                        # 要素の id。ID まで書いた操作（tap:list.row.牛乳）は、その ID
    pick: Optional[Pick] = None        # パターンの要素のどれを押すか
    by_label: bool = False             # id ではなく、ラベルで指す要素
    summary: Optional[str] = None      # 何をする操作か（フローのコメントになる）

    def label(self):
        return "tap {}{}".format(self.target, " [ラベル]" if self.by_label else "")


@dataclass
class Input:
    """入力欄に、決まった文字を打つ。"""
    target: str
    text: str
    by_label: bool = False
    summary: Optional[str] = None

    def label(self):
        return "text {}{}".format(self.target, " [ラベル]" if self.by_label else "")


@dataclass
class InputLater:
    """入力欄に打つ文字を、走らせるときに決める（止めて LLM が決める）。"""
    target: str
    by_label: bool = False
    summary: Optional[str] = None

    def label(self):
        return "text {}{}".format(self.target, " [ラベル]" if self.by_label else "")


@dataclass
class Scroll:
    """画面をスクロールする（画面の gestures）。要素は持たない。"""
    direction: str                     # down / up
    summary: Optional[str] = None

    def label(self):
        return "scroll {}".format(self.direction)


Action = Union[Tap, Input, InputLater, Scroll]


def resolve_action(spec: ActionSpec, target: Optional[str] = None, how: Optional[dict] = None):
    """画面の操作（ActionSpec）を、このステップでする操作の型にする。(操作, 呼び方の間違いの文の並び)。

    `target` はパターンの要素を ID まで書いたとき（`tap:list.row.牛乳`）の、その ID。
    `how` はテストケースが添えた値の決め方（`input` / `runtime` / `pick`）。

    **呼び方の間違いは、ここで返して走らせる前に止める。** パターンの要素を押すのに `pick` が
    無ければ、画面に見えている1件目を選ぶ（`Pick()`）。
    """
    how = how or {}
    target = target or spec.target
    by_label = spec.element is not None and spec.element.get("by") == "label"
    label = "{} {}{}".format(spec.op, target, " [ラベル]" if by_label else "")
    pattern = spec.element is not None and is_pattern(target)
    problems = []
    if how.get("runtime") and spec.op != "text":
        problems.append("{} に runtime は付けられない。どの行を押すかは"
                        "スクリプトが決める（条件があるなら pick に書く）".format(label))
    if "pick" in how and not pattern:
        problems.append("{} はパターンの要素（ID の末尾が *）ではないので、"
                        "実行時に選べない（pick を外す）".format(label))

    if spec.op == "scroll":
        return Scroll(spec.target, spec.summary), problems
    if spec.op == "text":
        if "input" not in how and not how.get("runtime"):
            problems.append("text {} に打つ文字が渡されていない（do に {{\"op\": …, \"input\": 値}} か "
                            "{{\"op\": …, \"runtime\": true}} で書く）。"
                            "マップは値を持たない。何を打つかはテストケースが決める".format(target))
        if pattern:
            # どれに打つかと何を打つかの2つを実行時に決めることになる。変数が1つしか持てない
            problems.append("text {} はパターンの要素なので、どの欄に打つかを ID まで書く"
                            "（text:{}<表示中の名前>）".format(target, pattern_prefix(target)))
        if how.get("runtime"):
            return InputLater(target, by_label, spec.summary), problems
        return Input(target, str(how.get("input", "")), by_label, spec.summary), problems
    pick = Pick(how["pick"]) if "pick" in how else (Pick() if pattern else None)
    return Tap(target, pick, by_label, spec.summary), problems
