#!/usr/bin/env python3
"""test-case-builder の plan.json から、フローを書いて manifest.json を作る。

  manifest.py <plan.json> <出力先ディレクトリ> [--map <アプリのリポジトリ>]

1. `route.py flow --plan <plan.json> --out-dir <plan.json のディレクトリ>` を叩く。
   経路が組めなければ route.py の理由をそのまま出して止まる（manifest は書かない）。
   組めたら route.py の標準出力（レビューの2段目になる操作列）をそのまま出す
2. plan.json と、route.py が置いた index.json から manifest.json を組む。証跡1枚＝1セクション

**1本で叩く。** route.py と manifest を別々に叩くと、plan を直したときに片方だけ
叩き直す余地ができ、どちらの項目がどの証跡か決まらなくなる。

**どちらからも写さない。** 証跡の名前・撮った画面・機械判定のID・フローのファイル名は
`index.json` から、`title` / `expect` / `from` は `plan.json` から取る。

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

plan の `explore` は**経路が組めなかった項目**。`flow` を持たないので `run_flows.py` は
飛ばし、sim-driver が探索で撮る。**末尾に並ぶ** — 機械判定の付かない項目がまとまる。

なぜスクリプトなのか。一覧の中身（証跡の名前、画面、機械判定のID、フローの
ファイル名）は route.py が既に計算したもので、**手で写すとタイポの余地ができる。**
plan の `shot` の名前と manifest の `src` がずれても、走らせるまで誰も気づかない。

`desc` / `result` / `note` は手順2で埋める。`result` を `PENDING` で置くのは、
build_report.py が result の無いセクションを拒むため（判定していない項目が
黙って OK で出ないように）。

**同じ場所に既にマニフェストがあれば、判定の欄（`desc` / `note` / `result`）を
引き継ぐ。** 引き継ぎのキーは証跡の名前。`title` / `expect` は plan が正で、
直すなら plan を直して叩き直す。
"""
import json
import subprocess
import sys
from pathlib import Path


def read_json(path, what):
    if not path.is_file():
        sys.exit(f"{what}が無い: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# screen-map スキルの route.py。skills/ の下で隣り合っている（install.sh のリンクをたどっても同じ）
ROUTE_PY = Path(__file__).resolve().parents[2] / "screen-map" / "scripts" / "route.py"


def main():
    argv = sys.argv[1:]
    if len(argv) < 2:
        sys.exit(__doc__)
    plan_path, out_dir = Path(argv[0]).expanduser(), argv[1]
    mapdir = None
    i = 2
    while i < len(argv):
        if argv[i] == "--map":
            mapdir = argv[i + 1]; i += 2
        else:
            sys.exit("知らない引数: " + argv[i] + "（題と meta は build_report.py に渡す）")

    plan = read_json(plan_path, "テストケース（test-case-builder の plan.json）")
    items, explore = plan.get("items") or [], plan.get("explore") or []
    src = plan_path.parent

    # 経路が組めた項目が1つも無ければ route.py は要らない（全部探索で撮る）
    if items:
        cmd = [sys.executable, str(ROUTE_PY), "flow", "--plan", str(plan_path),
               "--out-dir", str(src)]
        if mapdir:
            cmd += ["--map", mapdir]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            sys.exit(r.returncode)
        print()
    index = read_json(src / "index.json", "フローの一覧（route.py flow --out-dir の index.json）") \
        if items else []

    planned = [it.get("shot") for it in items]
    listed = [e["name"] for e in index]
    if planned != listed:
        sys.exit("plan.json の items と index.json の並びが合わない\n"
                 f"  plan:  {planned}\n  index: {listed}")

    out = Path(out_dir) / "manifest.json"

    # 判定の欄は、作り直しても消さない。footer など判定側が足した欄も残す
    kept, top = {}, {}
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
            kept = {sec.get("name"): sec for sec in old.get("sections", []) if sec.get("name")}
            top = {k: v for k, v in old.items() if k not in ("sections", "title", "meta")}
        except Exception:
            pass

    # 一覧のぶん（フローあり）＋ 探索のぶん。探索は末尾に積む
    entries = [(e, it) for e, it in zip(index, items)] + \
              [({"name": it.get("shot")}, it) for it in explore]

    # route.py はフローの中でしか重複を見られない。explore と衝突する余地が
    # 残るのでここでも弾く。同名だと後から撮ったほうが上書きし、
    # 2つの項目が同じ画像を指したまま通る。
    seen = {}
    for e, _ in entries:
        if not e["name"]:
            sys.exit("plan の explore に shot（証跡の名前）が無い項目がある")
        if e["name"] in seen:
            sys.exit(f"証跡の名前が重なっている: {e['name']}"
                     f"（{seen[e['name']]} と {e.get('flow') or '探索'}）")
        seen[e["name"]] = e.get("flow") or "探索"

    sections = []
    for e, it in entries:
        name = e["name"]
        prev = kept.get(name, {})
        sections.append({
            "name": name,                      # 引き継ぎと突き合わせのキー
            "title": it.get("title", ""),      # 確認項目。plan が正
            "from": it.get("from"),            # 操作を始める画面（plan）
            "screen": e.get("screen"),         # 撮った画面（route.py）。探索は撮るまで決まらない
            "expect": it.get("expect", ""),    # 証跡の中で何を確かめるか。同上
            "checked": e.get("checked"),       # None なら証跡だけが根拠
            "launch": e.get("launch"),         # true なら、ここでアプリを起動し直す
            "inputs": e.get("inputs") or {},    # 空の値があるうちは走らせられない
            "pre_flow": e.get("pre_flow"),      # 値を決める操作の手前まで。先に走らせる
            "flow": e.get("flow"),
            "images": [{"src": f"shots/{name}.png"}],
            "dump": f"shots/{name}.txt",
            "desc": prev.get("desc", ""),
            "note": prev.get("note", ""),      # この項目だけの但し書き。判定で埋める
            "result": prev.get("result", "PENDING"),
        })
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(top, sections=sections),
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    blank = sum(1 for s in sections if not s["flow"])
    empty = [s["name"] for s in sections if not s["title"] or not s["expect"]]
    print(out)
    print(f"  {len(sections)}セクション。")
    if empty:
        print(f"  title か expect が空: {', '.join(empty)}。plan.json を埋めて叩き直す")
    nochk = sum(1 for s in sections if s["flow"] and not s["checked"])
    if nochk:
        print(f"  {nochk}件はフローに機械判定が無い（証跡だけが根拠）")
    if blank:
        print(f"  {blank}件はフローが無い（探索で撮る。sim-driver に渡す）")


main()
