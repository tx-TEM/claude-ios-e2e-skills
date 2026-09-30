"""操作をすると起きること（Arrive / Visible / Value / Selected / Hidden / External）。"""
from dataclasses import dataclass
from typing import Union

from screenmap.screen import expect_kind


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


# 外のアプリが読み込みを終えたことを表す要素。外に居るまま撮るとき、この順に待つ。
# (見せる名前, Maestro の id の正規表現)。
#
# Safari は自分の状態をアクセシビリティ ID に出している。IsPageLoaded=true は Safari の
# 起動直後（読み込みが始まる前）や読み込みの途中でも出るので、それだけでは早い。停止ボタンが
# 更新ボタン（ReloadButton）に変わるのは、実測（iOS 18.3 / 26.5 / 27.0、iPhone と iPad）では
# どれも読み込みが終わってからだった。読み込みに失敗してもエラーページで両方が出るので、
# 待ちは落ちずにエラーページが撮れる。公開された仕様ではないので、OS を上げたら確かめ直す
LOADED = {
    "safari": (("TabDocument?…IsPageLoaded=true", r"^TabDocument\?.*IsPageLoaded=true.*"),
               ("ReloadButton", r"^ReloadButton$")),
}


@dataclass
class External:
    """アプリの外（Safari、App Store など）に出る。すぐ後で撮るなら外に居るまま撮り、そうでなければアプリに戻す（maestro.py の FlowWriter.check）。

    外に居るまま撮るとき、読み込みの終わりが分かるアプリ（`LOADED`）ならそれを待つ。ほかは確かめない。
    """
    name: str

    @property
    def loaded(self):
        """読み込みが終わったことを表す要素（見せる名前, 正規表現）の並び。分からなければ空。"""
        return LOADED.get(self.name, ())


Result = Union[Arrive, Visible, Value, Selected, Hidden, External]

OWN_RESULTS = {"visible": Visible, "value": Value, "selected": Selected, "hidden": Hidden}


def resolve_result(action, expects, back_to=None):
    """画面の操作に書いた `expect` の並びを、結果の型の並びに解く。

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
