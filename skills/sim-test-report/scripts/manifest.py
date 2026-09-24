#!/usr/bin/env python3
"""test-case-builder の plan.json から、フローを書いて manifest.json を作る。

  manifest.py <plan.json> <出力先ディレクトリ> --repo <アプリのリポジトリ> --device <端末>=<UDID> [--device …]

`--repo` はアプリのリポジトリ。**必ず渡す。** 画面マップ（その下の `screen-map/`）は、経路を組む項目があるときに読む。

証跡は `<出力先>/shots/<端末>/<名前>.png` に撮る。名前は項目の並び順から振る
（`test_01`, `test_02`, …。explore は items の続きの番号）。**1つのテストケースを複数の端末で
撮れる**（`--device iphone=<UDID> --device ipad=<UDID>`）。フローは端末によらず1組で、撮影先だけを
`${SHOTS}` のまま書き、run_flows.py が端末に合わせて埋める。どの端末で撮るか、どこに出すかは
撮る側の設定で、テストケース（plan）は持たない。

**どのシミュレーターで撮るかもここで決める。** UDID から機種名と OS を引いて、マニフェストの
`devices` に書く。run_flows.py と sim-driver はそこから UDID を読み、build_report.py は確認環境を
そこから出す。撮るときに手で渡し直さない。

1. plan の項目ごとに Maestro のフローを、スキル側の `.work/flows/<出力先の名前>/` に書く
   （route.py の `write_flows()`）。経路が組めなければ理由を出して止まる
   （manifest は書かない）。組めたら読める経路を出す
2. 返ってきた項目ごとの行から manifest.json を組む。証跡1枚＝1セクション

plan.json の形。**項目1つ＝ from から do を順に叩いて、1枚撮る。**

    {"app": "<bundle id>",
     "clear_state": false,
     "items": [
       {"from": "browse"},
       {"from": "browse",
        "do": [{"op": "text:browse.searchField", "runtime": true}]},
       {"from": "browse",
        "do": [{"op": "text:browse.searchField", "input": "zzzz"}]},
       {"from": "browse", "fresh": true,
        "do": ["tap:browse.bookRow.*"]}]}
  from      その項目の操作を始める画面。**項目は経路を持たない** —
            前の項目が終わった画面から from までは、ここで計算して
            繋ぐ（すでに居れば何もしない）
  do        確かめる操作。`tap:<id>` `scroll:down` のように種類を頭に
            付けて指せる（scroll は必須）。**並び順がそのまま実行順。**
            遷移する操作も書いてよく、行き先はマップの `to` で追う。
            着いた状態を見るだけの項目は空。入れる値の要る操作は
            {"op": 操作id, …} にして、次のどちらかを添える
              "runtime": true  **値を実行時に決める。** フローには値を
                   焼き込まず、`env` の未定のまま残す。着いた画面を
                   見ないと決まらないときに（打つ文字、どの行を叩くか）。
                   焼き込むと、データが変わっても古い値で黙って走る
              "input": 値      データに依らない値（一致しない語など）
            from までの経路の途中で叩く操作は位置で選ぶ。どれを選ぶかを
            気にするなら、それは確かめる操作なので do に書く
  fresh     その項目はアプリを起動し直した直後から始める。**項目の前提で
            あって、フローの切り方ではない** — 前の項目の状態（絞り込み、
            変えたデータ）が残ると前提が崩れるときだけ付ける
  title / expect  確認項目と期待。そのままマニフェストに入る
  explore   経路が組めなかった項目（from / title / expect / reason）。
            フローを持たず、末尾にセクションとして並ぶ

**フローはスキル側の `.work/flows/<出力先の名前>/` に書き、その場所をマニフェストの `flows` に
記録する。** run_flows.py はそこから読む。アプリのリポジトリには何も作らない。

**1本で叩く。** フローとマニフェストを別々に作ると、plan を直したときに片方だけ
作り直す余地ができ、どちらの項目がどの証跡か決まらなくなる。

**写さない。** 証跡の名前・撮った画面・自動確認のID・フローのファイル名は経路を計算した
結果から、`title` / `expect` / `from` は plan から、どちらも write_flows() が1行にして返す。

**ヘッダの題と meta（ブランチ・確認環境・実施日）は持たない。** レポートを組むときに
`build_report.py` へ直に渡す。確認環境は撮影する端末を決めるまで決まらない。

**`launch` をそのまま持ってくる。** そのフローが自分でアプリを起動するかどうかで、
**鎖の切れ目**を表す（1本目と plan で `fresh` を付けた項目が `true`）。`run_flows.py` は
落ちたときにどこまで諦めるかをこれで決め、レビューと判定は**そこでアプリが
起動し直ることを知らないと証跡を読み違える**（前の項目の状態が続いているのか、
まっさらなのか）。

**`inputs` は実行時に決める値。** plan で `runtime` を書いた項目に付く。打つ文字
にも、どの行を叩くかにも付く。値が空のうちはフローを走らせられない（`run_flows.py`
がそこで止まる）。着いた画面を見ないと決まらないものなので、埋めるのは撮影する側。
**埋める側は正規表現のエスケープをかけない。** 各値がセレクタ（正規表現）に入るか
inputText に入るかは `input_use` に書いてあり、エスケープは run_flows.py がする。

plan の `explore` は**経路が組めなかった項目**。`flow` を持たないので `run_flows.py` は
飛ばし、sim-driver が探索で撮る。**末尾に並ぶ** — 自動確認の付かない項目がまとまる。

なぜスクリプトなのか。一覧の中身（証跡の名前、画面、自動確認のID、フローの
ファイル名）は route.py が既に計算したもので、**手で写すとタイポの余地ができる。**
撮影の名前と manifest の `src` がずれても、走らせるまで誰も気づかない。

`desc` / `result` / `note` は手順2で埋める。`result` を `PENDING` で置くのは、
build_report.py が result の無いセクションを拒むため（判定していない項目が
黙って OK で出ないように）。

**同じ場所に既にマニフェストがあれば、判定の欄（`desc` / `note` / `result`）を
引き継ぐ。** 引き継ぎのキーは証跡の名前。`title` / `expect` は plan が正で、
直すなら plan を直して叩き直す。

**実行時に決めた値（`inputs`）も、同じ変数名のものは引き継ぐ。** 撮り直し
（`run_flows.py --only`）は手前の項目をなぞるので、前に撮ったときの値が要る。
消すと、手前の項目の判断を撮り直しのたびにやり直すことになる。plan を直して
変数が変わった（別の入力欄になった）ものは空に戻す。引き継いだものは出力に出す
— データが変わっていれば古い値で走るので、見て直せるように。
"""
import json
import sys
from pathlib import Path

import route   # 同じディレクトリ。経路の計算とフローの書き出し
import simulators


LABELS = {"iphone": "iPhone", "ipad": "iPad"}
FLOWS = Path(__file__).resolve().parents[1] / ".work" / "flows"   # スキル側の作業ディレクトリ


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    if len(argv) < 2:
        sys.exit(__doc__)
    plan_path, out_dir = Path(argv[0]).expanduser(), Path(argv[1]).expanduser()
    repo, devices = None, []
    i = 2
    while i < len(argv):
        if argv[i] == "--repo":
            repo = argv[i + 1]; i += 2
        elif argv[i] == "--device":
            name, _, udid = argv[i + 1].partition("=")
            if not udid:
                sys.exit(f"--device は <端末>=<UDID> で渡す: {argv[i + 1]}")
            devices.append((name, udid)); i += 2
        else:
            sys.exit("知らない引数: " + argv[i] + "（題と meta は build_report.py に渡す）")
    if not repo:
        sys.exit("--repo <アプリのリポジトリ> が要る")
    if not devices:
        sys.exit("--device <端末>=<UDID> が要る（iphone / ipad。複数の端末で撮るなら並べる）")
    names = [n for n, _ in devices]
    if len(set(names)) != len(names):
        sys.exit("--device の端末が重なっている: " + ", ".join(names))
    # 機種名と OS は UDID から引く。撮った端末として、確認環境にそのまま出る
    info = {}
    for n, u in devices:
        sim = simulators.lookup(u)
        info[n] = {"udid": u, "model": sim["model"], "os": sim["os"]}
    devices = names

    plan = route.load_plan(plan_path)
    items, explore = plan.get("items") or [], plan.get("explore") or []

    # フローは端末によらず1組。撮影先は ${SHOTS} のままで、run_flows.py が端末ごとに埋める。
    # **置き場はスキル側の .work に固定する。** 呼ぶ側のカレント（アプリのリポジトリ）に
    # 作ると、誰も片付けない。スキル側なら maestrod.py sweep が古いものを消す
    flows = FLOWS / out_dir.resolve().name
    rows = route.write_flows(plan, flows, repo) if items else []
    if items:
        print()

    out = out_dir / "manifest.json"

    # 判定の欄は、作り直しても消さない。footer など判定側が足した欄も残す
    kept, top = {}, {}
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
            kept = {sec.get("name"): sec for sec in old.get("sections", []) if sec.get("name")}
            top = {k: v for k, v in old.items()
                   if k not in ("sections", "title", "meta", "resume")}
        except Exception:
            pass

    # フローのある行 ＋ 探索のぶん。探索は末尾に積む
    entries = list(rows) + [{"name": route.shot_name(len(items) + n),
                             "title": it.get("title", ""), "from": it.get("from"),
                             "fresh": bool(it.get("fresh")), "do": it.get("do") or [],
                             "expect": it.get("expect", "")}
                            for n, it in enumerate(explore, 1)]

    sections = []
    for e in entries:
        name = e["name"]
        prev = kept.get(name, {})
        sections.append({
            "name": name,                      # 証跡・ダンプ・フローのファイル名。引き継ぎの鍵
            "title": e.get("title", ""),       # 確認項目。plan が正
            "from": e.get("from"),             # 操作を始める画面（plan）
            "fresh": e.get("fresh", False),    # 起動し直した直後から始める（plan）
            "do": e.get("do", []),             # 確かめる操作（plan）。レビューで読み上げる
            "screen": e.get("screen"),         # 撮った画面（経路の計算）。探索は撮るまで決まらない
            "expect": e.get("expect", ""),     # 証跡の中で何を確かめるか。plan が正
            "checked": e.get("checked"),       # None なら証跡だけが根拠
            "launch": e.get("launch"),         # true なら、ここでアプリを起動し直す
            "pre_flow": e.get("pre_flow"),     # 値を決める操作の手前まで。先に走らせる
            "flow": e.get("flow"),             # 全端末で同じフロー
            # 実行時に決める値の入る先（selector / text）。端末によらない。run_flows.py が
            # これを見て、セレクタに入る値だけ正規表現としてエスケープする
            "input_use": dict(e.get("input_use") or {}),
            # 実行時に決める値は端末ごと（その端末の画面を見て決める）
            "devices": {d: {"inputs": {k: ((prev.get("devices") or {}).get(d) or {})
                                           .get("inputs", {}).get(k, "")
                                       for k in (e.get("inputs") or {})}}
                        for d in devices},
            # 証跡は端末ごとに1枚。ダンプは同名の .txt
            "images": [{"src": f"shots/{d}/{name}.png", "label": LABELS.get(d, d)}
                       for d in devices],
            "desc": prev.get("desc", ""),
            "note": prev.get("note", ""),      # この項目だけの但し書き。判定で埋める
            "result": prev.get("result", "PENDING"),
        })
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(top, devices=info, flows=str(flows), sections=sections),
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    carried = []
    for sec in sections:
        for d, dev in sec["devices"].items():
            kept_inputs = {k: v for k, v in dev["inputs"].items() if v}
            if kept_inputs:
                carried.append("{} {}: {}".format(
                    sec["name"], d, ", ".join(f"{k}={v}" for k, v in kept_inputs.items())))
    blank = sum(1 for s in sections if not s["flow"])
    empty = [s["name"] for s in sections if not s["title"] or not s["expect"]]
    print(out)
    print(f"  {len(sections)}セクション × {len(devices)}端末")
    for n in devices:
        print(f"    {n}: {info[n]['model']} ({info[n]['os']})  {info[n]['udid']}")
    if empty:
        print(f"  title か expect が空: {', '.join(empty)}。plan.json を埋めて叩き直す")
    if carried:
        print("  前のマニフェストから引き継いだ実行時の値（データが変わっていれば直す）:")
        for c in carried:
            print("    " + c)
    nochk = sum(1 for s in sections if s["flow"] and not s["checked"])
    if nochk:
        print(f"  {nochk}件はフローに自動確認が無い（証跡だけが根拠）")
    if blank:
        print(f"  {blank}件はフローが無い（探索で撮る。sim-driver に渡す）")

main()
