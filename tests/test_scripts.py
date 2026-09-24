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
             "do": [{"op": "text:list.searchField", "runtime": True}]},
            {"from": "list", "title": "検索キーで確定", "expect": "キーボードが閉じる",
             "do": ["tap:Search"]},
            {"from": "list", "title": "末尾まで読む", "expect": "フッターが出る",
             "do": ["scroll:list.footer"]},
            {"from": "list", "fresh": True, "title": "行を開く", "expect": "詳細が開く",
             "do": [{"op": "tap:list.row.*", "runtime": True}]},
            {"from": "detail", "title": "戻る", "expect": "一覧に戻る",
             "do": ["tap:BackButton"]},
        ])
        self.assertEqual([r["name"] for r in rows],
                         ["test_01", "test_02", "test_03", "test_04", "test_05", "test_06"])
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
        # マップの to: list と食い違ったことは補足に出る
        self.assertIn("to: list と書いてあるが、歩いてきた履歴では home に戻る", flow)

    def test_back_via_list_returns_list_without_note(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a",
             "do": ["tap:list.row.*", "tap:BackButton"]}])
        flow = flows["test_01.yaml"]
        self.assertEqual(waits(flow)[-1], "^list$")
        self.assertNotIn("# 補足", flow)

    def test_back_without_to_is_a_back(self):
        # list の戻るには to が無い。それでも戻る操作として履歴から戻る
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a", "do": ["tap:BackButton"]}])
        self.assertEqual(waits(flows["test_01.yaml"])[-1], "^home$")

    def test_back_on_start_screen_stops(self):
        repo = Path(tempfile.mkdtemp()) / "app"
        shutil.copytree(FIXTURE, repo)
        home = repo / "screen-map" / "screens" / "home.yaml"
        home.write_text(home.read_text(encoding="utf-8")
                        + "  - tap: home.close\n    kind: dismiss\n", encoding="utf-8")
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
        rows, flows = write_flows([
            {"from": "detail", "title": "a", "expect": "a", "do": ["tap:BackButton"]},
            {"from": "list", "title": "b", "expect": "b"}])
        self.assertIn("# 補足", flows["test_01.yaml"])
        self.assertNotIn("# 補足", flows["test_02.yaml"])


class RuntimeInputs(unittest.TestCase):
    """#30: 実行時に決めた値は、tap のセレクタに入るものだけ正規表現としてエスケープする。"""

    def rows(self):
        rows, flows = write_flows([
            {"from": "list", "title": "a", "expect": "a",
             "do": [{"op": "text:list.searchField", "runtime": True}]},
            {"from": "list", "fresh": True, "title": "b", "expect": "b",
             "do": [{"op": "tap:list.row.*", "runtime": True}]}])
        return rows, flows

    def test_input_use_is_recorded(self):
        rows, _ = self.rows()
        self.assertEqual(rows[0]["input_use"], {"LIST_SEARCHFIELD": "text"})
        self.assertEqual(rows[1]["input_use"], {"LIST_ROW": "selector"})

    def fill(self, flow, var, value, use):
        body = RF["fill_env"](flow, RF["required_env"](flow),
                              {var: value, "SHOTS": "/tmp/shots"}, {var: use})
        env = dict(re.findall(r"^\s+([A-Z_]+):\s*'(.*)'$", body.split("\n---")[0], re.M))
        return env[var].replace("''", "'")

    def test_selector_values_are_escaped(self):
        rows, flows = self.rows()
        flow = flows[rows[1]["flow"]]
        pattern = re.search(r"id: '(\^list\\\.row\\\.\$\{LIST_ROW\}\$)'", flow).group(1)
        for value, other in [("牛乳(1L)", "牛乳1L"), ("a.b", "aXb"),
                             ("C++入門", None), ("50% off [new]", None), ("It's", None)]:
            with self.subTest(value=value):
                filled = pattern.replace("${LIST_ROW}", self.fill(flow, "LIST_ROW", value, "selector"))
                self.assertTrue(re.fullmatch(filled, "list.row." + value))
                if other:
                    self.assertFalse(re.fullmatch(filled, "list.row." + other))

    def test_text_values_are_not_escaped(self):
        rows, flows = self.rows()
        flow = flows[rows[0]["flow"]]
        self.assertEqual(self.fill(flow, "LIST_SEARCHFIELD", "牛乳(1L)", "text"), "牛乳(1L)")


class Manifest(unittest.TestCase):
    """manifest.py が plan から作るマニフェストの形。"""

    def test_manifest_from_plan(self):
        work = Path(tempfile.mkdtemp())
        plan = work / "plan.json"
        plan.write_text(json.dumps({"app": "jp.example.App", "items": [
            {"from": "list", "title": "a", "expect": "a",
             "do": [{"op": "tap:list.row.*", "runtime": True}]}],
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
        self.assertEqual(first["input_use"], {"LIST_ROW": "selector"})
        self.assertEqual(first["devices"]["iphone"]["inputs"], {"LIST_ROW": ""})
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
                "do": [{"op": "text:list.searchField", "runtime": True}]}
        m, _ = build([text])
        m["sections"][0]["devices"]["iphone"]["inputs"]["LIST_SEARCHFIELD"] = "牛乳(1L)"
        (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")

        # 同じ変数なら引き継ぎ、引き継いだことを出す
        m, printed = build([dict(text, fresh=True)])
        self.assertEqual(m["sections"][0]["devices"]["iphone"]["inputs"],
                         {"LIST_SEARCHFIELD": "牛乳(1L)"})
        self.assertIn("test_01 iphone: LIST_SEARCHFIELD=牛乳(1L)", printed)

        # 変数が変わったら空に戻す
        m, _ = build([{"from": "list", "title": "a", "expect": "a",
                       "do": [{"op": "tap:list.row.*", "runtime": True}]}])
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
            self.sections.append({
                "name": n, "flow": f"{n}.yaml", "launch": launch, "pre_flow": None,
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
        self.assertEqual(runs, [("run", "test_01", "iphone"), ("run", "test_02", "iphone"),
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
        self.assertIn("X: '牛乳'", self.calls[1][3])
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


if __name__ == "__main__":
    unittest.main()
