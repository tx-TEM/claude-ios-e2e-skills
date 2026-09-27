"""plan.json から、項目ごとの Maestro のフローを作る（manifest.py から呼ぶ）。plan の形は manifest.py --help。

plan はテストケース（`cases`）の集合で、項目は必ずどれかのテストケースに属する。
項目1つ＝「`from` から `do` を順に叩いて、1枚撮る」。**項目は経路を持たない。**
前の項目が終わった画面から次の項目の `from` までは bridge.py が繋ぐ（すでに居れば何もしない）。
ここがするのは、項目の `do` をマップの操作に引き当ててステップにすることと、
できたステップ列を maestro.py でフローに書くこと。

  plan の項目 ──→ build_steps() ──→ ステップ列 ──→ write_flows() ──→ フロー（撮影ごとに1本）
                   ├ 項目の間: bridge.py（Route.to）
                   └ 項目の do: do_step()
"""
import json
import re
import sys
from pathlib import Path

from .maestro import (add_returns, add_reveals, assign_vars, emit_flow, runtime_picks, runtime_sees, runtime_uses,
                      shot_context, split_at_shots, split_parts, var_of)
from screenmap.map import load_map
from screenmap.screen import DO_OPS, GESTURES, is_pattern, pattern_prefix
from .actions import HideKeyboard, resolve_action
from .bridge import Route, Unroutable, emit_path, report_problems
from .results import resolve_result
from .steps import Act, See, Shot, nest

PLAN_KEYS = {"app", "repo", "clear_state", "cases"}
CASE_KEYS = {"title", "items", "launch", "explore"}
ITEM_KEYS = {"id", "from", "do", "title", "expect", "when"}
DO_KEYS = {"op", "runtime", "input", "pick", "in"}
REF = re.compile(r"\{([^{}\s]*)\}")     # 期待の中で、同じテストケースの前の項目を指す


def shot_name(n):
    """項目の名前。テストケースをまたいで並び順から通しで振る（explore のテストケースも
    その場の番号）。証跡・ダンプ・フローのファイル名になる。端末はディレクトリで分けるので、
    名前には入れない。"""
    return "test_{:02d}".format(n)


# ---------- plan を読む ----------

def load_plan(path):
    """plan.json を読み、鍵とテストケースの形を確かめて返す。**知らない鍵は綴り違い** — 黙って
    無視すると、その指定が効かないまま走る。

    `repo`（アプリのリポジトリ）は必須。plan の画面 id と要素 id は、そのリポジトリの画面マップを
    前提に書いたものなので、どのマップかも plan が持つ。相対パスなら plan の置き場所から読み、
    返す plan では絶対パスにしておく（どこで叩いても同じマップを読むように）。
    """
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit("plan を読めない: {}: {}".format(path, e))
    unknown = set(plan) - PLAN_KEYS
    if unknown:
        hint = ""
        if unknown & {"items", "explore"}:
            hint = ("。項目はテストケースに入れる（{\"cases\": [{\"title\": …, \"items\": [...]}]}）。"
                    "経路が組めないテストケースには \"explore\": 理由 を付ける")
        elif unknown & {"runtime", "inputs"}:
            hint = "。打つ文字の決め方は do の操作に書く（{\"op\": …, \"runtime\": true}）"
        elif "shots_dir" in unknown:
            hint = "。証跡の出力先は manifest.py の引数で決まる"
        sys.exit("plan に知らない鍵: {}（使えるのは {}）{}".format(
            ", ".join(sorted(unknown)), ", ".join(sorted(PLAN_KEYS)), hint))
    if not plan.get("repo"):
        sys.exit("plan に repo（アプリのリポジトリ）が要る。plan の画面 id と要素 id が前提にしている"
                 "画面マップは、その下の screen-map/ にある")
    repo = Path(str(plan["repo"])).expanduser()
    plan["repo"] = str((repo if repo.is_absolute() else Path(path).resolve().parent / repo).resolve())
    read_cases(plan)
    return plan


def read_cases(plan):
    """plan の cases を確かめて、テストケースの並びにする。

    [{title, launch, explore, items: [{name, item, expect}]}]。`name` は
    テストケースをまたいだ通し番号、`item` は plan の項目そのまま、`expect` は前の項目を指す
    `{id}` を証跡の名前に展開したもの（「test_04（一覧の行をタップすると詳細に移る）」）。

    **テストケースの中の項目は前の項目に依存してよく、テストケースどうしは依存しない。**
    なので `{id}` で指せるのは、同じテストケースの中の自分より前の項目だけ。
    """
    cases = plan.get("cases")
    if cases is None:
        cases = []
    if not isinstance(cases, list):
        sys.exit("plan の cases はテストケースの配列で書く")
    out, titles, n = [], set(), 0
    for c, case in enumerate(cases, 1):
        where = " cases[{}] ".format(c)
        if not isinstance(case, dict):
            sys.exit("plan の{}はテストケース（{{\"title\": …, \"items\": [...]}}）で書く".format(where))
        unknown = set(case) - CASE_KEYS
        if unknown:
            hint = ""
            if unknown & {"from", "do", "expect"}:
                hint = "。項目はテストケースの items に入れる"
            elif "fresh" in unknown:
                hint = "。後に残る状態は画面マップの操作に leaves（と、既定に戻す操作 reset）を書く。スクリプトがテストケースの後に reset を叩くか、起動し直す。起動そのものを確かめるなら launch"
            elif "reason" in unknown:
                hint = "。経路が組めない理由は \"explore\": 理由 で書く"
            sys.exit("plan の{}に知らない鍵: {}（使えるのは {}）{}".format(
                where, ", ".join(sorted(unknown)), ", ".join(sorted(CASE_KEYS)), hint))
        title = case.get("title")
        if not isinstance(title, str) or not title.strip():
            sys.exit("plan の{}に title（何の機能を確かめるまとまりか）が無い".format(where))
        where = "テストケース「{}」".format(title)
        if title in titles:
            sys.exit("plan の{}が2つある。テストケースの題は分ける（マニフェストとレポートで"
                     "まとまりを引く鍵になる）".format(where))
        titles.add(title)
        if "launch" in case and not isinstance(case["launch"], bool):
            sys.exit("plan の{}の launch は true / false で書く".format(where))
        explore = case.get("explore")
        if "explore" in case and not (isinstance(explore, str) and explore.strip()):
            sys.exit("plan の{}の explore には、経路が組めない理由を文で書く".format(where))
        items = case.get("items")
        if not isinstance(items, list) or not items:
            sys.exit("plan の{}に items（項目）が無い".format(where))
        ids, rows = {}, []
        for item in items:
            n += 1
            name = shot_name(n)
            if not isinstance(item, dict):
                sys.exit("plan の {} の項目は {{\"from\": …, \"title\": …, \"expect\": …}} で書く".format(name))
            unknown = set(item) - ITEM_KEYS
            if unknown:
                hint = ""
                if "fresh" in unknown:
                    hint = ("。起動し直せるのはテストケースの境目だけ" + "。後に残る状態は画面マップの操作に leaves（と、既定に戻す操作 reset）を書く。スクリプトがテストケースの後に reset を叩くか、起動し直す" +
                            "。起動そのものを確かめるテストケースには launch を付ける")
                elif "shot" in unknown:
                    hint = "。証跡の名前は並び順から振る"
                elif unknown & {"steps", "goto"}:
                    hint = "。経路は書かない — from に着くまではスクリプトが計算する"
                elif unknown & {"runtime", "inputs"}:
                    hint = "。値の決め方は do の操作に書く（{\"op\": …, \"runtime\": true}）"
                elif "screen" in unknown:
                    hint = "。操作を始める画面は from"
                elif "reason" in unknown:
                    hint = "。経路が組めない理由は、テストケースに \"explore\": 理由 で書く"
                sys.exit("plan の {} に知らない鍵: {}（使えるのは {}）{}".format(
                    name, ", ".join(sorted(unknown)), ", ".join(sorted(ITEM_KEYS)), hint))
            if not item.get("from"):
                sys.exit("plan の {} に from（操作を始める画面）が無い".format(name))
            expect = item.get("expect", "")
            for ref in REF.findall(expect if isinstance(expect, str) else ""):
                if ref not in ids:
                    sys.exit("plan の {} の expect の {{{}}} が指す項目が無い。指せるのは同じテストケースの"
                             "自分より前の項目の id だけ（{}）".format(
                                 name, ref, ", ".join(ids) or "前に id の付いた項目が無い"))
            expect = REF.sub(lambda m: ids[m.group(1)], expect) if isinstance(expect, str) else expect
            if "id" in item:
                iid = item["id"]
                if not (isinstance(iid, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", iid)):
                    sys.exit("plan の {} の id は英数字と _ . - で書く: {}".format(
                        name, json.dumps(iid, ensure_ascii=False)))
                if iid in ids:
                    sys.exit("plan の{}の id「{}」が {} で使われている".format(where, iid, ids[iid]))
                ids[iid] = "{}（{}）".format(name, item.get("title", ""))
            rows.append({"name": name, "item": item, "expect": expect})
        out.append({"title": title, "launch": bool(case.get("launch")),
                    "explore": explore, "items": rows})
    return out


def read_items(plan):
    """フローを組む項目を確かめて、[{name, from, do: [(操作id, 値の決め方)], restart, when}] にする。

    `explore` の付いたテストケースは入れない（経路が組めず、フローを持たない）。

    `do` の要素は操作id（`"tap:Search"` / `"see:list.footer"`）か、値の決め方を添えた
    `{"op": 操作id, "runtime": true}` / `{"op": 操作id, "input": 値}`（打つ文字）/
    `{"op": 操作id, "pick": 条件}`（パターンの要素から条件に合うものを選ぶ）。
    子の要素（マップの `children`）には、どの親の中でするかを `in` で添えられる
    （`{"op": 操作id, "in": 親の ID}`。ほかの値の決め方と一緒に書いてよい）。

    `when` は項目の前提（マップの `when` の文言をそのまま写す）。条件つきの辺と、
    結果が分かれる操作の枝は、ここに同じ文言があるときだけ使う。

    `restart` が真の項目は、アプリを起動し直した直後から始める。**テストケースの頭にしか
    立たない** — そのテストケースに `launch` があるとき。後に残った状態で起動し直すかは、
    ここではなく build_steps() が画面マップの `leaves` / `reset` から決める。テストケースの
    途中で起動し直すと、前の項目を当てにしている項目の前提が消える。
    """
    out = []
    for case in read_cases(plan):
        if case["explore"]:
            continue       # フローで走らないので、前のテストケースが残した状態もそのまま次へ渡る
        for k, row in enumerate(case["items"]):
            item, name = row["item"], row["name"]
            do = item.get("do") or []
            if isinstance(do, (str, dict)):
                sys.exit("plan の {} の do は配列で書く: {}".format(name, json.dumps(do, ensure_ascii=False)))
            ops = []
            for d in do:
                if isinstance(d, str):
                    ops.append((d, {}))
                    continue
                modes = [k2 for k2 in ("runtime", "input", "pick") if k2 in d] if isinstance(d, dict) else []
                within = read_in(d.get("in")) if isinstance(d, dict) and "in" in d else None
                bad = not isinstance(d, dict) or not d.get("op") or set(d) - DO_KEYS \
                    or len(modes) > 1 or (not modes and within is None) or d.get("runtime") not in (None, True) \
                    or ("pick" in d and not (isinstance(d["pick"], str) and d["pick"].strip())) \
                    or within is False
                if bad:
                    sys.exit("plan の {} の do の要素は操作id か {{\"op\": 操作id, \"input\": 値}} か "
                             "{{\"op\": 操作id, \"runtime\": true}} か {{\"op\": 操作id, \"pick\": 条件}}、"
                             "親を選ぶなら {{\"op\": 操作id, \"in\": 親の ID か [外から順の ID] か "
                             "{{\"pick\": 条件}}}}: {}".format(name, json.dumps(d, ensure_ascii=False)))
                how = {modes[0]: d[modes[0]]} if modes else {}
                if within is not None:
                    how["in"] = within
                ops.append((d["op"], how))
            when = item.get("when") or []
            out.append({"name": name, "from": item["from"], "do": ops,
                        "restart": k == 0 and case["launch"],
                        "case": case["title"], "case_last": k == len(case["items"]) - 1,
                        "when": tuple([when] if isinstance(when, str) else when)})
    return out


def read_in(v):
    """plan の `in` を、パターンの親ごとの ID の並び（外から順）か `{"pick": 条件}` にする。読めなければ False。"""
    if isinstance(v, str) and v.strip():
        return [v]
    if isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v):
        return list(v)
    if isinstance(v, dict) and set(v) == {"pick"} and isinstance(v["pick"], str) and v["pick"].strip():
        return {"pick": v["pick"]}
    return False


# ---------- 項目をステップにする ----------

def build_steps(mp, items):
    """項目を順にステップ列にする。(ステップ列, 組めなかった理由, 補足, テストケースの後始末)。

    項目ごとに: `restart` なら起動し直す → bridge.py で `from` まで繋ぐ → `do` を1つずつ
    ステップにする → 撮る。**組めなかったらそこで止める**（先の項目は、手前の項目が
    終わった画面を前提にしているので、組んでも意味が無い）。

    **テストケースの後始末は画面マップが決める**（#83）。テストケースの `do` で、マップに
    `leaves`（後に残る状態）のある操作を使ったら、そのテストケースを撮り終えたあとに:

    - 全部に `reset`（既定に戻す操作）があれば、その画面まで繋いで `reset` を叩く。入力欄に
      打っていたら、最後にキーボードも閉じる
    - `reset` の無いものが1つでもあれば、次のテストケースの頭で起動し直す
    - `reset` まで経路が組めなければ、起動し直しに倒す
    - **テストケースの中ですでに `reset` の操作を叩いていたら、その `leaves` は片付いている。**
      後始末には入れない（クリアで絞り込みを解除するテストケース、など）

    次のテストケースがもともと起動し直す（`launch`）なら何もしない。最後の
    テストケースの後も何もしない。後始末は {テストケースの題: {"relaunch", "resets", "leaves"}} で
    返し、マニフェストの `cases` に載る（レビューで「ここで〜する」と読み上げる）。
    """
    route, steps, problems, afters = Route(mp), [], [], {}
    left, typed, restart_next = [], False, False
    for n, item in enumerate(items):
        name = item["name"]
        tag = "({}) goto {}".format(name, item["from"])
        first = len(steps)
        try:
            if (item["restart"] or restart_next) and steps:
                steps.append(route.restart())
            restart_next = False
            steps += route.to(item["from"], item["when"])
            for op, how in item["do"]:
                tag = "({}) do {}".format(name, op)
                spec, _, _ = resolve(mp, route.at, op)
                if spec is not None:
                    # テストケースの中で reset の操作をもう叩いたなら、その状態はもう戻っている。
                    # 後始末でもう一度叩くと、戻した結果その要素が消えている（空の入力欄では
                    # クリアボタンが出ない）ことがあり、そこで落ちる
                    done = "{}:{}".format(spec.op, spec.target)
                    left = [(sid, l) for sid, l in left if not (sid == route.at and l.reset == done)]
                if spec is not None and spec.leaves:
                    left.append((route.at, spec))
                typed = typed or (spec is not None and spec.op == "text")
                sts, wrong = do_step(mp, route, op, how, item["when"])
                steps += sts
                # 値の決め方の間違い。経路は組めるので、ほかの間違いもまとめて出す
                problems += [("call", "({}) {}".format(name, m)) for m in wrong]
            steps.append(Shot(name))
            if item.get("case_last"):
                nxt = items[n + 1] if n + 1 < len(items) else None
                after, restart_next = clean_up(mp, route, steps, left, typed, nxt)
                if after:
                    afters[item["case"]] = after
                left, typed = [], False
        except Unroutable as e:
            problems += [(kind, "{}: {}".format(tag, msg)) for kind, msg in e.problems]
        for st in steps[first:]:
            st.item = name
        if problems:
            break
    return steps, problems, route.notes, afters


def clean_up(mp, route, steps, left, typed, nxt):
    """テストケースの後始末（build_steps の説明）。(マニフェストに載せる後始末, 次の頭で起動し直すか)。
    steps には `reset` のステップを足す。"""
    if not left or nxt is None or nxt["restart"]:
        return None, False
    leaves = list(dict.fromkeys(spec.leaves for _, spec in left))
    if not all(spec.reset for _, spec in left):
        return {"relaunch": True, "resets": [], "leaves": leaves}, True
    # 後に残したものから戻す。同じ画面の同じ reset は1回
    resets = list(dict.fromkeys((sid, spec.reset) for sid, spec in reversed(left)))
    at, stack, mark = route.at, list(route.stack), len(steps)
    try:
        for sid, op in resets:
            steps += route.to(sid)
            sts, _ = do_step(mp, route, op, {}, ())
            steps += sts
        if typed:
            steps.append(Act(route.at, HideKeyboard(), []))
    except Unroutable:
        # reset まで繋げない（戻す画面に行けない、reset の要素が条件つき）。起動し直しに倒す
        del steps[mark:]
        route.at, route.stack = at, stack
        return {"relaunch": True, "resets": [], "leaves": leaves}, True
    return {"relaunch": False, "resets": [op for _, op in resets], "leaves": leaves}, False


def resolve(mp, at, wanted):
    """その画面の操作を1つ選ぶ。(操作, 要素, 実行時の値)。無ければ (None, None, None)。

    `tap:<id>` のように種類を頭に付けて指す。`see:<id>` は操作ではなく、要素を
    見る（見えるまでスクロールして確かめる）もので、操作は None で要素だけ返す。
    パターンの要素は具体的な ID でも指せる（`tap:list.row.牛乳`）。
    """
    want_op, _, want_target = wanted.partition(":")
    if want_op not in DO_OPS:
        want_op, want_target = None, wanted
    if want_op in GESTURES:
        for a in mp.screens[at].actions:
            if a.op == want_op and a.target == want_target:
                return a, None, None
        return None, None, None
    el, value = mp.screens[at].element(want_target)
    if el is None:
        return None, None, None
    if want_op == "see":
        return None, el, value
    for a in mp.screens[at].actions:
        if a.element is el and (want_op is None or a.op == want_op):
            return a, el, value
    return None, el, value


def known_ops(mp, at):
    scr = mp.screens[at]
    out = ["{}:{}".format(a.op, a.target) for a in scr.actions]
    out += ["see:{}".format(el.get("id")) for el in scr.elements if not el.get("actions")]
    return ", ".join(out)


def do_step(mp, route, op, how, given):
    """項目の do の1つをステップにする。(ステップの並び, 呼び方の間違いの文の並び)。
    画面を移る操作なら route の居る画面も進める。

    `how` はテストケースが添えた値の決め方（`input` / `runtime` / `pick`）と、子の要素の
    親の選び方（`in`）。パターンの要素を ID まで書いた操作（`tap:list.row.牛乳`）は、
    その ID を操作の対象にする。

    **子の要素（マップの `children`）なら、親を決めるステップ（Enter）を手前に置く。**
    パターンの親のどれの中でするかを、`in` が無ければ走らせるときに決める。
    """
    at = route.at
    how = dict(how)
    given_in = how.pop("in", None)
    kind, _, rest = op.partition(":")
    if kind == "see" and rest.startswith("text:"):
        if how or given_in is not None:
            raise Unroutable([("call", "「{}」に値は添えられない（待つ文言は op に書く）".format(op))])
        words = rest[len("text:"):]
        if not words.strip():
            raise Unroutable([("call", "「{}」に待つ文言が無い".format(op))])
        return [See(at, words, None, by_label=True, text=True)], []
    found, el, val = resolve(mp, at, op)
    target = None if val is None else pattern_prefix(el.get("id")) + val
    parents = mp.screens[at].parents(el) if el is not None else []
    if given_in is not None and not parents:
        raise Unroutable([("call", "「{}」は子の要素（マップの children）ではないので in は添えられない".format(op))])
    enters, within, wrong_in = nest(at, parents, given_in, "「{}」".format(op))
    if kind == "see" and el is not None:
        st = See(at, target or el.get("id"), el.get("name"), el.get("by") == "label", within=within)
        if how:
            if "pick" in how or not is_pattern(st.target) or st.by_label:
                raise Unroutable([("call", "「{}」に添えられるのは input か runtime で、パターンの要素"
                                           "（`*` で終わる ID）にだけ。その語を含む行を待つ".format(op))])
            st.contains = str(how["input"]) if "input" in how else None
            st.later = bool(how.get("runtime"))
        return enters + [st], wrong_in
    if found is None:
        raise Unroutable([("call", "{} に「{}」という操作がマップに無い。この画面にあるのは {}"
                                   .format(at, op, known_ops(mp, at) or "（無し）"))])
    if not found.in_tree():
        raise Unroutable([("map", "{} の「{}」は in_tree: false（座標が要る）。フローでは押せない"
                                  .format(at, found.label()))])
    action, wrong = resolve_action(found, target, how)
    wrong = wrong_in + wrong
    if found.is_back():
        st = route.back(found)       # 戻る先は歩いた履歴で決まる
        st.action = action
        st.within = within
        return enters + [st], wrong
    # 結果が分かれるなら、確認項目の前提（when）に合う枝だけ
    expects = [found.branches[branch_of(found, at, given)]] if found.branches else found.expects
    st = Act(at, action, resolve_result(found, expects), within=within)
    if st.arrive is None:
        return enters + [st], wrong
    if st.to not in mp.screens:
        raise Unroutable([("map", "{} の「{}」の遷移先 {} のファイルが無い"
                                  .format(at, found.label(), st.to))])
    return enters + [route.forward(st, st.to)], wrong


def branch_of(action, at, given):
    """結果が状態で分かれる操作の、確認項目の前提（when）に合う枝の番号。"""
    hit = [i for i, b in enumerate(action.branches) if b.get("when") in given]
    if len(hit) != 1:
        raise Unroutable([("call", "{} の「{}」は結果が分かれる（{}）。項目の when にどちらかを書く"
                                   .format(at, action.label(),
                                           " / ".join(b.get("when") for b in action.branches)))])
    return hit[0]


# ---------- フローに書く ----------

def write_flows(plan, out_dir, timeout=10000):
    """plan.json の項目ごとに Maestro のフローを out_dir に書き、項目ごとの行を返す。

    **項目（撮影）ごとに1本ずつ。** 走らせる側は順に run して inspect するだけで、
    証跡と同名のダンプが揃う。2本目以降は前の続き（起動し直さない）。
    標準出力には読める経路を出す（レビューの2段目）。組めなければ理由を出して
    終了コード 2 で終わり、何も書かない。

    **実行時に値を決める操作があれば、項目のフローをその手前で割る**（`parts`）。
    打つ文字、パターンの要素のどれを押すか。前の本で目的の画面に着き、対象が
    見えるまでスクロールして止まり、走らせる側が値を決めてから次の本を走らせる。

    返す行は、plan の項目の欄（`title` / `expect` / `from`）と、ここで計算した欄
    （撮った画面・自動確認のID・フローのファイル名・起動し直すか・実行時に決める値）を
    1つにしたもの。呼ぶ側が plan と突き合わせずに済むように。

    **フローは端末によらず1組。** 撮影先は `${SHOTS}` のまま書き、走らせる側
    （run_flows.py）が端末に合わせて埋める。経路も操作も端末で変わらない。
    """
    items = read_items(plan)
    app, clear = plan.get("app"), bool(plan.get("clear_state"))
    if not items:
        sys.exit("plan にフローで撮る項目が無い（全テストケースが explore ならフローは要らない）")
    if not app:
        sys.exit("plan に app（bundle id）が要る（xcrun simctl listapps <UDID> で調べる）")
    mp = load_map(plan.get("repo"))
    steps, problems, notes, afters = build_steps(mp, items)
    if problems:
        report_problems(problems)
        sys.exit(2)

    by_shot = {row["name"]: (case, row) for case in read_cases(plan) for row in case["items"]}
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    # 押す前のスクロール（Reveal）と、外から戻す操作（Return）を挟んでから切る。経路の表示（emit_path）は挟む前の steps で出す
    for seg_start, seg_steps, shot, lch in split_at_shots(mp, add_returns(add_reveals(steps)), None):
        names = assign_vars(seg_steps)
        parts = split_parts(seg_start, seg_steps)
        files = []
        for k, (p_start, p_steps) in enumerate(parts):
            last = k == len(parts) - 1
            # 自分で起動するのは1本目と、起動し直すテストケースの頭の項目の、最初の本だけ。他は居る場所から続ける
            flow, _ = emit_flow(mp, p_steps, app, clear, None, timeout, p_start, launch=lch and k == 0,
                                names=names)
            name = shot + ".yaml" if last else "{}.{}.yaml".format(shot, k + 1)
            (d / name).write_text(flow, encoding="utf-8")
            files.append({"flow": name, "decide": var_of(p_steps[0], names) if k > 0 else None})
        screen, checked = shot_context(mp, seg_start, seg_steps)
        uses = runtime_uses(seg_steps, names)
        picks = runtime_picks(mp, seg_steps, names)
        sees = runtime_sees(mp, seg_steps, names)
        # 走らせる側（や LLM）が埋める値。見えている1件目を選ぶものは run_flows.py が埋めるので入れない
        # 何番目か（_INDEX）は run_flows.py が数えるので入れない
        inputs = {v: "" for v, use in uses.items()
                  if use != "index" and not (v in picks and not picks[v]["pick"])}
        case, row = by_shot[shot]
        it = row["item"]
        last = row is case["items"][-1]
        written.append({"name": shot, "case": case["title"], "title": it.get("title", ""),
                        # テストケースの後始末（画面マップの leaves / reset から。最後の項目にだけ）
                        "after": afters.get(case["title"]) if last else None,
                        "from": it.get("from"),
                        "when": it.get("when") or [],
                        "do": it.get("do") or [], "expect": row["expect"],
                        "screen": screen, "checked": checked, "launch": lch,
                        "inputs": inputs, "input_use": uses, "picks": picks, "sees": sees,
                        "parts": files, "flow": files[-1]["flow"]})
    print(emit_path(mp, steps, notes))
    print("\n  フロー（{}本）: {}".format(len(written), out_dir))
    for w in written:
        print("    {}{}  →  ダンプ名 {}  自動確認 {}".format(
            w["flow"], " ★起動し直す" if w["launch"] else "",
            w["name"], w["checked"] or "なし"))
    return written
