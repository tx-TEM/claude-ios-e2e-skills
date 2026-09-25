"""ステップを Maestro のフロー（yaml）にする。セレクタ、待ち、フローの切り方、実行時に決める値の名前。

どのステップを積むかは flow.py（項目の do）と bridge.py（項目の間の経路）が決める。
ここはそれを Maestro のコマンドに書くだけ。
"""
import os
import re
from dataclasses import dataclass

from screenmap.model import BACKWARD, auto_of, expect_kind, is_pattern, pattern_prefix
from screenmap.steps import Act, Await, Restart, See, Shot

from .flowyaml import Comment, Raw, render

SHOTS_VAR = "SHOTS"   # 撮影先のディレクトリ。run_flows.py が端末ごとに埋める


@dataclass
class Reveal(object):
    """`act` の要素が全部見えるまでスクロールする。要素を操作する Act の直前に必ず挟む（add_reveals）。

    **独立したステップにしておくと、フローを割るときに何もしなくてよい。** 値を決める操作の
    手前で割ると、そのスクロールは前の本の最後に残る。run_flows.py は前の本を走らせてから
    画面を読んで値を決めるので、そのとき対象が見えている。
    """
    act: Act
    item: object = None
    to = None


def add_reveals(steps):
    """要素を操作する Act の直前に Reveal を挟む。See は自分がスクロールなので挟まない。"""
    out = []
    for st in steps:
        if isinstance(st, Act) and st.action.element is not None:
            out.append(Reveal(st, st.item))
        out.append(st)
    return out


def var_name(target):
    """操作のidから env の変数名を作る。`browse.search_field` → `BROWSE_SEARCH_FIELD`。
    末尾のパターン記号（`browse.book_row.*`）は落とす。

    **呼ぶ側に名前を決めさせない。** idから決まるので、フローと索引と
    マニフェストで同じ名前になり、突き合わせに手が要らない。
    """
    return re.sub(r"[^A-Za-z0-9]", "_", target.rstrip(".*")).upper()


def needs_value(st):
    """実行時に値を決めるステップか。打つ文字（runtime）か、パターンの要素のどれを押すか（pick）。"""
    return isinstance(st, Act) and st.needs_value()


def var_of(st):
    return st.var or var_name(st.action.target)


def index_var(var):
    """パターンの要素のうち何番目を押すか（Maestro の `index`）の変数名。"""
    return var + "_INDEX"


def picks_pattern(st):
    """パターンの要素のどれを押すかを実行時に決めるステップか（打つ文字ではなく）。"""
    return isinstance(st, Act) and st.pick is not None


def assign_vars(steps):
    """実行時に決める値に変数名を振る。**同じ区間で同じ名前が2回要れば `_2` を付ける**
    （同じ一覧を2回通るなど）。ここで振れば、フローとマニフェストで名前がずれない。"""
    used = {}
    for st in steps:
        if not needs_value(st):
            continue
        base = var_name(st.action.target)
        used[base] = used.get(base, 0) + 1
        st.var = base if used[base] == 1 else "{}_{}".format(base, used[base])


def runtime_uses(steps):
    """実行時に決める値の、変数名 → 入る先。`selector`（tapOn の id。正規表現）か
    `text`（inputText。文字そのまま）。

    **走らせる側がこれを見てエスケープを分ける。** セレクタは正規表現なので、
    `牛乳(1L)` や `C++入門` をそのまま入れると別物として解釈され、狙った行に
    当たらない（`a.b` なら `aXb` にも当たる）。逆に inputText をエスケープすると
    `\\` ごと打たれる。どちらに入るかはフローを書くここでしか分からない。
    """
    uses = {}
    for st in steps:
        if needs_value(st):
            uses[var_of(st)] = "text" if st.runtime else "selector"
        if picks_pattern(st):
            uses[index_var(var_of(st))] = "index"   # 数字そのもの。エスケープしない
    return uses


def runtime_picks(mp, steps):
    """パターンの要素を押すときの選び方。変数名 → {"pattern": ID, "pick": 条件, "exclude": [ID]}。

    条件が空なら、走らせる側（run_flows.py）が**画面に見えている1件目**を機械的に選ぶ。
    条件があれば止めて、ダンプと条件から選ばせる。

    `exclude` は、同じ画面で別の要素として定義されている、パターンの前方一致に当たる ID。
    **行の中の要素（`item_list.cell.title`）は行のパターン（`item_list.cell.*`）にも当たる。**
    外さないと、見えている1件目として行ではなくタイトルを選ぶ。
    """
    out = {}
    for st in steps:
        if not picks_pattern(st):
            continue
        a = st.action
        prefix = pattern_prefix(a.target)
        others = sorted(str(el.get("id")) for el in mp.elements(a.sid)
                        if el is not a.element and str(el.get("id") or "").startswith(prefix))
        out[var_of(st)] = {"pattern": a.target, "pick": st.pick or "", "exclude": others}
    return out


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


def element_sel(el, value=None, var=None):
    """要素のセレクタ。(キー, 値)。パターンの要素は、決まった値か実行時の変数で1つに絞る。"""
    eid = str(el.get("id"))
    if el.get("by") == "label":
        return "text", sel_text(eid)
    if is_pattern(eid) and value is not None:
        return "id", "^" + re.escape(pattern_prefix(eid) + value) + "$"
    if is_pattern(eid) and var:
        # 値はアクセシビリティ ID そのもの（`list.row.牛乳`）。ダンプの id の欄を写せば済む
        return "id", "^${" + var + "}$"
    return "id", sel_id(eid)


def step_sel(st):
    """ステップが指す要素のセレクタ（See は見る要素、Act は操作の要素）。"""
    if isinstance(st, See):
        return element_sel(st.element, st.value)
    el = st.action.element
    var = var_of(st) if needs_value(st) and el is not None and is_pattern(el.get("id")) else None
    return element_sel(el, st.value, var)


SCROLL_TIMEOUT = 60000   # scrollUntilVisible の上限。理由は reveal()
SETTLE_TIMEOUT = 3000    # 着いたあとの落ち着き待ちの上限


def reveal(key, value):
    """要素が全部見えるまでスクロールする。**マップに載っている要素を操作・確認する前に必ず入れる。**

    画面外の要素は、フローからは見つからずに落ちる。さらに悪いことに、ツリーには
    あるが画面外にある要素は、Maestro がその位置を叩いて**別の要素を押す**
    （mobile-dev-inc/Maestro#1275 と同じ症状）。見えている要素ならすぐ抜ける。

    **ここだけ要素を待つ時間（wait_for）より長い。** scrollUntilVisible はその間
    スクロールを繰り返す。スクロール1回は実測5〜8秒（maestrod.py の実測）で、
    60秒でも8〜12回ぶんにしかならない。
    """
    return {"scrollUntilVisible": {"element": {key: value}, "direction": Raw("DOWN"),
                                   "timeout": SCROLL_TIMEOUT}}


def wait_for(selector, value, timeout, extra=None):
    """要素が出るまで待つ。出なければ落ちる。

    `assertVisible` に `timeout` は渡せない（実測で `Unknown Property`）。
    既定の待ち時間は実測18秒で、通るぶんには足りるが、**本当に出ない要素で
    1つあたり18秒持っていかれる。** フローが唯一の検証手段になった以上、
    壊れたフローは早く落ちてほしいので、明示できる形にする。
    """
    return {"extendedWaitUntil": {"visible": dict({selector: value}, **(extra or {})), "timeout": timeout}}


def wait_gone(selector, value, timeout):
    return {"extendedWaitUntil": {"notVisible": {selector: value}, "timeout": timeout}}


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
    for iid, after in mp.auto_items(sid):
        if iid == keep:
            continue
        if (back and came not in after) or (not back and after):
            continue
        found = auto_of(mp, iid)
        if found is None:
            continue            # 書き間違いは check が出す
        anchor, close = found
        key, dismiss = element_sel(close.element)
        summary = (mp.screens.get(iid) or {}).get("summary")
        when = "（{} から戻ったとき）".format(came) if back else ""
        out.append(Comment("自動表示: {}{}{}（出ていたら閉じる）".format(iid, when, " — " + summary if summary else "")))
        out.append({"runFlow": {"when": {"visible": {"id": sel_id(anchor)}},
                                "commands": [{"tapOn": {key: dismiss}}]}})
    return out


def ready_lines(mp, sid, timeout):
    """読み込み完了の目印（`ready`）を待つ。any はどれか1つ、all は全部。"""
    r = (mp.screens.get(sid) or {}).get("ready") or {}
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
    a = (mp.screens.get(sid) or {}).get("anchor")
    if not a:
        notes.append("{} に anchor が無いので、着いたことを確かめられない".format(sid))
    return a


def ref_sel(st, ref):
    """expect が指す要素のセレクタ。`self` はその操作の要素（パターンなら押した1つ）。"""
    if ref == "self":
        return step_sel(st)
    return "id", sel_id(str(ref))


def step_comment(st):
    """そのステップが何をしているかの1行。**フローの中にコメントとして残す。**

    要約を別に作って見せると、レビューしたものと実際に走るものが別になる。
    同じ1つを読めるようにする。
    """
    a = st.action
    head = "{}: {}".format(st.screen, a.label())
    if st.value is not None:
        head += " [{}]".format(st.value)
    elif st.pick is not None:
        head += " [{}]".format(st.pick or "見えている1件目")
    if a.summary:
        head += " — " + str(a.summary)
    if st.to:
        head += " → " + st.to
    return Comment(head)


def emit_flow(mp, steps, app, clear_state, notes=None, timeout=10000,
              start=None, launch=True):
    """ステップ列から Maestro のフローを1本書く。(フローの中身, 補足) を返す。

    `launch=False` はアプリを起動し直さない。続きのフローを出すため。

    **補足はこのフローのステップに関係するものだけ。**
    """
    return FlowWriter(mp, steps, app, clear_state, notes, timeout).write(start or mp.start, launch)


class FlowWriter(object):
    """フロー1本を書く。積んだコマンド（`out`）・補足（`notes`）・居る画面（`at`）を持つ。

    `out` は Maestro のコマンドを dict で、注記を Comment で並べたもの。書き出しは
    flowyaml.render() が最後に1回だけする。
    """

    def __init__(self, mp, steps, app, clear_state, notes, timeout):
        self.mp, self.steps, self.app = mp, steps, app
        self.clear_state, self.timeout = clear_state, timeout
        self.notes = list(notes or [])
        self.out, self.at = [], None

    def write(self, start, launch):
        self.at = start
        if launch:
            self.relaunch(start, 0)
        else:
            self.out.append(Comment("続き: " + start + " から"))
            self.wait_anchor(start)
        for i, st in enumerate(self.steps):
            self.step(i, st)
        notes = list(dict.fromkeys(self.notes))   # 同じ画面の anchor 無しなどが重ならないように
        return render(self.app, self.env(), self.out, notes), notes

    def env(self):
        """env に置く変数名。**実行時に決める値は env に未定のまま置く。** 値を焼き込むと、
        データが変わったときに黙って古い値で走る。未定のままなら、埋まっていないことが
        走らせる前に分かる。撮影先も端末で変わるので焼き込まない。"""
        used = [SHOTS_VAR] if any(isinstance(st, Shot) for st in self.steps) else []
        for st in self.steps:
            if needs_value(st):
                used.append(var_of(st))
            if picks_pattern(st):
                used.append(index_var(var_of(st)))
        return list(dict.fromkeys(used))

    # ---- 着く ----

    def wait_anchor(self, sid):
        a = anchor_of(self.mp, sid, self.notes)
        if a:
            self.out.append(wait_for("id", sel_id(a), self.timeout))

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
            summary = (self.mp.screens.get(st.to) or {}).get("summary")
            self.out.append(Comment("{}: 自動表示 {} を待つ{}".format(
                st.screen, st.to, " — " + summary if summary else "")))
            self.wait_anchor(st.to)
            self.at = st.to
        elif isinstance(st, Shot):
            self.out.append({"takeScreenshot": "${" + SHOTS_VAR + "}/" + st.name})
        elif isinstance(st, Reveal):
            self.reveal(i, st)
        elif isinstance(st, See):
            name = st.element.get("name")
            self.out.append(Comment("{}: see {}{}".format(st.screen, st.element.get("id"),
                                                            " — " + name if name else "")))
            self.out.append(reveal(*step_sel(st)))
        else:
            self.action(i, st)

    def reveal(self, i, st):
        """押す前のスクロール。操作の説明（コメント）もここで先に書く。

        この本がここで終わるなら、次の本の頭で値を決めて押す。そう書いて止める。
        """
        act = st.act
        if i + 1 < len(self.steps) and self.steps[i + 1] is act:
            self.out.append(step_comment(act))
        else:
            what = "打つ文字" if act.runtime else "押すもの"
            self.out.append(Comment("次で使う {} が見えるまでスクロールして止める（ここで{}を決める）"
                                    .format(act.action.target, what)))
        self.out.append(reveal(*element_sel(act.action.element, act.value)))

    def action(self, i, st):
        a = st.action
        # 直前の Reveal が説明を書いていなければ（割った本の頭）、ここで書く
        prev = self.steps[i - 1] if i > 0 else None
        if not (isinstance(prev, Reveal) and prev.act is st):
            self.out.append(step_comment(st))
        op = getattr(self, "op_" + str(a.op), None)
        if op is None:
            self.notes.append("種類の分からない操作を飛ばした: {}".format(a.label()))
            return
        op(st)
        if st.to:
            self.arrive(st.to, i + 1, st.via, st.screen)
            self.at = st.to
        exps = st.outcome()
        for e in exps:
            self.expect(st, e)
        if not exps:
            self.notes.append("「{}」の結果を確かめる expect がマップに無い".format(a.label()))

    def op_tap(self, st):
        key, val = step_sel(st)
        target = {key: val}
        if picks_pattern(st):
            # 同じ名前の行が複数あると ID も同じになる。どれを押すかを index で1つに絞る。
            # Maestro の index は、当たった要素を画面上の位置順（上端の y、次に x）に並べた
            # 番号で、画面外の要素も数える（Filters.index / INDEX_COMPARATOR）。index を
            # 付けないとツリー順の先頭（押せるもの優先）になり、画面に見えているとは限らない。
            # ドキュメントには書かれていない挙動なので、Maestro を上げたら確かめ直す
            target["index"] = Raw("${" + index_var(var_of(st)) + "}")
        self.out.append({"tapOn": target})

    def op_text(self, st):
        key, val = step_sel(st)
        self.out.append({"tapOn": {key: val}})
        self.out.append("eraseText")         # 前の項目の文字が残ったまま打たない
        if st.runtime:
            self.out.append({"inputText": Raw("${" + var_of(st) + "}")})
        else:
            self.out.append({"inputText": str(st.input or "")})

    def op_scroll(self, st):
        if st.action.target == "up":
            self.out.append({"swipe": {"direction": Raw("DOWN")}})   # 内容を下へ＝上へ戻る
        else:
            self.out.append("scroll")

    # ---- 結果を確かめる ----

    def expect(self, st, e):
        kind = expect_kind(e)
        if kind in ("visible", "value"):
            self.out.append(wait_for(*ref_sel(st, e[kind]), timeout=self.timeout))
        elif kind == "selected":
            self.out.append(wait_for(*ref_sel(st, e[kind]), timeout=self.timeout,
                                     extra={"selected": True}))
        elif kind == "hidden":
            self.out.append(wait_gone(*ref_sel(st, e[kind]), timeout=self.timeout))
        elif kind == "external":
            # アプリの外に出た。確かめずに、落とさずに前に戻す
            self.out.append(Comment("アプリの外（{}）に出る。確かめずにアプリに戻す".format(e[kind])))
            self.out.append({"launchApp": {"stopApp": False}})
            self.wait_anchor(self.at)


def check_of(mp, st):
    """そのステップで最後に自動で確かめる ID。確かめないなら None。"""
    if isinstance(st, (Await, Restart)):
        sid = st.to if isinstance(st, Await) else mp.start
        return (mp.screens.get(sid) or {}).get("anchor")
    if isinstance(st, See):
        return st.element.get("id")
    checked = None
    if st.to:
        checked = (mp.screens.get(st.to) or {}).get("anchor")
    for e in st.outcome():
        kind = expect_kind(e)
        if kind in ("visible", "value", "selected", "hidden"):
            ref = e[kind]
            checked = st.action.target if ref == "self" else ref
        elif kind == "external":
            checked = None
    return checked


def shot_context(mp, seg_start, seg_steps):
    """そのフローが終わる画面と、最後に確かめたID。

    flow.py の `write_flows()` が返す行に載せるためのもの。**呼ぶ側が経路を読み直して導出せずに済ませる。**
    確かめたIDが無い（`expect` を持たない操作で終わった）なら None で、
    その証跡は自動確認なし＝画像だけが根拠になる。
    """
    at, checked = seg_start, (mp.screens.get(seg_start) or {}).get("anchor")
    for st in seg_steps:
        if isinstance(st, (Shot, Reveal)):
            continue
        if isinstance(st, Restart):
            at = mp.start
        elif st.to:
            at = st.to
        checked = check_of(mp, st)
    return at, checked


def split_at_shots(mp, steps, start, launch_first=True):
    """撮影ごとにステップを切り、(そのフローの起点, ステップ列, 撮る名前, 起動するか) で返す。

    **1本＝1枚＝1ダンプにするため。** ダンプはフローの途中では取れないので、
    証跡1枚ごとに構造を残すには、撮る地点でフローを終わらせるしかない。
    2本目以降は前のフローの続きになるので、歩き直しは起きない。

    返す4つ目は**そのフローが自分で起動するか。** 1本目と、`fresh` の項目が
    そう。**ここが鎖の切れ目**で、走らせる側は落ちたときにどこまで諦めるかを
    これで決める（次に起動するフローからは、前が落ちていても走る）。
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


def split_parts(seg_start, seg_steps):
    """実行時に値を決めるステップの手前で切る。[(起点, ステップ列), ...]。

    **値を決めるには、目的の画面に着いていて、選ぶ対象が見えている必要がある。**
    手前までを1本にして先に走らせ、止まったところで画面を見て決める。
    切る箇所ごとに1本増える（途中の一覧で1件選び、着いた先でまた選ぶ、など）。
    操作の直前には Reveal（対象までのスクロール）があるので、それが前の本の最後に残る。
    """
    parts, cur, at, start = [], [], seg_start, seg_start
    for st in seg_steps:
        if needs_value(st):
            parts.append((start, cur))
            cur, start = [], at
        cur.append(st)
        if st.to:
            at = st.to
    parts.append((start, cur))
    return parts
