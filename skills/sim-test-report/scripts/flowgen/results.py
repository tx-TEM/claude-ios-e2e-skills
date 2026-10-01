"""操作をすると起きること（Arrive / Closed / Visible / Value / Selected / Hidden / External）。"""
from dataclasses import dataclass
from typing import Optional, Union

from screenmap.screen import closes_then_opens, expect_kind


@dataclass
class Arrive:
    """別の画面に着く。`via` は push / modal / tab / back / dismiss（着いたときの自動表示の確かめ方が変わる）。"""
    screen: str
    via: str


@dataclass
class Closed:
    """居た画面が閉じる。そのあと別の画面に進む操作（メニューの選択肢でダイアログが出る、など）で、
    進む先の Arrive と一緒に出る。閉じた画面の anchor が消えたことで確かめる。

    戻った先の画面は確かめない。進んだ先が被さって、ツリーから隠れているので（実測）。

    `to` は閉じて残る画面（歩いた履歴で決める。resolve_result の `back_to`）。アプリの外に出るとき
    （`external`）は進む先の画面が無いので、アプリに戻したらここに居るとする。
    """
    screen: str
    to: Optional[str] = None


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


Result = Union[Arrive, Closed, Visible, Value, Selected, Hidden, External]

OWN_RESULTS = {"visible": Visible, "value": Value, "selected": Selected, "hidden": Hidden}


def resolve_result(action, expects, back_to=None):
    """画面の操作に書いた `expect` の並びを、結果の型の並びに解く。

    `screen: back` は `back_to`（歩いた履歴で決めた、実際に戻る画面）に、`self` は操作した
    要素の ID にする。分かれる結果は、呼ぶ側が選んだ枝の expect だけを渡す。

    `screen: back` と進む先（か `external`）の両方があれば、居た画面が閉じてから進む
    （Closed と、進む先の Arrive か External）。
    """
    closes = closes_then_opens(expects)
    out = []
    for e in expects:
        kind = expect_kind(e)
        if kind == "screen":
            if e["screen"] != "back":
                out.append(Arrive(e["screen"], e.get("via")))
            elif closes:
                out.append(Closed(action.sid, back_to))
            else:
                out.append(Arrive(back_to, e.get("via")))
        elif kind == "external":
            out.append(External(e[kind]))
        elif kind in OWN_RESULTS:
            ref = e[kind]
            own = ref == "self"
            out.append(OWN_RESULTS[kind](action.target if own else ref, own))
    return out
