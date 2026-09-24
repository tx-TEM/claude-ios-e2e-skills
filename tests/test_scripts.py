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


if __name__ == "__main__":
    unittest.main()
