"""スクリプトのテスト。screen-map（mapctl.py と screenmap/）と、
sim-test-report（manifest.py / run_flows.py と flowgen/ / device/）。

  python3 -m unittest discover tests            テストを走らせる
  UPDATE_SNAPSHOTS=1 python3 -m unittest ...    スナップショットを書き直す

**フローはスナップショットで比べる。** flow.py の `build_case()` / `render_case()` / `render_unit()`
（中で maestro.py の `emit_flow()`）はほぼ純関数で、fixture のマップと plan から書かれるフローの
中身がそのまま挙動になる（実行時に決める値はテスト用の決まった値で埋める）。書き直したら、差分を読んでから入れる。

シミュレーターも Maestro も要らない。manifest.py が UDID から機種を引く
`device.simulators.lookup()`（`run_manifest()`）と、run_flows.py が maestrod.py を叩く `sh()`
（`ItemRunBase` / `CaseRunBase`）を差し替える。
"""
import contextlib
import io
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
from unittest import mock
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "sim-test-report" / "scripts"
MAP_SCRIPTS = ROOT / "skills" / "screen-map" / "scripts"      # 画面マップの部品と mapctl.py
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "app"
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(MAP_SCRIPTS))

from screenmap import check as map_check  # noqa: E402
from screenmap import map as screen_map  # noqa: E402
from flowgen import flow as flows_of  # noqa: E402
from flowgen.actions import Tap  # noqa: E402
from flowgen.maestro import FlowWriter, check_of  # noqa: E402
from flowgen.results import External  # noqa: E402
from flowgen.steps import Act  # noqa: E402
import diffscope  # noqa: E402
import manifest_items as MI  # noqa: E402


def load_run_flows():
    """run_flows.py は読み込むと main() が走るので、関数だけ取り出す。"""
    src = (SCRIPTS / "run_flows.py").read_text(encoding="utf-8").replace("\nmain()\n", "\n")
    ns = {"__file__": str(SCRIPTS / "run_flows.py"), "__name__": "run_flows"}
    exec(compile(src, "run_flows.py", "exec"), ns)
    return ns


RF = load_run_flows()


def cases_manifest(items, title="A"):
    """テストの便宜。項目をまとめて1つのテストケースに入れたマニフェスト。"""
    return {"cases": [{"title": title, "items": items}]}


def cases_of(items):
    """テストの便宜。項目を1つずつテストケースにする。項目に `launch` を書くと、その項目だけの
    テストケースに付く（そこでアプリを起動し直す）。まとまりを確かめるテストは cases を直に書く。"""
    out = []
    for n, it in enumerate(items, 1):
        it = dict(it)
        case = {"title": "{}. {}".format(n, it.get("title", "")), "items": [it]}
        if it.pop("launch", False):
            case["launch"] = True
        out.append(case)
    return out


def fake_values(unit):
    """テストの便宜。1手を書くときに埋める値（run_flows.py が決めるもの。ダンプから選ぶ行と、inputs から取る打つ文字）。
    パターンの要素は `<接頭辞>選んだ`、打つ文字は `打った`。"""
    from flowgen.actions import InputLater
    from flowgen.steps import Act, Enter, See
    from screenmap.screen import pattern_prefix
    values, indexes = {}, {}
    for st in unit.steps:
        key = getattr(st, "key", None)
        if key is None:
            continue
        if isinstance(st, Enter):
            values[key] = pattern_prefix(st.target) + "選んだ"
        elif isinstance(st, See) and st.pick is not None:
            values[key] = pattern_prefix(st.target) + "選んだ"
            indexes[key] = 0
        elif isinstance(st, Act) and isinstance(st.action, InputLater):
            values[key] = "打った"
        elif isinstance(st, Act) and getattr(st.action, "pick", None) is not None:
            values[key] = pattern_prefix(st.action.target) + "選んだ"
            indexes[key] = 0
        for w in getattr(st, "within", None) or []:
            if w.enter is not None:
                values[w.enter.key] = pattern_prefix(w.id) + "選んだ"
    return values, indexes


def unit_files(mp, row, app="jp.example.App"):
    """テストの便宜。項目の Unit を、run_flows.py が流す名前（`test_01.1.yaml` … 最後は `test_01.yaml`）で書く。
    流すものが無い Unit（親を決めるだけ）は入れない。撮影先は `/shots`。"""
    out, units = {}, row["units"]
    for k, u in enumerate(units):
        values, indexes = fake_values(u)
        body = flows_of.render_unit(mp, app, False, u, values, indexes, "/shots")
        if body is not None:
            out[row["name"] + (".yaml" if k == len(units) - 1 else ".{}.yaml".format(k + 1))] = body
    return out


def write_flows(items, repo=FIXTURE, cases=None):
    """plan から、全部のテストケースが通ったときに流すフローを組んで、(行, {ファイル名: 中身}) を返す。
    `cases` を渡せばそれをそのまま使う（items は無視する）。

    組めるかは manifest.py と同じく `plan_rows()` で確かめる（組めなければ SystemExit）。手順は
    run_flows.py と同じく、前のテストケースが終わった状態（Cursor）から1つずつ組み、Unit ごとに
    書く（unit_files()。値はテスト用の決まった値）。行には plan_rows() が決めた後始末
    （`after`、テストケースの最後の項目にだけ）を足す。"""
    plan = {"app": "jp.example.App", "repo": str(repo), "cases": cases or cases_of(items)}
    with contextlib.redirect_stdout(io.StringIO()):
        static, afters = flows_of.plan_rows(plan)
    mp = screen_map.load_map(str(repo))
    cur, rows, flows = flows_of.Cursor.fresh(mp), [], {}
    for case in flows_of.flow_cases(plan):
        build = flows_of.build_case(mp, case["items"], cur)
        got, cur = flows_of.render_case(mp, cur, build)
        for r in got:
            flows.update(unit_files(mp, r, plan["app"]))
            r["after"] = afters.get(case["title"]) if r["name"] == case["items"][-1]["name"] else None
            rows.append(r)
    return rows, dict(sorted(flows.items()))


def launches(row):
    """その項目の頭でアプリを起動し直すか（項目の Unit のどれかが起動する）。"""
    return any(u.launch for u in row["units"])


def first_visible(dump, pattern):
    """テストの便宜。画面に見えている1件目の行の ID（run_flows.py の locate()）。無ければ None。"""
    found = RF["locate"](dump, pattern)
    return found[0] if found else None


def run_manifest(args):
    """manifest.py を叩いて標準出力を返す。UDID から機種を引くところだけ差し替える。"""
    argv = sys.argv
    sys.argv = ["manifest.py"] + [str(a) for a in args]
    try:
        with mock.patch("device.simulators.lookup",
                        lambda udid: {"model": "iPhone 17 Pro", "os": "iOS 26.5"}), \
                contextlib.redirect_stdout(io.StringIO()) as o:
            runpy.run_path(str(SCRIPTS / "manifest.py"), run_name="__main__")
    finally:
        sys.argv = argv
    return o.getvalue()


def waits(flow):
    """フローが着いたことを確かめる id の並び。"""
    return re.findall(r"visible:\n\s+id: '([^']*)'", flow)


class Snapshot(unittest.TestCase):
    """代表的な plan から書かれるフローを丸ごと比べる。"""

    def test_representative_plan(self):
        rows, flows = write_flows([
            {"from": "list", "title": "一覧が出る", "expect": "行が並ぶ"},
            {"from": "list", "title": "入力で絞り込む", "expect": "入力した語を含む行だけ",
             "do": [{"op": "text:list.search_field", "runtime": True}]},
            {"from": "list", "title": "検索キーで確定", "expect": "キーボードが閉じる",
             "do": [{"op": "text:list.search_field", "input": "猫"}, "tap:Search"]},
            {"from": "list", "title": "末尾まで読む", "expect": "フッターが出る",
             "do": ["see:list.footer"]},
            {"from": "list", "launch": True, "title": "行を開く", "expect": "詳細が開く",
             "do": ["tap:list.row.*"]},
            {"from": "detail", "title": "戻る", "expect": "一覧に戻る",
             "do": ["tap:BackButton"]},
            {"from": "detail", "title": "お気に入り", "expect": "印が付く",
             "do": ["tap:detail.favorite_button", "tap:detail.share_button"]},
            {"from": "detail", "when": ["未ログイン"], "title": "レビュー", "expect": "案内が出る",
             "do": ["tap:detail.review_button"]},
        ])
        self.assertEqual([r["name"] for r in rows],
                         ["test_01", "test_02", "test_03", "test_04", "test_05", "test_06",
                          "test_07", "test_08"])
        for name, body in flows.items():
            snap = SNAPSHOTS / name
            if os.environ.get("UPDATE_SNAPSHOTS"):
                SNAPSHOTS.mkdir(exist_ok=True)
                snap.write_text(body, encoding="utf-8")
                continue
            with self.subTest(flow=name):
                self.assertTrue(snap.exists(), f"{snap} が無い。UPDATE_SNAPSHOTS=1 で作る")
                self.assertEqual(body, snap.read_text(encoding="utf-8"))
        if not os.environ.get("UPDATE_SNAPSHOTS"):
            self.assertEqual(sorted(flows), sorted(p.name for p in SNAPSHOTS.glob("*.yaml")))


class BackUsesHistory(unittest.TestCase):
    """do の戻る操作は、マップの to ではなく歩いた履歴から戻り先を決める。"""

    def test_back_after_shortcut_returns_home(self):
        # 起点から詳細へはお気に入り（home.fav）が最短。戻ると一覧ではなくホーム
        rows, flows = write_flows([
            {"from": "detail", "title": "戻る", "expect": "e", "do": ["tap:BackButton"]}])
        flow = flows["test_01.yaml"]
        self.assertEqual(waits(flow)[-1], "^home$")
        self.assertEqual(rows[0]["checked"], "home")

    def test_back_via_list_returns_list_without_note(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a",
             "do": ["tap:list.row.*", "tap:BackButton"]}])
        flow = flows["test_01.yaml"]
        self.assertEqual(waits(flow)[-1], "(^list\\.row\\..*|^list\\.empty_view$)")
        self.assertIn("id: '^list$'", flow[flow.index("^BackButton$"):])
        self.assertNotIn("# 補足", flow)

    def test_back_from_list_returns_home(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a", "do": ["tap:BackButton"]}])
        self.assertEqual(waits(flows["test_01.yaml"])[-1], "^home$")
        self.assertNotIn("# 補足", flows["test_01.yaml"])

    def test_back_on_start_screen_stops(self):
        repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, repo)
        home = repo / "screen-map" / "screens" / "home.yaml"
        home.write_text(home.read_text(encoding="utf-8")
                        + "  - id: home.close\n    name: 閉じる\n    actions:\n      - tap:\n"
                        "        expect: {screen: back, via: dismiss}\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err, \
                self.assertRaises(SystemExit) as cm:
            write_flows([{"from": "home", "title": "a", "expect": "a",
                          "do": ["tap:home.close"]}], repo)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("起点 home に戻る操作", err.getvalue())
        shutil.rmtree(repo.parent)


def ups(flow):
    """上向きに探す要素のセレクタの並び。"""
    return re.findall(r"id: '([^']*)'\n\s+direction: UP", flow)


def run_order(flows):
    """全部の項目のフローを、流す順につないだもの。"""
    names = sorted({k.split(".")[0] for k in flows})
    return "".join(item_flow(flows, n) for n in names)


def item_flow(flows, name):
    """その項目で流すフローを、流す順につないだもの（経路の本 → 1手ずつ → 撮る本）。"""
    keys = sorted((k for k in flows if k == name + ".yaml" or k.startswith(name + ".")),
                  key=lambda k: (k == name + ".yaml", int(k.split(".")[1]) if k.count(".") == 2 else 0))
    return "".join(flows[k] for k in keys)


class ScrollUp(unittest.TestCase):
    """経路では、スクロールされているかもしれない画面で、押す前に上も探す。
    do の要素はフローで探さない（run_flows.py がダンプで上下を探す）。"""

    def test_route_on_a_scrolled_screen_searches_up(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a", "do": ["see:list.footer"]},
            {"from": "home", "title": "b", "expect": "b"}])
        self.assertEqual(ups(item_flow(flows, "test_01")), [])
        self.assertEqual(ups(item_flow(flows, "test_02")), ["^BackButton$"])

    def test_do_is_not_searched_by_the_flow(self):
        # do の要素は run_flows.py がダンプで探してから流す。フローは上下に探さない
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a", "do": ["see:list.footer"]},
            {"from": "list", "title": "b", "expect": "b",
             "do": [{"op": "text:list.search_field", "input": "猫"}, "see:list.footer"]}])
        flow = item_flow(flows, "test_02")
        self.assertEqual(ups(flow), [])
        self.assertNotIn("timeout: 60000", flow)
        # 見る要素は下端から離すだけ（下向きに centerElement。見えているのですぐ抜ける）
        self.assertIn("direction: DOWN\n    centerElement: true\n    timeout: 10000", flow)

    def test_pushed_screen_starts_at_top_and_back_keeps_position(self):
        # 一覧は下まで見た。詳細は push で開いたので上端から。戻った一覧は下までのまま
        rows, flows = write_flows(None, cases=[{"title": "T", "items": [
            {"from": "list", "title": "a", "expect": "a", "do": ["see:list.footer"]},
            {"from": "list", "title": "b", "expect": "b", "do": ["tap:list.row.*"]},
            {"from": "home", "title": "c", "expect": "c"}]}])
        flow = item_flow(flows, "test_03")
        self.assertEqual(flow.count("id: '^BackButton$'\n    direction: DOWN"), 2)
        self.assertEqual(ups(flow), ["^BackButton$"])
        self.assertNotIn("direction: UP\n    centerElement: true", flow)

    def test_restart_starts_at_top(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a", "do": ["see:list.footer"]},
            {"from": "list", "launch": True, "title": "b", "expect": "b"},
            {"from": "home", "title": "c", "expect": "c"}])
        self.assertEqual(ups(item_flow(flows, "test_03")), [])


class SeeWaits(unittest.TestCase):
    """see でマップに無い文言と、語を含む行を待てる。見る行は撮るときに条件で選べる（pick）。"""

    def test_text_outside_the_app(self):
        # 外に出て、外のページの文言を待ってから撮る。アプリには次のフローの頭で戻す
        rows, flows = write_flows([
            {"from": "detail", "title": "a", "expect": "a",
             "do": ["tap:detail.share_button", "see:text:図書カード"]},
            {"from": "detail", "title": "b", "expect": "b", "do": []}])
        first = item_flow(flows, "test_01")
        ext = first[first.index("# アプリの外（safari）に出る"):]
        self.assertNotIn("launchApp", ext)
        self.assertIn("visible:\n      text: '.*図書カード.*'", ext)
        self.assertLess(ext.index("図書カード"), ext.index("takeScreenshot"))
        self.assertNotIn("scrollUntilVisible", ext)
        # 文言を待つのは撮る手と同じ1本（外に居るまま撮る）
        self.assertIn("図書カード", flows["test_01.yaml"])
        self.assertIn("takeScreenshot", flows["test_01.yaml"])
        self.assertEqual(rows[0]["checked"], "図書カード")
        self.assertIn("# 続き: アプリの外から", item_flow(flows, "test_02"))

    def test_row_containing_a_word(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a",
             "do": [{"op": "text:list.search_field", "input": "猫"},
                    {"op": "see:list.row.*", "input": "猫"}]}])
        self.assertIn("id: '^list\\.row\\..*猫.*'", flows["test_01.yaml"])

    def test_row_picked_by_condition(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a",
             "do": [{"op": "text:list.search_field", "runtime": True},
                    {"op": "see:list.row.*", "pick": "在庫ありの行"}]}])
        # 見る行は撮るときに選び（テストでは「選んだ」）、その ID と何番目かで見る
        self.assertIn("element:\n      id: '^list\\.row\\.選んだ$'\n      index: 0", flows["test_01.yaml"])
        self.assertIn("# list: see list.row.* [在庫ありの行: list.row.選んだ]", flows["test_01.yaml"])
        # 撮影する側が決める値。鍵は do に書いた操作そのもの
        self.assertEqual(rows[0]["inputs"], {"text:list.search_field": "", "see:list.row.*": ""})

    def test_runtime_is_refused_on_see(self):
        # runtime は打つ文字（text）にだけ付く。見る行は pick で選ぶ
        code, err = build_err([{"from": "list", "title": "a", "expect": "a",
                                "do": [{"op": "see:list.row.*", "runtime": True}]}])
        self.assertIn("runtime は付けられない。どの行を見るかは pick に条件で書く", err)

    def test_pick_only_on_patterns(self):
        code, err = build_err([{"from": "list", "title": "a", "expect": "a",
                                "do": [{"op": "see:list.footer", "pick": "x"}]}])
        self.assertIn("パターンの要素", err)

    def test_values_only_on_patterns(self):
        code, err = build_err([{"from": "list", "title": "a", "expect": "a",
                                "do": [{"op": "see:list.footer", "input": "x"}]}])
        self.assertIn("パターンの要素", err)
        code, err = build_err([{"from": "list", "title": "a", "expect": "a",
                                "do": [{"op": "see:text:猫", "input": "x"}]}])
        self.assertIn("値は添えられない", err)


class NotesPerFlow(unittest.TestCase):
    """補足は、そのフローのステップに関係するものだけが付く。"""

    def test_note_only_on_its_flow(self):
        repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, repo)
        detail = repo / "screen-map" / "screens" / "detail.yaml"
        detail.write_text(detail.read_text(encoding="utf-8").replace(
            "elements:\n", "elements:\n  - id: detail.noop\n    name: 何もしない\n"
            "    actions:\n      - tap:\n        summary: 何も起きない\n", 1), encoding="utf-8")
        rows, flows = write_flows([
            {"from": "detail", "title": "a", "expect": "a", "do": ["tap:detail.noop"]},
            {"from": "list", "title": "b", "expect": "b"}], repo)
        self.assertIn("# 補足: 「tap detail.noop」の結果を確かめる expect がマップに無い",
                      flows["test_01.yaml"])
        self.assertNotIn("# 補足", flows["test_02.yaml"])
        shutil.rmtree(repo.parent)


class RuntimeInputs(unittest.TestCase):
    """実行時に決めた値は、tap のセレクタに入るものだけ正規表現としてエスケープする。
    値は流す直前にフローへ直接書く。"""

    def rows(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a",
             "do": [{"op": "text:list.search_field", "runtime": True}]},
            {"from": "list", "launch": True, "title": "b", "expect": "b",
             "do": [{"op": "tap:list.row.*", "pick": "いちばん長い名前"}]}])
        return rows, flows

    def test_keys_are_the_do_ops(self):
        rows, _ = self.rows()
        self.assertEqual(rows[0]["inputs"], {"text:list.search_field": ""})
        self.assertEqual(rows[1]["inputs"], {"tap:list.row.*": ""})
        hand = rows[1]["units"][-1]
        self.assertEqual((hand.kind, hand.step.key), ("hand", "tap:list.row.*"))

    def test_same_op_twice_gets_a_number(self):
        rows, _ = write_flows([{"from": "list", "title": "a", "expect": "a",
                                "do": [{"op": "text:list.search_field", "runtime": True},
                                       {"op": "text:list.search_field", "runtime": True}]}])
        self.assertEqual(list(rows[0]["inputs"]), ["text:list.search_field", "text:list.search_field#2"])

    def render(self, row, value):
        mp = screen_map.load_map(str(FIXTURE))
        u = row["units"][-1]
        return flows_of.render_unit(mp, "x", False, u, {u.step.key: value}, {u.step.key: 0}, "/shots")

    def test_selector_values_are_escaped(self):
        rows, _ = self.rows()
        for value, other in [("牛乳(1L)", "牛乳1L"), ("a.b", "aXb"),
                             ("C++入門", None), ("50% off [new]", None), ("It's", None)]:
            with self.subTest(value=value):
                flow = self.render(rows[1], "list.row." + value)
                filled = re.search(r"tapOn:\n    id: '(.*)'", flow).group(1).replace("''", "'")
                self.assertTrue(re.fullmatch(filled, "list.row." + value))
                self.assertFalse(re.fullmatch(filled, "listXrow." + value))
                if other:
                    self.assertFalse(re.fullmatch(filled, "list.row." + other))

    def test_text_values_are_not_escaped(self):
        rows, _ = self.rows()
        self.assertIn("- inputText: '牛乳(1L)'", self.render(rows[0], "牛乳(1L)"))


class Manifest(unittest.TestCase):
    """manifest.py が plan から作るマニフェストの形。"""

    def test_manifest_from_plan(self):
        work = Path(tempfile.mkdtemp())
        plan = work / "plan.json"
        plan.write_text(json.dumps({"app": "jp.example.App", "repo": str(FIXTURE), "cases": [
            {"title": "A", "items": [{"from": "list", "title": "a", "expect": "a",
                                      "do": ["tap:list.row.*"]}]},
            {"title": "B", "explore": "画面 settings がマップに無い",
             "items": [{"from": "settings", "title": "b", "expect": "b"}]}]}), encoding="utf-8")
        out = work / "out"
        run_manifest([plan, out, "--device", "iphone=AAAA", "--device", "ipad=BBBB"])
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        shutil.rmtree(Path(m["flows"]), ignore_errors=True)
        shutil.rmtree(work)

        self.assertEqual(sorted(m["devices"]), ["ipad", "iphone"])
        self.assertEqual(m["repo"], str(FIXTURE.resolve()))   # どのマップで組んだかを残す
        # テストケースの入れ子。項目の中身はテストケースの下にあり、題の文字列では紐づけない
        (a, first), (b, explore) = MI.walk(m)
        self.assertEqual((a["title"], b["title"]), ("A", "B"))
        self.assertNotIn("case", first)
        self.assertEqual(first["name"], "test_01")
        # フローを組むのに要るものをマニフェストに写す（run_flows.py は plan.json を読まない）
        self.assertEqual((m["app"], m["clear_state"]), ("jp.example.App", False))
        # フローは走らせるときに組む。ここでは名前だけ決め、置き場には何も書かない
        self.assertEqual(first["flow"], "test_01.yaml")
        self.assertFalse(Path(m["flows"]).exists())
        self.assertEqual((first["screen"], first["checked"]), ("detail", "detail"))
        # 条件の無い選択は run_flows.py が決めるので、inputs には入らない
        self.assertEqual(first["devices"]["iphone"], {"inputs": {}, "picked": {}})
        for key in ("units", "parts", "picks", "input_use", "launch"):
            self.assertNotIn(key, first)
        self.assertEqual([i["src"] for i in first["images"]],
                         ["shots/iphone/test_01.png", "shots/ipad/test_01.png"])
        self.assertEqual(first["result"], "PENDING")
        # 経路が組めなかったテストケースの項目は、フロー無しで並ぶ
        self.assertEqual(explore["name"], "test_02")
        self.assertIsNone(explore["flow"])
        self.assertEqual(MI.find(m, "test_02"), (b, explore))
        self.assertEqual(MI.find(m, "test_09"), (None, None))

    def test_inputs_are_kept_when_rebuilt(self):
        work = Path(tempfile.mkdtemp())
        plan = work / "plan.json"
        out = work / "out"

        def build(items):
            plan.write_text(json.dumps({"app": "x", "repo": str(FIXTURE), "cases": cases_of(items)}),
                            encoding="utf-8")
            printed = run_manifest([plan, out, "--device", "iphone=AAAA"])
            return json.loads((out / "manifest.json").read_text(encoding="utf-8")), printed

        text = {"from": "list", "title": "a", "expect": "a",
                "do": [{"op": "text:list.search_field", "runtime": True}]}
        m, _ = build([text])
        MI.find(m, "test_01")[1]["devices"]["iphone"]["inputs"]["text:list.search_field"] = "牛乳(1L)"
        (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")

        # 同じ鍵なら引き継ぎ、引き継いだことを出す
        m, printed = build([dict(text, launch=True)])
        self.assertEqual(MI.find(m, "test_01")[1]["devices"]["iphone"]["inputs"],
                         {"text:list.search_field": "牛乳(1L)"})
        self.assertIn("test_01 iphone: text:list.search_field=牛乳(1L)", printed)

        # 鍵が変わったら空に戻し、引き継がなかった値として出す
        m, printed = build([{"from": "list", "title": "a", "expect": "a",
                             "do": [{"op": "tap:list.row.*", "pick": "x"}]}])
        self.assertEqual(MI.find(m, "test_01")[1]["devices"]["iphone"]["inputs"], {"tap:list.row.*": ""})
        self.assertIn("引き継がなかった実行時の値", printed)
        self.assertIn("test_01 iphone: text:list.search_field=牛乳(1L)", printed)
        shutil.rmtree(Path(m["flows"]), ignore_errors=True)
        shutil.rmtree(work)

    def test_unexpected_is_kept_when_rebuilt(self):
        # retaker が plan を直して作り直しても、撮れなかった項目だけを撮り直せるように残す
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work)
        plan, out = work / "plan.json", work / "out"
        plan.write_text(json.dumps({"app": "x", "repo": str(FIXTURE), "cases": cases_of([
            {"from": "list", "title": "a", "expect": "a"}])}), encoding="utf-8")
        run_manifest([plan, out, "--device", "iphone=AAAA"])
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.addCleanup(shutil.rmtree, Path(m["flows"]), True)
        lost = {"kind": "failed", "reason": "フローが失敗"}
        MI.find(m, "test_01")[1]["devices"]["iphone"]["unexpected"] = lost
        (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        run_manifest([plan, out, "--device", "iphone=AAAA"])
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(MI.find(m, "test_01")[1]["devices"]["iphone"],
                         {"inputs": {}, "picked": {}, "unexpected": lost})


class Cases(unittest.TestCase):
    """plan はテストケースの集合。起動し直すのはテストケースの境目だけ。"""

    def case(self, title, *froms, **flags):
        return dict({"title": title, "items": [
            {"from": f, "title": "{} {}".format(title, n), "expect": "x"} for n, f in enumerate(froms, 1)]},
            **flags)

    def launches(self, cases):
        rows, _ = write_flows(None, cases=cases)
        return [(r["name"], launches(r)) for r in rows]

    def refused(self, plan):
        with self.assertRaises(SystemExit) as cm:
            flows_of.read_cases(plan)
        return str(cm.exception.code)

    def test_items_in_a_case_continue(self):
        # テストケースの中では起動し直さない。後ろのテストケースも、マップの leaves が無ければ続き
        self.assertEqual(self.launches([self.case("A", "list", "detail"), self.case("B", "list")]),
                         [("test_01", True), ("test_02", False), ("test_03", False)])

    def test_launch_restarts_its_own_head(self):
        self.assertEqual(self.launches([self.case("A", "list"), self.case("B", "list", launch=True)]),
                         [("test_01", True), ("test_02", True)])

    def test_explore_case_keeps_its_place(self):
        # 探索のテストケースはフローを持たず、番号はその場のまま
        cases = [self.case("A", "list"),
                 self.case("X", "list", explore="画面 history がマップに無い"),
                 self.case("B", "list", launch=True)]
        self.assertEqual(self.launches(cases), [("test_01", True), ("test_03", True)])

    def test_refs_expand_to_the_earlier_item(self):
        case = self.case("A", "list", "detail")
        case["items"][0]["id"] = "open"
        case["items"][1]["expect"] = "{open}で開いたもの"
        rows = flows_of.read_cases({"cases": [case]})[0]["items"]
        self.assertEqual(rows[1]["expect"], "test_01（A 1）で開いたもの")
        self.assertEqual([r["name"] for r in rows], ["test_01", "test_02"])

    def test_refs_only_to_earlier_items_in_the_same_case(self):
        a, b = self.case("A", "list"), self.case("B", "list", "detail")
        a["items"][0]["id"] = "open"
        b["items"][0]["expect"] = "{open}"
        self.assertIn("{open} が指す項目が無い", self.refused({"cases": [a, b]}))
        b = self.case("B", "list", "detail")
        b["items"][0]["expect"] = "{later}"
        b["items"][1]["id"] = "later"
        self.assertIn("{later} が指す項目が無い", self.refused({"cases": [b]}))

    def test_duplicate_ids_and_titles_are_refused(self):
        c = self.case("A", "list", "detail")
        c["items"][0]["id"] = c["items"][1]["id"] = "x"
        self.assertIn("id「x」が test_01", self.refused({"cases": [c]}))
        self.assertIn("テストケース「A」が2つある", self.refused({"cases": [self.case("A", "list")] * 2}))

    def test_item_cannot_restart(self):
        c = self.case("A", "list")
        c["items"][0]["fresh"] = True
        self.assertIn("plan の test_01 に知らない鍵: fresh", self.refused({"cases": [c]}))

    def test_manifest_lists_cases(self):
        work = Path(tempfile.mkdtemp())
        plan = work / "plan.json"
        plan.write_text(json.dumps({"app": "x", "repo": str(FIXTURE), "cases": [
            self.case("A", "list", "detail"),
            self.case("X", "list", explore="画面 history がマップに無い"),
            self.case("B", "list", launch=True)]}), encoding="utf-8")
        run_manifest([plan, work / "out", "--device", "iphone=AAAA"])
        m = json.loads((work / "out" / "manifest.json").read_text(encoding="utf-8"))
        shutil.rmtree(Path(m["flows"]), ignore_errors=True)
        shutil.rmtree(work)
        self.assertEqual([(c["title"], it["name"], bool(it["flow"])) for c, it in MI.walk(m)],
                         [("A", "test_01", True), ("A", "test_02", True),
                          ("X", "test_03", False), ("B", "test_04", True)])
        self.assertEqual([{k: v for k, v in c.items() if k != "items"} for c in m["cases"]], [
            {"title": "A", "launch": False, "explore": None, "after": None},
            {"title": "X", "launch": False, "explore": "画面 history がマップに無い", "after": None},
            {"title": "B", "launch": True, "explore": None, "after": None}])

    def test_report_heads_where_the_case_changes(self):
        ns = runpy.run_path(str(SCRIPTS / "build_report.py"), run_name="build_report")
        heads = ns["case_heads"]([
            {"title": "一覧から開く", "items": [{"title": "a"}, {"title": "b"}]},
            {"title": "起動する", "items": [{"title": "起動する"}]},   # 1項目で題が同じなら見出しは要らない
            {"title": "絞り込む", "items": [{"title": "語を打つ"}]}])
        self.assertEqual(heads, {1: "一覧から開く", 4: "絞り込む"})


class ValueHint(unittest.TestCase):
    """止まったときのメッセージが、何を決めるのかを取り違えさせない。"""

    def test_each_kind(self):
        from flowgen.actions import InputLater, Pick, Tap
        from flowgen.steps import Act, Enter, See
        hint = RF["value_hint"]
        self.assertIn("ID（ダンプの id の欄をまるごと）", hint(Act("list", Tap("list.row.*", Pick("在庫あり")))))
        self.assertIn("ID（ダンプの id の欄をまるごと）", hint(Enter("recommend", "recommend.carousel.*", Pick("x"))))
        self.assertIn("ID（ダンプの id の欄をまるごと）", hint(See("list", "list.row.*", pick=Pick("在庫あり"))))
        self.assertEqual(hint(Act("list", InputLater("list.search_field"))), "打つ文字を")


class LeavesReset(unittest.TestCase):
    """後に残る状態（leaves）と既定に戻す操作（reset）は画面マップに書き、テストケースの
    後始末はスクリプトが決める。"""

    SEARCH = ("  - id: list.search_field\n"
              "    name: 検索欄（上部）\n"
              "    actions:\n"
              "      - text:\n"
              "        summary: 入力が止まると絞り込む\n"
              "        expect: {value: self}\n")

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, self.repo)
        self.list = self.repo / "screen-map" / "screens" / "list.yaml"

    def tearDown(self):
        shutil.rmtree(self.repo.parent)

    def leaves(self, reset="tap:list.clear_button", clear="tap"):
        body = self.list.read_text(encoding="utf-8")
        assert self.SEARCH in body
        search = self.SEARCH + "        leaves: 入力欄の語と絞り込みの結果が残る\n"
        if reset:
            search += "        reset: {}\n".format(reset)
        button = ("  - id: list.clear_button\n"
                  "    name: クリア（検索欄の右端）\n"
                  "    actions:\n"
                  "      - {}:\n"
                  "        summary: 入力欄を空にし、絞り込みを解除する\n"
                  "        expect: {{value: list.search_field}}\n").format(clear)
        self.list.write_text(body.replace(self.SEARCH, search + button), encoding="utf-8")

    def flows(self, *cases):
        rows, flows = write_flows(None, repo=self.repo, cases=list(cases))
        return rows, run_order(flows)

    def typing(self, title, **flags):
        return dict({"title": title, "items": [
            {"from": "list", "do": [{"op": "text:list.search_field", "input": "猫"}],
             "title": title, "expect": "x"}]}, **flags)

    def opening(self, title):
        return {"title": title, "items": [{"from": "list", "do": ["tap:list.row.*"],
                                           "title": title, "expect": "x"}]}

    def check(self):
        mp = screen_map.load_map(str(self.repo))
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = map_check.cmd_check(mp)
        return code, o.getvalue()

    def test_reset_runs_after_the_case(self):
        self.leaves()
        rows, flow = self.flows(self.typing("打つ"), self.opening("開く"))
        # 打ったテストケースの後、次のテストケースの前にクリアを押してキーボードを閉じる。起動し直さない
        head = flow[flow.index("takeScreenshot: '/shots/test_01'"):]
        self.assertLess(head.index("id: '^list\\.clear_button$'"), head.index("- hideKeyboard"))
        self.assertLess(head.index("- hideKeyboard"), head.index("list\\.row"))
        self.assertEqual([launches(r) for r in rows], [True, False])
        self.assertEqual(rows[0]["after"], {"relaunch": False, "resets": ["tap:list.clear_button"],
                                            "leaves": ["入力欄の語と絞り込みの結果が残る"]})

    def test_leaves_without_reset_relaunches(self):
        self.leaves(reset=None)
        rows, _ = self.flows(self.typing("打つ"), self.opening("開く"))
        self.assertEqual([launches(r) for r in rows], [True, True])
        self.assertEqual(rows[0]["after"]["relaunch"], True)

    def test_nothing_after_the_last_case_or_before_a_relaunch(self):
        self.leaves()
        opening = dict(self.opening("起動して開く"), launch=True)
        rows, flow = self.flows(self.typing("打つ"), opening, self.typing("最後に打つ"))
        self.assertNotIn("clear_button", flow)
        self.assertEqual([r["after"] for r in rows], [None, None, None])

    def test_explore_case_between_passes_the_clean_up_on(self):
        # 探索のテストケースはフローで走らないので、前の後始末はその次のフローのテストケースの前にする
        self.leaves(reset=None)
        explore = {"title": "探索", "explore": "画面 history がマップに無い",
                   "items": [{"from": "list", "title": "x", "expect": "x"}]}
        rows, _ = self.flows(self.typing("打つ"), explore, self.opening("開く"))
        self.assertEqual([(r["name"], launches(r)) for r in rows], [("test_01", True), ("test_03", True)])

    def test_reset_already_done_in_the_case_is_not_repeated(self):
        # 打ってからクリアで戻すテストケース。クリアのあとは入力欄が空でクリアボタンが出ないので、
        # 後始末でもう一度押しに行くと落ちる
        self.leaves()
        case = self.typing("打ってクリアする")
        case["items"].append({"from": "list", "do": ["tap:list.clear_button"], "title": "クリア", "expect": "x"})
        rows, flow = self.flows(case, self.opening("開く"))
        after_case = flow[flow.index("'/shots/test_02'"):]
        self.assertNotIn("clear_button", after_case)
        self.assertEqual([r["after"] for r in rows], [None, None, None])
        self.assertEqual([launches(r) for r in rows], [True, False, False])

    def test_keyboard_closed_at_the_end_even_without_reset(self):
        # 打ってからクリアで終わるテストケース。戻す状態は無いが、キーボードは最後に閉じる
        self.leaves()
        case = self.typing("打ってクリアする")
        case["items"].append({"from": "list", "do": ["tap:list.clear_button"], "title": "クリア", "expect": "x"})
        rows, flow = self.flows(case, self.opening("開く"))
        between = flow[flow.index("'/shots/test_01'"):flow.index("'/shots/test_02'")]
        self.assertNotIn("hideKeyboard", between)          # テストケースの中では閉じない
        self.assertEqual(flow[flow.index("'/shots/test_02'"):].count("- hideKeyboard"), 1)

    def test_keyboard_closed_when_the_map_has_no_leaves(self):
        # 打った操作にマップの leaves が無くても、キーボードは最後に閉じる
        rows, flow = self.flows(self.typing("打つ"), self.opening("開く"))
        self.assertEqual(flow.count("- hideKeyboard"), 1)
        self.assertEqual([r["after"] for r in rows], [None, None])

    def test_items_inside_a_case_are_not_reset(self):
        # テストケースの中では前の項目の状態を当てにするので、戻さない
        self.leaves()
        case = self.typing("打って開く")
        case["items"].append({"from": "list", "do": ["tap:list.row.*"], "title": "開く", "expect": "x"})
        rows, flow = self.flows(case, self.opening("もう一度開く"))
        between = flow[flow.index("'/shots/test_01'"):flow.index("'/shots/test_02'")]
        self.assertNotIn("clear_button", between)
        self.assertIn("clear_button", flow[flow.index("'/shots/test_02'"):])

    def test_check(self):
        self.leaves()
        code, out = self.check()
        self.assertEqual(code, 0, out)
        body = self.list.read_text(encoding="utf-8").replace("reset: tap:list.clear_button", "reset: tap:list.nothing")
        self.list.write_text(body, encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("reset「tap:list.nothing」が list の操作に無い", out)

    def test_check_suggests_leaves_on_text(self):
        code, out = self.check()
        self.assertIn("「text list.search_field」 に leaves が無い", out)


class PlanRepo(unittest.TestCase):
    """plan が、どのアプリの画面マップを前提にしたかを持つ（repo）。manifest.py は引数で受け取らない。"""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.work)

    def load(self, plan):
        f = self.work / "plans" / "plan.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(plan), encoding="utf-8")
        return flows_of.load_plan(f)

    def test_repo_is_required(self):
        with self.assertRaises(SystemExit) as cm:
            self.load({"app": "x", "cases": []})
        self.assertIn("plan に repo（アプリのリポジトリ）が要る", str(cm.exception.code))

    def test_relative_repo_is_read_from_the_plan_file(self):
        app = self.work / "app"
        app.mkdir()
        plan = self.load({"app": "x", "repo": "../app", "cases": []})
        self.assertEqual(plan["repo"], str(app.resolve()))


class DiffScope(unittest.TestCase):
    """確かめる変更の範囲を決める（diffscope.py）。

    master ── A                                 既定ブランチ
               └── C（Tag.swift）          feature-x（オープン PR #11）
                    └── E, F（FavoriteButton / FavoriteBadge / FavoriteTests）  mine（今のブランチ）
    """

    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.repo = self.work / "app"
        self.repo.mkdir()
        self.git("init", "-b", "master")
        self.commit({"Old.swift": "a"}, "A")
        origin = self.work / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.repo), str(origin)], check=True)
        self.git("remote", "add", "origin", str(origin))
        self.git("fetch", "-q", "origin")
        self.git("remote", "set-head", "origin", "master")
        self.git("checkout", "-q", "-b", "feature-x")
        self.feature_x = self.commit({"Tag.swift": "c"}, "C")
        self.git("checkout", "-q", "-b", "mine")
        self.commit({"FavoriteButton.swift": "e"}, "E")
        self.commit({"FavoriteBadge.swift": "f", "FavoriteTests.swift": "f"}, "F")

    def tearDown(self):
        shutil.rmtree(self.work)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t",
                               *args], check=True, capture_output=True, text=True).stdout

    def commit(self, files, msg):
        for f, body in files.items():
            (self.repo / f).write_text(body + "\n")
        self.git("add", "."); self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def prs(self, *prs):
        return mock.patch.object(diffscope, "open_prs", lambda repo: list(prs))

    def pr(self, number, branch, oid):
        return {"number": number, "headRefName": branch, "headRefOid": oid}

    MINE = ["FavoriteBadge.swift", "FavoriteButton.swift", "FavoriteTests.swift"]

    def test_commits_in_another_open_pr_are_not_in_range(self):
        with self.prs(self.pr(11, "feature-x", self.feature_x)):
            sc = diffscope.scope(self.repo)
        self.assertEqual(sorted(sc["files"]), self.MINE)
        self.assertIn("PR #11", sc["via"])

    def test_without_open_prs_the_default_branch_decides(self):
        with self.prs():
            sc = diffscope.scope(self.repo)
        self.assertIn("既定ブランチ origin/master", sc["via"])
        self.assertEqual(sorted(sc["files"]), sorted(self.MINE + ["Tag.swift"]))

    def test_without_gh_the_default_branch_decides_and_says_so(self):
        with mock.patch.object(diffscope, "open_prs", lambda repo: None):
            sc = diffscope.scope(self.repo)
        self.assertIn("Tag.swift", sc["files"])
        self.assertTrue(any("gh" in n for n in sc["notes"]))

    def test_own_pr_is_not_a_candidate(self):
        # 今のブランチ自身の PR を候補にすると、差分が空になる
        pushed = self.git("rev-parse", "HEAD~1").strip()
        with self.prs(self.pr(11, "feature-x", self.feature_x), self.pr(12, "mine", pushed)):
            sc = diffscope.scope(self.repo)
        self.assertEqual(sorted(sc["files"]), self.MINE)
        self.assertIn("PR #11", sc["via"])

    def test_pr_stacked_on_this_branch_is_not_a_candidate(self):
        head = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "-b", "child")
        child = self.commit({"Child.swift": "g"}, "G")
        self.git("checkout", "-q", "mine")
        with self.prs(self.pr(11, "feature-x", self.feature_x), self.pr(13, "child", child)):
            sc = diffscope.scope(self.repo)
        self.assertEqual(sorted(sc["files"]), self.MINE)
        self.assertTrue(any("PR #13" in n for n in sc["notes"]))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)

    def test_base_can_be_given(self):
        sc = diffscope.scope(self.repo, "feature-x")
        self.assertEqual(sorted(sc["files"]), self.MINE)

    def test_working_tree_is_included_without_head(self):
        (self.repo / "Old.swift").write_text("changed\n")
        (self.repo / "NewDraft.swift").write_text("new\n")
        (self.repo / ".gitignore").write_text("build/\n")
        (self.repo / "build").mkdir()
        (self.repo / "build" / "out.o").write_text("x")
        files = diffscope.scope(self.repo, "feature-x")["files"]
        self.assertIn("Old.swift", files)
        self.assertIn("NewDraft.swift", files)
        self.assertNotIn("build/out.o", files)
        self.assertNotIn("NewDraft.swift", diffscope.scope(self.repo, "feature-x", "HEAD")["files"])

    def test_default_branch_moved_on_is_not_in_range(self):
        # 起点は分岐点。既定ブランチがその後に進んでも、その変更は入らない
        self.git("checkout", "-q", "master")
        self.commit({"Other.swift": "o"}, "other")
        self.git("push", "-q", "origin", "master")
        self.git("checkout", "-q", "mine")
        with self.prs():
            self.assertNotIn("Other.swift", diffscope.scope(self.repo)["files"])


class Leftovers(unittest.TestCase):
    """ダンプの置き場は実行ごと・端末ごとに分け、前の回のダンプが今回のものに紛れない。"""

    def test_run_key_splits_runs_and_devices(self):
        # maestrod.py も読み込むと main() が走るので、関数だけ取り出す
        src = (SCRIPTS / "maestrod.py").read_text(encoding="utf-8").replace("\nmain()\n", "\n")
        ns = {"__file__": str(SCRIPTS / "maestrod.py"), "__name__": "maestrod"}
        exec(compile(src, "maestrod.py", "exec"), ns)
        key = ns["run_key"]
        self.assertEqual(key("/x/sim-test-report-20260928-a/shots/iphone"), "sim-test-report-20260928-a/iphone")
        # なぞる項目と、探すための読み・送りは作業用の置き場から。実行と端末で分け、証跡のダンプとは下を分ける
        self.assertEqual(key("/s/.work/replay/sim-test-report-20260928-a/iphone"),
                         "sim-test-report-20260928-a/iphone/replay")
        self.assertTrue(key(None).startswith("_probe/"))


class Interrupts(unittest.TestCase):
    """自動表示は画面として書き、auto_shows に並べた画面に着いたときだけ確かめて閉じる。"""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, self.repo)
        screens = self.repo / "screen-map" / "screens"
        self.dialog = screens / "review_dialog.yaml"
        self.dialog.write_text(
            "anchor: review_dialog\n"
            "names: [レビュー依頼]\n"
            "summary: 起動3回目以降にランダムで出る\n"
            "elements:\n"
            "  - id: review_dialog.later_button\n"
            "    name: 後で\n"
            "    actions:\n"
            "      - tap:\n"
            "        summary: 閉じる\n"
            "        expect: {screen: back, via: dismiss}\n", encoding="utf-8")
        detail = screens / "detail.yaml"
        detail.write_text(detail.read_text(encoding="utf-8") + "auto_shows: [review_dialog]\n",
                          encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.repo.parent)

    def check(self):
        mp = screen_map.load_map(str(self.repo))
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = map_check.cmd_check(mp)
        return code, o.getvalue()

    def test_checked_after_arriving(self):
        rows, flows = write_flows([{"from": "detail", "title": "a", "expect": "a"}], self.repo)
        flow = flows["test_01.yaml"]
        # 詳細に入ったら、先に自動表示を閉じてから詳細の anchor を待つ。
        # 自動表示が出ている間は、下の画面の anchor がツリーから隠れるので、逆だと落ちる
        block = flow[flow.index("id: '^home\\.fav$'"):]
        self.assertIn("# 自動表示: review_dialog — 起動3回目以降にランダムで出る（出ていたら閉じる）", block)
        self.assertIn("- runFlow:\n    when:\n      visible:\n        id: '^review_dialog$'\n"
                      "    commands:\n      - tapOn:\n          id: '^review_dialog\\.later_button$'", block)
        self.assertLess(block.index("runFlow"), block.index("id: '^detail$'"))
        self.assertLess(block.index("id: '^detail$'"), block.index("takeScreenshot"))

    def test_start_screen_auto_show_is_closed_before_waiting(self):
        # 起点の画面に自動表示がある（起動直後に被さる）。起点の anchor より先に閉じる
        home = self.repo / "screen-map" / "screens" / "home.yaml"
        home.write_text(home.read_text(encoding="utf-8") + "auto_shows: [review_dialog]\n",
                        encoding="utf-8")
        rows, flows = write_flows([{"from": "home", "title": "a", "expect": "a"}], self.repo)
        flow = flows["test_01.yaml"]
        self.assertLess(flow.index("- launchApp"), flow.index("runFlow"))
        self.assertLess(flow.index("runFlow"), flow.index("id: '^home$'"))

    def test_not_checked_on_other_screens(self):
        rows, flows = write_flows([{"from": "list", "title": "a", "expect": "a"}], self.repo)
        self.assertNotIn("runFlow", flows["test_01.yaml"])

    def test_checked_in_a_hand(self):
        # do の1手で詳細に着く。その手のフローで閉じる
        # （1本目: 一覧まで / 2本目: 行を押して詳細 / 3本目: 戻る / 4本目: 入力して撮る）
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a",
             "do": ["tap:list.row.*", "tap:BackButton",
                    {"op": "text:list.search_field", "runtime": True}]}], self.repo)
        self.assertEqual([u.kind for u in rows[0]["units"]], ["flow", "hand", "hand", "hand"])
        self.assertIn("runFlow", flows["test_01.2.yaml"])
        self.assertNotIn("runFlow", flows["test_01.3.yaml"])

    def test_label_dismiss(self):
        # 閉じるボタンに ID が振れない（UIAlertAction など）ときは、ほかの操作と同じく by: label
        self.dialog.write_text(self.dialog.read_text(encoding="utf-8").replace(
            "  - id: review_dialog.later_button\n    name: 後で\n",
            "  - id: 後で\n    name: 後で\n    by: label\n"), encoding="utf-8")
        rows, flows = write_flows([{"from": "detail", "title": "a", "expect": "a"}], self.repo)
        self.assertIn("tapOn:\n          text: '.*後で.*'", flows["test_01.yaml"])

    def test_auto_show_as_target_waits_instead_of_closing(self):
        # 自動表示そのものを確かめる項目。被さる先（detail）に着いたら、閉じずに出るまで待つ
        rows, flows = write_flows([
            {"from": "review_dialog", "launch": True, "title": "レビュー依頼が出る",
             "expect": "レビュー依頼のダイアログが出ている"}], self.repo)
        flow = flows["test_01.yaml"]
        self.assertNotIn("runFlow", flow)
        self.assertIn("# detail: 自動表示 review_dialog を待つ", flow)
        # 出る先（detail）の anchor は待たない。自動表示が被さって隠れるので
        self.assertNotIn("id: '^detail$'", flow)
        self.assertEqual(waits(flow)[-2:], ["^home$", "^review_dialog$"])
        self.assertEqual(rows[0]["screen"], "review_dialog")
        self.assertEqual(rows[0]["checked"], "review_dialog")

    def test_closing_the_auto_show_returns_to_host(self):
        # 閉じる操作そのものも確かめられる。戻り先は履歴で被さる先
        rows, flows = write_flows([
            {"from": "review_dialog", "launch": True, "title": "後でで閉じる",
             "expect": "詳細画面に戻っている",
             "do": ["tap:review_dialog.later_button"]}], self.repo)
        # 経路はダイアログが出るまで。閉じるボタンは1手で押す（ダンプで見てから流すので anchor は待たない）
        self.assertEqual(waits(flows["test_01.1.yaml"])[-2:], ["^home$", "^review_dialog$"])
        flow = flows["test_01.yaml"]
        self.assertEqual(waits(flow), ["^detail$", "^detail\\.title$"])
        self.assertEqual(rows[0]["screen"], "detail")
        # 戻る操作で戻った画面では、自動表示を確かめない（入ったときに出るもの）
        self.assertNotIn("runFlow", flow)

    def test_other_items_still_close_it(self):
        rows, flows = write_flows([
            {"from": "review_dialog", "launch": True, "title": "a", "expect": "a"},
            {"from": "detail", "launch": True, "title": "b", "expect": "b"}], self.repo)
        self.assertNotIn("runFlow", flows["test_01.yaml"])
        self.assertIn("runFlow", flows["test_02.yaml"])

    def test_check_passes_and_counts_dialog_as_reachable(self):
        code, out = self.check()
        self.assertEqual(code, 0)
        self.assertIn("到達できない: なし", out)
        self.assertIn("detail: 自動表示 review_dialog を着くたびに確かめる", out)

    def test_check_reports_missing_file(self):
        self.dialog.unlink()
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("自動表示 review_dialog のファイルが無い", out)

    def test_check_reports_missing_dismiss(self):
        self.dialog.write_text("anchor: review_dialog\nelements: []\n", encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("anchor と閉じる操作（screen: back）の両方が要る", out)


class ManifestItems(unittest.TestCase):
    """名前で1項目を引くときは、属するテストケースを親として一緒に返す。"""

    def cli(self, *names):
        f = Path(tempfile.mkdtemp()) / "manifest.json"
        f.write_text(json.dumps({"cases": [
            {"title": "A", "launch": False, "items": [{"name": "test_01", "title": "a"},
                                                      {"name": "test_02", "title": "b"}]},
            {"title": "B", "items": [{"name": "test_03", "title": "c"}]}]}), encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPTS / "manifest_items.py"), str(f), *names],
                           capture_output=True, text=True)
        shutil.rmtree(f.parent)
        return r

    def test_lists_all_items_in_order(self):
        self.assertEqual(self.cli().stdout.splitlines(),
                         ["test_01\tA\ta", "test_02\tA\tb", "test_03\tB\tc"])

    def test_finds_an_item_with_its_case(self):
        got = json.loads(self.cli("test_02").stdout)
        self.assertEqual(got, [{"case": {"title": "A", "launch": False, "siblings": ["test_01", "test_02"]},
                                "item": {"name": "test_02", "title": "b"}}])

    def test_unknown_name_is_refused(self):
        r = self.cli("test_09")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("test_09 という項目が無い", r.stderr)


class ReportShape(unittest.TestCase):
    """判定の欄は LLM が書くので形が揺れる。build_report.py --check で書いた直後に止める。"""

    def problems(self, manifest):
        ns = runpy.run_path(str(SCRIPTS / "build_report.py"), run_name="build_report")
        return ns["shape_problems"](manifest)

    def test_footer_must_be_text(self):
        out = self.problems({"cases": [], "footer": {"確認していないこと": "エラー系"}})
        self.assertEqual(len(out), 1)
        self.assertIn("footer が文字列ではない（dict）", out[0])

    def test_section_fields_must_be_text(self):
        out = self.problems(cases_manifest([
            {"name": "test_01", "title": "a", "desc": ["x"], "note": {"a": 1}, "result": "OK"}]))
        self.assertEqual(len(out), 2)
        self.assertIn("test_01 の desc が文字列ではない（list）", out[0])
        self.assertIn("test_01 の note が文字列ではない（dict）", out[1])

    def test_pending_is_reported_but_retake_passes(self):
        out = self.problems(cases_manifest([
            {"name": "test_01", "title": "a", "result": "PENDING"},
            {"name": "test_02", "title": "b", "result": "RETAKE"}]))
        self.assertEqual(len(out), 1)
        self.assertIn("test_01 の result が 'PENDING'", out[0])

    def test_text_footer_passes(self):
        self.assertEqual(self.problems(dict(cases_manifest([
            {"name": "test_01", "title": "a", "desc": "x", "result": "NG"}]),
            footer="確認していないこと: エラー系\n作成したデータ: 無し")), [])

    def test_skip_needs_a_reason(self):
        # 撮れなかった項目は、理由（desc）が無いと読む側に何も残らない
        out = self.problems(cases_manifest([
            {"name": "test_01", "title": "a", "desc": " ", "result": "SKIP"},
            {"name": "test_02", "title": "b", "desc": "フローが落ちた", "result": "SKIP"}]))
        self.assertEqual(len(out), 1)
        self.assertIn("test_01 は SKIP なのに desc が空", out[0])


def tiny_png(path):
    """1x1 の PNG を書く。"""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))


class ReportSkip(unittest.TestCase):
    """撮れなかった項目（SKIP）も、マニフェストから外さずにカードとして出す。"""

    def build(self, items):
        ns = runpy.run_path(str(SCRIPTS / "build_report.py"), run_name="build_report")
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work)
        tiny_png(work / "shots" / "iphone" / "test_01.png")
        tiny_png(work / "shots" / "iphone" / "test_02.png")
        manifest = work / "manifest.json"
        manifest.write_text(json.dumps(cases_manifest(items)), encoding="utf-8")
        return ns["build"](manifest, "題").read_text(encoding="utf-8")

    @staticmethod
    def images(name):
        return [{"src": "shots/{}/{}.png".format(d, name), "label": d} for d in ("iphone", "ipad")]

    def test_skip_is_counted_apart(self):
        doc = self.build([
            {"name": "test_01", "title": "a", "desc": "x", "result": "OK", "images": [{"src": "shots/iphone/test_01.png"}]},
            {"name": "test_03", "title": "c", "desc": "フローが落ちた", "result": "SKIP",
             "images": self.images("test_03")}])
        self.assertIn('<span class="ok">OK 1</span><span class="ng">NG 0</span>'
                      '<span class="skip">撮れなかった 1</span><span class="all">全 2 項目</span>', doc)
        self.assertIn('<section class="card skip">', doc)
        self.assertIn("— 撮れなかった</span>", doc)
        self.assertIn("<span>撮れなかった理由</span><p>フローが落ちた</p>", doc)
        # 1枚も撮れていなければ、証跡の枠ごと出さない
        card = doc[doc.index('<section class="card skip">'):]
        self.assertNotIn('class="shots', card[:card.index("</section>")])

    def test_skip_shows_the_devices_that_were_shot(self):
        # iPhone は撮れて iPad は撮れなかった。iPhone の画像だけ出す
        doc = self.build([{"name": "test_02", "title": "b", "desc": "iPad はフローが落ちた",
                           "result": "SKIP", "images": self.images("test_02")}])
        self.assertEqual(doc.count("<figure>"), 1)
        self.assertIn("<figcaption>iphone</figcaption>", doc)

    def test_no_skip_no_pill(self):
        doc = self.build([{"name": "test_01", "title": "a", "desc": "x", "result": "NG",
                           "images": [{"src": "shots/iphone/test_01.png"}]}])
        self.assertNotIn('class="skip"', doc)
        self.assertIn('<span class="all">全 1 項目</span>', doc)

def reads_screen(args):
    """run_flows.py が画面を読む inspect か（撮った証跡の隣にダンプを置く inspect ではなく）。
    読むときは作業用の置き場（`.work/replay/<出力先の名前>/<端末>`。テストでは `<tmp>/replay`）を渡す。"""
    return len(args) == 3 or "replay" in Path(args[3]).parts


def dump_line(cx, cy, on, rid, text="", state="", height=40):
    """elements.py の出力の1行（タブ区切り）。上端は中心から高さの半分を引く。"""
    return "\t".join(["({},{})".format(cx, cy), on, str(cy - height // 2), rid, text, state]) + "\n"


DUMP_HEAD = "画面: list\n" + "\t".join(["tap", "画面内", "上端", "id", "テキスト", "状態"]) + "\n"


def build_err(items, repo=FIXTURE):
    """組めない plan。stderr に出た理由を返す。"""
    with contextlib.redirect_stderr(io.StringIO()) as err, \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            write_flows(items, repo)
        except SystemExit as e:
            return e.code, err.getvalue()
    raise AssertionError("組めてしまった")


class Routing(unittest.TestCase):
    """経路は expect の screen を辺にして引く。"""

    def mp(self, repo=FIXTURE):
        return screen_map.load_map(str(repo))

    def test_tab_is_preferred_at_same_length(self):
        # home からの settings は、メニュー（push）とタブの2通り。同じ長さならタブ
        hops = self.mp().path_from("home", "settings")
        self.assertEqual([e[0].target for _, e in hops], ["tab_bar.settings"])

    def test_conditional_edge_needs_when(self):
        code, err = build_err([{"from": "login_alert", "title": "a", "expect": "a"}])
        self.assertEqual(code, 2)
        self.assertIn("条件つき（when: 未ログイン）", err)
        self.assertIn("項目の when に同じ文言を書く", err)
        rows, _ = write_flows([{"from": "login_alert", "when": ["未ログイン"], "title": "a", "expect": "a"}])
        self.assertEqual(rows[0]["screen"], "login_alert")

    def test_branch_in_do_needs_when(self):
        code, err = build_err([{"from": "detail", "title": "a", "expect": "a",
                                "do": ["tap:detail.review_button"]}])
        self.assertIn("結果が分かれる（ログイン中 / 未ログイン）", err)
        rows, flows = write_flows([{"from": "detail", "when": ["ログイン中"], "title": "a",
                                    "expect": "a", "do": ["tap:detail.review_button"]}])
        self.assertEqual(rows[0]["screen"], "review_editor")

    def test_element_when_is_not_used_for_routing_but_ok_in_do(self):
        repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, repo)
        detail = repo / "screen-map" / "screens" / "detail.yaml"
        # フォローボタンを押すと設定に移ることにする（条件つきの要素の辺）
        detail.write_text(detail.read_text(encoding="utf-8").replace(
            "expect: {hidden: self}", "expect: {screen: settings, via: push}"), encoding="utf-8")
        hops = screen_map.load_map(str(repo)).path_from("detail", "settings")
        self.assertIsNone(hops)
        hops = screen_map.load_map(str(repo)).path_from("detail", "settings", ("フォローしていないとき",))
        self.assertEqual([e[0].target for _, e in hops], ["detail.follow_button"])
        rows, _ = write_flows([{"from": "detail", "title": "a", "expect": "a",
                                "do": ["tap:detail.follow_button"]}], repo)
        self.assertEqual(rows[0]["screen"], "settings")
        shutil.rmtree(repo.parent)

    def test_unknown_op_lists_what_the_screen_has(self):
        code, err = build_err([{"from": "list", "title": "a", "expect": "a", "do": ["tap:list.nothing"]}])
        self.assertIn("see:list.empty_view", err)
        self.assertIn("scroll:down", err)


class Flows(unittest.TestCase):
    """フローに積むもの。"""

    def test_scroll_before_every_tap_on_the_way(self):
        # 経路で押す要素は、押す前に見えるまでスクロールする
        rows, flows = write_flows([{"from": "detail", "title": "a", "expect": "a",
                                    "do": ["tap:detail.favorite_button"]}])
        route = flows["test_01.1.yaml"]
        block = route[route.index("# home: tap home.fav"):]
        self.assertLess(block.index("scrollUntilVisible:\n    element:\n      id: '^home\\.fav$'"),
                        block.index("- tapOn"))
        # do の要素は run_flows.py がダンプで探してあるので、1手のフローは押すだけ
        hand = flows["test_01.yaml"]
        self.assertNotIn("scrollUntilVisible", hand)
        self.assertTrue(hand.split("---\n", 1)[1].startswith("# detail: tap detail.favorite_button"))
        self.assertIn("id: '^detail\\.favorite_button$'\n      selected: true", hand)

    def test_arrival_waits_anchor_then_ready_then_settles(self):
        rows, flows = write_flows([{"from": "list", "title": "a", "expect": "a"}])
        flow = flows["test_01.yaml"]
        tail = flow[flow.index("id: '^list$'"):]
        self.assertLess(tail.index("id: '^list$'"), tail.index("list\\.empty_view"))
        self.assertLess(tail.index("list\\.empty_view"), tail.index("waitForAnimationToEnd"))
        self.assertLess(tail.index("waitForAnimationToEnd"), tail.index("takeScreenshot"))

    def test_see_scrolls_to_the_element(self):
        rows, flows = write_flows([{"from": "list", "title": "a", "expect": "a", "do": ["see:list.footer"]}])
        self.assertIn("# list: see list.footer — 一覧の末尾\n- scrollUntilVisible:", flows["test_01.yaml"])
        self.assertEqual(rows[0]["checked"], "list.footer")

    def test_hidden_and_external(self):
        # 項目の途中で外に出るなら、その場でアプリに戻す
        rows, flows = write_flows([{"from": "detail", "title": "a", "expect": "a",
                                    "do": ["tap:detail.share_button", "tap:detail.follow_button"]}])
        flow = item_flow(flows, "test_01")
        self.assertIn("notVisible:\n      id: '^detail\\.follow_button$'", flow)
        ext = flow[flow.index("# アプリの外（safari）に出る"):flow.index("follow_button")]
        self.assertIn("- launchApp:\n    stopApp: false", ext)
        self.assertIn("id: '^detail$'", ext)

    def test_ending_outside_is_shot_outside(self):
        # 外に出る操作で終わる項目は、外に居るまま撮る。戻すのは次のフローの頭
        rows, flows = write_flows([
            {"from": "detail", "title": "a", "expect": "a", "do": ["tap:detail.share_button"]},
            {"from": "detail", "title": "b", "expect": "b", "do": ["tap:detail.follow_button"]}])
        # 2つ目は同じ画面で続くが、外から戻す本と、1手の本に分かれる
        first, second = flows["test_01.yaml"], item_flow(flows, "test_02")
        ext = first[first.index("# アプリの外（safari）に出る"):]
        self.assertNotIn("launchApp", ext)
        # Safari のページの読み込みが終わるまで待ってから撮る（IsPageLoaded だけでは早いので更新ボタンも）
        self.assertLess(ext.index("id: '^TabDocument\\?.*IsPageLoaded=true.*'"), ext.index("id: '^ReloadButton$'"))
        self.assertLess(ext.index("ReloadButton"), ext.index("takeScreenshot"))
        self.assertEqual(rows[0]["checked"], "ReloadButton")
        head = second[:second.index("follow_button")]
        self.assertIn("# 続き: アプリの外から", head)
        self.assertLess(head.index("- launchApp:\n    stopApp: false"), head.index("id: '^detail$'"))

    def test_ending_outside_elsewhere_is_not_checked(self):
        # 読み込みの終わりが分からない外のアプリ（Safari 以外）は、動きが止まるのだけ待つ
        self.assertEqual(External("safari").loaded[-1][0], "ReloadButton")
        self.assertEqual(External("app_store").loaded, ())
        st = Act("detail", Tap("detail.share_button"), [External("app_store")], stay=True)
        self.assertIsNone(check_of(None, st))
        w = FlowWriter(None, [st], "jp.example.App", False)
        w.check(st, st.result[0], stay=True)
        self.assertEqual([c for c in w.out if isinstance(c, dict)], [{"waitForAnimationToEnd": {"timeout": 3000}}])

    def test_ending_outside_before_restart(self):
        # 次の項目が起動し直すなら、戻す操作は挟まない
        rows, flows = write_flows([
            {"from": "detail", "title": "a", "expect": "a", "do": ["tap:detail.share_button"]},
            {"from": "list", "launch": True, "title": "b", "expect": "b", "do": []}])
        self.assertNotIn("アプリの外から", flows["test_02.yaml"])
        self.assertIn("- stopApp", flows["test_02.yaml"])

    def test_gesture(self):
        rows, flows = write_flows([{"from": "list", "title": "a", "expect": "a", "do": ["scroll:down"]}])
        self.assertIn("- scroll\n- extendedWaitUntil:\n    visible:\n      id: '^list\\.footer$'",
                      flows["test_01.yaml"])

    def test_pattern_value_can_be_fixed(self):
        rows, flows = write_flows([{"from": "list", "title": "a", "expect": "a",
                                    "do": ["tap:list.row.C++入門"]}])
        self.assertEqual([(u.kind, u.step.key if u.kind == "hand" else None) for u in rows[0]["units"]],
                         [("flow", None), ("hand", "tap:list.row.C++入門")])
        self.assertEqual(rows[0]["inputs"], {})
        self.assertIn("id: '^list\\.row\\.C\\+\\+入門$'", flows["test_01.yaml"])

    def test_pattern_on_the_way_is_picked(self):
        # from までの途中で一覧の行を押す。見えている1件目を選ぶので、そこは1手で流す
        rows, _ = write_flows([{"from": "list", "title": "a", "expect": "a"},
                               {"from": "detail", "title": "b", "expect": "b"}])
        self.assertEqual([u.step.key for u in rows[1]["units"] if u.kind == "hand"], ["経路 tap:list.row.*"])
        self.assertEqual(rows[1]["inputs"], {})

    def test_runtime_on_tap_is_refused(self):
        code, err = build_err([{"from": "list", "title": "a", "expect": "a",
                                "do": [{"op": "tap:list.row.*", "runtime": True}]}])
        self.assertIn("runtime は付けられない", err)

    def test_pick_on_non_pattern_is_refused(self):
        code, err = build_err([{"from": "list", "title": "a", "expect": "a",
                                "do": [{"op": "tap:Search", "pick": "x"}]}])
        self.assertIn("パターンの要素（ID の末尾が *）ではないので、実行時に選べない", err)

    def test_same_op_twice_gets_two_keys(self):
        rows, flows = write_flows([{"from": "list", "title": "a", "expect": "a",
                                    "do": ["tap:list.row.*", "tap:BackButton", "tap:list.row.*"]}])
        self.assertEqual([u.step.key for u in rows[0]["units"] if u.kind == "hand"],
                         ["tap:list.row.*", "tap:BackButton", "tap:list.row.*#2"])


class AutoShowAfter(unittest.TestCase):
    """after つきの自動表示は、after の画面から戻ったときだけ確かめる。"""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, self.repo)
        screens = self.repo / "screen-map" / "screens"
        (screens / "review_dialog.yaml").write_text(
            "anchor: review_dialog\nsummary: 詳細から戻ったときに出る\nelements:\n"
            "  - id: review_dialog.later_button\n    name: 後で\n    actions:\n      - tap:\n"
            "        summary: 閉じる\n        expect: {screen: back, via: dismiss}\n", encoding="utf-8")
        lst = screens / "list.yaml"
        lst.write_text(lst.read_text(encoding="utf-8")
                       + "auto_shows:\n  - {screen: review_dialog, after: detail}\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.repo.parent)

    def test_checked_only_when_back_from_after(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a"},
            {"from": "list", "title": "b", "expect": "b", "do": ["tap:list.row.*", "tap:BackButton"]}],
            self.repo)
        self.assertNotIn("runFlow", flows["test_01.yaml"])
        flow = flows["test_02.yaml"]
        self.assertIn("# 自動表示: review_dialog（detail から戻ったとき）", flow)
        back = flow[flow.index("^BackButton$"):]
        self.assertLess(back.index("runFlow"), back.index("id: '^list$'"))

    def test_as_target_goes_to_after_and_back(self):
        rows, flows = write_flows([{"from": "review_dialog", "title": "a", "expect": "a"}], self.repo)
        flow = flows["test_01.yaml"]
        self.assertIn("# list: 自動表示 review_dialog を待つ", flow)
        self.assertNotIn("runFlow", flow)
        self.assertEqual(rows[0]["screen"], "review_dialog")

    def test_check(self):
        mp = screen_map.load_map(str(self.repo))
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = map_check.cmd_check(mp)
        self.assertEqual(code, 0, o.getvalue())
        self.assertIn("自動表示 review_dialog を detail から戻るたびに確かめる", o.getvalue())
        lst = self.repo / "screen-map" / "screens" / "list.yaml"
        lst.write_text(lst.read_text(encoding="utf-8").replace("after: detail", "after: viewer"),
                       encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = map_check.cmd_check(screen_map.load_map(str(self.repo)))
        self.assertEqual(code, 1)
        self.assertIn("自動表示 review_dialog の after viewer の画面が無い", o.getvalue())


class FirstVisible(unittest.TestCase):
    """パターンの要素は、ツリー順ではなく画面に見えている1件目を選ぶ。"""

    DUMP = (DUMP_HEAD
            + dump_line(195, -40, "×", "list.row.吾輩は猫である", "吾輩は猫である")
            + dump_line(195, 60, "○", "list", "一覧")
            + dump_line(195, 300, "○", "list.row.C++入門", "C++入門", "選択")
            + dump_line(195, 380, "○", "list.row.こころ", "")
            + dump_line(195, 900, "×", "list.row.坊っちゃん", "坊っちゃん"))

    def test_skips_offscreen_and_strips_state(self):
        self.assertEqual(first_visible(self.DUMP, "list.row.*"), "list.row.C++入門")

    def test_locate_counts_offscreen_rows_for_index(self):
        # Maestro の index は画面外も含めた、同じ ID の行の位置順。画面外の 吾輩は猫である は別の ID なので数えず、C++入門 は1件なので 0
        self.assertEqual(RF["locate"](self.DUMP, "list.row.*"), ("list.row.C++入門", 0, 1))
        dump = (dump_line(195, -40, "×", "list.row.牛乳", "")
                + dump_line(195, 300, "○", "list.row.牛乳", "")
                + dump_line(195, 380, "○", "list.row.牛乳#2", ""))
        self.assertEqual(RF["locate"](dump, "list.row.*"), ("list.row.牛乳", 1, 2))
        # 名前そのものが #2 で終わる行があれば、そちらを採る
        self.assertEqual(RF["locate"](dump, "list.row.*", value="list.row.牛乳#2"), ("list.row.牛乳#2", 0, 1))
        self.assertIsNone(RF["locate"](dump, "list.row.*", value="list.row.牛乳#3"))

    def test_none_when_nothing_visible(self):
        self.assertIsNone(first_visible(self.DUMP, "detail.cell.*"))

    def test_id_with_spaces(self):
        dump = dump_line(201, 241, "○", "list.row.BOITEUX ・ BOITEUSE", "BOITEUX ・ BOITEUSE, 李 箱")
        self.assertEqual(first_visible(dump, "list.row.*"), "list.row.BOITEUX ・ BOITEUSE")

    def test_index_follows_the_top_edge(self):
        # 背の高い行 A（上端 200、中心 300）と低い行 B（上端 240、中心 260）。中心で並べると
        # B が先だが、Maestro の index は上端で並べるので A が 0
        dump = (dump_line(195, 260, "○", "list.row.牛乳", "B", height=40)
                + dump_line(195, 300, "○", "list.row.牛乳", "A", height=200))
        self.assertEqual(RF["locate"](dump, "list.row.*"), ("list.row.牛乳", 0, 2))
        self.assertEqual(RF["locate"](dump, "list.row.*", value="list.row.牛乳#2"), ("list.row.牛乳", 1, 2))


class ElementsOutput(unittest.TestCase):
    """elements.py の出力はタブ区切りで、id と上端を独立した欄に持つ。"""

    def test_columns(self):
        dump = {"ui_schema": {}, "elements": [{"b": "[0,0][402,874]", "c": [
            {"b": "[16,208][386,274]", "a11y": "BOITEUX ・ BOITEUSE, 李 箱",
             "rid": "browse.book_row.BOITEUX ・ BOITEUSE"},
            {"b": "[16,120][200,160]", "txt": "作品名", "rid": "browse.target_picker.title", "selected": True},
            {"b": "[16,-80][386,-20]", "rid": "browse.book_row.上の行"},
            {"b": "[50,20][90,40]", "txt": "0:02"}]}]}
        f = Path(tempfile.mkdtemp()) / "d.json"
        f.write_text(json.dumps(dump, ensure_ascii=False), encoding="utf-8")
        import subprocess
        out = subprocess.run([sys.executable, str(SCRIPTS / "device" / "elements.py"), str(f)],
                             capture_output=True, text=True).stdout.splitlines()
        shutil.rmtree(f.parent)
        self.assertEqual(out[1].split("\t"), ["tap", "画面内", "上端", "id", "テキスト", "状態"])
        rows = [l.split("\t") for l in out[2:]]
        self.assertIn(["(201,-50)", "×", "-80", "browse.book_row.上の行", "", ""], rows)
        self.assertIn(["(70,30)", "○", "20", "", "0:02", ""], rows)
        self.assertIn(["(108,140)", "○", "120", "browse.target_picker.title", "作品名", "選択"], rows)
        self.assertIn(["(201,241)", "○", "208", "browse.book_row.BOITEUX ・ BOITEUSE",
                       "BOITEUX ・ BOITEUSE, 李 箱", ""], rows)
        # 読む側がそのまま使える
        self.assertEqual(RF["locate"]("\n".join(out), "browse.book_row.*"), ("browse.book_row.BOITEUX ・ BOITEUSE", 0, 1))


class CoveredRows(unittest.TestCase):
    """画面の中でも、前面の要素の裏にある行は `裏` にし、見えている1件目に選ばない。"""

    def run_elements(self, dump):
        f = Path(tempfile.mkdtemp()) / "d.json"
        f.write_text(json.dumps(dump, ensure_ascii=False), encoding="utf-8")
        import subprocess
        out = subprocess.run([sys.executable, str(SCRIPTS / "device" / "elements.py"), str(f)],
                             capture_output=True, text=True).stdout
        shutil.rmtree(f.parent)
        return out, {l.split("\t")[3] or l.split("\t")[4]: l.split("\t")[1]
                     for l in out.splitlines()[2:]}

    def row(self, y0, name):
        return {"b": "[16,{}][386,{}]".format(y0, y0 + 66), "a11y": name, "rid": "browse.row." + name}

    def app(self, *windows):
        return {"ui_schema": {}, "elements": [
            {"b": "[0,0][402,874]", "a11y": "App", "c": [
                {"b": "[0,0][402,874]", "c": list(w)} for w in windows]},
            {"b": "[0,0][402,54]"}]}   # ステータスバーの窓

    def test_rows_under_pinned_header_and_status_bar(self):
        # スクロールした一覧。行が先に並び、固定された検索欄が後ろに並ぶ（実測の形）
        out, on = self.run_elements(self.app([
            self.row(-18, "A"), self.row(48, "B"), self.row(115, "C"), self.row(182, "D"),
            {"b": "[8,72][394,116]", "rid": "browse.search_field", "txt": "作品名で絞り込む"},
            {"b": "[16,126][386,158]", "rid": "browse.target_picker", "txt": "作品名"}]))
        self.assertEqual([on["browse.row." + n] for n in "ABCD"], ["裏", "裏", "裏", "○"])
        self.assertEqual(on["browse.search_field"], "○")
        self.assertEqual(first_visible(out, "browse.row.*"), "browse.row.D")

    def test_navigation_bar_is_in_front_even_if_listed_first(self):
        # NavigationStack はバーを中身より前に並べるが、バーが前面
        out, on = self.run_elements(self.app([
            {"b": "[0,62][402,116]", "rid": "Nav", "c": [
                {"b": "[16,62][60,106]", "rid": "BackButton", "a11y": "Back"}]},
            self.row(50, "A"), self.row(116, "B"),
            {"b": "[16,70][386,110]", "txt": "スクロールした中身"}]))
        self.assertEqual(on["BackButton"], "○")
        self.assertEqual(on["browse.row.A"], "裏")
        self.assertEqual(on["browse.row.B"], "○")

    def test_later_window_is_in_front(self):
        # キーボードは後ろの窓。タブバー（バー）より前面
        out, on = self.run_elements(self.app(
            [self.row(600, "A"),
             {"b": "[0,791][402,874]", "a11y": "Tab Bar", "c": [
                 {"b": "[154,795][248,849]", "rid": "tab.search", "a11y": "さがす"}]}],
            [{"b": "[0,538][402,874]", "c": [
                {"b": "[8,805][76,874]", "a11y": "Next keyboard"},
                {"b": "[100,805][300,874]", "a11y": "space"},
                {"b": "[8,600][394,660]", "a11y": "qwerty"}]}]))
        self.assertEqual(on["tab.search"], "裏")
        self.assertEqual(on["browse.row.A"], "裏")
        self.assertEqual(on["space"], "○")

    def test_same_thing_is_not_covering(self):
        # SwiftUI が文言をまとめた要素は、中のラベルより後ろに並ぶ。ラベルは裏ではない
        out, on = self.run_elements(self.app([
            {"b": "[32,167][80,187]", "a11y": "作品名"},
            {"b": "[16,151][386,203]", "a11y": "作品名, BOITEUX"}]))
        self.assertEqual(on["作品名"], "○")

    def test_inset_rows_are_not_bars(self):
        # iPad の行は余白があっても幅の96%。上端近くの行をバーと取り違えない
        dump = {"ui_schema": {}, "elements": [{"b": "[0,0][834,1210]", "a11y": "App", "c": [
            {"b": "[0,0][834,1210]", "c": [
                {"b": "[0,24][834,88]", "rid": "tabs", "c": [
                    {"b": "[377,33][457,65]", "rid": "tab.search", "a11y": "さがす"}]},
                {"b": "[16,7][818,86]", "rid": "browse.row.A", "a11y": "A"}]}]}]}
        out, on = self.run_elements(dump)
        self.assertEqual(on["tab.search"], "○")
        self.assertEqual(on["browse.row.A"], "裏")


class ItemRunBase:
    """1項目を run_item() で流す。plan から組んだ行（units）を使い、Maestro は叩かない。

    `sh` を差し替える。画面を読むと `self.dumps` を頭から1つずつ返し（尽きたら最後のものを
    返し続ける）、生のダンプは `self.raw`。流したもの（名前と中身）は `self.calls` に残す。
    項目は1つのテストケースに `self.items` を並べ、その最後の項目を流す。
    """

    items = [{"from": "list", "do": ["see:list.footer"]}]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.flows = self.tmp / "flows"
        self.flows.mkdir(parents=True)
        self.out.mkdir()
        self.calls, self.dumps, self.raw = [], [DUMP_HEAD], ""
        self.saved = {k: RF[k] for k in ("sh", "HERE", "pause")}
        RF["HERE"] = self.tmp / "scripts"
        RF["pause"] = lambda: None
        self.build()

        def fake_sh(args, quiet=True):
            if args[0] == "run":
                self.calls.append(("run", args[3], args[2]))
                return 0
            if args[0] == "inspect" and reads_screen(args):
                self.calls.append(("inspect", args[2], args[3] if len(args) > 3 else ""))
                state = self.tmp / ".work" / "state"
                state.mkdir(parents=True, exist_ok=True)
                dump = self.dumps.pop(0) if len(self.dumps) > 1 else self.dumps[0]
                (state / "last_dump_AAAA.txt").write_text(dump, encoding="utf-8")
                (state / "last_raw_AAAA.json").write_text(self.raw, encoding="utf-8")
            return 0
        RF["sh"] = fake_sh

    def build(self):
        items = [dict({"title": "t", "expect": "x"}, **it) for it in self.items]
        rows, _ = write_flows(None, cases=[{"title": "T", "items": items}])
        self.row = rows[-1]
        self.sec = {"name": self.row["name"], "flow": self.row["flow"],
                    "devices": {"iphone": {"inputs": dict(self.row["inputs"]), "picked": {}}},
                    "desc": "", "note": "", "result": "PENDING"}

    def tearDown(self):
        RF.update(self.saved)
        shutil.rmtree(self.tmp)

    def given(self, key, value):
        self.sec["devices"]["iphone"]["inputs"][key] = value

    def run_item(self, first=0, retake=False, ask=None):
        """(結果, 標準出力)。結果は ("done", None) / ("failed", 理由) / ("stopped", 何手目か)。
        `retake` なら撮り直し中として流す（止まったあとはテストケースの頭からなぞり直す）。
        `ask` は条件つきの選択の問答（--next と送った回数）。流したあとの問答は `self.dv.ask`。"""
        shots = self.out / "shots" / "iphone"
        shots.mkdir(parents=True, exist_ok=True)
        dv = RF["DeviceRun"]("iphone", "AAAA", shots, self.tmp / "replay", self.flows,
                             self.out / "progress_iphone.log", "jp.example.App",
                             screen_map.load_map(str(FIXTURE)), False, retake, ask)
        self.dv = dv
        with contextlib.redirect_stdout(io.StringIO()) as o, contextlib.redirect_stderr(io.StringIO()):
            got = RF["run_item"](dv, self.sec, self.row, False, first)
        return got, o.getvalue()

    def swipes(self):
        """探すために流したもの（下へ / 上へ / 親を次へ / 親を戻す / 親を寄せる）。"""
        out = []
        for c, name, body in self.calls:
            if c != "run" or ".seek" not in name or "takeScreenshot" in body:
                continue
            out.append("center" if "centerElement" in body else "next" if "LEFT" in body
                       else "back" if "RIGHT" in body else "up" if "direction: DOWN" in body else "down")
        return out

    def flows_run(self):
        """流した項目の本（run の名前）。探すためのものは除く。"""
        return [n for c, n, _ in self.calls if c == "run" and ".seek" not in n]

    def body(self, name):
        return [b for c, n, b in self.calls if c == "run" and n == name][0]

    def picked(self):
        return self.sec["devices"]["iphone"]["picked"]

    def log(self):
        return (self.out / "progress_iphone.log").read_text(encoding="utf-8")


class SeekRun(ItemRunBase, unittest.TestCase):
    """do の要素をダンプを読みながら探す。送っても見えている ID が変わらなければ端で、
    下の端 → 上の端まで探して無ければ、送らずに読み直してから諦める（フローの
    scrollUntilVisible の60秒を払わない）。"""

    TOP = (DUMP_HEAD + dump_line(195, 60, "○", "list", "")
           + dump_line(195, 300, "○", "list.row.こころ", ""))
    MID = (DUMP_HEAD + dump_line(195, 60, "○", "list", "")
           + dump_line(195, 300, "○", "list.row.坊っちゃん", ""))
    FOOTER = MID + dump_line(195, 700, "○", "list.footer", "")

    def test_on_screen_needs_no_swipe(self):
        self.dumps = [self.FOOTER]
        self.assertEqual(self.run_item()[0], ("done", None))
        self.assertEqual(self.swipes(), [])
        self.assertEqual(self.flows_run(), ["test_01.1", "test_01"])

    def test_found_after_swiping(self):
        self.dumps = [self.TOP, self.MID, self.FOOTER]
        self.assertEqual(self.run_item()[0], ("done", None))
        self.assertEqual(self.swipes(), ["down", "down"])
        self.assertEqual(self.flows_run(), ["test_01.1", "test_01"])
        # 流したものを1本の記録に残す。探して送った回数も、そのままの行で
        record = (self.flows / "test_01.yaml").read_text(encoding="utf-8")
        self.assertTrue(record.startswith("appId: jp.example.App\n---\n"))
        self.assertEqual(record.count("---"), 1)
        self.assertIn("# run_flows.py がダンプで探す: list.footer\n- scroll\n- scroll\n", record)
        # 画面を読むときは作業用の置き場を渡す。maestrod.py がそこから実行ごと・端末ごとの置き場を決める
        reads = [where for c, n, where in self.calls if c == "inspect"]
        self.assertTrue(reads)
        self.assertEqual(set(reads), {str(self.tmp / "replay")})
        self.assertLess(record.index("- scroll\n"), record.index("takeScreenshot"))

    def test_gives_up_at_both_edges(self):
        # 下へ送って変わらなくなったら下の端。上へも同じ。読み直しても無ければ、do は流さずに諦める
        self.dumps = [self.TOP, self.MID, self.MID, self.TOP]
        got = self.run_item()[0]
        self.assertEqual(got, ("failed", "list.footer が画面に無い（上下の端まで探した）"))
        self.assertEqual(self.swipes(), ["down", "down", "up", "up"])
        self.assertEqual(self.flows_run(), ["test_01.1"])
        self.assertIn("test_01 撮影できず list.footer が画面に無い", self.log())
        reads = [n for c, n, _ in self.calls if c == "inspect"]
        self.assertEqual(len(reads), 1 + 4 + RF["SEEK_WAITS"])

    def test_below_the_screen_in_the_tree_scrolls_down(self):
        self.dumps = [self.MID + dump_line(195, 1400, "×", "list.footer", ""), self.FOOTER]
        self.assertEqual(self.run_item()[0], ("done", None))
        self.assertEqual(self.swipes(), ["down"])

    def test_above_the_screen_in_the_tree_scrolls_up_first(self):
        self.dumps = [self.MID + dump_line(195, -200, "×", "list.footer", ""), self.FOOTER]
        self.assertEqual(self.run_item()[0], ("done", None))
        self.assertEqual(self.swipes(), ["up"])

    def test_late_element_is_found_by_reading_again(self):
        # 両端まで探して無くても、読み込みが遅れて出るものは送らずに読み直して拾う
        self.dumps = [self.TOP, self.TOP, self.TOP, self.TOP, self.FOOTER]
        self.assertEqual(self.run_item()[0], ("done", None))
        self.assertEqual(self.swipes(), ["down", "up"])

    def test_behind_the_keyboard_is_left_to_the_flow(self):
        # 前面の要素の裏にある。送っても出てこないが、ツリーにはあるので流してみる
        self.dumps = [self.TOP + dump_line(195, 700, "裏", "list.footer", "")]
        self.assertEqual(self.run_item()[0], ("done", None))

    def test_off_screen_in_the_tree_is_not_found_at_the_end(self):
        # ツリーに画面外（×）で出ているだけの要素は、両端まで送っても見えなければ見つかったとしない。
        # 押すと Maestro がその位置を叩いて別の要素を押す
        self.dumps = [self.TOP + dump_line(195, 1400, "×", "list.footer", "")]
        self.assertEqual(self.run_item()[0], ("failed", "list.footer が画面に無い（上下の端まで探した）"))

    def test_growing_list_stops_after_the_limit(self):
        # 読み込みで伸び続ける一覧は端に着かない。回数で諦める
        self.dumps = [DUMP_HEAD + dump_line(195, 300, "○", "list.row.{}".format(n), "")
                      for n in range(RF["SEEK_SWIPES"] + 2)]
        got = self.run_item()[0]
        self.assertEqual(got, ("failed", "list.footer が見つからない（{}回送っても端に着かない）"
                                         .format(RF["SEEK_SWIPES"])))
        self.assertEqual(len(self.swipes()), RF["SEEK_SWIPES"])


class AutoPickRun(ItemRunBase, unittest.TestCase):
    """条件の無い選択は run_flows.py が画面を読んで決め、picked に書く。条件つきは止めて決めさせる。"""

    items = [{"from": "list", "do": ["tap:list.row.*"]}]
    SAME = (DUMP_HEAD + dump_line(195, -40, "×", "list.row.牛乳", "")
            + dump_line(195, 300, "○", "list.row.牛乳", "")
            + dump_line(195, 380, "○", "list.row.牛乳", ""))

    def setUp(self):
        ItemRunBase.setUp(self)
        self.dumps = [FirstVisible.DUMP]

    def conditional(self):
        self.items = [{"from": "list", "do": [{"op": "tap:list.row.*", "pick": "下の方の牛乳"}]}]
        self.build()

    def test_picks_first_visible_and_writes_it_into_the_flow(self):
        self.assertEqual(self.run_item()[0], ("done", None))
        self.assertEqual(self.picked(), {"tap:list.row.*": "list.row.C++入門"})
        body = self.body("test_01")
        self.assertIn("tapOn:\n    id: '^list\\.row\\.C\\+\\+入門$'\n    index: 0", body)
        self.assertIn("test_01 撮影済み（選んだ: tap:list.row.*=list.row.C++入門）", self.log())

    def test_same_name_rows_get_index(self):
        # 同じ名前の行が画面外（上）に1件、画面内に2件。見えている1件目は、位置順で2件目
        self.dumps = [self.SAME]
        self.run_item()
        self.assertEqual(self.picked(), {"tap:list.row.*": "list.row.牛乳"})
        self.assertIn("index: 1", self.body("test_01"))
        self.assertIn("tap:list.row.*=list.row.牛乳（同じ名前 3件のうち上から2件目）", self.log())

    def test_conditional_pick_with_ordinal(self):
        # 条件で選んだ値に #2 を付けると、見えている同じ名前のうち上から2件目
        self.conditional()
        self.dumps = [self.SAME]
        self.given("tap:list.row.*", "list.row.牛乳#2")
        self.run_item(first=1)
        self.assertEqual(self.picked(), {"tap:list.row.*": "list.row.牛乳"})
        self.assertIn("id: '^list\\.row\\.牛乳$'\n    index: 2", self.body("test_01"))

    def test_conditional_pick_not_on_screen_loses_the_item(self):
        self.conditional()
        self.given("tap:list.row.*", "list.row.坊っちゃん")   # 画面外
        self.assertEqual(self.run_item(first=1)[0], ("failed", "list.row.坊っちゃん が画面に見えていない"))
        self.assertIn("list.row.坊っちゃん が画面に見えていない", self.log())

    def test_id_outside_the_pattern_stops(self):
        # 接頭辞を落として書いた（こころ）。押さずに止め、直せば続きから走れる
        self.conditional()
        self.given("tap:list.row.*", "こころ")
        got, out = self.run_item(first=1)
        self.assertEqual(got, ("stopped", 1))
        self.assertEqual([c for c in self.calls if c[0] == "run"], [])
        self.assertIn("tap:list.row.* の値 こころ が list.row.* に当たらない", out)

    def test_nothing_visible_loses_the_item(self):
        self.dumps = [DUMP_HEAD]
        self.assertEqual(self.run_item()[0], ("failed", "list.row.* が画面に無い（上下の端まで探した）"))

    def test_conditional_pick_stops_for_input(self):
        # 行が見えるところまで運んで止まる。叩き直せばその手から
        self.conditional()
        got, out = self.run_item()
        self.assertEqual(got, ("stopped", 1))
        self.assertEqual(self.flows_run(), ["test_01.1"])
        self.assertIn('この中に条件「下の方の牛乳」に合うものがあれば、その ID', out)
        self.assertIn("同じコマンドに --value '<ID>' を付けて叩き直す（続きから走る。", out)
        # いま見えている行を候補に出す。同じ ID の2件目は <ID>#2 で書ける
        self.assertIn("  - list.row.C++入門 ｜ C++入門\n  - list.row.こころ\n", out)
        self.assertNotIn("坊っちゃん", out)              # 画面外の行は候補にしない
        self.assertIn("無ければ、同じコマンドに --next を付けて叩き直す", out)
        self.assertEqual(self.swipes(), ["up"])          # 並びの先頭まで戻してから出す

    def test_same_ids_are_numbered(self):
        self.conditional()
        self.dumps = [self.SAME]
        got, out = self.run_item()
        self.assertIn("  - list.row.牛乳\n  - list.row.牛乳#2\n", out)

    def test_resume_starts_from_the_stopped_hand(self):
        self.conditional()
        self.given("tap:list.row.*", "list.row.こころ")
        self.run_item(first=1)
        self.assertEqual(self.flows_run(), ["test_01"])

    def test_resume_keeps_the_record_of_the_hands_before(self):
        # 止まる前に流した手（経路）を記録から落とさず、その続きに書く
        self.conditional()
        self.run_item()
        before = (self.flows / "test_01.yaml").read_text(encoding="utf-8")
        self.assertIn("launchApp", before)
        self.given("tap:list.row.*", "list.row.こころ")
        self.run_item(first=1)
        record = (self.flows / "test_01.yaml").read_text(encoding="utf-8")
        self.assertEqual(record.count("---"), 1)
        self.assertLess(record.index("launchApp"), record.index("# ここから再開"))
        self.assertLess(record.index("# ここから再開"), record.index("tapOn:\n    id: '^list\\.row\\.こころ$'"))


class TypedRun(ItemRunBase, unittest.TestCase):
    """打つ文字（text の runtime）は撮影する側が決める。入力欄まで運んで止まる。"""

    items = [{"from": "list", "do": [{"op": "text:list.search_field", "runtime": True}]}]

    def setUp(self):
        ItemRunBase.setUp(self)
        self.dumps = [DUMP_HEAD + dump_line(195, 120, "○", "list.search_field", "")]

    def test_stops_at_the_field(self):
        got, out = self.run_item()
        self.assertEqual(got, ("stopped", 1))
        self.assertIn("打つ文字を", out)
        self.assertNotIn("<ID>#2", out)

    def test_typed_as_is(self):
        self.given("text:list.search_field", "牛乳(1L)")
        self.assertEqual(self.run_item(first=1)[0], ("done", None))
        self.assertIn("- inputText: '牛乳(1L)'", self.body("test_01"))
        self.assertEqual(self.picked(), {})


class SeePickRun(ItemRunBase, unittest.TestCase):
    """見る行を条件で選ぶ（see の pick）。いま見えている行から選ばせ、無ければ --next で1画面ずつ送る。"""

    items = [{"from": "list", "do": [{"op": "see:list.row.*", "pick": "古い作品の行"}]}]
    BELOW = (DUMP_HEAD
             + dump_line(195, 60, "○", "list", "一覧")
             + dump_line(195, 300, "○", "list.row.坊っちゃん", "坊っちゃん, 夏目 漱石"))

    def setUp(self):
        ItemRunBase.setUp(self)
        self.dumps = [FirstVisible.DUMP]

    def test_stops_with_the_rows_on_screen(self):
        got, out = self.run_item()
        self.assertEqual(got, ("stopped", 1))
        self.assertIn('この中に条件「古い作品の行」に合うものがあれば', out)
        self.assertIn("  - list.row.C++入門 ｜ C++入門\n  - list.row.こころ\n", out)
        self.assertEqual(self.swipes(), ["up"])

    def test_chosen_row_is_seen_and_shot(self):
        self.given("see:list.row.*", "list.row.こころ")
        self.assertEqual(self.run_item(first=1)[0], ("done", None))
        self.assertEqual(self.swipes(), [])               # 選んだ画面のまま見る。送り直さない
        body = self.body("test_01")
        self.assertIn("element:\n      id: '^list\\.row\\.こころ$'\n      index: 0", body)
        self.assertEqual(self.picked(), {"see:list.row.*": "list.row.こころ"})

    def test_next_sends_one_screen_and_stops_again(self):
        self.dumps = [FirstVisible.DUMP, self.BELOW]     # 探して読む → 送って読む
        got, out = self.run_item(first=1, ask={"next": True})
        self.assertEqual(got, ("stopped", 1))
        self.assertEqual(self.swipes(), ["down"])
        self.assertIn("  - list.row.坊っちゃん ｜ 坊っちゃん, 夏目 漱石\n", out)
        self.assertNotIn("C++入門", out)
        self.assertEqual(self.dv.ask["paged"], {"see:list.row.*": 1})
        self.assertEqual((self.dv.ask["key"], self.dv.ask["pick"]), ("see:list.row.*", True))   # --next を受け付ける
        self.assertEqual(self.sec["devices"]["iphone"]["inputs"]["see:list.row.*"], "")   # 値の欄は空のまま

    def test_next_at_the_end_loses_the_item(self):
        # 送っても見えている行が変わらない。端まで見て無かった
        got, out = self.run_item(first=1, ask={"next": True})
        self.assertEqual(got, ("not_found", {"condition": "古い作品の行", "reason": "上下の端まで見た"}))
        # 最後に見た画面（端まで送ったところ）を、ふつうの項目と同じ名前で撮ってダンプを置く
        shot = [b for c, n, b in self.calls if c == "run" and "takeScreenshot" in b]
        self.assertEqual(len(shot), 1)
        self.assertIn("shots/iphone/test_01'", shot[0])

    def test_last_screen_dump_is_put_next_to_the_shot(self):
        fake, args = RF["sh"], []
        RF["sh"] = lambda a, quiet=True: (args.append(a), fake(a, quiet))[1]
        self.run_item(first=1, ask={"next": True})
        self.assertIn(["inspect", "AAAA", "test_01", str(self.out / "shots" / "iphone")], args)

    def test_inner_texts_come_from_the_row_subtree(self):
        # 行の中の文字は、生のダンプの入れ子から取る。枠の中心で拾うと、画面全体の要素（スクロールバー）が混ざる
        self.raw = json.dumps({"ui_schema": {}, "elements": [{"b": "[0,0][402,874]", "a11y": "Vertical scroll bar", "c": [
            {"b": "[0,280][402,320]", "rid": "list.row.C++入門", "a11y": "C++入門",
             "c": [{"b": "[16,284][200,300]", "a11y": "C++入門"}, {"b": "[16,302][200,316]", "a11y": "山田 太郎、鈴木 花子"}]}]}]})
        got, out = self.run_item()
        self.assertIn("  - list.row.C++入門 ｜ C++入門 ｜ 中: C++入門 / 山田 太郎、鈴木 花子\n", out)
        self.assertNotIn("scroll bar", out)

    def test_next_is_counted_and_stops_at_the_limit(self):
        # 伸び続ける一覧は端に着かない。--next で送るのは SEEK_SWIPES 回まで
        got, out = self.run_item(first=1, ask={"next": True, "paged": {"see:list.row.*": RF["SEEK_SWIPES"]}})
        self.assertEqual(got[0], "not_found")
        self.assertEqual(got[1]["reason"], "上下に{}画面送っても端に着かない".format(RF["SEEK_SWIPES"]))
        self.assertEqual(self.swipes(), [])               # 上限なら送らずに落とす

    def test_retake_sends_again_from_the_top(self):
        # 撮り直しは頭からなぞり直すので、画面は先頭に戻っている。送った回数ぶん送り直してから1画面進める
        self.dumps = [FirstVisible.DUMP, FirstVisible.DUMP, self.BELOW, FirstVisible.DUMP, self.BELOW]
        got, out = self.run_item(retake=True, ask={"next": True, "paged": {"see:list.row.*": 2}})
        self.assertEqual(got, ("stopped", 1))
        self.assertEqual(self.swipes(), ["up", "down", "down", "down"])
        self.assertEqual(self.dv.ask["paged"], {"see:list.row.*": 3})

    def test_count_starts_over_when_shown_from_the_top(self):
        # --next が付いていなければ、先頭から出し直す
        self.run_item(first=1, ask={"paged": {"see:list.row.*": 5}})
        self.assertEqual(self.dv.ask["paged"], {"see:list.row.*": 0})
        self.assertEqual(self.swipes(), ["up"])

    def test_wrong_id_keeps_the_stop_open_for_next(self):
        self.given("see:list.row.*", "detail.title")
        self.run_item(first=1)
        self.assertEqual((self.dv.ask["key"], self.dv.ask["pick"]), ("see:list.row.*", True))

    def test_id_of_another_pattern_stops(self):
        self.given("see:list.row.*", "detail.title")
        got, out = self.run_item(first=1)
        self.assertEqual(got, ("stopped", 1))
        self.assertIn("に当たらない", out)


class ParentRun(ItemRunBase, unittest.TestCase):
    """子の要素は、親を縦に探して寄せ、親の枠の中を横に送って探す。端の決め方は縦と同じ。"""

    RAW = json.dumps({"ui_schema": {}, "elements": [{"b": "[0,0][402,874]", "c": [
        {"b": "[0,220][402,410]", "rid": "recommend.carousel.8"},
        {"b": "[0,478][402,668]", "rid": "recommend.carousel.9"}]}]})
    CAROUSELS = (DUMP_HEAD + dump_line(201, 315, "○", "recommend.carousel.8", "", height=190)
                 + dump_line(201, 573, "○", "recommend.carousel.9", "", height=190))
    BOOKS = (CAROUSELS + dump_line(74, 315, "○", "recommend.book.上の段", "")
             + dump_line(-40, 573, "×", "recommend.book.はみ出し", "")
             + dump_line(74, 573, "○", "recommend.book.下の段", ""))

    items = [{"from": "recommend", "do": [{"op": "tap:recommend.book.*", "in": "recommend.carousel.9"}]}]

    def setUp(self):
        ItemRunBase.setUp(self)
        self.raw = self.RAW
        self.dumps = [self.BOOKS]

    def test_picks_first_visible_in_the_parent(self):
        # 上のカルーセルの行（上の段）は、画面の上のほうに見えていても選ばない
        self.assertEqual(self.run_item()[0], ("done", None))
        self.assertEqual(self.picked(), {"tap:recommend.book.*": "recommend.book.下の段"})
        # 送る親は、送る前に縦に寄せる（下端にかかったカルーセルの上からスワイプするとタブバーを叩く）
        self.assertEqual(self.swipes(), ["center"])

    def test_swipes_inside_the_parent(self):
        self.dumps = [self.CAROUSELS, self.CAROUSELS, self.BOOKS]
        self.assertEqual(self.run_item()[0], ("done", None))
        self.assertEqual(self.swipes(), ["center", "next"])
        swipe = [b for c, n, b in self.calls if c == "run" and "LEFT" in b][0]
        self.assertIn("from:\n      id: '^recommend\\.carousel\\.9$'", swipe)

    def test_gives_up_at_both_ends_of_the_parent(self):
        self.dumps = [self.CAROUSELS]
        got = self.run_item()[0]
        self.assertEqual(got, ("failed", "recommend.book.* が画面に無い（recommend.carousel.9 の中の左右の端まで探した）"))
        self.assertEqual(self.swipes(), ["center", "next", "back"])

    def test_pick_in_the_parent_sends_inside_it(self):
        # 条件つきでカードを選ぶ。候補は親の中に見えているカードだけで、--next は親の中を横に送る
        self.items = [{"from": "recommend", "do": [{"op": "see:recommend.book.*", "pick": "著者が複数",
                                                    "in": "recommend.carousel.9"}]}]
        self.build()
        got, out = self.run_item()
        self.assertEqual(got, ("stopped", 1))
        self.assertEqual(self.swipes(), ["center", "back"])   # 寄せてから、親の先頭まで戻す
        self.assertIn("  - recommend.book.下の段\n", out)
        self.assertNotIn("上の段", out)
        self.calls = []
        # 親を探して読む → 親の中を読む → 送って読む
        self.dumps = [self.BOOKS, self.BOOKS, self.CAROUSELS + dump_line(74, 573, "○", "recommend.book.次の段", "")]
        got, out = self.run_item(first=1, ask={"next": True})
        self.assertEqual(self.swipes(), ["center", "next"])
        self.assertIn("  - recommend.book.次の段\n", out)

    def test_missing_parent_loses_the_item(self):
        self.items = [{"from": "recommend", "do": [{"op": "tap:recommend.book.*", "in": "recommend.carousel.3"}]}]
        self.build()
        self.assertEqual(self.run_item()[0],
                         ("failed", "recommend.carousel.3 が画面に無い（上下の端まで探した）"))

    def test_parent_pattern_is_the_first_visible(self):
        self.items = [{"from": "recommend", "do": ["tap:recommend.book.*"]}]
        self.build()
        self.assertEqual(self.run_item()[0], ("done", None))
        self.assertEqual(self.picked(), {"tap:recommend.book.* in recommend.carousel.*": "recommend.carousel.8",
                                         "tap:recommend.book.*": "recommend.book.上の段"})
        self.assertIn("childOf:\n      id: '^recommend\\.carousel\\.8$'", self.body("test_01"))


class Liveness(unittest.TestCase):
    """check はマップの ID がその画面の files に残っているかを確かめる。"""

    SOURCE = """
struct ListView: View {
    var body: some View {
        VStack {
            TextField("", text: $q).accessibilityIdentifier("list.search_field")
            ForEach(rows) { row in
                Row(row).accessibilityIdentifier("list.row.\\(row.title)")
            }
            if rows.isEmpty { EmptyView().accessibilityIdentifier("list.empty_view") }
            Footer().accessibilityIdentifier("list.footer")
        }
        .accessibilityIdentifier("list")
    }
}
"""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, self.repo)
        self.map = self.repo / "screen-map"
        (self.repo / "Sources").mkdir()
        self.src = self.repo / "Sources" / "ListView.swift"
        self.src.write_text(self.SOURCE, encoding="utf-8")
        self.add_files("list.yaml", ["Sources/ListView.swift"])

    def tearDown(self):
        shutil.rmtree(self.repo.parent)

    def add_files(self, name, files):
        f = self.map / "screens" / name
        f.write_text("files:\n" + "".join("  - {}\n".format(x) for x in files)
                     + f.read_text(encoding="utf-8"), encoding="utf-8")

    def check(self):
        mp = screen_map.load_map(str(self.repo))
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = map_check.cmd_check(mp)
        return code, o.getvalue()

    def test_all_alive(self):
        # パターンの行（list.row.*）は補間の前の固定部分で当たる。OS の ID は探さない
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertNotIn("dead", out)
        self.assertIn("files が無い（読めない）ので確かめられない: detail home", out)

    def test_removed_id_is_dead(self):
        self.src.write_text(self.SOURCE.replace('"list.footer"', '"list.bottom"'), encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("list: list.footer が実装に見つからない（dead。files: Sources/ListView.swift）", out)

    def test_moved_to_another_screen(self):
        self.src.write_text(self.SOURCE.replace('"list.footer"', '""'), encoding="utf-8")
        (self.repo / "Sources" / "Home.swift").write_text('Text("").accessibilityIdentifier("list.footer")\n'
                                                           'x.accessibilityIdentifier("home")', encoding="utf-8")
        self.add_files("home.yaml", ["Sources/Home.swift"])
        code, out = self.check()
        self.assertIn("list: list.footer はこの画面の files に無く、home の files にある", out)
        self.assertNotIn("list.footer が実装に見つからない", out)

    def test_missing_file(self):
        # 無いファイルは警告。読めるファイルが1つも無い画面は確かめられない（dead にしない）
        self.add_files("detail.yaml", ["Sources/Gone.swift"])
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("detail: files の Sources/Gone.swift が無い", out)
        self.assertIn("確かめられない: detail home", out)

    def test_system_ids(self):
        # BackButton / Search は共通で外れる。アプリ固有の OS の ID は config.yaml で足す
        code, out = self.check()
        self.assertNotIn("BackButton が実装に見つからない", out)
        self.edit_list("gestures:\n", "  - id: Done\n    name: 完了（キーボード）\ngestures:\n")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("list: Done が実装に見つからない", out)
        self.assertIn("config.yaml の system_ids に足す", out)
        (self.map / "config.yaml").write_text("start: home\nsystem_ids: [Done]\n", encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def edit_list(self, old, new):
        f = self.map / "screens" / "list.yaml"
        text = f.read_text(encoding="utf-8")
        self.assertIn(old, text)
        f.write_text(text.replace(old, new), encoding="utf-8")


class LabelAnchor(unittest.TestCase):
    """ID を付けられない OS の部品（UIMenu、confirmationDialog、許可ダイアログ）は、anchor を表示テキストで書く。"""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, self.repo)
        self.screens = self.repo / "screen-map" / "screens"
        (self.repo / "Menu.swift").write_text('Button("メニュー") {}.accessibilityIdentifier("detail.menu_button")\n',
                                              encoding="utf-8")
        detail = self.screens / "detail.yaml"
        detail.write_text(detail.read_text(encoding="utf-8").replace(
            "elements:\n",
            "elements:\n"
            "  - id: detail.menu_button\n"
            "    name: メニュー\n"
            "    actions:\n"
            "      - tap:\n"
            "        summary: メニューを開く\n"
            "        expect: {screen: item_menu, via: modal}\n", 1).replace(
            "ready:\n  all: [detail.title]\n", ""), encoding="utf-8")
        self.menu = self.screens / "item_menu.yaml"
        self.menu.write_text(
            "anchor: 共有する\n"
            "anchor_by: label\n"
            "names: [作品のメニュー]\n"
            "files: [Menu.swift]\n"
            "elements:\n"
            "  - id: 共有する\n"
            "    name: 共有する\n"
            "    by: label\n"
            "    actions:\n"
            "      - tap:\n"
            "        summary: メニューが閉じて、Safari で作品のページを開く\n"
            "        expect:\n"
            "          - {screen: back, via: dismiss}\n"
            "          - {external: safari}\n"
            "  - id: レビューを書く\n"
            "    name: レビューを書く\n"
            "    by: label\n"
            "    actions:\n"
            "      - tap:\n"
            "        summary: メニューが閉じて、ログインの案内が出る\n"
            "        expect:\n"
            "          - {screen: back, via: dismiss}\n"
            "          - {screen: login_alert, via: modal}\n"
            "  - id: キャンセル\n"
            "    name: キャンセル\n"
            "    by: label\n"
            "    actions:\n"
            "      - tap:\n"
            "        summary: 閉じる\n"
            "        expect: {screen: back, via: dismiss}\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.repo.parent)

    def check(self):
        mp = screen_map.load_map(str(self.repo))
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = map_check.cmd_check(mp)
        return code, o.getvalue()

    def test_arrival_waits_for_the_label(self):
        rows, flows = write_flows([{"from": "item_menu", "title": "a", "expect": "a"}], self.repo)
        flow = flows["test_01.yaml"]
        self.assertIn("- extendedWaitUntil:\n    visible:\n      text: '.*共有する.*'", flow)
        self.assertLess(flow.index("id: '^detail\\.menu_button$'"), flow.index("text: '.*共有する.*'"))
        self.assertEqual(rows[0]["screen"], "item_menu")

    def test_closing_the_menu_then_opening_an_alert(self):
        # メニューの選択肢は、メニューが閉じてからアラートが出る。アラートを閉じると詳細に戻る
        # （閉じたメニューには戻らない）
        rows, flows = write_flows([
            {"from": "login_alert", "title": "a", "expect": "a"},
            {"from": "login_alert", "do": ["tap:login_alert.cancel_button"], "title": "b", "expect": "b"}],
            self.repo)
        flow = flows["test_01.yaml"]
        tap = flow.index("text: '.*レビューを書く.*'")
        arrive = flow.index("id: '^login_alert$'")
        closed = flow.index("- extendedWaitUntil:\n    notVisible:\n      text: '.*共有する.*'")
        self.assertLess(tap, arrive)
        self.assertLess(arrive, closed)
        # 戻った先（詳細）は、アラートが被さって隠れるので待たない
        self.assertEqual(waits(flow[tap:]), ["^login_alert$"])
        self.assertEqual(rows[1]["screen"], "detail")
        self.assertEqual(waits(flows["test_02.yaml"])[0], "^detail$")

    def test_closing_the_menu_then_leaving_the_app(self):
        # メニューの選択肢で Safari に出る。メニューは閉じているので、アプリに戻した先は詳細
        rows, flows = write_flows([
            {"from": "item_menu", "do": ["tap:共有する"], "title": "a", "expect": "a"},
            {"from": "detail", "do": ["see:detail.title"], "title": "b", "expect": "b"}], self.repo)
        self.assertEqual(rows[0]["screen"], "detail")
        self.assertIn("→ アプリの外（safari）。戻すと detail\n", flows["test_01.yaml"])
        nxt = flows["test_02.1.yaml"] if "test_02.1.yaml" in flows else flows["test_02.yaml"]
        self.assertIn("# アプリの外から detail に戻す", nxt)
        self.assertEqual(waits(nxt)[0], "^detail$")
        self.assertNotIn("共有する", nxt)

    def test_closing_is_not_a_back_action(self):
        mp = screen_map.load_map(str(self.repo))
        self.assertEqual(mp.screens["item_menu"].back_action().target, "キャンセル")
        self.assertIn("item_menu", [d for _, _, d, _, _ in mp.edges("detail")])
        self.assertIn("login_alert", [d for _, _, d, _, _ in mp.edges("item_menu")])

    def test_route_says_the_menu_closed(self):
        out = write_flows_path([{"from": "login_alert", "title": "a", "expect": "a"}], self.repo)
        self.assertIn("✓ login_alert に着いたことを確認", out)
        self.assertIn("✓ item_menu が閉じた", out)

    def test_two_forward_screens_are_still_refused(self):
        self.menu.write_text(self.menu.read_text(encoding="utf-8").replace(
            "          - {screen: back, via: dismiss}\n          - {screen: login_alert",
            "          - {screen: detail, via: push}\n          - {screen: login_alert"), encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("item_menu: 「tap レビューを書く [ラベル]」 に移る先が2つある", out)

    def test_route_says_it_waits_by_label(self):
        out = write_flows_path([{"from": "item_menu", "title": "a", "expect": "a"}], self.repo)
        self.assertIn("✓ item_menu に着いたことを確認（「共有する」の文言で待つ）", out)

    def test_check_passes_without_looking_for_the_label_in_source(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("到達できない: なし", out)
        self.assertNotIn("共有する が実装に見つからない", out)
        self.assertNotIn("移る先が2つある", out)

    def test_label_anchor_is_not_an_id_to_point_at(self):
        # 表示テキストは ID ではないので、ほかの画面の expect / ready からは指せない
        detail = self.screens / "detail.yaml"
        detail.write_text(detail.read_text(encoding="utf-8").replace(
            "expect: {selected: self}", "expect: {visible: 共有する}").replace(
            "  - id: 共有する\n", ""), encoding="utf-8")
        self.menu.write_text(self.menu.read_text(encoding="utf-8").replace(
            "  - id: 共有する\n    name: 共有する\n    by: label\n    actions:\n      - tap:\n"
            "        summary: メニューが閉じて、Safari で作品のページを開く\n        expect:\n"
            "          - {screen: back, via: dismiss}\n          - {external: safari}\n", ""),
            encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("expect が指す 共有する がどの画面の要素にも無い", out)

    def test_check_rejects_other_values_and_patterns(self):
        self.menu.write_text(self.menu.read_text(encoding="utf-8").replace(
            "anchor_by: label", "anchor_by: id"), encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("item_menu: anchor_by に書けるのは label だけ", out)
        self.menu.write_text(self.menu.read_text(encoding="utf-8").replace(
            "anchor_by: id", "anchor_by: label").replace("anchor: 共有する", "anchor: 共有*"),
            encoding="utf-8")
        code, out = self.check()
        self.assertIn("item_menu: anchor がラベル指定なのでパターンにできない", out)

    def test_os_dialog_as_auto_show(self):
        # OS の許可ダイアログ。起動直後に被さるので、起点の anchor より先に文言で見て閉じる
        (self.screens / "att_dialog.yaml").write_text(
            "anchor: トラッキングしないように要求\n"
            "anchor_by: label\n"
            "names: [トラッキングの許可]\n"
            "elements:\n"
            "  - id: Appにトラッキングしないように要求\n"
            "    name: 許可しない\n"
            "    by: label\n"
            "    actions:\n"
            "      - tap:\n"
            "        summary: 許可せずに閉じる\n"
            "        expect: {screen: back, via: dismiss}\n", encoding="utf-8")
        home = self.screens / "home.yaml"
        home.write_text(home.read_text(encoding="utf-8") + "auto_shows: [att_dialog]\n", encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 0, out)
        rows, flows = write_flows([{"from": "home", "title": "a", "expect": "a"}], self.repo)
        flow = flows["test_01.yaml"]
        self.assertIn("- runFlow:\n    when:\n      visible:\n        text: '.*トラッキングしないように要求.*'\n"
                      "    commands:\n      - tapOn:\n          text: '.*Appにトラッキングしないように要求.*'", flow)
        self.assertLess(flow.index("runFlow"), flow.index("id: '^home$'"))

    def test_screen_is_found_by_text(self):
        mp = screen_map.load_map(str(self.repo))
        self.assertEqual(flows_of.screen_at(mp, ["x"], ["共有する", "キャンセル"]), ("item_menu", ["item_menu"]))
        # 前後に何か付いていても当てる（フローの text セレクタと同じ）
        self.assertEqual(flows_of.screen_at(mp, [], ["\u200e共有する"]), ("item_menu", ["item_menu"]))
        self.assertEqual(flows_of.screen_at(mp, ["detail"], ["キャンセル"]), ("detail", ["detail"]))
        self.assertEqual(flows_of.screen_at(mp, ["detail"], ["共有する"])[0], None)

    def test_dump_texts_are_read(self):
        dump = (DUMP_HEAD + dump_line(10, 20, "○", "", "共有する") + dump_line(10, 900, "×", "", "画面外")
                + dump_line(10, 60, "○", "detail"))
        self.assertEqual(RF["shown"](dump), (["detail"], ["共有する"]))


class Check(unittest.TestCase):
    """check は expect / ready が指す ID と、スキーマの形を確かめる。"""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, self.repo)
        self.screens = self.repo / "screen-map" / "screens"

    def tearDown(self):
        shutil.rmtree(self.repo.parent)

    def check(self):
        mp = screen_map.load_map(str(self.repo))
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = map_check.cmd_check(mp)
        return code, o.getvalue()

    def edit(self, name, old, new):
        f = self.screens / name
        text = f.read_text(encoding="utf-8")
        self.assertIn(old, text)
        f.write_text(text.replace(old, new), encoding="utf-8")

    def test_fixture_passes(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_screen_that_is_not_a_mapping(self):
        # 中身が辞書でない画面ファイルでも落ちずに、不整合として出す
        (self.screens / "weird.yaml").write_text("- [1, 2]\n", encoding="utf-8")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("weird: 画面の中身が辞書になっていない", out)

    def test_undefined_expect_id(self):
        self.edit("list.yaml", "expect: {visible: list.footer}", "expect: {visible: list.count_label}")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("expect が指す list.count_label がどの画面の要素にも無い", out)

    def test_undefined_ready_id(self):
        self.edit("detail.yaml", "all: [detail.title]", "all: [detail.body]")
        code, out = self.check()
        self.assertIn("ready の detail.body がどの画面の要素にも無い", out)

    def test_bad_via_and_mixed_when(self):
        self.edit("home.yaml", "expect: {screen: list, via: push}", "expect: {screen: list, via: back}")
        self.edit("detail.yaml", "          - when: 未ログイン\n", "          - ")
        code, out = self.check()
        self.assertIn("via は push / modal / tab（戻る操作は screen: back）", out)
        self.assertIn("when のある項目と無い項目が混ざっている", out)


class NestingCheck(Check):
    """children を持てるのは scroll かパターンの要素だけ。子の ID は親の接頭辞で始めない。"""

    def test_children_need_scroll_or_pattern(self):
        self.edit("recommend.yaml", "    scroll: horizontal\n    children:\n      - id: recommend.filter.*",
                  "    children:\n      - id: recommend.filter.*")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("recommend.filter_bar は scroll もパターンでもないので children を持てない", out)

    def test_vertical_scroll_is_refused(self):
        self.edit("recommend.yaml", "  - id: recommend.filter_bar\n    name: 絞り込みのチップの列（上部、横スクロール）\n"
                                    "    scroll: horizontal",
                  "  - id: recommend.filter_bar\n    name: 絞り込みのチップの列（上部、横スクロール）\n"
                  "    scroll: vertical")
        code, out = self.check()
        self.assertIn("recommend.filter_bar の scroll は horizontal だけ", out)

    def test_scroll_without_children(self):
        self.edit("list.yaml", "  - id: list.footer\n", "  - id: list.footer\n    scroll: horizontal\n")
        code, out = self.check()
        self.assertIn("list.footer に scroll があるのに children が無い", out)

    def test_child_id_with_parent_prefix(self):
        self.edit("recommend.yaml", "      - id: recommend.more\n", "      - id: recommend.carousel.more\n")
        code, out = self.check()
        self.assertIn("recommend.carousel.more が親 recommend.carousel.* のパターンに前方一致する", out)

    def test_duplicate_id_across_levels(self):
        self.edit("recommend.yaml", "      - id: recommend.more\n", "      - id: recommend.filter_bar\n")
        code, out = self.check()
        self.assertIn("要素 recommend.filter_bar が2回ある", out)


class Nesting(unittest.TestCase):
    """子の要素は childOf で親の中を指す。親を縦に寄せて横に送るのは run_flows.py で、フローには書かない。"""

    def hands(self, row):
        return [u.step.key for u in row["units"] if u.kind == "hand"]

    def test_pattern_parent_is_chosen_at_run_time(self):
        rows, flows = write_flows([{"from": "recommend", "do": ["see:recommend.more"]}])
        # 親を選ぶ手（Enter）と、見る手に分かれる。親を選ぶ手は流すものが無い（run_flows.py が
        # ダンプで選ぶだけ）。親を縦に寄せて横に送るのも run_flows.py
        self.assertEqual(self.hands(rows[0]), ["see:recommend.more in recommend.carousel.*", "see:recommend.more"])
        self.assertEqual(sorted(flows), ["test_01.1.yaml", "test_01.yaml"])
        self.assertEqual(rows[0]["inputs"], {})
        body = flows["test_01.yaml"]
        self.assertNotIn("repeat", body)
        self.assertNotIn("swipe", body)
        self.assertIn("- extendedWaitUntil:\n    visible:\n      id: '^recommend\\.more$'\n"
                      "      childOf:\n        id: '^recommend\\.carousel\\.選んだ$'", body)

    def test_in_names_the_parent(self):
        rows, flows = write_flows([{"from": "recommend",
                                    "do": [{"op": "tap:recommend.more", "in": "recommend.carousel.9"}]}])
        self.assertEqual(self.hands(rows[0]), ["tap:recommend.more"])
        body = flows["test_01.yaml"]
        self.assertIn("- tapOn:\n    id: '^recommend\\.more$'\n    childOf:\n"
                      "      id: '^recommend\\.carousel\\.9$'", body)

    def test_child_pattern_is_picked_inside_the_parent(self):
        rows, flows = write_flows([{"from": "recommend", "do": ["tap:recommend.book.*"]}])
        self.assertEqual(self.hands(rows[0]), ["tap:recommend.book.* in recommend.carousel.*", "tap:recommend.book.*"])
        # 親の値は前の手で決まっていて、この手でも使う
        self.assertIn("- tapOn:\n    id: '^recommend\\.book\\.選んだ$'\n    childOf:\n"
                      "      id: '^recommend\\.carousel\\.選んだ$'\n    index: 0", flows["test_01.yaml"])

    def test_fixed_parent_needs_no_choice(self):
        rows, flows = write_flows([{"from": "recommend", "do": ["tap:recommend.filter.新着"]}])
        self.assertEqual([u.kind for u in rows[0]["units"]], ["flow", "hand"])
        body = flows["test_01.yaml"]
        self.assertIn("childOf:\n      id: '^recommend\\.filter_bar$'", body)
        # 押した要素そのものの結果（selected: self）も、同じ親の中を見る
        self.assertIn("- extendedWaitUntil:\n    visible:\n      id: '^recommend\\.filter\\.新着$'\n"
                      "      childOf:\n        id: '^recommend\\.filter_bar$'\n      selected: true", body)

    def test_pick_condition_for_the_parent(self):
        rows, _ = write_flows([{"from": "recommend",
                                "do": [{"op": "see:recommend.more", "in": {"pick": "10件に満たないカテゴリー"}}]}])
        self.assertEqual(rows[0]["inputs"], {"see:recommend.more in recommend.carousel.*": ""})

    def test_route_through_a_child_picks_the_first_parent(self):
        # 経路の途中で子を押すときも、親は見えている1件目。おすすめから詳細へはカードを押す
        path = write_flows_path(None, cases=[{"title": "A", "items": [
            {"from": "recommend", "title": "a", "expect": "x"}, {"from": "detail", "title": "b", "expect": "x"}]}])
        self.assertIn("recommend.carousel.* のどれの中でするかを決める [見えている1件目]", path)
        self.assertIn("tap recommend.book.* [見えている1件目] in recommend.carousel.*（見えている1件目）", path)
        # 経路の子の要素も1手で流す（親の中を横に送るのは run_flows.py）
        rows, flows = write_flows(None, cases=[{"title": "A", "items": [
            {"from": "recommend", "title": "a", "expect": "x"}, {"from": "detail", "title": "b", "expect": "x"}]}])
        self.assertEqual(self.hands(rows[1]), ["経路 in recommend.carousel.*", "経路 tap:recommend.book.*"])
        self.assertEqual(rows[1]["inputs"], {})

    def test_in_on_a_top_level_element(self):
        _, err = build_err([{"from": "recommend", "do": [{"op": "see:recommend.filter_bar", "in": "x"}]}])
        self.assertIn("子の要素（マップの children）ではないので in は添えられない", err)

    def test_in_outside_the_parent_pattern(self):
        _, err = build_err([{"from": "recommend", "do": [{"op": "tap:recommend.more", "in": "recommend.filter_bar"}]}])
        self.assertIn("in の recommend.filter_bar が親 recommend.carousel.* に当たらない", err)


def write_flows_path(items, repo=FIXTURE, cases=None):
    """plan の経路が組めるかを確かめて（manifest.py と同じ plan_rows()）、人が読む経路を返す。"""
    plan = {"app": "jp.example.App", "repo": str(repo), "cases": cases or cases_of(items)}
    with contextlib.redirect_stdout(io.StringIO()) as o:
        flows_of.plan_rows(plan)
    return o.getvalue()


class CaseRunBase:
    """run_flows.py の run_device を、fixture のマップから manifest.py で作ったマニフェストで走らせる。

    Maestro は叩かない。`sh` を差し替えて、走らせたフロー（名前・撮影先・中身）を記録する。
    `failing` に入れた run 名は落ち、`fail_once` に入れたものは1回目だけ落ちる。画面を読むと
    `self.dump` が返る（既定は一覧の anchor が見えている画面）。
    """

    CASES = [
        {"title": "A", "items": [{"from": "list", "title": "a1", "expect": "x"},
                                 {"from": "list", "do": ["see:list.footer"], "title": "a2", "expect": "x"}]},
        {"title": "B", "items": [{"from": "list", "do": ["tap:list.row.*"], "title": "b1", "expect": "x"},
                                 {"from": "detail", "title": "b2", "expect": "x"}]},
        {"title": "C", "launch": True, "items": [{"from": "home", "title": "c1", "expect": "x"}]},
        {"title": "D", "items": [{"from": "list", "title": "d1", "expect": "x"}]},
    ]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = getattr(self, "repo", None) or FIXTURE
        plan = self.tmp / "plan.json"
        plan.write_text(json.dumps({"app": "jp.example.App", "repo": str(self.repo), "cases": self.CASES},
                                   ensure_ascii=False), encoding="utf-8")
        self.out = self.tmp / "out"
        run_manifest([plan, self.out, "--device", "iphone=AAAA"])
        self.manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.flows = self.tmp / "flows"
        self.calls, self.failing, self.fail_once = [], set(), set()
        # do で探す要素も見えている画面。探しに送ると、ダンプが変わらないので端になる
        self.dump = (FirstVisible.DUMP + dump_line(195, 120, "○", "list.search_field", "")
                     + dump_line(195, 700, "○", "list.footer", ""))
        self.saved = {k: RF[k] for k in ("sh", "HERE", "pause")}
        RF["HERE"] = self.tmp / "scripts"
        RF["pause"] = lambda: None

        def fake_sh(args, quiet=True):
            cmd = args[0]
            if cmd == "run":
                target, name, dest = args[2], args[3], args[4]
                body = target
                self.calls.append(("run", name, Path(dest).name, body))
                if name in self.fail_once:
                    self.fail_once.discard(name)
                    return 1
                return 1 if name in self.failing else 0
            if cmd == "inspect":
                self.calls.append(("inspect", args[2], "", ""))
                if reads_screen(args):
                    state = self.tmp / ".work" / "state"
                    state.mkdir(parents=True, exist_ok=True)
                    (state / "last_dump_AAAA.txt").write_text(self.dump, encoding="utf-8")
            return 0
        RF["sh"] = fake_sh

    def tearDown(self):
        RF.update(self.saved)
        shutil.rmtree(self.tmp)

    def run_device(self, retake=None, resume=None, ask=None):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return RF["run_device"](self.manifest, self.out / "manifest.json", self.flows,
                                    "iphone", "AAAA", resume, retake, ask)

    def runs(self):
        """流した項目の本（探すために送ったものは除く）。"""
        return [n for c, n, _, _ in self.calls if c == "run" and ".seek" not in n]

    def body(self, name, nth=0):
        return [b for c, n, _, b in self.calls if c == "run" and n == name][nth]

    def sec(self, name):
        return MI.find(self.manifest, name)[1]

    def log(self):
        return (self.out / "progress_iphone.log").read_text(encoding="utf-8")


class CaseRun(CaseRunBase, unittest.TestCase):
    """テストケースごとに組んで走らせる。落ちたら、そのテストケースの残りだけ飛ばして次へ繋ぐ。"""

    def test_all_cases_run_in_order(self):
        stopped, done, lost = self.run_device()
        self.assertEqual((stopped, done, lost), (None, 6, []))
        # test_03 は前の項目と同じ一覧から、行を押す1手だけ
        self.assertEqual(self.runs(), ["test_01", "test_02", "test_03", "test_04", "test_05", "test_06"])
        # 起動するのは最初と、launch の付いたテストケースの頭だけ。ほかは前の続き
        self.assertEqual(["launchApp" in self.body(n) for n in
                          ("test_01", "test_02", "test_03", "test_04", "test_05", "test_06")],
                         [True, False, False, False, True, False])
        # 流したものの記録は端末ごとの置き場に書く
        self.assertTrue((self.flows / "iphone" / "test_03.yaml").exists())

    def test_failure_skips_only_the_rest_of_its_case(self):
        self.failing.add("test_01")
        stopped, done, lost = self.run_device()
        self.assertEqual(lost, ["iphone test_01", "iphone test_02"])
        self.assertEqual(done, 4)
        self.assertNotIn("test_02", self.runs())
        log = self.log()
        self.assertIn("test_02 撮影できず 同じテストケースの test_01 が落ちたので飛ばした", log)
        # 撮れなかったことは端末ごとに残す。判定（result）ではない
        dev = lambda n: self.sec(n)["devices"]["iphone"]
        self.assertEqual(dev("test_01")["unexpected"], {"kind": "failed", "reason": "フローが失敗"})
        self.assertEqual(dev("test_02")["unexpected"],
                         {"kind": "skipped", "reason": "同じテストケースの test_01 が落ちた"})
        self.assertNotIn("unexpected", dev("test_03"))
        self.assertEqual(self.sec("test_01")["result"], "PENDING")
        self.assertIn("test_01 のあと: 画面 list に居る。test_03 はそこから繋ぐ", log)
        # 落ちた地点の画面から繋ぐ。起動し直さず、一覧で行を押す1手から
        self.assertEqual(self.runs()[1], "test_03")
        self.assertNotIn("launchApp", self.body("test_03"))

    def test_unknown_screen_relaunches_the_next_case(self):
        self.failing.add("test_01")
        self.dump = DUMP_HEAD          # どの画面の anchor も見えていない（アラート、アプリの外など）
        self.run_device()
        self.assertIn("stopApp", self.body("test_03.1"))
        self.assertIn("test_01 のあと: どの画面の anchor も見えていない。test_03 は起動し直して始める",
                      self.log())

    def test_two_anchors_relaunch_the_next_case(self):
        self.failing.add("test_01")
        self.dump = FirstVisible.DUMP + dump_line(195, 120, "○", "detail", "詳細")
        self.run_device()
        self.assertIn("stopApp", self.body("test_03.1"))
        self.assertIn("anchor が複数見えている（detail, list）", self.log())

    def test_head_failing_after_recovery_is_run_again_after_relaunch(self):
        self.failing.add("test_01")
        self.fail_once.add("test_03")
        stopped, done, lost = self.run_device()
        self.assertEqual(lost, ["iphone test_01", "iphone test_02"])
        self.assertEqual(done, 4)
        self.assertEqual(self.runs().count("test_03"), 2)
        self.assertNotIn("launchApp", self.body("test_03", 0))
        # 2回目は起動し直して一覧まで運んでから押す
        self.assertIn("stopApp", self.body("test_03.1"))
        self.assertIn("test_03 繋いだ頭で落ちた。起動し直してテストケース「B」を走らせ直す", self.log())
        # 走らせ直して撮れたので、1回目に落ちた記録は残らない
        self.assertNotIn("unexpected", self.sec("test_03")["devices"]["iphone"])

    def test_failure_before_a_launch_case_does_not_read_the_screen(self):
        self.failing.add("test_03")
        self.run_device()
        self.assertEqual(self.runs(), ["test_01", "test_02", "test_03", "test_05", "test_06"])
        # 次は起動し直すテストケースなので、居る画面は読まない
        self.assertNotIn(("inspect", "test_03.where", "", ""), self.calls)

    def test_failed_item_does_not_keep_the_old_shot_or_verdict(self):
        d = self.out / "shots" / "iphone"
        d.mkdir(parents=True, exist_ok=True)
        (d / "test_04.png").write_text("前の回", encoding="utf-8")
        for _, s in MI.walk(self.manifest):
            s["result"] = "OK"
        self.failing.add("test_03")
        self.run_device()
        self.assertFalse((d / "test_04.png").exists())
        self.assertEqual(self.sec("test_03")["result"], "PENDING")
        self.assertEqual(self.sec("test_04")["result"], "PENDING")   # 巻き添えで撮れなかった項目も
        self.assertEqual(self.sec("test_01")["result"], "PENDING")   # 撮った項目は判定し直す

    def test_fresh_run_clears_log_stale_shots_and_old_flows(self):
        d = self.out / "shots" / "iphone"
        d.mkdir(parents=True, exist_ok=True)
        (d / "test_99.png").write_text("前の回", encoding="utf-8")        # 今回のマニフェストに無い名前
        (d / "test_99.txt").write_text("前の回", encoding="utf-8")
        (self.flows / "iphone").mkdir(parents=True)
        (self.flows / "iphone" / "test_15.yaml").write_text("old", encoding="utf-8")
        log = self.out / "progress_iphone.log"
        log.write_text("前の回の行\n", encoding="utf-8")
        self.run_device()
        self.assertFalse((d / "test_99.png").exists())
        self.assertFalse((d / "test_99.txt").exists())
        self.assertFalse((self.flows / "iphone" / "test_15.yaml").exists())
        self.assertNotIn("前の回の行", log.read_text(encoding="utf-8"))


class Recovery(unittest.TestCase):
    """落ちた地点の画面と、組んだときの履歴から、次のテストケースをどこから組むか。"""

    def test_screen_needs_exactly_one_anchor(self):
        mp = screen_map.load_map(str(FIXTURE))
        self.assertEqual(flows_of.screen_at(mp, ["list", "list.row.x"]), ("list", ["list"]))
        self.assertEqual(flows_of.screen_at(mp, ["x"]), (None, []))
        self.assertEqual(flows_of.screen_at(mp, ["list", "detail"]), (None, ["detail", "list"]))

    def test_stack_is_taken_from_where_it_was_planned(self):
        mp = screen_map.load_map(str(FIXTURE))
        plan = {"app": "x", "repo": str(FIXTURE), "cases": [
            {"title": "A", "items": [{"from": "detail", "title": "a", "expect": "x"}]}]}
        case = flows_of.flow_cases(plan)[0]
        build = flows_of.build_case(mp, case["items"], flows_of.Cursor.fresh(mp))
        self.assertEqual(flows_of.stack_at(build, "test_01", "home"), ["home"])
        detail = flows_of.stack_at(build, "test_01", "detail")
        self.assertEqual(detail[-1], "detail")
        self.assertEqual(detail[0], "home")
        # 組んだ予定のどこにも無い画面は、その画面だけ（どこから来たか分からない）
        self.assertEqual(flows_of.stack_at(build, "test_01", "settings"), ["settings"])

    def test_cursor_round_trips_through_json(self):
        mp = screen_map.load_map(str(FIXTURE))
        c = flows_of.Cursor("list", ["home", "list"], flows_of.ScrollState(unknown=True, scrolled=["list"]),
                            None, False, [("list", "text:list.search_field")], True, True)
        back = flows_of.Cursor.from_json(json.loads(json.dumps(c.to_json())))
        self.assertEqual(back.to_json(), c.to_json())
        self.assertTrue(back.scroll.is_scrolled("detail"))
        self.assertEqual(flows_of.Cursor.fresh(mp).restart, True)


class NotFoundRun(CaseRunBase, unittest.TestCase):
    """条件に合う要素が無かった（探しきって無い）のは、撮る側の失敗（unexpected）ではない。
    not_found を付け、撮り直しに拾わせない。後ろの項目は巻き添えで飛ばし、同じく not_found にする。"""

    CASES = [
        {"title": "A", "items": [{"from": "list", "do": [{"op": "see:list.row.*", "pick": "古い作品の行"}],
                                  "title": "a1", "expect": "x"},
                                 {"from": "list", "title": "a2", "expect": "x"}]},
        {"title": "B", "items": [{"from": "list", "title": "b1", "expect": "x"}]},
    ]

    def test_not_found_is_kept_apart_from_unexpected(self):
        stopped, done, lost = self.run_device(ask={"next": True})
        dev = self.sec("test_01")["devices"]["iphone"]
        self.assertEqual(dev["not_found"], {"condition": "古い作品の行", "reason": "上下の端まで見た"})
        self.assertNotIn("unexpected", dev)
        after = self.sec("test_02")["devices"]["iphone"]
        self.assertEqual(after["not_found"]["after"], "test_01")
        self.assertNotIn("unexpected", after)
        self.assertEqual(lost, [])                        # 撮れなかった（撮る側の失敗）には並べない
        self.assertEqual(done, 1)                         # 次のテストケースは撮る
        self.assertEqual(RF["to_retake"](self.manifest), ([], []))
        self.assertIn("test_01 撮影済み 条件「古い作品の行」に合う要素が無い（上下の端まで見た）", self.log())

    def test_records_are_cleared_when_shot_again(self):
        self.sec("test_01")["devices"]["iphone"]["not_found"] = {"condition": "x", "reason": "y"}
        self.sec("test_01")["devices"]["iphone"]["inputs"]["see:list.row.*"] = "list.row.C++入門"
        self.run_device()
        self.assertNotIn("not_found", self.sec("test_01")["devices"]["iphone"])


class RecoveryResets(CaseRunBase, unittest.TestCase):
    """落ちたテストケースが後に残したかもしれないものは、次のテストケースの頭で reset を叩いてみる。"""

    CASES = [
        {"title": "T", "items": [{"from": "list", "do": [{"op": "text:list.search_field", "input": "猫"}],
                                  "title": "t", "expect": "x"},
                                 {"from": "list", "do": ["see:list.footer"], "title": "t2", "expect": "x"}]},
        {"title": "N", "items": [{"from": "list", "title": "n", "expect": "x"}]},
    ]

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, self.repo)
        LeavesReset.leaves(self)
        CaseRunBase.setUp(self)

    def tearDown(self):
        CaseRunBase.tearDown(self)
        shutil.rmtree(self.repo.parent)

    list = property(lambda self: self.repo / "screen-map" / "screens" / "list.yaml")
    SEARCH = LeavesReset.SEARCH

    def test_reset_and_keyboard_at_the_next_head(self):
        self.failing.add("test_02")
        self.run_device()
        body = self.body("test_03")
        self.assertIn("list.clear_button", body)
        self.assertIn("hideKeyboard", body)
        self.assertNotIn("launchApp", body)

    def test_reset_failing_relaunches(self):
        self.failing.add("test_02")
        self.fail_once.add("test_03")
        stopped, done, lost = self.run_device()
        self.assertEqual(done, 2)
        self.assertIn("stopApp", self.body("test_03", 1))


class RetakeRuns(unittest.TestCase):
    """撮り直す項目だけを、そのテストケースの頭からなぞって撮る。撮り直す項目は判定の RETAKE で決まる。"""

    def runs(self, names):
        plan = {"app": "x", "repo": str(FIXTURE), "cases": CaseRunBase.CASES}
        cases = flows_of.flow_cases(plan)
        return [[(it["name"], m) for it, m in its] for _, its in RF["retake_runs"](cases, names)]

    def test_head_item_runs_alone(self):
        self.assertEqual(self.runs(["test_03"]), [[("test_03", "shot")]])

    def test_item_in_a_case_replays_from_its_head(self):
        self.assertEqual(self.runs(["test_04"]), [[("test_03", "replay"), ("test_04", "shot")]])

    def test_case_runs_once_for_two_items(self):
        self.assertEqual(self.runs(["test_02", "test_01", "test_04"]),
                         [[("test_01", "shot"), ("test_02", "shot")],
                          [("test_03", "replay"), ("test_04", "shot")]])

    def test_marked_items_are_retaken_and_explored_ones_go_to_sim_driver(self):
        manifest = {"cases": [{"title": "A", "items": [
            {"name": "test_01", "flow": "test_01.yaml", "result": "OK"},
            {"name": "test_02", "flow": "test_02.yaml", "result": "RETAKE"}]},
            {"title": "X", "explore": "理由", "items": [{"name": "test_03", "flow": None, "result": "RETAKE"}]}]}
        self.assertEqual(RF["to_retake"](manifest), (["test_02"], ["test_03"]))

    def test_items_not_shot_are_retaken_before_judging(self):
        # 撮れなかった項目は、判定に回す前に撮り直す。判定が付いたもの（探索で撮って OK、
        # このまま出すと決めて SKIP）は、記録が残っていても拾わない
        lost = {"iphone": {"unexpected": {"kind": "failed", "reason": "x"}}, "ipad": {}}
        manifest = {"cases": [{"title": "A", "items": [
            {"name": "test_01", "flow": "test_01.yaml", "result": "PENDING", "devices": {"iphone": {}}},
            {"name": "test_02", "flow": "test_02.yaml", "result": "PENDING", "devices": lost},
            {"name": "test_03", "flow": "test_03.yaml", "result": "OK", "devices": lost},
            {"name": "test_04", "flow": "test_04.yaml", "result": "SKIP", "devices": lost},
            {"name": "test_05", "flow": "test_05.yaml", "result": "RETAKE", "devices": {}}]}]}
        self.assertEqual(RF["to_retake"](manifest), (["test_02", "test_05"], []))


class RetakeRun(CaseRunBase, unittest.TestCase):
    """撮り直しが実際に何を走らせ、どこへ撮り、何を書き換えるか。"""

    def setUp(self):
        CaseRunBase.setUp(self)
        for _, s in MI.walk(self.manifest):
            s.update(desc=f"{s['name']} の前の判定", result="OK")

    def test_replays_case_head_and_shoots_only_target(self):
        stopped, done, lost = self.run_device(["test_04"])
        self.assertEqual((stopped, done, lost), (None, 1, []))
        runs = [(c, n, d) for c, n, d, _ in self.calls]
        # 手前の項目は作業用の置き場（replay の下の端末名）に撮り、ダンプは取らない。
        # 行を押す手は、ダンプで行を探して選んでから流す
        self.assertEqual(runs, [("run", "test_03.1", "iphone"), ("inspect", "test_03.seek1", ""),
                                ("run", "test_03", "iphone"),
                                ("run", "test_04", "iphone"), ("inspect", "test_04", "")])
        # テストケースの頭で起動し直す（前のテストケースはなぞらない）
        self.assertIn("stopApp", self.body("test_03.1"))
        self.assertIn(str((self.tmp / ".work" / "replay" / "out" / "iphone").resolve()), self.body("test_03"))
        self.assertNotIn(str((self.out / "shots").resolve()), self.body("test_03"))
        # なぞった項目の判定はそのまま。撮った項目だけ PENDING
        self.assertEqual(self.sec("test_03")["result"], "OK")
        self.assertEqual(self.sec("test_04")["result"], "PENDING")
        self.assertEqual(self.sec("test_01")["result"], "OK")
        log = self.log()
        self.assertIn("--- 撮り直し: test_04", log)
        self.assertIn("test_03 なぞった", log)
        self.assertIn("test_04 撮影済み", log)
        # 記録は撮った項目だけ書く（なぞった項目の記録は前の回のまま）
        self.assertTrue((self.flows / "iphone" / "test_04.yaml").exists())
        self.assertFalse((self.flows / "iphone" / "test_03.yaml").exists())

    def test_other_cases_are_not_touched(self):
        self.run_device(["test_06"])
        self.assertEqual(self.runs(), ["test_06"])
        self.assertIn("stopApp", self.body("test_06"))

    def test_failure_while_replaying_loses_target(self):
        self.failing.add("test_03")
        stopped, done, lost = self.run_device(["test_04"])
        self.assertEqual(done, 0)
        self.assertEqual(lost, ["iphone test_03（なぞる途中で落ちた）", "iphone test_04"])
        # 撮れていないので、前の回の判定は捨てて PENDING。前の回の証跡と OK が残っていると
        # レポートに出てしまう。PENDING なら build_report.py が止める
        self.assertEqual(self.sec("test_04")["result"], "PENDING")
        self.assertNotIn("test_04", self.runs())

    def test_retake_clears_the_record_and_replay_failure_skips_target(self):
        self.failing.add("test_03")
        self.run_device()
        self.assertEqual(self.sec("test_04")["devices"]["iphone"]["unexpected"]["kind"], "skipped")
        self.failing.clear()
        self.run_device(["test_04"])
        self.assertNotIn("unexpected", self.sec("test_04")["devices"]["iphone"])
        # なぞる項目（test_03）の記録は、撮り直しでは触らない
        self.assertEqual(self.sec("test_03")["devices"]["iphone"]["unexpected"]["kind"], "failed")
        self.failing.add("test_03")
        self.run_device(["test_04"])
        self.assertEqual(self.sec("test_04")["devices"]["iphone"]["unexpected"],
                         {"kind": "skipped", "reason": "同じテストケースの test_03 が落ちた"})

    def test_retake_appends_to_the_log(self):
        log = self.out / "progress_iphone.log"
        log.write_text("最初の回の行\n", encoding="utf-8")
        self.run_device(["test_06"])
        text = log.read_text(encoding="utf-8")
        self.assertIn("最初の回の行", text)
        self.assertIn("--- 撮り直し: test_06", text)


class MainAnswer(CaseRunBase, unittest.TestCase):
    """止まったときの返事は引数で渡す（--value / --next）。inputs に書くのは run_flows.py だけ。"""

    CASES = [
        {"title": "A", "items": [{"from": "list", "do": [{"op": "text:list.search_field", "runtime": True}],
                                  "title": "a1", "expect": "x"}]},
        {"title": "B", "items": [{"from": "list", "do": [{"op": "see:list.row.*", "pick": "古い作品の行"}],
                                  "title": "b1", "expect": "x"}]},
    ]

    def setUp(self):
        CaseRunBase.setUp(self)
        self.path = self.out / "manifest.json"
        self.saved["simulators"] = RF["simulators"]
        RF["simulators"] = type("Sims", (), {"lookup": staticmethod(lambda udid: {"booted": True})})

    def main(self, *extra):
        RF["save"](self.path, self.manifest)
        argv = sys.argv
        sys.argv = ["run_flows.py", str(self.path)] + list(extra)
        try:
            out = type("Out", (io.StringIO,), {"reconfigure": lambda self, **k: None})()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                try:
                    RF["main"]()
                    code = 0
                except SystemExit as e:
                    code = e.code
        finally:
            sys.argv = argv
        self.printed = out.getvalue()
        self.manifest = json.loads(self.path.read_text(encoding="utf-8"))
        return code

    def inputs(self, name):
        return self.sec(name)["devices"]["iphone"]["inputs"]

    def test_value_is_written_by_the_script_and_the_run_goes_on(self):
        self.assertEqual(self.main(), 1)
        ask = self.manifest["resume"]["ask"]
        self.assertEqual({k: ask[k] for k in ("item", "device", "key", "pick")},
                         {"item": "test_01", "device": "iphone", "key": "text:list.search_field", "pick": False})
        self.assertIn("同じコマンドに --value '<値>' を付けて叩き直す", self.printed)
        self.assertEqual(self.main("--value", "猫"), 1)      # 次の項目（条件つきの選択）で止まる
        self.assertEqual(self.inputs("test_01"), {"text:list.search_field": "猫"})
        self.assertEqual(self.manifest["resume"]["ask"]["key"], "see:list.row.*")
        self.assertTrue(self.manifest["resume"]["ask"]["pick"])

    def test_next_is_only_for_a_pick_and_clears_the_value(self):
        self.main()                                       # 打つ文字で止まる（条件つきの選択ではない）
        self.assertIn("--next は、条件つきの選択で止まって候補が出たあとにだけ付ける", self.main("--next"))
        self.assertEqual(self.manifest["resume"]["ask"]["key"], "text:list.search_field")   # 止まったまま
        self.main("--value", "猫")
        self.manifest["cases"][1]["items"][0]["devices"]["iphone"]["inputs"]["see:list.row.*"] = "list.row.書き間違い"
        self.main("--next")
        self.assertEqual(self.inputs("test_02"), {"see:list.row.*": ""})   # 「無い」は値を空にする

    def test_value_without_a_stop_and_both_are_refused(self):
        self.assertEqual(self.main("--value", "猫"),
                         "--value は、入力が未定で止まったあとにだけ付ける（いまは止まっていない）。--value を外して叩く")
        self.main()
        self.assertIn("一緒に付けない", self.main("--value", "猫", "--next"))
        self.assertEqual(self.main("--value"), "--value の後ろに値が無い（--value '<値>'）")


class MainRetake(CaseRunBase, unittest.TestCase):
    """run_flows.py はマニフェストを見て、RETAKE の項目があればそれだけを撮り直す。"""

    CASES = [
        {"title": "A", "items": [{"from": "list", "title": "a1", "expect": "x"}]},
        {"title": "B", "items": [{"from": "list", "title": "b1", "expect": "x"},
                                 {"from": "list", "do": [{"op": "text:list.search_field", "runtime": True}],
                                  "title": "b2", "expect": "x"}]},
    ]

    def setUp(self):
        CaseRunBase.setUp(self)
        self.path = self.out / "manifest.json"
        self.saved["simulators"] = RF["simulators"]
        RF["simulators"] = type("Sims", (), {"lookup": staticmethod(lambda udid: {"booted": True})})
        # 手前の項目の打つ文字は前の回に決めてある
        self.sec("test_03")["devices"]["iphone"]["inputs"]["text:list.search_field"] = "猫"
        for _, s in MI.walk(self.manifest):
            s["result"] = "OK"

    def main(self):
        RF["save"](self.path, self.manifest)
        argv = sys.argv
        sys.argv = ["run_flows.py", str(self.path)]
        try:
            out = type("Out", (io.StringIO,), {"reconfigure": lambda self, **k: None})()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                try:
                    RF["main"]()
                    code = 0
                except SystemExit as e:
                    code = e.code
        finally:
            sys.argv = argv
        self.printed = out.getvalue()
        self.manifest = json.loads(self.path.read_text(encoding="utf-8"))
        return code

    def test_only_marked_items_are_shot(self):
        self.sec("test_02")["result"] = "RETAKE"
        self.assertEqual(self.main(), 0)
        # テストケース B の頭。起動し直して一覧まで運び、撮る（1本）
        self.assertEqual(self.runs(), ["test_02"])
        self.assertIn("stopApp", self.body("test_02"))
        self.assertEqual(self.sec("test_02")["result"], "PENDING")
        self.assertEqual(self.sec("test_01")["result"], "OK")
        self.assertNotIn("resume", self.manifest)

    def test_nothing_marked_shoots_everything(self):
        self.assertEqual(self.main(), 0)
        self.assertEqual([n for n in self.runs() if "." not in n], ["test_01", "test_02", "test_03"])
        self.assertEqual(self.sec("test_01")["result"], "PENDING")

    def test_stopped_retake_is_retaken_again_not_everything(self):
        # 撮り直す項目で止まると、その項目は PENDING に戻って RETAKE の印が消える。
        # resume の retake に残っているので、叩き直しても全部は撮らない
        self.sec("test_03")["result"] = "RETAKE"
        self.sec("test_03")["devices"]["iphone"]["inputs"]["text:list.search_field"] = ""
        self.assertEqual(self.main(), 1)
        # 止まった手からは続けないので、そう案内する
        self.assertIn("撮り直す項目のテストケースの頭からなぞり直す", self.printed)
        self.assertNotIn("続きから走る", self.printed)
        self.assertEqual(self.manifest["resume"]["retake"], ["test_03"])
        self.assertEqual(self.sec("test_03")["result"], "PENDING")
        self.calls.clear()
        self.sec("test_03")["devices"]["iphone"]["inputs"]["text:list.search_field"] = "猫"
        self.assertEqual(self.main(), 0)
        self.assertNotIn("test_01", self.runs())
        self.assertIn("test_03", self.runs())
        self.assertNotIn("resume", self.manifest)


class ResumeRun(CaseRunBase, unittest.TestCase):
    """入力が未定で止まったら、そのテストケースを組んだ状態を返し、叩き直すと同じ手順の続きから走る。"""

    CASES = [
        {"title": "A", "items": [{"from": "list", "title": "a1", "expect": "x"}]},
        {"title": "B", "items": [{"from": "list", "title": "b1", "expect": "x"},
                                 {"from": "list", "do": ["see:list.footer",
                                                         {"op": "text:list.search_field", "runtime": True}],
                                  "title": "b2", "expect": "x"}]},
        {"title": "C", "items": [{"from": "list", "title": "c1", "expect": "x"}]},
    ]

    def test_stops_and_resumes_from_the_stopped_hand(self):
        stopped, done, lost = self.run_device()
        # 一覧の末尾を見る1手は流し、打つ文字の手で止まる
        self.assertEqual((stopped["from"], stopped["part"], done), ("test_03", 1, 2))
        self.assertEqual(self.runs(), ["test_01", "test_02", "test_03.1"])
        self.assertEqual(stopped["cursor"]["at"], "list")          # B を組んだ状態（A が終わった一覧）
        self.sec("test_03")["devices"]["iphone"]["inputs"]["text:list.search_field"] = "牛乳(1L)"
        self.calls.clear()
        stopped, done, lost = self.run_device(resume=json.loads(json.dumps(stopped)))
        self.assertEqual((stopped, done, lost), (None, 2, []))
        # 止まった手から。手前の項目（test_02）と前の手はもう流れている
        self.assertEqual(self.runs(), ["test_03", "test_04"])
        self.assertIn("- inputText: '牛乳(1L)'", self.body("test_03"))
        self.assertIn("--- 再開: test_03 の 2手目から", self.log())


class MiniYaml(unittest.TestCase):
    def test_flow_mapping(self):
        from screenmap.mini_yaml import load_yaml
        f = Path(tempfile.mkdtemp()) / "x.yaml"
        f.write_text("a:\n  - tap:\n    expect: {screen: x, via: push}\n"
                     "b: [c, {screen: d, after: [e, f]}]\n"
                     "c: \"x, y: z\"\n", encoding="utf-8")
        self.assertEqual(load_yaml(f), {
            "a": [{"tap": None, "expect": {"screen": "x", "via": "push"}}],
            "b": ["c", {"screen": "d", "after": ["e", "f"]}],
            "c": "x, y: z"})
        shutil.rmtree(f.parent)


if __name__ == "__main__":
    unittest.main()
