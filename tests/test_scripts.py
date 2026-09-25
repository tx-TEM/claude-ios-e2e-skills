"""sim-test-report のスクリプト（route.py / manifest.py / run_flows.py）のテスト。

  python3 -m unittest discover tests            テストを走らせる
  UPDATE_SNAPSHOTS=1 python3 -m unittest ...    スナップショットを書き直す

**フローはスナップショットで比べる。** route.py の `build()` と `emit_flow()` は
ほぼ純関数で、fixture のマップと plan から書かれるフローの中身がそのまま
挙動になる。書き直したら、差分を読んでから入れる。

シミュレーターも Maestro も要らない。manifest.py が UDID から機種を引く
`simulators.lookup()` だけ差し替える。
"""
import contextlib
import io
import json
import os
import re
import runpy
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "sim-test-report" / "scripts"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "app"
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"
sys.path.insert(0, str(SCRIPTS))

import route  # noqa: E402


def load_run_flows():
    """run_flows.py は読み込むと main() が走るので、関数だけ取り出す。"""
    src = (SCRIPTS / "run_flows.py").read_text(encoding="utf-8").replace("\nmain()\n", "\n")
    ns = {"__file__": str(SCRIPTS / "run_flows.py"), "__name__": "run_flows"}
    exec(compile(src, "run_flows.py", "exec"), ns)
    return ns


RF = load_run_flows()


def write_flows(items, repo=FIXTURE):
    """plan から フローを書いて、(行, {ファイル名: 中身}) を返す。経路の表示は捨てる。"""
    out = Path(tempfile.mkdtemp())
    plan = {"app": "jp.example.App", "items": items}
    with contextlib.redirect_stdout(io.StringIO()):
        rows = route.write_flows(plan, out, str(repo))
    flows = {p.name: p.read_text(encoding="utf-8") for p in sorted(out.glob("*.yaml"))}
    shutil.rmtree(out)
    return rows, flows


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
             "do": ["tap:Search"]},
            {"from": "list", "title": "末尾まで読む", "expect": "フッターが出る",
             "do": ["see:list.footer"]},
            {"from": "list", "fresh": True, "title": "行を開く", "expect": "詳細が開く",
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
    """#28: do の戻る操作は、マップの to ではなく歩いた履歴から戻り先を決める。"""

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


class NotesPerFlow(unittest.TestCase):
    """#31: 補足は、そのフローのステップに関係するものだけが付く。"""

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
    """#30: 実行時に決めた値は、tap のセレクタに入るものだけ正規表現としてエスケープする。"""

    def rows(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a",
             "do": [{"op": "text:list.search_field", "runtime": True}]},
            {"from": "list", "fresh": True, "title": "b", "expect": "b",
             "do": [{"op": "tap:list.row.*", "pick": "いちばん長い名前"}]}])
        return rows, flows

    def test_input_use_is_recorded(self):
        rows, _ = self.rows()
        self.assertEqual(rows[0]["input_use"], {"LIST_SEARCH_FIELD": "text"})
        self.assertEqual(rows[1]["input_use"], {"LIST_ROW": "selector", "LIST_ROW_INDEX": "index"})
        self.assertEqual(rows[0]["inputs"], {"LIST_SEARCH_FIELD": ""})
        self.assertEqual(rows[1]["inputs"], {"LIST_ROW": ""})
        self.assertEqual(rows[1]["picks"], {"LIST_ROW": {"pattern": "list.row.*", "pick": "いちばん長い名前",
                                                        "exclude": []}})

    def fill(self, flow, var, value, use):
        body = RF["fill_env"](flow, RF["required_env"](flow),
                              {var: value, var + "_INDEX": 0, "SHOTS": "/tmp/shots"}, {var: use})
        env = dict(re.findall(r"^\s+([A-Z_]+):\s*'(.*)'$", body.split("\n---")[0], re.M))
        return env[var].replace("''", "'")

    def test_selector_values_are_escaped(self):
        rows, flows = self.rows()
        flow = flows[rows[1]["flow"]]
        # 値はアクセシビリティ ID そのもの
        pattern = re.search(r"id: '(\^\$\{LIST_ROW\}\$)'", flow).group(1)
        for value, other in [("牛乳(1L)", "牛乳1L"), ("a.b", "aXb"),
                             ("C++入門", None), ("50% off [new]", None), ("It's", None)]:
            with self.subTest(value=value):
                filled = pattern.replace("${LIST_ROW}", self.fill(flow, "LIST_ROW", "list.row." + value, "selector"))
                self.assertTrue(re.fullmatch(filled, "list.row." + value))
                self.assertFalse(re.fullmatch(filled, "listXrow." + value))
                if other:
                    self.assertFalse(re.fullmatch(filled, "list.row." + other))

    def test_text_values_are_not_escaped(self):
        rows, flows = self.rows()
        flow = flows[rows[0]["flow"]]
        self.assertEqual(self.fill(flow, "LIST_SEARCH_FIELD", "牛乳(1L)", "text"), "牛乳(1L)")


class Manifest(unittest.TestCase):
    """manifest.py が plan から作るマニフェストの形。"""

    def test_manifest_from_plan(self):
        work = Path(tempfile.mkdtemp())
        plan = work / "plan.json"
        plan.write_text(json.dumps({"app": "jp.example.App", "items": [
            {"from": "list", "title": "a", "expect": "a",
             "do": ["tap:list.row.*"]}],
            "explore": [{"from": "settings", "title": "b", "expect": "b",
                         "reason": "画面 settings がマップに無い"}]}), encoding="utf-8")
        out = work / "out"
        fake = types.ModuleType("simulators")
        fake.lookup = lambda udid: {"model": "iPhone 17 Pro", "os": "iOS 26.5"}
        argv, mods = sys.argv, dict(sys.modules)
        sys.modules["simulators"] = fake
        sys.argv = ["manifest.py", str(plan), str(out), "--repo", str(FIXTURE),
                    "--device", "iphone=AAAA", "--device", "ipad=BBBB"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(SCRIPTS / "manifest.py"), run_name="__main__")
        finally:
            sys.argv = argv
            sys.modules.clear()
            sys.modules.update(mods)
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        shutil.rmtree(Path(m["flows"]), ignore_errors=True)
        shutil.rmtree(work)

        self.assertEqual(sorted(m["devices"]), ["ipad", "iphone"])
        first, explore = m["sections"]
        self.assertEqual(first["name"], "test_01")
        self.assertEqual(first["input_use"], {"LIST_ROW": "selector", "LIST_ROW_INDEX": "index"})
        # 条件の無い選択は run_flows.py が決めるので、inputs には入らない
        self.assertEqual(first["devices"]["iphone"], {"inputs": {}, "picked": {}})
        self.assertEqual(first["picks"], {"LIST_ROW": {"pattern": "list.row.*", "pick": "", "exclude": []}})
        self.assertEqual(first["parts"], [{"flow": "test_01.1.yaml", "decide": None},
                                          {"flow": "test_01.yaml", "decide": "LIST_ROW"}])
        self.assertEqual([i["src"] for i in first["images"]],
                         ["shots/iphone/test_01.png", "shots/ipad/test_01.png"])
        self.assertEqual(first["result"], "PENDING")
        # 経路が組めなかった項目は末尾に、フロー無しで並ぶ
        self.assertEqual(explore["name"], "test_02")
        self.assertIsNone(explore["flow"])
        self.assertEqual(explore["input_use"], {})

    def test_inputs_are_kept_when_rebuilt(self):
        work = Path(tempfile.mkdtemp())
        plan = work / "plan.json"
        out = work / "out"

        def build(items):
            plan.write_text(json.dumps({"app": "x", "items": items}), encoding="utf-8")
            fake = types.ModuleType("simulators")
            fake.lookup = lambda udid: {"model": "iPhone 17 Pro", "os": "iOS 26.5"}
            argv, mods = sys.argv, dict(sys.modules)
            sys.modules["simulators"] = fake
            sys.argv = ["manifest.py", str(plan), str(out), "--repo", str(FIXTURE),
                        "--device", "iphone=AAAA"]
            try:
                with contextlib.redirect_stdout(io.StringIO()) as o:
                    runpy.run_path(str(SCRIPTS / "manifest.py"), run_name="__main__")
            finally:
                sys.argv = argv
                sys.modules.clear()
                sys.modules.update(mods)
            return json.loads((out / "manifest.json").read_text(encoding="utf-8")), o.getvalue()

        text = {"from": "list", "title": "a", "expect": "a",
                "do": [{"op": "text:list.search_field", "runtime": True}]}
        m, _ = build([text])
        m["sections"][0]["devices"]["iphone"]["inputs"]["LIST_SEARCH_FIELD"] = "牛乳(1L)"
        (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")

        # 同じ変数なら引き継ぎ、引き継いだことを出す
        m, printed = build([dict(text, fresh=True)])
        self.assertEqual(m["sections"][0]["devices"]["iphone"]["inputs"],
                         {"LIST_SEARCH_FIELD": "牛乳(1L)"})
        self.assertIn("test_01 iphone: LIST_SEARCH_FIELD=牛乳(1L)", printed)

        # 変数が変わったら空に戻す
        m, _ = build([{"from": "list", "title": "a", "expect": "a",
                       "do": [{"op": "tap:list.row.*", "pick": "x"}]}])
        self.assertEqual(m["sections"][0]["devices"]["iphone"]["inputs"], {"LIST_ROW": ""})
        shutil.rmtree(Path(m["flows"]), ignore_errors=True)
        shutil.rmtree(work)


class RetakeRuns(unittest.TestCase):
    """#24: 撮り直す項目だけを、同じ鎖の頭からなぞって撮る。"""

    SECTIONS = [
        {"name": "test_01", "flow": "test_01.yaml", "launch": True},
        {"name": "test_02", "flow": "test_02.yaml", "launch": False},
        {"name": "test_03", "flow": "test_03.yaml", "launch": False},
        {"name": "test_04", "flow": "test_04.yaml", "launch": True},    # fresh
        {"name": "test_05", "flow": "test_05.yaml", "launch": False},
        {"name": "test_06", "flow": None},                             # explore
    ]

    def runs(self, only):
        return [(s["name"], m) for s, m in RF["retake_runs"](self.SECTIONS, only)]

    def test_fresh_item_runs_alone(self):
        self.assertEqual(self.runs(["test_04"]), [("test_04", "shot")])

    def test_item_in_chain_replays_from_head(self):
        self.assertEqual(self.runs(["test_03"]),
                         [("test_01", "replay"), ("test_02", "replay"), ("test_03", "shot")])

    def test_chain_runs_once_for_two_items(self):
        self.assertEqual(self.runs(["test_02", "test_05"]),
                         [("test_01", "replay"), ("test_02", "shot"),
                          ("test_04", "replay"), ("test_05", "shot")])

    def test_explore_item_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self.runs(["test_06"])
        self.assertIn("sim-driver", str(cm.exception.code))


class RetakeRun(unittest.TestCase):
    """#24: run_flows.py --only が実際に何を走らせ、どこへ撮り、何を書き換えるか。

    Maestro は叩かない。`sh` を差し替えて、呼ばれた順と撮影先を記録する。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.flows = self.tmp / "flows"
        self.flows.mkdir(parents=True)
        self.out.mkdir()
        # 鎖は test_01〜03 と test_04〜05。test_02 は実行時の値 X を使う
        env = {"test_02": ["X"]}
        self.sections = []
        for n, launch in [("test_01", True), ("test_02", False), ("test_03", False),
                          ("test_04", True), ("test_05", False)]:
            names = ["SHOTS"] + env.get(n, [])
            (self.flows / f"{n}.yaml").write_text(
                "appId: x\nenv:\n" + "".join(f"  {k}: ''\n" for k in names)
                + "---\n- takeScreenshot: '${SHOTS}/" + n + "'\n", encoding="utf-8")
            parts = [{"flow": f"{n}.yaml", "decide": None}]
            if env.get(n):
                (self.flows / f"{n}.1.yaml").write_text("appId: x\n---\n", encoding="utf-8")
                parts = [{"flow": f"{n}.1.yaml", "decide": None},
                         {"flow": f"{n}.yaml", "decide": env[n][0]}]
            self.sections.append({
                "name": n, "flow": f"{n}.yaml", "launch": launch, "parts": parts,
                "input_use": {k: "text" for k in env.get(n, [])},
                "devices": {"iphone": {"inputs": {k: "" for k in env.get(n, [])}}},
                "desc": f"{n} の前の判定", "note": "", "result": "OK"})
        self.sections[1]["devices"]["iphone"]["inputs"]["X"] = "牛乳"
        self.calls, self.failing = [], set()
        self.saved = {k: RF[k] for k in ("sh", "HERE")}
        RF["HERE"] = self.tmp / "scripts"

        def fake_sh(args, quiet=True):
            # run <UDID> <フロー> <名前> <撮影先> / inspect <UDID> <名前> <撮影先>
            cmd = args[0]
            name, dest = (args[3], args[4]) if cmd == "run" else (args[2], args[3])
            body = args[2] if cmd == "run" else ""
            self.calls.append((cmd, name, Path(dest).name, body))
            return 1 if (cmd == "run" and name in self.failing) else 0
        RF["sh"] = fake_sh

    def tearDown(self):
        RF.update(self.saved)
        shutil.rmtree(self.tmp)

    def run_device(self, only):
        manifest = {"sections": self.sections}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return RF["run_device"](manifest, self.out / "manifest.json", self.flows,
                                    "iphone", "AAAA", None, only)

    def sec(self, name):
        return next(s for s in self.sections if s["name"] == name)

    def test_replays_head_and_shoots_only_target(self):
        stopped, done, lost = self.run_device(["test_03"])
        self.assertEqual((stopped, done, lost), (None, 1, []))
        runs = [(c, n, d) for c, n, d, _ in self.calls]
        # 手前の2本は作業用の置き場（replay の下の端末名）に撮り、ダンプは取らない
        self.assertEqual(runs, [("run", "test_01", "iphone"), ("run", "test_02.1", "iphone"),
                                ("run", "test_02", "iphone"),
                                ("run", "test_03", "iphone"), ("inspect", "test_03", "iphone")])
        replay_dest = self.tmp / ".work" / "replay" / "out" / "iphone"
        self.assertTrue(replay_dest.is_dir())
        shots = self.out / "shots" / "iphone"
        self.assertTrue(shots.is_dir())
        # なぞった項目の判定はそのまま。撮った項目だけ PENDING
        self.assertEqual(self.sec("test_01")["result"], "OK")
        self.assertEqual(self.sec("test_02")["desc"], "test_02 の前の判定")
        self.assertEqual(self.sec("test_03")["result"], "PENDING")
        self.assertEqual(self.sec("test_04")["result"], "OK")
        # なぞる項目にも実行時の値が入る
        self.assertIn("X: '牛乳'", self.calls[2][3])
        log = (self.out / "progress_iphone.log").read_text(encoding="utf-8")
        self.assertIn("test_01 なぞった", log)
        self.assertIn("test_03 撮影済み", log)

    def test_replay_writes_to_scratch_not_shots(self):
        self.run_device(["test_02"])
        body = self.calls[0][3]
        # test_01 は SHOTS を作業用の置き場に向けて走る
        self.assertIn(str((self.tmp / ".work" / "replay" / "out" / "iphone").resolve()), body)
        self.assertNotIn(str((self.out / "shots").resolve()), body)

    def test_fresh_target_runs_alone(self):
        self.run_device(["test_04"])
        self.assertEqual([(c, n) for c, n, _, _ in self.calls],
                         [("run", "test_04"), ("inspect", "test_04")])

    def test_replay_without_value_stops(self):
        self.sec("test_02")["devices"]["iphone"]["inputs"]["X"] = ""
        with self.assertRaises(SystemExit) as cm:
            self.run_device(["test_03"])
        self.assertIn("test_02 の値が未定", str(cm.exception.code))
        self.assertIn("前に撮ったときの値が要る", str(cm.exception.code))

    def test_failure_while_replaying_loses_target(self):
        self.failing.add("test_02")
        stopped, done, lost = self.run_device(["test_03"])
        self.assertEqual(done, 0)
        self.assertEqual(lost, ["iphone test_02（なぞる途中で落ちた）", "iphone test_03"])
        # 撮れていないので、前の判定（RETAKE を出した判定）はそのまま
        self.assertEqual(self.sec("test_03")["result"], "OK")
        self.assertNotIn(("inspect", "test_03"), [(c, n) for c, n, _, _ in self.calls])

    def test_other_chain_is_not_touched(self):
        self.run_device(["test_05"])
        self.assertEqual([n for c, n, _, _ in self.calls if c == "run"], ["test_04", "test_05"])

    def test_full_run_is_unchanged(self):
        stopped, done, lost = self.run_device(None)
        self.assertEqual(done, 5)
        self.assertTrue(all(s["result"] == "PENDING" for s in self.sections))


class Interrupts(unittest.TestCase):
    """#33: 自動表示は画面として書き、auto_shows に並べた画面に着いたときだけ確かめて閉じる。"""

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
        mp = route.ScreenMap(route.find_map(str(self.repo)))
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = route.cmd_check(mp)
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

    def test_checked_in_pre_flow(self):
        # 値を決める手前で詳細に着く。割れた途中の本でも閉じる
        # （1本目: 一覧まで / 2本目: 行を押して詳細、戻る / 3本目: 入力）
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a",
             "do": ["tap:list.row.*", "tap:BackButton",
                    {"op": "text:list.search_field", "runtime": True}]}], self.repo)
        self.assertEqual([p["decide"] for p in rows[0]["parts"]], [None, "LIST_ROW", "LIST_SEARCH_FIELD"])
        self.assertIn("runFlow", flows[rows[0]["parts"][1]["flow"]])

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
            {"from": "review_dialog", "fresh": True, "title": "レビュー依頼が出る",
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
            {"from": "review_dialog", "fresh": True, "title": "後でで閉じる",
             "expect": "詳細画面に戻っている",
             "do": ["tap:review_dialog.later_button"]}], self.repo)
        flow = flows["test_01.yaml"]
        self.assertEqual(waits(flow)[-4:], ["^home$", "^review_dialog$", "^detail$", "^detail\\.title$"])
        self.assertEqual(rows[0]["screen"], "detail")
        # 戻る操作で戻った画面では、自動表示を確かめない（入ったときに出るもの）
        self.assertNotIn("runFlow", flow)

    def test_other_items_still_close_it(self):
        rows, flows = write_flows([
            {"from": "review_dialog", "fresh": True, "title": "a", "expect": "a"},
            {"from": "detail", "fresh": True, "title": "b", "expect": "b"}], self.repo)
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


class ReportShape(unittest.TestCase):
    """判定の欄は LLM が書くので形が揺れる。build_report.py --check で書いた直後に止める。"""

    def problems(self, manifest):
        ns = runpy.run_path(str(SCRIPTS / "build_report.py"), run_name="build_report")
        return ns["shape_problems"](manifest)

    def test_footer_must_be_text(self):
        out = self.problems({"sections": [], "footer": {"確認していないこと": "エラー系"}})
        self.assertEqual(len(out), 1)
        self.assertIn("footer が文字列ではない（dict）", out[0])

    def test_section_fields_must_be_text(self):
        out = self.problems({"sections": [
            {"name": "test_01", "title": "a", "desc": ["x"], "note": {"a": 1}, "result": "OK"}]})
        self.assertEqual(len(out), 2)
        self.assertIn("test_01 の desc が文字列ではない（list）", out[0])
        self.assertIn("test_01 の note が文字列ではない（dict）", out[1])

    def test_pending_is_reported_but_retake_passes(self):
        out = self.problems({"sections": [
            {"name": "test_01", "title": "a", "result": "PENDING"},
            {"name": "test_02", "title": "b", "result": "RETAKE"}]})
        self.assertEqual(len(out), 1)
        self.assertIn("test_01 の result が 'PENDING'", out[0])

    def test_text_footer_passes(self):
        self.assertEqual(self.problems({"sections": [
            {"name": "test_01", "title": "a", "desc": "x", "result": "NG"}],
            "footer": "確認していないこと: エラー系\n作成したデータ: 無し"}), [])

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
    """#50: 経路は expect の screen を辺にして引く。"""

    def mp(self, repo=FIXTURE):
        return route.load_map(str(repo))

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
        hops = route.load_map(str(repo)).path_from("detail", "settings")
        self.assertIsNone(hops)
        hops = route.load_map(str(repo)).path_from("detail", "settings", ("フォローしていないとき",))
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
    """#50: フローに積むもの。"""

    def test_scroll_before_every_tap(self):
        rows, flows = write_flows([{"from": "detail", "title": "a", "expect": "a",
                                    "do": ["tap:detail.favorite_button"]}])
        flow = flows["test_01.yaml"]
        block = flow[flow.index("# detail: tap detail.favorite_button"):]
        self.assertLess(block.index("scrollUntilVisible:\n    element:\n      id: '^detail\\.favorite_button$'"),
                        block.index("- tapOn"))
        self.assertIn("id: '^detail\\.favorite_button$'\n      selected: true", block)

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
        rows, flows = write_flows([{"from": "detail", "title": "a", "expect": "a",
                                    "do": ["tap:detail.follow_button", "tap:detail.share_button"]}])
        flow = flows["test_01.yaml"]
        self.assertIn("notVisible:\n      id: '^detail\\.follow_button$'", flow)
        ext = flow[flow.index("# アプリの外（safari）に出る"):]
        self.assertIn("- launchApp:\n    stopApp: false", ext)
        self.assertIn("id: '^detail$'", ext)
        self.assertIsNone(rows[0]["checked"])

    def test_gesture(self):
        rows, flows = write_flows([{"from": "list", "title": "a", "expect": "a", "do": ["scroll:down"]}])
        self.assertIn("- scroll\n- extendedWaitUntil:\n    visible:\n      id: '^list\\.footer$'",
                      flows["test_01.yaml"])

    def test_pattern_value_can_be_fixed(self):
        rows, flows = write_flows([{"from": "list", "title": "a", "expect": "a",
                                    "do": ["tap:list.row.C++入門"]}])
        self.assertEqual(rows[0]["parts"], [{"flow": "test_01.yaml", "decide": None}])
        self.assertIn("id: '^list\\.row\\.C\\+\\+入門$'", flows["test_01.yaml"])

    def test_pattern_on_the_way_is_picked(self):
        # from までの途中で一覧の行を押す。見えている1件目を選ぶので、そこで割れる
        rows, _ = write_flows([{"from": "list", "title": "a", "expect": "a"},
                               {"from": "detail", "title": "b", "expect": "b"}])
        self.assertEqual(rows[1]["picks"], {"LIST_ROW": {"pattern": "list.row.*", "pick": "", "exclude": []}})
        self.assertEqual(rows[1]["inputs"], {})

    def test_elements_under_the_pattern_are_excluded(self):
        repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, repo)
        lst = repo / "screen-map" / "screens" / "list.yaml"
        lst.write_text(lst.read_text(encoding="utf-8").replace(
            "  - id: list.empty_view\n", "  - id: list.row.title\n    name: 行の題名\n  - id: list.empty_view\n"),
            encoding="utf-8")
        rows, _ = write_flows([{"from": "list", "title": "a", "expect": "a", "do": ["tap:list.row.*"]}], repo)
        self.assertEqual(rows[0]["picks"]["LIST_ROW"]["exclude"], ["list.row.title"])
        shutil.rmtree(repo.parent)

    def test_runtime_on_tap_is_refused(self):
        code, err = build_err([{"from": "list", "title": "a", "expect": "a",
                                "do": [{"op": "tap:list.row.*", "runtime": True}]}])
        self.assertIn("runtime は付けられない", err)

    def test_pick_on_non_pattern_is_refused(self):
        code, err = build_err([{"from": "list", "title": "a", "expect": "a",
                                "do": [{"op": "tap:Search", "pick": "x"}]}])
        self.assertIn("パターンの要素（ID の末尾が *）ではないので、実行時に選べない", err)

    def test_same_pattern_twice_gets_two_vars(self):
        rows, flows = write_flows([{"from": "list", "title": "a", "expect": "a",
                                    "do": ["tap:list.row.*", "tap:BackButton", "tap:list.row.*"]}])
        self.assertEqual([p["decide"] for p in rows[0]["parts"]], [None, "LIST_ROW", "LIST_ROW_2"])


class AutoShowAfter(unittest.TestCase):
    """#50: after つきの自動表示は、after の画面から戻ったときだけ確かめる。"""

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
        mp = route.load_map(str(self.repo))
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = route.cmd_check(mp)
        self.assertEqual(code, 0, o.getvalue())
        self.assertIn("自動表示 review_dialog を detail から戻るたびに確かめる", o.getvalue())
        lst = self.repo / "screen-map" / "screens" / "list.yaml"
        lst.write_text(lst.read_text(encoding="utf-8").replace("after: detail", "after: viewer"),
                       encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = route.cmd_check(route.load_map(str(self.repo)))
        self.assertEqual(code, 1)
        self.assertIn("自動表示 review_dialog の after viewer の画面が無い", o.getvalue())


class FirstVisible(unittest.TestCase):
    """#50: パターンの要素は、ツリー順ではなく画面に見えている1件目を選ぶ。"""

    DUMP = (DUMP_HEAD
            + dump_line(195, -40, "×", "list.row.吾輩は猫である", "吾輩は猫である")
            + dump_line(195, 60, "○", "list", "一覧")
            + dump_line(195, 300, "○", "list.row.C++入門", "C++入門", "選択")
            + dump_line(195, 380, "○", "list.row.こころ", "")
            + dump_line(195, 900, "×", "list.row.坊っちゃん", "坊っちゃん"))

    def test_skips_offscreen_and_strips_state(self):
        self.assertEqual(RF["first_visible"](self.DUMP, "list.row.*"), "list.row.C++入門")

    def test_elements_inside_the_row_are_excluded(self):
        # 行の中のタイトル（list.row.title）も行のパターンに当たる。マップで別の要素なら選ばない
        dump = (dump_line(195, 280, "○", "list.row.title", "吾輩は猫である")
                + dump_line(195, 300, "○", "list.row.こころ", ""))
        self.assertEqual(RF["first_visible"](dump, "list.row.*"), "list.row.title")
        self.assertEqual(RF["first_visible"](dump, "list.row.*", ["list.row.title"]), "list.row.こころ")
        self.assertEqual(RF["first_visible"](dump, "list.row.*", ["list.row.t*"]), "list.row.こころ")

    def test_locate_counts_offscreen_rows_for_index(self):
        # Maestro の index は画面外も含めた位置順。吾輩は猫である は1件しか無いので 0
        self.assertEqual(RF["locate"](self.DUMP, "list.row.*"), ("list.row.C++入門", 0, 1))
        dump = (dump_line(195, -40, "×", "list.row.牛乳", "")
                + dump_line(195, 300, "○", "list.row.牛乳", "")
                + dump_line(195, 380, "○", "list.row.牛乳#2", ""))
        self.assertEqual(RF["locate"](dump, "list.row.*"), ("list.row.牛乳", 1, 2))
        # 名前そのものが #2 で終わる行があれば、そちらを採る
        self.assertEqual(RF["locate"](dump, "list.row.*", value="list.row.牛乳#2"), ("list.row.牛乳#2", 0, 1))
        self.assertIsNone(RF["locate"](dump, "list.row.*", value="list.row.牛乳#3"))

    def test_none_when_nothing_visible(self):
        self.assertIsNone(RF["first_visible"](self.DUMP, "detail.cell.*"))

    def test_id_with_spaces(self):
        dump = dump_line(201, 241, "○", "list.row.BOITEUX ・ BOITEUSE", "BOITEUX ・ BOITEUSE, 李 箱")
        self.assertEqual(RF["first_visible"](dump, "list.row.*"), "list.row.BOITEUX ・ BOITEUSE")

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
        out = subprocess.run([sys.executable, str(SCRIPTS / "elements.py"), str(f)],
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


class AutoPickRun(unittest.TestCase):
    """#50: 条件の無い選択は run_flows.py が画面を読んで決め、picked に書く。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "out"
        self.flows = self.tmp / "flows"
        self.flows.mkdir(parents=True)
        self.out.mkdir()
        (self.flows / "test_01.1.yaml").write_text("appId: x\n---\n", encoding="utf-8")
        (self.flows / "test_01.yaml").write_text(
            "appId: x\nenv:\n  SHOTS: ''\n  LIST_ROW: ''\n  LIST_ROW_INDEX: ''\n---\n- tapOn:\n"
            "    id: '^${LIST_ROW}$'\n    index: ${LIST_ROW_INDEX}\n",
            encoding="utf-8")
        self.sec = {"name": "test_01", "flow": "test_01.yaml", "launch": True,
                    "parts": [{"flow": "test_01.1.yaml", "decide": None},
                              {"flow": "test_01.yaml", "decide": "LIST_ROW"}],
                    "input_use": {"LIST_ROW": "selector", "LIST_ROW_INDEX": "index"},
                    "picks": {"LIST_ROW": {"pattern": "list.row.*", "pick": "", "exclude": []}},
                    "devices": {"iphone": {"inputs": {}, "picked": {}}},
                    "desc": "", "note": "", "result": "PENDING"}
        self.saved = {k: RF[k] for k in ("sh", "HERE")}
        RF["HERE"] = self.tmp / "scripts"
        self.calls = []
        self.dump = FirstVisible.DUMP

        def fake_sh(args, quiet=True):
            self.calls.append(args)
            if args[0] == "inspect" and len(args) == 3:
                state = self.tmp / ".work" / "state"
                state.mkdir(parents=True, exist_ok=True)
                (state / "last_dump_AAAA.txt").write_text(self.dump, encoding="utf-8")
            return 0
        RF["sh"] = fake_sh

    def tearDown(self):
        RF.update(self.saved)
        shutil.rmtree(self.tmp)

    def run_device(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return RF["run_device"]({"sections": [self.sec]}, self.out / "manifest.json",
                                    self.flows, "iphone", "AAAA", None)

    def test_picks_first_visible_and_fills_env(self):
        stopped, done, lost = self.run_device()
        self.assertEqual((stopped, done, lost), (None, 1, []))
        self.assertEqual(self.sec["devices"]["iphone"]["picked"], {"LIST_ROW": "list.row.C++入門", "LIST_ROW_INDEX": 0})
        body = [a[2] for a in self.calls if a[0] == "run" and a[3] == "test_01"][0]
        self.assertIn("LIST_ROW: 'list\\.row\\.C\\+\\+入門'", body)
        self.assertIn("LIST_ROW_INDEX: '0'", body)
        log = (self.out / "progress_iphone.log").read_text(encoding="utf-8")
        self.assertIn("test_01 撮影済み（選んだ: LIST_ROW=list.row.C++入門）", log)

    def test_same_name_rows_get_index(self):
        # 同じ名前の行が画面外（上）に1件、画面内に2件。見えている1件目は、位置順で2件目
        self.dump = (dump_line(195, -40, "×", "list.row.牛乳", "")
                     + dump_line(195, 300, "○", "list.row.牛乳", "")
                     + dump_line(195, 380, "○", "list.row.牛乳", ""))
        self.run_device()
        self.assertEqual(self.sec["devices"]["iphone"]["picked"], {"LIST_ROW": "list.row.牛乳", "LIST_ROW_INDEX": 1})
        log = (self.out / "progress_iphone.log").read_text(encoding="utf-8")
        self.assertIn("LIST_ROW=list.row.牛乳（同じ名前 3件のうち上から2件目）", log)

    def test_conditional_pick_with_ordinal(self):
        # 条件で選んだ値に #2 を付けると、見えている同じ名前のうち上から2件目
        self.dump = (dump_line(195, -40, "×", "list.row.牛乳", "")
                     + dump_line(195, 300, "○", "list.row.牛乳", "")
                     + dump_line(195, 380, "○", "list.row.牛乳", ""))
        self.sec["picks"]["LIST_ROW"]["pick"] = "下の方の牛乳"
        self.sec["devices"]["iphone"]["inputs"] = {"LIST_ROW": "list.row.牛乳#2"}
        with contextlib.redirect_stdout(io.StringIO()):
            RF["run_device"]({"sections": [self.sec]}, self.out / "manifest.json",
                             self.flows, "iphone", "AAAA", ("test_01", 1))
        self.assertEqual(self.sec["devices"]["iphone"]["picked"], {"LIST_ROW": "list.row.牛乳", "LIST_ROW_INDEX": 2})
        body = [a[2] for a in self.calls if a[0] == "run"][0]
        self.assertIn("LIST_ROW: 'list\\.row\\.牛乳'", body)
        self.assertIn("LIST_ROW_INDEX: '2'", body)

    def test_conditional_pick_not_on_screen_loses_the_item(self):
        self.sec["picks"]["LIST_ROW"]["pick"] = "x"
        self.sec["devices"]["iphone"]["inputs"] = {"LIST_ROW": "list.row.坊っちゃん"}   # 画面外
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            stopped, done, lost = RF["run_device"]({"sections": [self.sec]}, self.out / "manifest.json",
                                                   self.flows, "iphone", "AAAA", ("test_01", 1))
        self.assertEqual(lost, ["iphone test_01"])
        self.assertIn("list.row.坊っちゃん が画面に見えていない",
                      (self.out / "progress_iphone.log").read_text(encoding="utf-8"))

    def test_id_outside_the_pattern_stops(self):
        # 接頭辞を落として書いた（こころ）。押さずに止め、直せば続きから走れる
        self.sec["picks"]["LIST_ROW"]["pick"] = "x"
        self.sec["devices"]["iphone"]["inputs"] = {"LIST_ROW": "こころ"}
        with contextlib.redirect_stdout(io.StringIO()) as o:
            stopped, done, lost = RF["run_device"]({"sections": [self.sec]}, self.out / "manifest.json",
                                                   self.flows, "iphone", "AAAA", ("test_01", 1))
        self.assertEqual(stopped, ("test_01", 1))
        self.assertEqual([a for a in self.calls if a[0] == "run"], [])
        self.assertIn("LIST_ROW の値 こころ が list.row.* に当たらない", o.getvalue())

    def test_nothing_visible_loses_the_item(self):
        self.dump = DUMP_HEAD
        stopped, done, lost = self.run_device()
        self.assertEqual((done, lost), (0, ["iphone test_01"]))
        self.assertIn("押す list.row.* が画面に見えていない",
                      (self.out / "progress_iphone.log").read_text(encoding="utf-8"))

    def test_conditional_pick_stops_for_input(self):
        self.sec["picks"]["LIST_ROW"]["pick"] = "いちばん長い名前"
        self.sec["devices"]["iphone"]["inputs"] = {"LIST_ROW": ""}
        stopped, done, lost = self.run_device()
        self.assertEqual(stopped, ("test_01", 1))
        self.assertEqual([a[3] for a in self.calls if a[0] == "run"], ["test_01.1"])

    def test_resume_starts_from_the_stopped_part(self):
        self.sec["picks"]["LIST_ROW"]["pick"] = "いちばん長い名前"
        self.sec["devices"]["iphone"]["inputs"] = {"LIST_ROW": "list.row.こころ"}
        with contextlib.redirect_stdout(io.StringIO()):
            RF["run_device"]({"sections": [self.sec]}, self.out / "manifest.json",
                             self.flows, "iphone", "AAAA", ("test_01", 1))
        self.assertEqual([a[3] for a in self.calls if a[0] == "run"], ["test_01"])


class Check(unittest.TestCase):
    """#50: check は expect / ready が指す ID と、スキーマの形を確かめる。"""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, self.repo)
        self.screens = self.repo / "screen-map" / "screens"

    def tearDown(self):
        shutil.rmtree(self.repo.parent)

    def check(self):
        mp = route.ScreenMap(route.find_map(str(self.repo)))
        with contextlib.redirect_stdout(io.StringIO()) as o:
            code = route.cmd_check(mp)
        return code, o.getvalue()

    def edit(self, name, old, new):
        f = self.screens / name
        text = f.read_text(encoding="utf-8")
        self.assertIn(old, text)
        f.write_text(text.replace(old, new), encoding="utf-8")

    def test_fixture_passes(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)

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

    def test_old_schema_is_refused(self):
        shutil.rmtree(self.repo / "screen-map")
        shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "old_app" / "screen-map",
                        self.repo / "screen-map")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("migrate_map.py", out)
        with self.assertRaises(SystemExit) as cm:
            route.load_map(str(self.repo))
        self.assertIn("migrate_map.py", str(cm.exception.code))


class Migrate(unittest.TestCase):
    """#50: 古い形のマップを変換し、そのまま check と経路計算が通る。"""

    def test_migrate_old_fixture(self):
        repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "old_app", repo)
        argv = sys.argv
        sys.argv = ["migrate_map.py", "--repo", str(repo), "--write"]
        try:
            with contextlib.redirect_stdout(io.StringIO()) as o:
                runpy.run_path(str(SCRIPTS / "migrate_map.py"), run_name="__main__")
        finally:
            sys.argv = argv
        out = o.getvalue()
        self.assertIn("移した画面: detail home list review_dialog", out)
        self.assertIn("select（index: 0, capture: itemTitle）を捨てた", out)
        self.assertIn("に条件が書いてある", out)
        mp = route.load_map(str(repo))
        lst = {el["id"]: el for el in mp.elements("list")}
        self.assertEqual(lst["list.empty_view"]["when"], "0件のとき")
        self.assertIn("list.count_label", lst)          # 観測点だった ID も要素になる
        self.assertEqual(lst["Clear text"]["by"], "label")
        self.assertIs(lst["list.banner"]["in_tree"], False)
        self.assertEqual(mp.screens["list"]["gestures"][0]["scroll"], "down")
        self.assertEqual(mp.screens["list"]["auto_shows"], ["review_dialog"])
        with contextlib.redirect_stdout(io.StringIO()) as o:
            self.assertEqual(route.cmd_check(mp), 0, o.getvalue())
        self.assertEqual([e[0].target for _, e in mp.path_from("home", "detail")], ["home.fav"])
        shutil.rmtree(repo.parent)


class MiniYaml(unittest.TestCase):
    def test_flow_mapping(self):
        from mini_yaml import load_yaml
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
