"""plan.json から、項目ごとの Maestro のフローを作る。plan の形は manifest.py --help。

plan はテストケース（`cases`）の集合で、項目は必ずどれかのテストケースに属する。
項目1つ＝「`from` から `do` を順に叩いて、1枚撮る」。**項目は経路を持たない。**
前の項目が終わった画面から次の項目の `from` までは bridge.py が繋ぐ（すでに居れば何もしない）。
ここがするのは、項目の `do` をマップの操作に引き当ててステップにすることと、
できたステップ列を maestro.py でフローに書くこと。

**フローはテストケースごとに、走らせる直前に組む。** 途中で1本落ちても、落ちた地点の画面から
次のテストケースを組めるように。テストケースをまたいで持ち越すもの（居る画面、履歴、
スクロール、アプリの外に居るか、後に残した状態）は `Cursor` に持たせて受け渡す。

  plan の項目 ─→ build_case() ─→ ステップ列 ─→ render_case() ─→ 項目ごとの Unit の並び＋次の Cursor
  （1テストケース）├ 頭: 前のテストケースの後始末（clean_up）か、起動し直し
                   ├ 項目の間: bridge.py（Route.to）
                   └ 項目の do: do_step()

**1項目は「経路のフロー」と「`do` の1手ずつ」に分かれる**（Unit）。経路（`from` まで）は
1本のフローで、要素は `scrollUntilVisible` で探す。ただし経路の途中の、値を決めるステップ
（パターンの要素を押す）と子の要素（と親を決める Enter）は、`do` と同じく1手にする（route_keys）。`do` の要素は run_flows.py が
ダンプを読みながら探し、スクロールの端に着いたら諦め、見つかれば1手ぶんのフロー（操作と
着いた確認）を流す。フローの `scrollUntilVisible` は端を検知しないので、無い要素では
上限の60秒を払ってしまう。フローは流す直前に `render_unit()` で書き、値はそこで埋める。

呼ぶのは2か所。

- manifest.py（手順0）: `plan_rows()`。テストケースごとに起動直後の画面から組めるかを確かめる
- run_flows.py（手順1）: `build_case()` と `render_case()`。前のテストケースが終わった状態から組む
"""
import json
import re
import sys
from pathlib import Path

from dataclasses import dataclass, field
from typing import Optional

from .maestro import (Return, Reveal, ScrollState, add_returns, add_reveals, caller_keys, emit_flow,
                      needs_value, shot_context, split_at_shots)
from screenmap.map import load_map
from screenmap.screen import DO_OPS, GESTURES, is_pattern, pattern_prefix
from .actions import HideKeyboard, resolve_action
from .bridge import Route, Unroutable, emit_path, report_problems
from .results import resolve_result
from .steps import Act, Enter, Restart, See, Shot, goes_out, nest, stays_out

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
    """フローを組む項目を確かめて、[{name, from, do: [(操作id, 値の決め方)], restart, case, case_last, when}] にする。

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
    ここではなく build_case() が画面マップの `leaves` / `reset` から決める。テストケースの
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


# ---------- テストケースをまたいで持ち越すもの ----------

@dataclass
class Cursor:
    """前のテストケースが終わった状態。次のテストケースはここから組む。

    - `at` / `stack`: 居る画面と、歩いてきた履歴（bridge.py の Route と同じもの）
    - `scroll`: どの画面がスクロールされているかもしれないか（maestro.py の ScrollState）
    - `out`: アプリの外に居るまま終わったなら、戻す画面。次のテストケースの頭で戻す
    - `restart`: 次のテストケースの頭で起動し直す
    - `left` / `typed`: 後に残した状態（画面マップの `leaves` のある操作を [(画面, 操作id)]）と、
      入力欄に打ったか。次のテストケースの頭で `reset` を叩いてキーボードを閉じる（clean_up）。
      戻せなければ、そこで起動し直す
    - `recovered`: 前のテストケースのフローが落ち、落ちた地点の画面から繋ぐ。頭の項目が
      落ちたら、繋ぎ方が悪かったのかもしれないので、起動し直してもう1回だけ走らせる

    **JSON にできる形だけで持つ。** 入力が未定で止まったとき、そのテストケースを組んだ
    状態をマニフェストに書いておき、叩き直したときに同じフローを組み直す。
    """
    at: str
    stack: list
    scroll: ScrollState = field(default_factory=ScrollState)
    out: Optional[str] = None
    restart: bool = False
    left: list = field(default_factory=list)
    typed: bool = False
    recovered: bool = False

    @classmethod
    def fresh(cls, mp):
        """何も走らせていない。起動から始める。"""
        return cls(mp.start, [mp.start], ScrollState(), restart=True)

    def to_json(self):
        return {"at": self.at, "stack": list(self.stack), "scroll": self.scroll.to_json(),
                "out": self.out, "restart": self.restart, "left": [list(x) for x in self.left],
                "typed": self.typed, "recovered": self.recovered}

    @classmethod
    def from_json(cls, d):
        return cls(d["at"], list(d["stack"]), ScrollState.from_json(d.get("scroll")), d.get("out"),
                   bool(d.get("restart")), [tuple(x) for x in d.get("left") or []],
                   bool(d.get("typed")), bool(d.get("recovered")))


def op_id(spec):
    return "{}:{}".format(spec.op, spec.target)


def flow_cases(plan):
    """フローで撮るテストケースの並び。[{title, items, next}]。`items` は read_items() の行、
    `next` は次のフローで撮るテストケースの頭の項目（無ければ None）。後始末をするか
    （次が起動し直すなら要らない）を決めるのに使う。"""
    out = []
    for item in read_items(plan):
        if not out or out[-1]["title"] != item["case"]:
            out.append({"title": item["case"], "items": [], "next": None})
        out[-1]["items"].append(item)
    for k in range(len(out) - 1):
        out[k]["next"] = out[k + 1]["items"][0]
    return out


# ---------- 項目をステップにする ----------

@dataclass
class CaseBuild:
    """1テストケースを組んだ結果。"""
    steps: list
    problems: list
    notes: list
    route: object                 # 組み終えたところの Route（居る画面・履歴・trail）
    left: list                    # 最後まで走ったときに後に残るもの [(画面, 操作の定義)]
    typed: bool
    touched: list                 # テストケースの中で使った leaves のある操作。落ちたときに戻す候補
    ends: dict                    # 項目の名前 → その項目を撮り終えた時点の route.trail の長さ


def build_case(mp, items, cursor):
    """1テストケースの項目を順にステップ列にする（CaseBuild）。`cursor` の状態から始める。

    頭で、前のテストケースが残したものを片付ける（clean_up）。起動し直すテストケース
    （`launch`）や、`cursor.restart` なら、代わりに起動し直す。そのあと項目ごとに:
    bridge.py で `from` まで繋ぐ → `do` を1つずつステップにする → 撮る。**組めなかったら
    そこで止める**（先の項目は、手前の項目が終わった画面を前提にしているので、組んでも
    意味が無い）。

    **`do` のステップには鍵（`key`）を振る**（steps.py の KEYS）。run_flows.py はそれを1手ずつ流す。

    **テストケースの後始末は画面マップが決める**。テストケースの `do` で、マップに
    `leaves`（後に残る状態）のある操作を使ったら、次のテストケースの頭で（clean_up）:

    - 全部に `reset`（既定に戻す操作）があれば、その画面まで繋いで `reset` を叩く
    - `reset` の無いものが1つでもあれば、起動し直す
    - `reset` まで経路が組めなければ、起動し直しに倒す
    - **テストケースの中ですでに `reset` の操作を叩いていたら、その `leaves` は片付いている。**
      後始末には入れない（クリアで絞り込みを解除するテストケース、など）

    **キーボードは、そのテストケースで入力欄に打っていれば、次のテストケースの頭で閉じる**
    （`hideKeyboard`）。`reset` を叩いたならそのあと。戻す状態が無くても閉じる（テスト
    ケースの中でクリアして終わった、打った操作にマップの `leaves` が無い、など）。テスト
    ケースの中の項目の間では閉じない — 後ろの項目が、打ったあとにキーボードのキー
    （検索キー）を押すことがある。
    """
    route = Route(mp, cursor.at, cursor.stack)
    steps, problems = [], []
    restart = cursor.restart or items[0]["restart"]
    if not restart and (cursor.left or cursor.typed):
        specs = [(sid, resolve(mp, sid, op)[0]) for sid, op in cursor.left]
        if any(spec is None for _, spec in specs):
            restart = True     # マップが変わって戻す操作が引けない。起動し直しに倒す
        else:
            _, restart = clean_up(mp, route, steps, specs, cursor.typed, items[0])
    left, touched, typed, ends = [], [], False, {}
    for n, item in enumerate(items):
        name = item["name"]
        tag = "({}) goto {}".format(name, item["from"])
        first = 0 if n == 0 else len(steps)
        try:
            if n == 0 and restart:
                steps.append(route.restart())
            steps += route.to(item["from"], item["when"])
            seen = {}
            for k, (op, how) in enumerate(item["do"]):
                tag = "({}) do {}".format(name, op)
                spec, _, _ = resolve(mp, route.at, op)
                if spec is not None:
                    # テストケースの中で reset の操作をもう叩いたなら、その状態はもう戻っている。
                    # 後始末でもう一度叩くと、戻した結果その要素が消えている（空の入力欄では
                    # クリアボタンが出ない）ことがあり、そこで落ちる
                    done = op_id(spec)
                    left = [(sid, l) for sid, l in left if not (sid == route.at and l.reset == done)]
                if spec is not None and spec.leaves:
                    left.append((route.at, spec))
                    touched.append((route.at, spec))
                typed = typed or (spec is not None and spec.op == "text")
                sts, wrong = do_step(mp, route, op, how, item["when"])
                # 鍵を振る。run_flows.py はこれを持つステップを1手ずつ流し、値もこの鍵で持つ
                seen[op] = seen.get(op, 0) + 1
                key = op if seen[op] == 1 else "{}#{}".format(op, seen[op])
                for st in sts:
                    st.key = "{} in {}".format(key, st.target) if isinstance(st, Enter) else key
                steps += sts
                # 値の決め方の間違い。経路は組めるので、ほかの間違いもまとめて出す
                problems += [("call", "({}) {}".format(name, m)) for m in wrong]
            steps.append(Shot(name))
        except Unroutable as e:
            problems += [(kind, "{}: {}".format(tag, msg)) for kind, msg in e.problems]
        for st in steps[first:]:
            st.item = name
        ends[name] = len(route.trail)
        if problems:
            break
    return CaseBuild(steps, problems, route.notes, route, left, typed, touched, ends)


def clean_up(mp, route, steps, left, typed, nxt):
    """前のテストケースの後始末（build_case の説明）。(マニフェストに載せる後始末, 起動し直すか)。
    steps には `reset` と、キーボードを閉じるステップを足す。`nxt` は後始末のあとに始める項目。"""
    if nxt is None or nxt["restart"]:
        return None, False
    if not left:
        # 戻す状態は無い（leaves の無い操作だけ、またはテストケースの中で reset 済み）。
        # 打っていればキーボードだけ閉じる
        if typed:
            steps.append(Act(route.at, HideKeyboard(), []))
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
            if any(needs_value(st) for st in sts):
                # 戻す操作に走らせるときに決める値が要る（パターンの要素など）。後始末では値を決めない。起動し直しに倒す
                raise Unroutable([("call", "reset に値が要る")])
            steps += sts
        if typed:
            steps.append(Act(route.at, HideKeyboard(), []))
    except Unroutable:
        # reset まで繋げない（戻す画面に行けない、reset の要素が条件つき）。起動し直しに倒す
        del steps[mark:]
        route.at, route.stack = at, stack
        return {"relaunch": True, "resets": [], "leaves": leaves}, True
    return {"relaunch": False, "resets": [op for _, op in resets], "leaves": leaves}, False


def stack_at(build, name, screen):
    """フローが落ちたあと、落ちた地点の画面 `screen` に積まれていた履歴。

    組んだときに居た画面と履歴（route.trail）を、落ちた項目 `name` を撮り終えるところまで
    後ろから見て、`screen` に居たときのものを採る。**組んだ予定のどこにも無い画面なら、
    その画面だけ** — どこから来たか分からないので、戻る操作が要る経路は組めなくなる
    （組めなければ起動し直しに倒す）。
    """
    trail = build.route.trail[:build.ends.get(name, len(build.route.trail))]
    for at, stack in reversed(trail):
        if at == screen:
            return list(stack)
    return [screen]


def screen_at(mp, ids, texts=()):
    """画面に出ている ID と表示テキストの並びから、居る画面を決める。(画面, anchor が見えている画面の並び)。

    `anchor_by: label` の画面は、anchor を含む表示テキストがあれば見えているとする（フローの
    `text` セレクタと同じく、前後に何か付いていても当てる）。

    **anchor が見えている画面がちょうど1つのときだけ決める。** 無い（アラート、システムの
    ダイアログ、アプリの外）か、2つ以上ある（シートの下に元の画面の anchor が残る、など）
    なら None で、呼ぶ側は起動し直す。
    """
    def hit(scr):
        anchor = str(scr.anchor)
        if scr.anchor_by_label:
            return any(anchor in t for t in texts)
        if anchor.endswith("*"):
            return any(i.startswith(anchor[:-1]) for i in ids)
        return anchor in ids
    hits = sorted(sid for sid, scr in mp.screens.items() if scr.anchor and hit(scr))
    return (hits[0] if len(hits) == 1 else None), hits


def recovered(mp, build, name, screen):
    """落ちたあと、次のテストケースをどこから組むか（Cursor）。`screen` は落ちた地点の画面
    （決まらなければ None）。

    - 画面が決まらなければ、起動し直す
    - 決まれば、その画面と、そこに積まれていた履歴（stack_at）から繋ぐ。スクロールは
      分からないので、どの画面も「スクロールされているかもしれない」とみなす
    - 後に残したかもしれないもの（そのテストケースで使った `leaves` のある操作を全部）は、
      次のテストケースの頭で `reset` を叩いてみる。どこで落ちたかで、もう叩いたかは変わる
      ので全部を対象にし、叩けなければ（頭の項目が落ちたら）起動し直してもう1回走らせる
    """
    if screen is None:
        return Cursor(mp.start, [mp.start], ScrollState(), restart=True)
    left = list(dict.fromkeys((sid, op_id(spec)) for sid, spec in build.touched))
    return Cursor(screen, stack_at(build, name, screen), ScrollState(unknown=True), None, False,
                  left, build.typed or any(spec.op == "text" for _, spec in build.touched),
                  recovered=True)


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
    if st.arrive is None and st.closed is not None:
        return enters + [route.close(st)], wrong      # 閉じてアプリの外に出る
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

@dataclass
class Unit:
    """run_flows.py が1回で流すもの。

    - `flow`: 経路。前の項目が終わった画面から `from` まで（起動し直し、後始末、アプリの外から
      戻す、も入る）。そのまま1本で流す。経路の途中に1手が挟まれば、その前後で分かれる
    - `hand`: 鍵を持つステップ1つ（`steps[0]`）。`do` の1手と、経路の途中で値を決めるステップ・
      子の要素（route_keys）。run_flows.py がダンプを読んで対象を探し、値を決めてから流す。
      項目の最後の1手には撮影（Shot）が付く

    `start` は流し始める画面、`launch` はこの1本でアプリを起動し直すか。
    """
    kind: str
    start: str
    steps: list
    launch: bool = False

    @property
    def step(self):
        return self.steps[0]


def route_keys(steps):
    """経路のうち、1手で流すステップに鍵を振る。`経路 <操作>`（同じ項目で重なれば `#2`）。

    - 値を決めるステップ（パターンの要素を押す。見えている1件目）
    - 子の要素（カルーセルの中のカードなど）と、その親を決める Enter。親の中を横に送って探すのは
      run_flows.py の仕事で、経路のフローには書かない
    """
    used = {}
    for st in steps:
        if getattr(st, "key", None) is not None:
            continue
        if not (needs_value(st) or getattr(st, "within", None)):
            continue
        if isinstance(st, Enter):
            base = "経路 in {}".format(st.target)
        else:
            base = "経路 {}:{}".format(st.action.label().split()[0], st.action.target)
        used[(st.item, base)] = used.get((st.item, base), 0) + 1
        n = used[(st.item, base)]
        st.key = base if n == 1 else "{}#{}".format(base, n)


def units_of(seg_start, seg_steps, launch):
    """1項目のステップ列を Unit の並びにする。鍵の無いステップは続くかぎり1本、鍵を持つステップ
    （`do` と、経路の route_keys）は1手ずつ。撮影は最後が1手ならそれに付ける（そうでなければ経路の本に）。"""
    units, cur, at, start = [], [], seg_start, seg_start
    for st in seg_steps:
        if getattr(st, "key", None) is not None:
            if cur:
                units.append(Unit("flow", start, cur, launch and not units))
                cur = []
            units.append(Unit("hand", st.screen, [st]))
            if st.to:
                at = st.to
            start = at
            continue
        if isinstance(st, Shot) and units and units[-1].kind == "hand" and not cur:
            units[-1].steps.append(st)
            continue
        cur.append(st)
        if st.to:
            at = st.to
    if cur:
        units.append(Unit("flow", start, cur, launch and not units))
    if launch and units[0].kind != "flow":
        # 起動し直した画面がそのまま from。起動するだけの本を頭に置く
        units.insert(0, Unit("flow", seg_start, [], True))
    return units


def render_case(mp, app, clear, cursor, build, timeout=10000):
    """組んだテストケース（CaseBuild）を、項目ごとの Unit の並びにする。(項目ごとの行, 次の Cursor)。

    **項目（撮影）ごとに区切る。** 走らせる側は項目ごとに Unit を順に流して inspect するだけで、
    証跡と同名のダンプが揃う。2つ目以降の項目は前の続き（起動し直さない）。テストケースの
    頭の項目は、`cursor` の画面からの続きか、起動し直し（`Restart` が頭にあれば）。

    行は項目ごとに、Unit の並び（`units`）と、ここで計算した欄（撮った画面・自動確認のID・
    記録のファイル名・起動し直すか・撮影する側が決める値の鍵）。**フローの中身は書かない** —
    値は流す直前に決まるので、run_flows.py が `render_unit()` で書く。
    """
    steps = list(build.steps)
    route_keys(steps)
    if cursor.out and not (steps and isinstance(steps[0], Restart)):
        # 前のテストケースがアプリの外に居るまま終わった。戻してから始める
        steps.insert(0, Return(cursor.out, steps[0].item if steps else None))
    scroll = cursor.scroll.copy()
    # 経路の押す前のスクロール（Reveal）と、外から戻す操作（Return）を挟んでから切る
    steps, out = add_returns(add_reveals(steps, scroll))
    for i, st in enumerate(steps):
        if goes_out(st):
            # 外に居るまま撮るか。1手ずつ書くと撮影は別の本になるので、ここで並び全体から決める
            st.stay = stays_out(steps, i, skip=(Reveal,))
    rows = []
    for seg_start, seg_steps, shot, lch in split_at_shots(mp, steps, cursor.at, launch_first=False):
        screen, checked = shot_context(mp, seg_start, seg_steps)
        rows.append({"name": shot, "units": units_of(seg_start, seg_steps, lch), "flow": shot + ".yaml",
                     "screen": screen, "checked": checked, "launch": lch,
                     "inputs": {k: "" for k in caller_keys(seg_steps)}})
    end = Cursor(build.route.at, list(build.route.stack), scroll, out, False,
                 [(sid, op_id(spec)) for sid, spec in build.left], build.typed)
    return rows, end


def render_unit(mp, app, clear, unit, values=None, indexes=None, shots=".", timeout=10000):
    """Unit を1本のフローに書く。流すものが無ければ None（親を決めるだけの Enter）。

    `values` / `indexes` は run_flows.py が決めた値と、同じ ID の行のうち何番目か（鍵はステップの
    `key`）。`shots` は撮影先。1手のフローは頭で anchor を待たない（ダンプで見てから流すので）。
    """
    if unit.kind == "hand" and isinstance(unit.step, Enter) and len(unit.steps) == 1:
        return None
    flow, _ = emit_flow(mp, unit.steps, app, clear, None, timeout, unit.start, launch=unit.launch,
                        values=values, indexes=indexes, shots=shots, head=unit.kind == "flow")
    return flow


def plan_rows(plan, timeout=10000):
    """manifest.py の仕事。テストケースごとに組めるかを確かめ、項目ごとの行を返す。
    (項目の名前 → 行, テストケースの題 → 後始末)。フローは書かない。

    **テストケースごとに、起動直後の画面から組む。** 走らせるときは前のテストケースが
    終わった画面から繋ぐが、繋げなければ起動し直して起点から繋ぐ（run_flows.py）。
    なので、起点から組めることが保証されていれば、どこで落ちても次のテストケースに
    入れる。テストケースの並び順にも左右されない。テストケースの中は、前の項目が
    終わった画面から順に繋ぐ。

    組めなければ、**全部のテストケースの理由を**出して終了コード 2 で終わる（テスト
    ケースどうしは依存しないので、1つ組めなくても他は確かめられる）。組めたら
    テストケースごとの読める経路を標準出力に出す。

    返す行は `render_case()` の行（`units` は除く）。撮る画面と自動確認のIDは
    `from` と `do` で決まり、どこから繋いだかによらない。後始末（`after`）は、その
    テストケースを最後まで走らせたあとの `leaves` / `reset` から決める。走らせるときに
    `reset` の画面まで繋げなければ、そこで起動し直す。
    """
    mp = load_map(plan.get("repo"))
    app, clear = plan.get("app"), bool(plan.get("clear_state"))
    if not app:
        sys.exit("plan に app（bundle id）が要る（xcrun simctl listapps <UDID> で調べる）")
    rows, afters, problems, shown = {}, {}, [], []
    for case in flow_cases(plan):
        build = build_case(mp, case["items"], Cursor.fresh(mp))
        if build.problems:
            problems += build.problems
            continue
        after, _ = clean_up(mp, build.route, [], build.left, build.typed, case["next"])
        afters[case["title"]] = after
        steps = build.steps[1:] if build.steps and isinstance(build.steps[0], Restart) else build.steps
        shown.append("■ {}\n{}".format(case["title"], emit_path(mp, steps, build.notes)))
        for r in render_case(mp, app, clear, Cursor.fresh(mp), build, timeout)[0]:
            r.pop("units")
            rows[r["name"]] = r
    if problems:
        report_problems(problems)
        sys.exit(2)
    print("\n\n".join(shown))
    return rows, afters


def plan_of(manifest):
    """マニフェストから、フローを組むための plan を組み直す。run_flows.py が使う。

    テストケースの並びと `launch` / `explore`、その下の項目の `from` / `do` / `when` は
    `cases` に、アプリ（`app` / `clear_state` / `repo`）はマニフェストの頭にある。plan.json は
    読まない — マニフェストを作ったあとに plan を直していると、レビューしたものと違う
    フローを走らせることになる。
    """
    for key in ("app", "repo", "cases"):
        if not manifest.get(key):
            sys.exit("マニフェストに {} が無い（古いマニフェスト）。manifest.py で作り直す".format(key))
    if "sections" in manifest:
        sys.exit("古いマニフェスト（sections が平らに並んでいる）。manifest.py で作り直す")
    cases = []
    for c in manifest["cases"]:
        case = {"title": c["title"],
                "items": [{"from": it["from"], "do": it.get("do") or [],
                           "when": it.get("when") or []} for it in c["items"]]}
        if c.get("launch"):
            case["launch"] = True
        if c.get("explore"):
            case["explore"] = c["explore"]
        cases.append(case)
    return {"app": manifest["app"], "repo": manifest["repo"],
            "clear_state": bool(manifest.get("clear_state")), "cases": cases}
