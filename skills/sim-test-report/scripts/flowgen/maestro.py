"""ステップを Maestro のフロー（yaml）にする。セレクタ、待ち、フローの切り方。

どのステップを積むかは flow.py（項目の do）と bridge.py（項目の間の経路）が決める。
ここはそれを Maestro のコマンドに書くだけ。

**実行時に決める値は、書くときに直接埋める**（`values`。鍵はステップの `key`、steps.py の KEYS）。
フローは run_flows.py が流す直前に1本ずつ書くので、`env` に未定のまま置く必要が無い。
"""
import os
import re
from dataclasses import dataclass
from typing import Optional

from screenmap.screen import BACKWARD, is_pattern, pattern_prefix
from .actions import HideKeyboard, Input, InputLater, Scroll, Tap
from .results import Arrive, Closed, External, Hidden, Selected, Value, Visible
from .steps import Act, Await, Enter, Restart, See, Shot, goes_out, stays_out, waits_text

from .flowyaml import Comment, Raw, render


@dataclass
class Reveal:
    """`act` の要素が全部見えるまでスクロールする。経路の、要素を操作する Act の直前に挟む（add_reveals）。

    **1手ずつ流すステップ（`key` を持つもの）には挟まない。** そちらは run_flows.py がダンプを
    読んで探し、見えてから流す（add_reveals が飛ばす）。
    """
    act: Act
    item: Optional[str] = None
    up: bool = False          # 下で見つからなければ上も探すか（add_reveals が決める）
    to = None


FRESH = ("push", "modal")   # 新しく開く画面。上端から始まる


@dataclass
class Return:
    """アプリの外から、`screen` に戻す。外に出たまま撮った次のフローの頭に置く（add_returns）。"""
    screen: str
    item: Optional[str] = None
    to = None




def add_returns(steps):
    """アプリの外に出る操作のすぐ後で撮るなら、撮ったあとに Return を挟む。(ステップ列, 外に居る画面)。

    **外に出たこと自体を確かめる項目は、外に出たまま撮る。** 外に出る操作は確かめようが
    無いので、フローはすぐアプリに戻していた。それでは項目の `do` が外に出る操作で
    終わると、証跡にアプリに戻った画面が写る。撮るまでは外に居て、戻すのは
    次のフローの頭に回す。フローは撮る地点で切れるので、Return は次のフローの
    1つ目になる。

    次の項目が起動し直す（Restart）なら挟まない。起動し直しで戻る。項目の途中で外に
    出るなら、今までどおりその場で戻す（FlowWriter.check）。

    **テストケースの最後の撮影のあとに何も無ければ、外に居るまま終わる。** そのときは
    戻す画面を2つ目に返す。次のテストケースを組むときに、その頭に Return を置く
    （flow.py の `Cursor.out`）。
    """
    out = []
    for k, st in enumerate(steps):
        out.append(st)
        prev = next((p for p in reversed(steps[:k]) if not isinstance(p, Reveal) and not waits_text(p)), None)
        nxt = steps[k + 1] if k + 1 < len(steps) else None
        if isinstance(st, Shot) and goes_out(prev) and nxt is not None and not isinstance(nxt, Restart):
            out.append(Return(prev.to or prev.screen, prev.item))
    prev = next((p for p in reversed(steps[:-1]) if not isinstance(p, Reveal) and not waits_text(p)), None)
    # 外に出た操作で居た画面が閉じたなら（メニューの選択肢）、戻すのは閉じて残る画面
    left_out = (prev.to or prev.screen) if steps and isinstance(steps[-1], Shot) and goes_out(prev) else None
    return out, left_out


class ScrollState:
    """どの画面がスクロールされているかもしれないか。

    add_reveals() が頭から追って更新する。**テストケースをまたいで持ち越す**（flow.py の
    `Cursor`）ので、テストケースごとに組んでも、前のテストケースで下までスクロールした
    画面では上も探す。

    **`unknown` は、どこまでスクロールしたか分からない**（フローが途中で落ちたあと）。
    そのときは、上端から始まると分かった画面（起動し直した、push / modal で開いた）以外を
    全部「スクロールされているかもしれない」とみなす。上も探すぶん遅いが、届かずに落ちない。
    """

    def __init__(self, unknown=False, scrolled=(), fresh=()):
        self.unknown = unknown
        self.scrolled = set(scrolled)
        self.fresh = set(fresh)         # unknown のときに、上端から始まると分かった画面

    def copy(self):
        return ScrollState(self.unknown, self.scrolled, self.fresh)

    def is_scrolled(self, sid):
        return sid in self.scrolled or (self.unknown and sid not in self.fresh)

    def mark(self, sid):
        self.scrolled.add(sid)

    def forget(self, sid):
        """上端から始まる（push / modal で開いた）。"""
        self.scrolled.discard(sid)
        self.fresh.add(sid)

    def clear(self):
        """起動し直した。どの画面も上端から。"""
        self.__init__()

    def to_json(self):
        return {"unknown": self.unknown, "scrolled": sorted(self.scrolled), "fresh": sorted(self.fresh)}

    @classmethod
    def from_json(cls, d):
        d = d or {}
        return cls(bool(d.get("unknown")), d.get("scrolled") or (), d.get("fresh") or ())


def add_reveals(steps, state=None):
    """経路の、要素を操作する Act の直前に Reveal を挟む。1手ずつ流すステップ（`key` を持つ）には
    挟まない（run_flows.py がダンプで探す）。

    **スクロールされているかもしれない画面では、上も探す（`up`）。** 押す前のスクロールは
    下向きなので、前の項目で下までスクロールしたままの画面では、上にある要素に届かない。
    どの画面がスクロールされているかは、ここでテストケースのステップを頭から追って決める。
    前のテストケースまでの分は `state`（ScrollState）で受け取り、追った結果もそこに書き戻す。

    - その画面で押す・見る・`scroll` をしたら、スクロールされているかもしれない
    - 起動し直したときと、push / modal で開いた画面は上端から始まる
    - 戻る（back / dismiss）で戻った画面とタブの先は、前の位置のまま
    """
    out, state = [], state if state is not None else ScrollState()
    for st in steps:
        if isinstance(st, Restart):
            state.clear()
        elif isinstance(st, Await):
            state.forget(st.to)
        elif isinstance(st, (Enter, See)):
            state.mark(st.screen)
        elif isinstance(st, Act):
            if st.key is None and not isinstance(st.action, (Scroll, HideKeyboard)):
                out.append(Reveal(st, st.item, state.is_scrolled(st.screen)))
            state.mark(st.screen)
            if st.arrive and st.arrive.via in FRESH:
                state.forget(st.to)
        out.append(st)
    return out


def needs_value(st):
    """実行時に値を決めるステップか。打つ文字か、パターンの要素のどれに操作するか、
    見る行が含む語（`see` の `runtime`）、パターンの親のどれの中でするか（Enter）。"""
    return isinstance(st, (Act, See, Enter)) and st.needs_value()


def picks_pattern(st):
    """パターンの要素のどれを押すかを実行時に決めるステップか（打つ文字ではなく）。"""
    return isinstance(st, Act) and isinstance(st.action, Tap) and st.action.pick is not None


def decided_by_caller(st):
    """撮影する側（LLM）が値を決めるか。見えている1件目を選ぶもの（スクリプトが選ぶ）は偽。"""
    if isinstance(st, Enter):
        return bool(st.pick.condition)
    if picks_pattern(st):
        return bool(st.action.pick.condition)
    return needs_value(st)


def caller_keys(steps):
    """撮影する側（LLM）が決める値の鍵の並び。マニフェストの `inputs` に空で置く。

    打つ文字（`runtime`）、見る行が含む語（`see` の `runtime`）、条件つきで選ぶ行と親（`pick`）。
    条件の無いパターンの要素は run_flows.py が見えている1件目を選ぶので入れない。
    """
    return [st.key for st in steps if getattr(st, "key", None) and decided_by_caller(st)]


def sel_id(value):
    """マップの id を Maestro のセレクタにする。

    Maestro の id は正規表現なので、そのまま渡すと `.` が任意の1文字になり、
    `browse` が `browsex` にも当たる。**当たってほしいものにだけ当てる。**
    末尾 `*` はマップのパターン記法（`browse.book_row.*`）で、前方一致の意味。
    """
    if value.endswith("*"):
        return "^" + re.escape(value[:-1]) + ".*"
    return "^" + re.escape(value) + "$"


def sel_text(value):
    # 表示文言には不可視文字が混ざることがあるので前後を緩める（sim-driver と同じ理由）
    return ".*" + re.escape(value) + ".*"


def exact(value):
    """具体的な ID（ダンプの id の欄を写したもの）のセレクタ。正規表現としてエスケープする。"""
    return "^" + re.escape(value) + "$"


def element_sel(target, by_label=False, value=None):
    """要素のセレクタ。(キー, 値)。パターンの要素は、実行時に決めた ID で1つに絞る。"""
    if by_label:
        return "text", sel_text(target)
    if is_pattern(target) and value:
        return "id", exact(value)
    return "id", sel_id(target)


def parent_sel(w, values=None):
    """親1つの id のセレクタの値。走らせるときに決めた親は、その ID で。"""
    if w.enter is not None:
        return exact((values or {})[w.enter.key])
    return sel_id(w.value)


def within_sel(within, values=None):
    """親の並び（外から順）を、いちばん内側の親のセレクタ（外側は childOf で入れ子）にする。無ければ None。

    **親は具体的な ID で指す。** Maestro の `childOf` に正規表現で複数の親に当たる値を渡すと、
    最初に当たった親の中しか探さない（実測）。2段以上の入れ子の `childOf` は実測していない。
    """
    sel = None
    for w in within:
        d = {"id": parent_sel(w, values)}
        if sel is not None:
            d["childOf"] = sel
        sel = d
    return sel


def scope(within, values=None):
    """子の要素のセレクタに足すもの（`childOf`）。親が無ければ空。"""
    sel = within_sel(within, values)
    return {"childOf": sel} if sel else {}


def step_sel(st, values=None):
    """ステップが指す要素のセレクタ（See は見る要素、Act は操作の要素）。"""
    values = values or {}
    if isinstance(st, See):
        if st.contains is not None or st.later:
            # その語を含む行。語は正規表現としてエスケープする
            word = st.contains if st.contains is not None else values[st.key]
            return "id", "^" + re.escape(pattern_prefix(st.target)) + ".*" + re.escape(word) + ".*"
        return element_sel(st.target, st.by_label)
    a = st.action
    value = values.get(st.key) if needs_value(st) and is_pattern(a.target) else None
    return element_sel(a.target, a.by_label, value)


SCROLL_TIMEOUT = 60000   # 経路の scrollUntilVisible の上限。理由は reveal()
SETTLE_TIMEOUT = 3000    # 着いたあとの落ち着き待ちの上限


def reveal(key, value, up=False):
    """要素が全部見えるまでスクロールする。**経路で要素を押す前に必ず入れる。**

    画面外の要素は、フローからは見つからずに落ちる。さらに悪いことに、ツリーには
    あるが画面外にある要素は、Maestro がその位置を叩いて**別の要素を押す**
    （mobile-dev-inc/Maestro#1275 と同じ症状）。見えている要素ならすぐ抜ける。

    **ここだけ要素を待つ時間（wait_for）より長い。** scrollUntilVisible はその間
    スクロールを繰り返す。スクロール1回は実測5〜8秒（maestrod.py の実測）で、
    60秒でも8〜12回ぶんにしかならない。

    **`centerElement` は付けない。** 押すだけなら下端でも困らない（見る要素を下端から離すのは
    項目の `see` の1手で、FlowWriter.step が書く）。

    **`up` なら、下向きを `optional` にして、そのあとに上向きも探す。** 要素が見えているか
    下にあれば下向きで止まり、上向きは見えている要素なのですぐ抜ける。上にあれば下向きは
    時間切れ（落ちない）になり、上向きで見つかる。scrollUntilVisible はスクロールの端を
    検知しない（Orchestra.scrollUntilVisible）ので、この時間切れは上限いっぱいかかる。
    上限を縮めると、長く下までスクロールしたあとで上に戻りきれないので縮めない。
    **項目の `do` の要素はここを通らない**。run_flows.py がダンプを読みながら送り、見えて
    いる ID が変わらなくなったら端として諦める。確かめたい要素が無いときにこの上限を払わない。
    経路の要素はマップに載っていて基本的に必ずあるので、ここで困らない。

    **下を先にする。** 画面は上端から始まるので、`up` の付かない画面と同じく、下にある
    要素はこれまでどおりの速さで見つかる。待ちが増えるのは上にある要素（`up` が無いと
    届かなかったもの）だけ。

    `when: notVisible` で上向きを飛ばすことはしない。画面外でもツリーに残っている要素は
    visible と判定されることがあり、条件に使えない。
    """
    def scroll(direction, optional=False):
        body = {"element": {key: value}, "direction": Raw(direction), "timeout": SCROLL_TIMEOUT}
        if optional:
            body["optional"] = True
        return {"scrollUntilVisible": body}
    if up:
        return [scroll("DOWN", optional=True), scroll("UP")]
    return [scroll("DOWN")]


def wait_for(selector, value, timeout, extra=None):
    """要素が出るまで待つ。出なければ落ちる。

    `assertVisible` に `timeout` は渡せない（実測で `Unknown Property`）。
    既定の待ち時間は実測18秒で、通るぶんには足りるが、**本当に出ない要素で
    1つあたり18秒持っていかれる。** フローが唯一の検証手段になった以上、
    壊れたフローは早く落ちてほしいので、明示できる形にする。
    """
    return {"extendedWaitUntil": {"visible": dict({selector: value}, **(extra or {})), "timeout": timeout}}


def wait_gone(selector, value, timeout, extra=None):
    return {"extendedWaitUntil": {"notVisible": dict({selector: value}, **(extra or {})), "timeout": timeout}}


def auto_checks(mp, sid, keep=None, via=None, came=None):
    """その画面の `auto_shows`（自動で出ることがある画面）が出ていたら閉じる。

    **入ったときは after の無い項目を、戻ってきたときは after に来た画面がある項目を**
    確かめる。ビューアを閉じた直後に出るレビュー依頼などは後者。

    **閉じるのは既定の扱い。** マップは「出ることがある」という事実だけを持ち、
    邪魔か確かめたいかはテストケースが決める。確かめたい項目（`from` にその画面）
    では、`keep` に渡した画面は閉じずに待つ（bridge.py が積む `await`）。

    **出ていないときに1つあたり約7秒かかる**（Maestro が「無い」と決めるまで
    `optionalLookupTimeoutMs` の既定7秒を待つ。下げる設定は無い）。だから
    `auto_shows` を書いた画面に着いたときだけ積み、after で絞れるものは絞る。
    """
    out = []
    back = via in BACKWARD
    for iid, after in mp.screens[sid].auto_items():
        if iid == keep:
            continue
        if (back and came not in after) or (not back and after):
            continue
        found = mp.screens[iid].auto_close() if iid in mp.screens else None
        if found is None:
            continue            # 書き間違いは check が出す
        anchor, close = found
        at_key, at_val = element_sel(str(anchor), mp.screens[iid].anchor_by_label)
        key, dismiss = element_sel(close.target, close.element.get("by") == "label")
        summary = mp.screens[iid].summary
        when = "（{} から戻ったとき）".format(came) if back else ""
        out.append(Comment("自動表示: {}{}{}（出ていたら閉じる）".format(iid, when, " — " + summary if summary else "")))
        out.append({"runFlow": {"when": {"visible": {at_key: at_val}},
                                "commands": [{"tapOn": {key: dismiss}}]}})
    return out


def ready_lines(mp, sid, timeout):
    """読み込み完了の目印（`ready`）を待つ。any はどれか1つ、all は全部。"""
    r = (mp.screens[sid].ready if sid in mp.screens else None) or {}
    out = []
    if r.get("any"):
        alts = [sel_id(str(x)) for x in r["any"]]
        out.append(Comment("{}: 読み込み完了（どれか1つ）".format(sid)))
        out.append(wait_for("id", alts[0] if len(alts) == 1 else "(" + "|".join(alts) + ")", timeout))
    if r.get("all"):
        out.append(Comment("{}: 読み込み完了（全部）".format(sid)))
        out.extend(wait_for("id", sel_id(str(x)), timeout) for x in r["all"])
    return out


def settle():
    """最後の落ち着き待ち。要素が出ても、ほかのセクションや画像がまだ読み込み中のことがある。"""
    return {"waitForAnimationToEnd": {"timeout": SETTLE_TIMEOUT}}


def anchor_of(mp, sid, notes):
    """その画面の anchor のセレクタ (キー, 値)。無ければ None。

    `anchor_by: label` の画面（ID を付けられない OS の部品）は、表示テキストで待つ。
    """
    a = mp.anchor(sid)
    if not a:
        notes.append("{} に anchor が無いので、着いたことを確かめられない".format(sid))
        return None
    return element_sel(str(a), mp.screens[sid].anchor_by_label)


def result_sel(st, r, values=None):
    """結果が指す要素のセレクタ。操作した要素そのもの（own）なら、操作したのと同じもの（パターンなら操作した1つ）。"""
    if r.own:
        return step_sel(st, values)
    return "id", sel_id(r.id)


def step_comment(st):
    """そのステップが何をしているかの1行。**フローの中にコメントとして残す。**

    要約を別に作って見せると、レビューしたものと実際に走るものが別になる。
    同じ1つを読めるようにする。
    """
    a = st.action
    head = "{}: {}".format(st.screen, a.label())
    if isinstance(a, Tap) and a.pick is not None:
        head += " [{}]".format(a.pick.condition or "見えている1件目")
    if st.within:
        head += " in " + " > ".join(w.label() for w in st.within)
    if a.summary:
        head += " — " + str(a.summary)
    out = next((r for r in getattr(st, "result", []) if isinstance(r, External)), None)
    if st.to and not st.arrive and out is not None:
        # 居た画面が閉じてアプリの外に出る。to は着く画面ではなく、アプリに戻したときに居る画面
        head += " → アプリの外（{}）。戻すと {}".format(out.name, st.to)
    elif st.to:
        head += " → " + st.to
    return Comment(head)


def emit_flow(mp, steps, app, clear_state, notes=None, timeout=10000,
              start=None, launch=True, values=None, indexes=None, shots=None, head=True):
    """ステップ列から Maestro のフローを1本書く。(フローの中身, 補足) を返す。

    `launch=False` はアプリを起動し直さない。続きのフローを出すため。
    `values` / `indexes` は実行時に決めた値と、同じ ID の行のうち何番目か（鍵はステップの `key`）。
    `shots` は撮影先のディレクトリ（端末と、撮るかなぞるかで変わる）。
    `head=False` は、居る画面の anchor を頭で待たない（1手ずつ流すフロー。run_flows.py が
    ダンプで見てから流すので要らず、アプリの外に居るまま撮るときは待てない）。

    **補足はこのフローのステップに関係するものだけ。**
    """
    return FlowWriter(mp, steps, app, clear_state, notes, timeout, values, indexes, shots).write(
        start or mp.start, launch, head)


class FlowWriter:
    """フロー1本を書く。積んだコマンド（`out`）・補足（`notes`）・居る画面（`at`）を持つ。

    `out` は Maestro のコマンドを dict で、注記を Comment で並べたもの。書き出しは
    flowyaml.render() が最後に1回だけする。
    """

    def __init__(self, mp, steps, app, clear_state, notes, timeout, values=None, indexes=None, shots=None):
        self.mp, self.steps, self.app = mp, steps, app
        self.values, self.indexes = values or {}, indexes or {}
        self.shots = shots or "."
        self.clear_state, self.timeout = clear_state, timeout
        self.notes = list(notes or [])
        self.out, self.at = [], None

    def write(self, start, launch, head=True):
        self.at = start
        if launch:
            self.relaunch(start, 0)
        elif self.steps and isinstance(self.steps[0], Return):
            self.out.append(Comment("続き: アプリの外から"))   # anchor は戻してから待つ
        elif head:
            self.out.append(Comment("続き: " + start + " から"))
            self.wait_anchor(start)
        for i, st in enumerate(self.steps):
            self.step(i, st)
        notes = list(dict.fromkeys(self.notes))   # 同じ画面の anchor 無しなどが重ならないように
        return render(self.app, self.out, notes), notes

    # ---- 着く ----

    def wait_anchor(self, sid):
        a = anchor_of(self.mp, sid, self.notes)
        if a:
            self.out.append(wait_for(*a, timeout=self.timeout))

    def awaited_after(self, i):
        """steps[i] の次が自動表示を待つステップなら、その画面（着いた画面で閉じない）。"""
        nxt = next((st for st in self.steps[i:] if not isinstance(st, Shot)), None)
        return nxt.to if isinstance(nxt, Await) else None

    def relaunch(self, sid, i):
        # 起点に戻してから始める。launchApp だけでは前の項目の画面に居座ることがある。
        # clearState はログイン状態まで消えるので、要ると言われたときだけ。
        self.out.append("stopApp")
        self.out.append({"launchApp": {"clearState": True}} if self.clear_state else "launchApp")
        self.out.append(Comment("起点: " + sid))
        self.arrive(sid, i)

    def arrive(self, sid, i, via=None, came=None):
        """sid に着いたときの確認。自動表示を閉じる → anchor → ready → 落ち着き待ち。

        **自動表示を先に閉じる。** シートやダイアログがモーダルで出ている間、下の画面は
        アクセシビリティのツリーから隠れる（実測）。先に anchor を待つと、自動表示が
        出た回は anchor が見えないまま時間切れになる。

        次のステップが自動表示を待つなら、anchor は待たない（見えないので）。
        """
        keep = self.awaited_after(i)
        self.out.extend(auto_checks(self.mp, sid, keep, via, came))
        if keep:
            return
        self.wait_anchor(sid)
        self.out.extend(ready_lines(self.mp, sid, self.timeout))
        self.out.append(settle())

    # ---- ステップの種類ごと ----

    def step(self, i, st):
        if isinstance(st, Restart):
            self.out.append(Comment("ここで起動し直す（前の状態から次の前提に行けないため）"))
            self.relaunch(self.mp.start, i + 1)
            self.at = self.mp.start
        elif isinstance(st, Await):
            # 自動表示を確かめる項目。閉じずに、出るまで待つ（出なければ落ちる）
            summary = self.mp.screens[st.to].summary
            self.out.append(Comment("{}: 自動表示 {} を待つ{}".format(
                st.screen, st.to, " — " + summary if summary else "")))
            self.wait_anchor(st.to)
            self.at = st.to
        elif isinstance(st, Shot):
            self.out.append({"takeScreenshot": "{}/{}".format(self.shots, st.name)})
        elif isinstance(st, Return):
            self.out.append(Comment("アプリの外から {} に戻す".format(st.screen)))
            self.out.append({"launchApp": {"stopApp": False}})
            self.wait_anchor(st.screen)
            self.at = st.screen
        elif isinstance(st, Reveal):
            self.reveal(i, st)
        elif isinstance(st, Enter):
            # 何も叩かない。どの親の中でするかは run_flows.py がダンプを読んで決めた
            self.out.append(Comment("{}: {} の中でする（{}。{}）".format(
                st.screen, st.target, self.values.get(st.key, "未定"),
                st.pick.condition or "見えている1件目")))
        elif isinstance(st, See) and st.text:
            # マップに無い文言（アプリの外など）。スクロールせずに出るまで待つ
            self.out.append(Comment("{}: 「{}」が出るまで待つ".format(st.screen, st.target)))
            self.out.append(wait_for(*step_sel(st, self.values), timeout=self.timeout))
        elif isinstance(st, See):
            name = st.name
            what = st.target
            if st.contains is not None:
                what += "（「{}」を含む行）".format(st.contains)
            elif st.later:
                what += "（「{}」を含む行）".format(self.values.get(st.key, "未定"))
            if st.within:
                what += " in " + " > ".join(self.parent_label(w) for w in st.within)
            self.out.append(Comment("{}: see {}{}".format(st.screen, what,
                                                            " — " + name if name else "")))
            key, val = step_sel(st, self.values)
            if st.within:
                # 親の中まで run_flows.py が送って見つけてある。見えていることだけ確かめる
                self.out.append(wait_for(key, val, self.timeout, extra=scope(st.within, self.values)))
            else:
                # run_flows.py が画面の中に見つけてある。下端から離すだけ（下端にかかって
                # いれば少し送る。見えているのですぐ抜ける — 時間は着いたあとの待ちと同じ上限）。
                # 付けないと、要素が下端に入ったところで止まり、その下が証跡に写らない。
                # centerElement は中央までは寄せず、要素の中心が画面の上から7割の線より上に来たら
                # 止まる（Maestro の UiElement.isElementNearScreenCenter）。上向きには付けない —
                # 上のほうに見えている要素を下へ寄せようとして空打ちし（実測5回・約8秒）、
                # アプリによっては「引っ張って更新」で一覧を読み込み直す
                self.out.append({"scrollUntilVisible": {
                    "element": {key: val}, "direction": Raw("DOWN"), "centerElement": True,
                    "timeout": self.timeout}})
        else:
            self.action(i, st)

    def parent_label(self, w):
        if w.enter is not None and w.enter.key in self.values:
            return self.values[w.enter.key]
        return w.label()

    def reveal(self, i, st):
        """押す前のスクロール（経路だけ）。操作の説明（コメント）もここで先に書く。"""
        act = st.act
        self.out.append(step_comment(act))
        key, val = element_sel(act.action.target, act.action.by_label)
        self.out.extend(reveal(key, val, up=st.up))

    def action(self, i, st):
        a = st.action
        # 直前の Reveal が説明を書いていなければ（1手のフロー、scroll など）、ここで書く
        prev = self.steps[i - 1] if i > 0 else None
        if not (isinstance(prev, Reveal) and prev.act is st):
            self.out.append(step_comment(st))
        if isinstance(a, Tap):
            self.tap(st)
        elif isinstance(a, (Input, InputLater)):
            self.type_text(st)
        elif isinstance(a, HideKeyboard):
            self.out.append("hideKeyboard")
        else:
            self.scroll(a)
        # 着いたことを先に確かめる（着いた画面の上で、ほかの結果を見る）
        if st.arrive:
            self.arrive(st.to, i + 1, st.arrive.via, st.screen)
            self.at = st.to
        elif st.to:
            self.at = st.to      # 閉じてアプリの外に出た。アプリに戻したら、閉じて残る画面に居る
        # すぐ後で撮るなら（間に文言を待つだけなら）、外に出たまま撮る（add_returns）。1手ずつ書くと
        # 撮影は別の本にあるので、組んだときに項目の並び全体で決めたもの（st.stay）を使う
        stay = st.stay if st.stay is not None else stays_out(self.steps, i, skip=(Reveal,))
        for r in st.result:
            if not isinstance(r, Arrive):
                self.check(st, r, stay)
        if not st.result and not isinstance(a, HideKeyboard):
            self.notes.append("「{}」の結果を確かめる expect がマップに無い".format(a.label()))

    def tap(self, st):
        key, val = step_sel(st, self.values)
        target = dict({key: val}, **scope(st.within, self.values))
        if picks_pattern(st) and st.key in self.indexes:
            # 同じ名前の行が複数あると ID も同じになる。どれを押すかを index で1つに絞る。
            # Maestro の index は、当たった要素を画面上の位置順（上端の y、次に x）に並べた
            # 番号で、画面外の要素も数える（Filters.index / INDEX_COMPARATOR）。index を
            # 付けないとツリー順の先頭（押せるもの優先）になり、画面に見えているとは限らない。
            # ドキュメントには書かれていない挙動なので、Maestro を上げたら確かめ直す
            target["index"] = int(self.indexes[st.key])
        self.out.append({"tapOn": target})

    def type_text(self, st):
        key, val = step_sel(st, self.values)
        self.out.append({"tapOn": dict({key: val}, **scope(st.within, self.values))})
        self.out.append("eraseText")         # 前の項目の文字が残ったまま打たない
        if isinstance(st.action, InputLater):
            self.out.append({"inputText": self.values[st.key]})   # 打つ文字そのもの。エスケープしない
        else:
            self.out.append({"inputText": st.action.text})

    def scroll(self, a):
        if a.direction == "up":
            self.out.append({"swipe": {"direction": Raw("DOWN")}})   # 内容を下へ＝上へ戻る
        else:
            self.out.append("scroll")

    # ---- 結果を確かめる ----

    def result_scope(self, st, r):
        """結果が指す要素が、操作した要素と同じ親の中の子なら、その親の中を見る（`self` と同じ考え方）。

        操作した要素そのもの（own）は操作と同じ親の中。ほかの子の要素は、その要素の親が
        操作した要素の親の並びの頭と一致すれば、そこまでの親の中。それ以外（画面の直下の
        要素、別の画面の要素）は絞らない。
        """
        if r.own:
            return scope(st.within, self.values)
        scr = self.mp.screens.get(st.screen)
        el, _ = scr.element(r.id) if scr else (None, None)
        ps = [str(p.get("id")) for p in scr.parents(el)] if el is not None else []
        if ps and ps == [w.id for w in st.within[:len(ps)]]:
            return scope(st.within[:len(ps)], self.values)
        return {}

    def check(self, st, r, stay=False):
        """着く以外の結果を1つ確かめる。`stay` なら、外に出る操作のあとアプリに戻さない。"""
        if isinstance(r, Closed):
            # 居た画面（メニューなど）が閉じた。戻った先は進んだ先が被さって隠れるので待たない
            a = anchor_of(self.mp, r.screen, self.notes)
            if a:
                self.out.append(Comment("{} が閉じた".format(r.screen)))
                self.out.append(wait_gone(*a, timeout=self.timeout))
        elif isinstance(r, (Visible, Value)):
            self.out.append(wait_for(*result_sel(st, r, self.values), timeout=self.timeout,
                                     extra=self.result_scope(st, r) or None))
        elif isinstance(r, Selected):
            self.out.append(wait_for(*result_sel(st, r, self.values), timeout=self.timeout,
                                     extra=dict(self.result_scope(st, r), selected=True)))
        elif isinstance(r, Hidden):
            self.out.append(wait_gone(*result_sel(st, r, self.values), timeout=self.timeout,
                                      extra=self.result_scope(st, r) or None))
        elif isinstance(r, External) and stay and r.loaded:
            # 外に出たまま撮る。外のアプリが読み込みの終わりを出していれば、それを待つ
            self.out.append(Comment("アプリの外（{}）に出る。ページの読み込みが終わるまで待って、外に居るまま撮る".format(r.name)))
            self.out.extend(wait_for("id", sel, self.timeout) for _, sel in r.loaded)
            self.out.append(settle())
        elif isinstance(r, External) and stay:
            # 外に出たまま撮る。外のアプリに anchor は無いので、動きが止まるのだけ待つ
            self.out.append(Comment("アプリの外（{}）に出る。確かめずに、外に居るまま撮る".format(r.name)))
            self.out.append(settle())
        elif isinstance(r, External):
            # アプリの外に出た。確かめずに、落とさずに前に戻す
            self.out.append(Comment("アプリの外（{}）に出る。確かめずにアプリに戻す".format(r.name)))
            self.out.append({"launchApp": {"stopApp": False}})
            self.wait_anchor(self.at)


def check_of(mp, st):
    """そのステップで最後に自動で確かめる ID。確かめないなら None。"""
    if isinstance(st, (Await, Restart)):
        sid = st.to if isinstance(st, Await) else mp.start
        return mp.anchor(sid)
    if isinstance(st, See):
        return st.target
    checked = None
    if st.arrive:
        checked = mp.anchor(st.to)
    for r in st.result:
        if isinstance(r, (Visible, Value, Selected, Hidden)):
            checked = r.id
        elif isinstance(r, External):
            # 外に居るまま撮るなら、読み込みの終わりを待っている（FlowWriter.check）
            checked = r.loaded[-1][0] if st.stay and r.loaded else None
    return checked


def shot_context(mp, seg_start, seg_steps):
    """その項目が終わる画面と、最後に確かめたID。

    flow.py の `render_case()` が返す行に載せるためのもの。**呼ぶ側が経路を読み直して導出せずに済ませる。**
    確かめたIDが無い（`expect` を持たない操作で終わった）なら None で、
    その証跡は自動確認なし＝画像だけが根拠になる。
    """
    at, checked = seg_start, mp.anchor(seg_start)
    for st in seg_steps:
        if isinstance(st, (Shot, Reveal, Return, Enter)):
            continue
        if isinstance(st, Restart):
            at = mp.start
        elif st.to:
            at = st.to
        checked = check_of(mp, st)
    return at, checked


def split_at_shots(mp, steps, start, launch_first=True):
    """項目（撮影）ごとにステップを切り、(その項目の起点, ステップ列, 撮る名前, 起動し直すか) で返す。

    **1項目＝1枚＝1ダンプにするため。** ダンプはフローの途中では取れないので、撮る地点で
    項目を区切り、その項目の Unit を流し終えてから読む（flow.py の units_of、run_flows.py）。
    2つ目以降の項目は前の項目の続きになるので、歩き直しは起きない。

    返す4つ目は**その項目の頭でアプリを起動し直すか。** 起動し直すテストケースの頭の項目
    （ステップの頭に Restart がある）と、`launch_first` のときの1つ目がそう。units_of はそれを
    項目の最初の経路のフローに付ける。走らせる側は、
    その項目の頭でアプリを起動し直したことをマニフェストに残す（判定が証跡を読むため）。
    """
    out, cur, at = [], [], start or mp.start
    seg_start, launch = at, launch_first
    for st in steps:
        if isinstance(st, Restart):
            # build が直前の撮影を保証しているので、cur は空
            at = seg_start = mp.start
            launch = True
            continue
        cur.append(st)
        if isinstance(st, Shot):
            out.append((seg_start, cur, os.path.basename(st.name), launch))
            cur, seg_start, launch = [], at, False
        elif st.to:
            at = st.to
    if cur:
        out.append((seg_start, cur, None, launch))
    return out
