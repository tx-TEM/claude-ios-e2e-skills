"""ステップ列（walk.py の build()）から Maestro のフローと、人が読む経路を書く。"""
import os
import re

from screen_map import BACKWARD, auto_of, expect_kind, is_pattern, pattern_prefix

SHOTS_VAR = "SHOTS"   # 撮影先のディレクトリ。run_flows.py が端末ごとに埋める


def var_name(target):
    """操作のidから env の変数名を作る。`browse.search_field` → `BROWSE_SEARCH_FIELD`。
    末尾のパターン記号（`browse.book_row.*`）は落とす。

    **呼ぶ側に名前を決めさせない。** idから決まるので、フローと索引と
    マニフェストで同じ名前になり、突き合わせに手が要らない。
    """
    return re.sub(r"[^A-Za-z0-9]", "_", target.rstrip(".*")).upper()


def needs_value(st):
    """実行時に値を決めるステップか。打つ文字（runtime）か、パターンの要素のどれを押すか（pick）。"""
    return "action" in st and (bool(st.get("runtime")) or "pick" in st)


def var_of(st):
    return st.get("var") or var_name(st["action"].target)


def index_var(var):
    """パターンの要素のうち何番目を押すか（Maestro の `index`）の変数名。"""
    return var + "_INDEX"


def picks_pattern(st):
    """パターンの要素のどれを押すかを実行時に決めるステップか（打つ文字ではなく）。"""
    return "pick" in st and "action" in st


def assign_vars(steps):
    """実行時に決める値に変数名を振る。**同じ区間で同じ名前が2回要れば `_2` を付ける**
    （同じ一覧を2回通るなど）。ここで振れば、フローとマニフェストで名前がずれない。"""
    used = {}
    for st in steps:
        if not needs_value(st):
            continue
        base = var_name(st["action"].target)
        used[base] = used.get(base, 0) + 1
        st["var"] = base if used[base] == 1 else "{}_{}".format(base, used[base])


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
            uses[var_of(st)] = "text" if st.get("runtime") else "selector"
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
        if "pick" not in st or "action" not in st:
            continue
        a = st["action"]
        prefix = pattern_prefix(a.target)
        others = sorted(str(el.get("id")) for el in mp.elements(a.sid)
                        if el is not a.element and str(el.get("id") or "").startswith(prefix))
        out[var_of(st)] = {"pattern": a.target, "pick": st.get("pick") or "", "exclude": others}
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


def q(s):
    """yaml のシングルクォート。正規表現の `\\` を素通しするためダブルにしない。"""
    return "'" + s.replace("'", "''") + "'"


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
    a = st.get("action")
    el = st.get("see") if a is None else a.element
    var = var_of(st) if a is not None and needs_value(st) and a.element is not None \
        and is_pattern(a.element.get("id")) else None
    return element_sel(el, st.get("value"), var)


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
    return ("- scrollUntilVisible:\n    element:\n      {}: {}\n"
            "    direction: DOWN\n    timeout: {}".format(key, q(value), SCROLL_TIMEOUT))


def wait_for(selector, value, timeout, extra=""):
    """要素が出るまで待つ。出なければ落ちる。

    `assertVisible` に `timeout` は渡せない（実測で `Unknown Property`）。
    既定の待ち時間は実測18秒で、通るぶんには足りるが、**本当に出ない要素で
    1つあたり18秒持っていかれる。** フローが唯一の検証手段になった以上、
    壊れたフローは早く落ちてほしいので、明示できる形にする。
    """
    return ("- extendedWaitUntil:\n    visible:\n      {}: {}{}\n    timeout: {}"
            .format(selector, q(value), extra, timeout))


def wait_gone(selector, value, timeout):
    return ("- extendedWaitUntil:\n    notVisible:\n      {}: {}\n    timeout: {}"
            .format(selector, q(value), timeout))


def auto_checks(mp, sid, keep=None, via=None, came=None):
    """その画面の `auto_shows`（自動で出ることがある画面）が出ていたら閉じる。

    **入ったときは after の無い項目を、戻ってきたときは after に来た画面がある項目を**
    確かめる。ビューアを閉じた直後に出るレビュー依頼などは後者。

    **閉じるのは既定の扱い。** マップは「出ることがある」という事実だけを持ち、
    邪魔か確かめたいかはテストケースが決める。確かめたい項目（`from` にその画面）
    では、`keep` に渡した画面は閉じずに待つ（walk.py の `build()` が積む `await`）。

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
        out.append("# 自動表示: {}{}{}（出ていたら閉じる）".format(iid, when, " — " + summary if summary else ""))
        out.append("- runFlow:\n    when:\n      visible:\n        id: {}\n    commands:\n"
                   "      - tapOn:\n          {}: {}".format(q(sel_id(anchor)), key, q(dismiss)))
    return out


def ready_lines(mp, sid, timeout):
    """読み込み完了の目印（`ready`）を待つ。any はどれか1つ、all は全部。"""
    r = (mp.screens.get(sid) or {}).get("ready") or {}
    out = []
    if r.get("any"):
        alts = [sel_id(str(x)) for x in r["any"]]
        out.append("# {}: 読み込み完了（どれか1つ）".format(sid))
        out.append(wait_for("id", alts[0] if len(alts) == 1 else "(" + "|".join(alts) + ")", timeout))
    if r.get("all"):
        out.append("# {}: 読み込み完了（全部）".format(sid))
        out.extend(wait_for("id", sel_id(str(x)), timeout) for x in r["all"])
    return out


def settle():
    """最後の落ち着き待ち。要素が出ても、ほかのセクションや画像がまだ読み込み中のことがある。"""
    return "- waitForAnimationToEnd:\n    timeout: {}".format(SETTLE_TIMEOUT)


def anchor_of(mp, sid, notes):
    a = (mp.screens.get(sid) or {}).get("anchor")
    if not a:
        notes.append("{} に anchor が無いので、着いたことを確かめられない".format(sid))
    return a


def outcome(st):
    """そのステップで確かめる expect の並び（分岐なら選んだ枝だけ）。"""
    a = st["action"]
    if a.branches:
        return [a.branches[st["branch"]]] if "branch" in st else []
    return a.expects


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
    a = st["action"]
    head = "# {}: {}".format(st["screen"], a.label())
    if st.get("value") is not None:
        head += " [{}]".format(st["value"])
    elif "pick" in st:
        head += " [{}]".format(st["pick"] or "見えている1件目")
    if a.summary:
        head += " — " + str(a.summary)
    if st.get("to"):
        head += " → " + st["to"]
    return head


def emit_flow(mp, steps, app, clear_state, notes=None, timeout=10000,
              start=None, launch=True, tail=None):
    """ステップ列から Maestro のフローを1本書く。(フローの中身, 補足) を返す。

    `launch=False` はアプリを起動し直さない。続きのフローを出すため。

    `tail` は、次のフローの頭で値を決めて押すステップ。**その要素までのスクロールを
    このフローの最後に置く** — 値を決めるときに、選ぶ対象が画面に見えていないと
    選べない（見えている1件目を選ぶのも、条件で選ぶのも同じ）。

    **補足はこのフローのステップに関係するものだけ。**
    """
    return FlowWriter(mp, steps, app, clear_state, notes, timeout).write(start or mp.start, launch, tail)


class FlowWriter(object):
    """フロー1本を書く。書いた行（`out`）・補足（`notes`）・居る画面（`at`）を持つ。"""

    def __init__(self, mp, steps, app, clear_state, notes, timeout):
        self.mp, self.steps, self.app = mp, steps, app
        self.clear_state, self.timeout = clear_state, timeout
        self.notes = list(notes or [])
        self.out, self.at = [], None

    def write(self, start, launch, tail):
        self.header()
        self.at = start
        if launch:
            self.relaunch(start, 0)
        else:
            self.out.append("# 続き: " + start + " から")
            self.wait_anchor(start)
        for i, st in enumerate(self.steps):
            self.step(i, st)
        if tail is not None:
            what = "打つ文字" if tail.get("runtime") else "押すもの"
            self.out.append("# 次で使う {} が見えるまでスクロールして止める（ここで{}を決める）"
                            .format(tail["action"].target, what))
            self.out.append(reveal(*element_sel(tail["action"].element)))
        notes = list(dict.fromkeys(self.notes))   # 同じ画面の anchor 無しなどが重ならないように
        if notes:
            self.out.append("")
            self.out.extend("# 補足: " + n for n in notes)
        return "\n".join(self.out) + "\n", notes

    def header(self):
        """appId と env。**実行時に決める値は env に未定のまま置く。** 値を焼き込むと、
        データが変わったときに黙って古い値で走る。未定のままなら、埋まっていないことが
        走らせる前に分かる。撮影先も端末で変わるので焼き込まない。"""
        used = [SHOTS_VAR] if any("shot" in st for st in self.steps) else []
        for st in self.steps:
            if needs_value(st):
                used.append(var_of(st))
            if picks_pattern(st):
                used.append(index_var(var_of(st)))
        self.out.append("appId: " + self.app)
        if used:
            self.out.append("env:")
            self.out.extend("  {}: ''".format(v) for v in dict.fromkeys(used))
        self.out.append("---")

    # ---- 着く ----

    def wait_anchor(self, sid):
        a = anchor_of(self.mp, sid, self.notes)
        if a:
            self.out.append(wait_for("id", sel_id(a), self.timeout))

    def awaited_after(self, i):
        """steps[i] の次が自動表示を待つステップなら、その画面（着いた画面で閉じない）。"""
        nxt = next((st for st in self.steps[i:] if "shot" not in st), None)
        return nxt["to"] if nxt and nxt.get("await") else None

    def relaunch(self, sid, i):
        # 起点に戻してから始める。launchApp だけでは前の項目の画面に居座ることがある。
        # clearState はログイン状態まで消えるので、要ると言われたときだけ。
        self.out.append("- stopApp")
        self.out.append("- launchApp:\n    clearState: true" if self.clear_state else "- launchApp")
        self.out.append("# 起点: " + sid)
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
        if st.get("restart"):
            self.out.append("# ここで起動し直す（前の状態から次の前提に行けないため）")
            self.relaunch(self.mp.start, i + 1)
            self.at = self.mp.start
        elif st.get("await"):
            # 自動表示を確かめる項目。閉じずに、出るまで待つ（出なければ落ちる）
            summary = (self.mp.screens.get(st["to"]) or {}).get("summary")
            self.out.append("# {}: 自動表示 {} を待つ{}".format(
                st["screen"], st["to"], " — " + summary if summary else ""))
            self.wait_anchor(st["to"])
            self.at = st["to"]
        elif "shot" in st:
            self.out.append("- takeScreenshot: " + q("${" + SHOTS_VAR + "}/" + st["shot"]))
        elif "see" in st:
            name = st["see"].get("name")
            self.out.append("# {}: see {}{}".format(st["screen"], st["see"].get("id"),
                                                    " — " + name if name else ""))
            self.out.append(reveal(*step_sel(st)))
        else:
            self.action(i, st)

    def action(self, i, st):
        a = st["action"]
        self.out.append(step_comment(st))
        if a.element is not None and not st.get("revealed"):
            self.out.append(reveal(*step_sel(st)))
        op = getattr(self, "op_" + str(a.op), None)
        if op is None:
            self.notes.append("種類の分からない操作を飛ばした: {}".format(a.label()))
            return
        op(st)
        if st.get("to"):
            self.arrive(st["to"], i + 1, st.get("via"), st["screen"])
            self.at = st["to"]
        exps = outcome(st)
        for e in exps:
            self.expect(st, e)
        if not exps:
            self.notes.append("「{}」の結果を確かめる expect がマップに無い".format(a.label()))

    def op_tap(self, st):
        key, val = step_sel(st)
        line = "- tapOn:\n    {}: {}".format(key, q(val))
        if picks_pattern(st):
            # 同じ名前の行が複数あると ID も同じになる。どれを押すかを index で1つに絞る。
            # Maestro の index は、当たった要素を画面上の位置順（上端の y、次に x）に並べた
            # 番号で、画面外の要素も数える（Filters.index / INDEX_COMPARATOR）。index を
            # 付けないとツリー順の先頭（押せるもの優先）になり、画面に見えているとは限らない。
            # ドキュメントには書かれていない挙動なので、Maestro を上げたら確かめ直す
            line += "\n    index: ${" + index_var(var_of(st)) + "}"
        self.out.append(line)

    def op_text(self, st):
        key, val = step_sel(st)
        self.out.append("- tapOn:\n    {}: {}".format(key, q(val)))
        self.out.append("- eraseText")       # 前の項目の文字が残ったまま打たない
        if st.get("runtime"):
            self.out.append("- inputText: ${" + var_of(st) + "}")
        else:
            self.out.append("- inputText: " + q(str(st.get("input", ""))))

    def op_scroll(self, st):
        if st["action"].target == "up":
            self.out.append("- swipe:\n    direction: DOWN")   # 内容を下へ＝上へ戻る
        else:
            self.out.append("- scroll")

    # ---- 結果を確かめる ----

    def expect(self, st, e):
        kind = expect_kind(e)
        if kind in ("visible", "value"):
            self.out.append(wait_for(*ref_sel(st, e[kind]), timeout=self.timeout))
        elif kind == "selected":
            self.out.append(wait_for(*ref_sel(st, e[kind]), timeout=self.timeout,
                                     extra="\n      selected: true"))
        elif kind == "hidden":
            self.out.append(wait_gone(*ref_sel(st, e[kind]), timeout=self.timeout))
        elif kind == "external":
            # アプリの外に出た。確かめずに、落とさずに前に戻す
            self.out.append("# アプリの外（{}）に出る。確かめずにアプリに戻す".format(e[kind]))
            self.out.append("- launchApp:\n    stopApp: false")
            self.wait_anchor(self.at)


def check_of(mp, st):
    """そのステップで最後に自動で確かめる ID。確かめないなら None。"""
    if st.get("await") or st.get("restart"):
        sid = st["to"] if st.get("await") else mp.start
        return (mp.screens.get(sid) or {}).get("anchor")
    if "see" in st:
        return st["see"].get("id")
    checked = None
    if st.get("to"):
        checked = (mp.screens.get(st["to"]) or {}).get("anchor")
    for e in outcome(st):
        kind = expect_kind(e)
        if kind in ("visible", "value", "selected", "hidden"):
            ref = e[kind]
            checked = st["action"].target if ref == "self" else ref
        elif kind == "external":
            checked = None
    return checked


def shot_context(mp, seg_start, seg_steps):
    """そのフローが終わる画面と、最後に確かめたID。

    plans.py の `write_flows()` が返す行に載せるためのもの。**呼ぶ側が経路を読み直して導出せずに済ませる。**
    確かめたIDが無い（`expect` を持たない操作で終わった）なら None で、
    その証跡は自動確認なし＝画像だけが根拠になる。
    """
    at, checked = seg_start, (mp.screens.get(seg_start) or {}).get("anchor")
    for st in seg_steps:
        if "shot" in st:
            continue
        if st.get("restart"):
            at = mp.start
        elif st.get("to"):
            at = st["to"]
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
        if st.get("restart"):
            # build が直前の撮影を保証しているので、cur は空
            at = seg_start = mp.start
            launch = True
            continue
        cur.append(st)
        if "shot" in st:
            out.append((seg_start, cur, os.path.basename(str(st["shot"])), launch))
            cur, seg_start, launch = [], at, False
        elif st.get("to"):
            at = st["to"]
    if cur:
        out.append((seg_start, cur, None, launch))
    return out


def split_parts(seg_start, seg_steps):
    """実行時に値を決めるステップの手前で切る。[(起点, ステップ列), ...]。

    **値を決めるには、目的の画面に着いていて、選ぶ対象が見えている必要がある。**
    手前までを1本にして先に走らせ、止まったところで画面を見て決める。
    切る箇所ごとに1本増える（途中の一覧で1件選び、着いた先でまた選ぶ、など）。
    2本目以降の頭のステップは、前の本の最後でスクロール済み（`revealed`）。
    """
    parts, cur, at, start = [], [], seg_start, seg_start
    for st in seg_steps:
        if needs_value(st) and (cur or parts):
            parts.append((start, cur))
            cur, start = [], at
            st = dict(st, revealed=True)
        elif needs_value(st):
            # 区間の頭で値が要る。スクロールだけの本を先に置く
            parts.append((start, []))
            st = dict(st, revealed=True)
        cur.append(st)
        if st.get("to"):
            at = st["to"]
    parts.append((start, cur))
    return parts


def emit_path(mp, steps, notes, start=None):
    """人が読む経路。**どこに自動確認があり、どこが証跡頼みかを明示する。**

    フローを見せてレビューを受けるとき、いちばん知りたいのは「この確認は
    何によって裏付けられるのか」。遷移は `anchor`、遷移しない操作は `expect`
    が assert になるが、**`expect` を持たない操作は何も確かめていない**。
    そこは証跡のPNGだけが根拠になるので、黙って並べない。
    """
    at = start or mp.start
    chain = [at]
    for st in steps:
        if st.get("restart"):
            at = mp.start
            chain.append("（起動し直す）")
            chain.append(at)
            continue
        if "shot" in st:
            continue
        if st.get("to"):
            at = st["to"]
            chain.append(at)
    out = [" → ".join(chain), ""]

    w = max([len(st["screen"]) for st in steps if "screen" in st] + [len(at), 4])
    rows, checked, unchecked = [], 0, 0

    def row(left, right):
        nonlocal checked, unchecked
        rows.append((left, right))
        if right:
            checked += right.startswith("✓")
            unchecked += right.startswith("—")

    start_anchor = (mp.screens.get(chain[0]) or {}).get("anchor")
    row("  {}  {}".format(chain[0].ljust(w), "起点" if chain[0] == mp.start else "続き"),
        "✓ {} が出ている".format(start_anchor) if start_anchor else "— anchor が無い")

    for n, st in enumerate(steps):
        if st.get("restart"):
            a = (mp.screens.get(mp.start) or {}).get("anchor")
            row("  {}  アプリを起動し直す".format("".ljust(w)), None)
            row("  {}  起点".format(mp.start.ljust(w)),
                "✓ {} が出ている".format(a) if a else "— anchor が無い")
            continue
        if "shot" in st:
            # 撮影行は右カラムを持たないので、桁揃えの計算から外す
            row("  {}  撮影 {}".format("".ljust(w), os.path.basename(st["shot"])), None)
            continue
        if st.get("await"):
            dest = (mp.screens.get(st["to"]) or {}).get("anchor")
            row("  {}  自動表示 {} を待つ".format(st["screen"].ljust(w), st["to"]),
                "✓ {} が出ている".format(dest) if dest else "— {} に anchor が無い".format(st["to"]))
            continue
        if "see" in st:
            row("  {}  see {}".format(st["screen"].ljust(w), st["see"].get("id")),
                "✓ {} が見える".format(st["see"].get("id")))
            continue
        a = st["action"]
        extra = ' "{}"'.format(st.get("input", "")) if a.op == "text" and "input" in st else ""
        if st.get("value") is not None:
            extra += " [{}]".format(st["value"])
        elif "pick" in st:
            extra += " [{}]".format(st["pick"] or "見えている1件目")
        left = "  {}  {}{}".format(st["screen"].ljust(w), a.label(), extra)
        nxt = next((x for x in steps[n + 1:] if "shot" not in x), None)
        rights = []
        if st.get("to") and nxt and nxt.get("await"):
            rights.append("（{} が被さって隠れるので、次の自動表示で確かめる）".format(st["to"]))
        elif st.get("to"):
            dest = (mp.screens.get(st["to"]) or {}).get("anchor")
            rights.append("✓ {} に着いたことを確認".format(st["to"]) if dest
                          else "— {} に anchor が無い".format(st["to"]))
        for e in outcome(st):
            kind = expect_kind(e)
            ref = e.get(kind)
            ref = a.target if ref == "self" else ref
            if kind in ("visible", "value"):
                rights.append("✓ {} が出ている{}".format(ref, "（値は証跡で見る）" if kind == "value" else ""))
            elif kind == "selected":
                rights.append("✓ {} が選択状態".format(ref))
            elif kind == "hidden":
                rights.append("✓ {} が消えた".format(ref))
            elif kind == "external":
                rights.append("— アプリの外（{}）に出る。確かめずに戻す".format(ref))
        if not rights:
            rights.append("— 自動確認なし。証跡で見る")
        row(left, rights[0])
        for r in rights[1:]:
            row("  {}".format("".ljust(w)), r)

    pad = max(len(l) for l, r in rows if r is not None)
    for left, right in rows:
        out.append(left if not right else "{}  {}".format(left.ljust(pad), right))

    out.append("")
    out.append("  自動確認あり {}件 / 証跡だけ {}件".format(checked, unchecked))
    for n in notes:
        out.append("  補足: " + n)
    return "\n".join(out)
