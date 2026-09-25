"""plan.json を読み、項目ごとのフローを書く（manifest.py から呼ぶ）。plan の形は manifest.py --help。"""
import json
import re
import sys
from pathlib import Path

from flow import (assign_vars, emit_flow, emit_path, runtime_picks, runtime_uses, shot_context,
                  split_at_shots, split_parts, var_of)
from screen_map import load_map
from walk import build


PLAN_KEYS = {"app", "clear_state", "items", "explore"}
ITEM_KEYS = {"from", "do", "fresh", "title", "expect", "when"}


def shot_name(n):
    """項目の名前。並び順から振る（explore は items の続きの番号）。証跡・ダンプ・
    フローのファイル名になる。端末はディレクトリで分けるので、名前には入れない。"""
    return "test_{:02d}".format(n)
DO_KEYS = {"op", "runtime", "input", "pick"}


EXPLORE_KEYS = {"from", "title", "expect", "reason", "when"}


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


def read_plan(plan):
    """plan.json をセグメントの列に開く。経路の計算も検査も build() に任せる。

    項目1つ＝「`from` から `do` を順に叩いて、1枚撮る」。**項目は経路を持たない。**
    `from` へどう着くかはここで `goto` を挟んで計算させる（すでに居れば何もしない）。
    テストケースが持つのは、どこから何を確かめるかだけ。

    `do` の要素は操作id（`"tap:Search"` / `"see:list.footer"`）か、値の決め方を添えた
    `{"op": 操作id, "runtime": true}` / `{"op": 操作id, "input": 値}`（打つ文字）/
    `{"op": 操作id, "pick": 条件}`（パターンの要素から条件に合うものを選ぶ）。

    `when` は項目の前提（マップの `when` の文言をそのまま写す）。条件つきの辺と、
    結果が分かれる操作の枝は、ここに同じ文言があるときだけ使う。

    `fresh` が真の項目は、アプリを起動し直した直後から始める（その項目の前提）。
    撮影は並び順から振った名前（`shot_name()`）で、置き場は `${SHOTS}` のまま残す。`title` / `expect` は読まない
    （manifest.py が読む）。`explore` も見ない — 経路が組めなかった項目で、フローを持たない。
    """
    items = plan.get("items") or []
    segments, owners = [], []   # owners: セグメントごとの項目名。エラーをどの項目か読めるように
    for n, item in enumerate(items, 1):
        shot = shot_name(n)
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
                shot, ", ".join(sorted(unknown)), ", ".join(sorted(ITEM_KEYS)), hint))
        start = item.get("from")
        if not start:
            sys.exit("plan の {} に from（操作を始める画面）が無い".format(shot))
        do = item.get("do") or []
        if isinstance(do, (str, dict)):
            sys.exit("plan の {} の do は配列で書く: {}".format(
                shot, json.dumps(do, ensure_ascii=False)))
        when = item.get("when") or []
        if isinstance(when, str):
            when = [when]

        def add(what, value="", **ctx):
            ctx["item"] = shot
            ctx["when"] = tuple(when)
            segments.append((what, value, ctx)); owners.append(shot)
        if item.get("fresh") and segments:
            add("restart")
        add("goto", start)
        for d in do:
            if isinstance(d, str):
                add("do", d)
                continue
            modes = [k for k in ("runtime", "input", "pick") if k in d] if isinstance(d, dict) else []
            bad = not isinstance(d, dict) or not d.get("op") or set(d) - DO_KEYS \
                or len(modes) != 1 or d.get("runtime") not in (None, True) \
                or ("pick" in d and not (isinstance(d["pick"], str) and d["pick"].strip()))
            if bad:
                sys.exit("plan の {} の do の要素は操作id か {{\"op\": 操作id, \"input\": 値}} か "
                         "{{\"op\": 操作id, \"runtime\": true}} か {{\"op\": 操作id, \"pick\": 条件}}: {}".format(
                             shot, json.dumps(d, ensure_ascii=False)))
            add("do", d["op"], **{modes[0]: d[modes[0]]})
        add("shot", shot)
    return segments, plan.get("app"), bool(plan.get("clear_state")), owners


def report_problems(problems, owners=None):
    """組めなかった理由を stderr に出す。plan から来たなら、区間の番号に項目の名前を添える。"""
    print("経路を組めなかった:", file=sys.stderr)
    for _, msg in problems:
        m = re.match(r"\[(\d+)\]", msg) if owners else None
        if m and 0 < int(m.group(1)) <= len(owners):
            msg = "{} ({})".format(m.group(0), owners[int(m.group(1)) - 1]) + msg[m.end():]
        print("  " + msg, file=sys.stderr)
    if any(kind == "map" for kind, _ in problems):
        print("\nマップの穴。埋めるのは screen-map の仕事で、"
              "ここで推測して繋がない。", file=sys.stderr)


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
    segments, app, clear, owners = read_plan(plan)
    items = plan.get("items") or []
    if not segments:
        sys.exit("plan の items が空（経路が組めた項目が無いならフローは要らない）")
    if not app:
        sys.exit("plan に app（bundle id）が要る（xcrun simctl listapps <UDID> で調べる）")
    mp = load_map(repo)
    steps, problems, notes = build(mp, segments)
    if problems:
        report_problems(problems, owners)
        sys.exit(2)

    by_shot = {shot_name(n): it for n, it in enumerate(items, 1)}
    segs = split_at_shots(mp, steps, None)
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for seg_start, seg_steps, shot, lch in segs:
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
            decide = var_of(p_steps[0]) if k > 0 else None
            files.append({"flow": name, "decide": decide})
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
