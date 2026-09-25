"""plan.json から、項目ごとの Maestro のフローを作る（manifest.py から呼ぶ）。plan の形は manifest.py --help。

項目1つ＝「`from` から `do` を順に叩いて、1枚撮る」。**項目は経路を持たない。**
前の項目が終わった画面から次の項目の `from` までは screen-map の screenmap/route.py が繋ぐ（すでに居れば何もしない）。
ここがするのは、項目の `do` をマップの操作に引き当ててステップにすることと、
できたステップ列を maestro.py でフローに書くこと。

  plan の項目 ──→ build_steps() ──→ ステップ列 ──→ write_flows() ──→ フロー（撮影ごとに1本）
                   ├ 項目の間: route.py（Route.to）
                   └ 項目の do: do_step()
"""
import json
import sys
from pathlib import Path

from .maestro import (assign_vars, emit_flow, runtime_picks, runtime_uses, shot_context,
                      split_at_shots, split_parts, var_of)
from screenmap.model import DO_OPS, FORWARD, GESTURES, is_pattern, load_map, pattern_prefix
from screenmap.route import Route, Unroutable, emit_path, report_problems
from screenmap.steps import Act, See, Shot

PLAN_KEYS = {"app", "clear_state", "items", "explore"}
ITEM_KEYS = {"from", "do", "fresh", "title", "expect", "when"}
DO_KEYS = {"op", "runtime", "input", "pick"}
EXPLORE_KEYS = {"from", "title", "expect", "reason", "when"}


def shot_name(n):
    """項目の名前。並び順から振る（explore は items の続きの番号）。証跡・ダンプ・
    フローのファイル名になる。端末はディレクトリで分けるので、名前には入れない。"""
    return "test_{:02d}".format(n)


# ---------- plan を読む ----------

def load_plan(path):
    """plan.json を読み、鍵を確かめて返す。**知らない鍵は綴り違い** — 黙って無視すると、
    その指定が効かないまま走る。"""
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit("plan を読めない: {}: {}".format(path, e))
    unknown = set(plan) - PLAN_KEYS
    if unknown:
        hint = ""
        if unknown & {"runtime", "inputs"}:
            hint = "。打つ文字の決め方は do の操作に書く（{\"op\": …, \"runtime\": true}）"
        elif "shots_dir" in unknown:
            hint = "。証跡の出力先は manifest.py の引数で決まる"
        sys.exit("plan に知らない鍵: {}（使えるのは {}）{}".format(
            ", ".join(sorted(unknown)), ", ".join(sorted(PLAN_KEYS)), hint))
    for n, it in enumerate(plan.get("explore") or [], 1):
        unknown = set(it) - EXPLORE_KEYS
        if unknown:
            sys.exit("plan の explore[{}] に知らない鍵: {}（使えるのは {}）".format(
                n, ", ".join(sorted(unknown)), ", ".join(sorted(EXPLORE_KEYS))))
    return plan


def read_items(plan):
    """plan の items を確かめて、[{name, from, do: [(操作id, 値の決め方)], fresh, when}] にする。

    `do` の要素は操作id（`"tap:Search"` / `"see:list.footer"`）か、値の決め方を添えた
    `{"op": 操作id, "runtime": true}` / `{"op": 操作id, "input": 値}`（打つ文字）/
    `{"op": 操作id, "pick": 条件}`（パターンの要素から条件に合うものを選ぶ）。

    `when` は項目の前提（マップの `when` の文言をそのまま写す）。条件つきの辺と、
    結果が分かれる操作の枝は、ここに同じ文言があるときだけ使う。

    `fresh` が真の項目は、アプリを起動し直した直後から始める（その項目の前提）。
    `title` / `expect` は読まない（manifest.py が読む）。`explore` も見ない —
    経路が組めなかった項目で、フローを持たない。
    """
    out = []
    for n, item in enumerate(plan.get("items") or [], 1):
        name = shot_name(n)
        unknown = set(item) - ITEM_KEYS
        if unknown:
            hint = ""
            if "shot" in unknown:
                hint = "。証跡の名前は並び順から振る"
            elif unknown & {"steps", "goto"}:
                hint = "。経路は書かない — from に着くまでは route.py が計算する"
            elif unknown & {"runtime", "inputs"}:
                hint = "。値の決め方は do の操作に書く（{\"op\": …, \"runtime\": true}）"
            elif "screen" in unknown:
                hint = "。操作を始める画面は from"
            sys.exit("plan の {} に知らない鍵: {}（使えるのは {}）{}".format(
                name, ", ".join(sorted(unknown)), ", ".join(sorted(ITEM_KEYS)), hint))
        if not item.get("from"):
            sys.exit("plan の {} に from（操作を始める画面）が無い".format(name))
        do = item.get("do") or []
        if isinstance(do, (str, dict)):
            sys.exit("plan の {} の do は配列で書く: {}".format(name, json.dumps(do, ensure_ascii=False)))
        ops = []
        for d in do:
            if isinstance(d, str):
                ops.append((d, {}))
                continue
            modes = [k for k in ("runtime", "input", "pick") if k in d] if isinstance(d, dict) else []
            bad = not isinstance(d, dict) or not d.get("op") or set(d) - DO_KEYS \
                or len(modes) != 1 or d.get("runtime") not in (None, True) \
                or ("pick" in d and not (isinstance(d["pick"], str) and d["pick"].strip()))
            if bad:
                sys.exit("plan の {} の do の要素は操作id か {{\"op\": 操作id, \"input\": 値}} か "
                         "{{\"op\": 操作id, \"runtime\": true}} か {{\"op\": 操作id, \"pick\": 条件}}: {}".format(
                             name, json.dumps(d, ensure_ascii=False)))
            ops.append((d["op"], {modes[0]: d[modes[0]]}))
        when = item.get("when") or []
        out.append({"name": name, "from": item["from"], "do": ops, "fresh": bool(item.get("fresh")),
                    "when": tuple([when] if isinstance(when, str) else when)})
    return out


# ---------- 項目をステップにする ----------

def build_steps(mp, items):
    """項目を順にステップ列にする。(ステップ列, 組めなかった理由, 補足)。

    項目ごとに: `fresh` なら起動し直す → route.py で `from` まで繋ぐ → `do` を1つずつ
    ステップにする → 撮る。**組めなかったらそこで止める**（先の項目は、手前の項目が
    終わった画面を前提にしているので、組んでも意味が無い）。
    """
    route, steps, problems = Route(mp), [], []
    for item in items:
        name = item["name"]
        tag = "({}) goto {}".format(name, item["from"])
        first = len(steps)
        try:
            if item["fresh"] and steps:
                steps.append(route.restart())
            steps += route.to(item["from"], item["when"])
            for op, how in item["do"]:
                tag = "({}) do {}".format(name, op)
                steps.append(do_step(mp, route, op, how, item["when"]))
            steps.append(Shot(name))
        except Unroutable as e:
            problems += [(kind, "{}: {}".format(tag, msg)) for kind, msg in e.problems]
        for st in steps[first:]:
            st.item = name
        if problems:
            break
    check_values(steps, problems)
    return steps, problems, route.notes


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
        for a in mp.actions(at):
            if a.op == want_op and a.target == want_target:
                return a, None, None
        return None, None, None
    el, value = mp.element(at, want_target)
    if el is None:
        return None, None, None
    if want_op == "see":
        return None, el, value
    for a in mp.actions(at):
        if a.element is el and (want_op is None or a.op == want_op):
            return a, el, value
    return None, el, value


def known_ops(mp, at):
    out = ["{}:{}".format(a.op, a.target) for a in mp.actions(at)]
    out += ["see:{}".format(el.get("id")) for el in mp.elements(at) if not el.get("actions")]
    return ", ".join(out)


def do_step(mp, route, op, how, given):
    """項目の do の1つをステップにする。画面を移る操作なら route の居る画面も進める。

    `how` はテストケースが添えた値の決め方（`input` / `runtime` / `pick`）。
    """
    at = route.at
    found, el, val = resolve(mp, at, op)
    if op.partition(":")[0] == "see" and el is not None:
        return See(at, el, value=val)
    if found is None:
        raise Unroutable([("call", "{} に「{}」という操作がマップに無い。この画面にあるのは {}"
                                   .format(at, op, known_ops(mp, at) or "（無し）"))])
    if not found.in_tree():
        raise Unroutable([("map", "{} の「{}」は in_tree: false（座標が要る）。フローでは押せない"
                                  .format(at, found.label()))])
    if found.is_back():
        st = route.back(found)
    else:
        st = Act(at, found)
        if found.branches:
            st.branch = branch_of(found, at, given)
    st.value = val
    for k, v in how.items():          # input / runtime / pick
        setattr(st, k, v)
    if found.is_back():
        return st
    dest = next((e for e in st.outcome() if e.get("screen") and e.get("via") in FORWARD), None)
    if dest is None:
        return st
    if dest["screen"] not in mp.screens:
        raise Unroutable([("map", "{} の「{}」の遷移先 {} のファイルが無い"
                                  .format(at, found.label(), dest["screen"]))])
    st.to, st.via = dest["screen"], dest["via"]
    return route.forward(st, dest["screen"])


def branch_of(action, at, given):
    """結果が状態で分かれる操作の、確認項目の前提（when）に合う枝の番号。"""
    hit = [i for i, b in enumerate(action.branches) if b.get("when") in given]
    if len(hit) != 1:
        raise Unroutable([("call", "{} の「{}」は結果が分かれる（{}）。項目の when にどちらかを書く"
                                   .format(at, action.label(),
                                           " / ".join(b.get("when") for b in action.branches)))])
    return hit[0]


def check_values(steps, problems):
    """値の決め方（打つ文字、どの行を押すか）を確かめる。**呼び方の間違いは走らせる前に止める。**

    パターンの要素（ID の末尾が *）を押すステップには `pick` を付ける（空なら、画面に
    見えている1件目）。どれを押すかは走らせるときに決まる。
    """
    for st in steps:
        if not isinstance(st, Act):
            continue
        a = st.action
        where = "({}) ".format(st.item) if st.item else ""
        pattern = a.element is not None and is_pattern(a.element.get("id")) and st.value is None
        if st.runtime and a.op != "text":
            problems.append(("call", "{}{} に runtime は付けられない。どの行を押すかは"
                             "スクリプトが決める（条件があるなら pick に書く）".format(where, a.label())))
        if st.pick is not None and not pattern:
            problems.append(("call", "{}{} はパターンの要素（ID の末尾が *）ではないので、"
                             "実行時に選べない（pick を外す）".format(where, a.label())))
        if a.op == "text" and st.input is None and not st.runtime:
            problems.append(("call", "{}text {} に打つ文字が渡されていない（do に {{\"op\": …, \"input\": 値}} か "
                             "{{\"op\": …, \"runtime\": true}} で書く）。"
                             "マップは値を持たない。何を打つかはテストケースが決める"
                             .format(where, a.target)))
        if pattern and a.op == "text":
            # どれに打つかと何を打つかの2つを実行時に決めることになる。変数が1つしか持てない
            problems.append(("call", "{}text {} はパターンの要素なので、どの欄に打つかを ID まで書く"
                             "（text:{}<表示中の名前>）".format(where, a.target, pattern_prefix(a.target))))
        elif pattern:
            st.pick = st.pick or ""


# ---------- フローに書く ----------

def write_flows(plan, out_dir, repo, timeout=10000):
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
        sys.exit("plan の items が空（経路が組めた項目が無いならフローは要らない）")
    if not app:
        sys.exit("plan に app（bundle id）が要る（xcrun simctl listapps <UDID> で調べる）")
    mp = load_map(repo)
    steps, problems, notes = build_steps(mp, items)
    if problems:
        report_problems(problems)
        sys.exit(2)

    by_shot = {shot_name(n): it for n, it in enumerate(plan.get("items") or [], 1)}
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for seg_start, seg_steps, shot, lch in split_at_shots(mp, steps, None):
        assign_vars(seg_steps)
        parts = split_parts(seg_start, seg_steps)
        files = []
        for k, (p_start, p_steps) in enumerate(parts):
            last = k == len(parts) - 1
            tail = None if last else parts[k + 1][1][0]
            # 自分で起動するのは1本目と fresh の項目の、最初の本だけ。他は居る場所から続ける
            flow, _ = emit_flow(mp, p_steps, app, clear, None, timeout,
                                p_start, launch=lch and k == 0, tail=tail)
            name = shot + ".yaml" if last else "{}.{}.yaml".format(shot, k + 1)
            (d / name).write_text(flow, encoding="utf-8")
            files.append({"flow": name, "decide": var_of(p_steps[0]) if k > 0 else None})
        screen, checked = shot_context(mp, seg_start, seg_steps)
        uses = runtime_uses(seg_steps)
        picks = runtime_picks(mp, seg_steps)
        # 走らせる側（や LLM）が埋める値。見えている1件目を選ぶものは run_flows.py が埋めるので入れない
        # 何番目か（_INDEX）は run_flows.py が数えるので入れない
        inputs = {v: "" for v, use in uses.items()
                  if use != "index" and not (v in picks and not picks[v]["pick"])}
        it = by_shot.get(shot, {})
        written.append({"name": shot, "title": it.get("title", ""),
                        "from": it.get("from"), "fresh": bool(it.get("fresh")),
                        "when": it.get("when") or [],
                        "do": it.get("do") or [], "expect": it.get("expect", ""),
                        "screen": screen, "checked": checked, "launch": lch,
                        "inputs": inputs, "input_use": uses, "picks": picks,
                        "parts": files, "flow": files[-1]["flow"]})
    print(emit_path(mp, steps, notes))
    print("\n  フロー（{}本）: {}".format(len(written), out_dir))
    for w in written:
        print("    {}{}  →  ダンプ名 {}  自動確認 {}".format(
            w["flow"], " ★起動し直す" if w["launch"] else "",
            w["name"], w["checked"] or "なし"))
    return written
