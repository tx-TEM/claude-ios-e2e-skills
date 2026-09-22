#!/usr/bin/env python3
"""route.py が出したフローの一覧から manifest.json の骨組みを作る。

  manifest.py <フローのディレクトリ> <出力先ディレクトリ>
              [--title <題>] [--meta <行>]... [--explore <証跡の名前>]...

`route.py --out-dir` が置いた `index.json` を読み、証跡1枚＝1セクションの manifest を書く。
**一覧から導ける欄だけ埋め、判断が要る欄（`title` / `expect`）は空で残す。**
呼ぶ側（test-case-builder の項目）がそこを書く。

`route.py flow --out-dir` は1回の実行ぶんのフロー一式を書くので、**マニフェストも1つ。**
ディレクトリを渡せばその中の `index.json` を読む。

**`launch` をそのまま持ってくる。** そのフローが自分でアプリを起動するかどうかで、
**鎖の切れ目**を表す（1本目と `--restart` の直後が `true`）。`run_flows.py` は
落ちたときにどこまで諦めるかをこれで決め、レビューと判定は**そこでアプリが
起動し直ることを知らないと証跡を読み違える**（前の項目の状態が続いているのか、
まっさらなのか）。

`--explore` は**経路が組めなかった項目**。一覧に無いので、名前だけ渡して
セクションを足す。`flow` を持たないので `run_flows.py` は飛ばし、sim-driver が
探索で撮る。**末尾に並ぶ** — 機械判定の付かない項目がまとまる。

なぜスクリプトなのか。一覧の中身（証跡の名前、画面、機械判定のID、フローの
ファイル名）は route.py が既に計算したもので、**手で写すとタイポの余地ができる。**
`--shot` の名前と manifest の `src` がずれても、走らせるまで誰も気づかない。

`desc` と `result` は手順2で埋める。`result` を `未判定` で置くのは、
build_report.py が result の無いセクションを拒むため（判定していない項目が
黙って OK で出ないように）。

**同じ場所に既にマニフェストがあれば、人が書いた欄を引き継ぐ。** レビューで
導線が変わればフローを作り直すことになり、そのたびに title と expect が
消えるのでは使えない。引き継ぎのキーは証跡の名前。
"""
import json
import sys
from pathlib import Path


def read_index(src):
    """`route.py --out-dir` の index.json を読む。ディレクトリでも中を見る。"""
    src = Path(src)
    if src.is_dir():
        src = src / "index.json"
    if not src.is_file():
        sys.exit(f"フローの一覧が無い: {src}")
    return json.loads(src.read_text(encoding="utf-8"))


def main():
    argv = sys.argv[1:]
    if len(argv) < 2:
        sys.exit(__doc__)
    index_path, out_dir = argv[0], argv[1]
    title, meta, explore = "動作確認レポート", [], []
    i = 2
    while i < len(argv):
        if argv[i] == "--title":
            title = argv[i + 1]; i += 2
        elif argv[i] == "--meta":
            meta.append(argv[i + 1]); i += 2
        elif argv[i] == "--explore":
            explore.append(argv[i + 1]); i += 2
        else:
            sys.exit("知らない引数: " + argv[i])

    entries = read_index(index_path)
    out = Path(out_dir) / "manifest.json"

    # 人が書いた欄は、作り直しても消さない
    kept = {}
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
            kept = {sec.get("name"): sec for sec in old.get("sections", []) if sec.get("name")}
            title = old.get("title", title) if title == "動作確認レポート" else title
            meta = meta or old.get("meta", [])
        except Exception:
            pass

    # 一覧のぶん（フローあり）＋ 探索のぶん。探索は末尾に積む
    entries = list(entries) + [{"name": n} for n in explore]

    sections, carried = [], 0
    for e in entries:
        name = e["name"]
        prev = kept.get(name, {})
        carried += 1 if (prev.get("title") or prev.get("expect")) else 0
        sections.append({
            "name": name,                      # 引き継ぎと突き合わせのキー
            "title": prev.get("title", ""),    # 確認項目。一覧からは導けない
            "screen": e.get("screen"),
            "expect": prev.get("expect", ""),  # 渡したデータで何が起きるか。同上
            "checked": e.get("checked"),       # None なら証跡だけが根拠
            "launch": e.get("launch"),         # true なら、ここでアプリを起動し直す
            "flow": e.get("flow"),
            "images": [{"src": f"shots/{name}.png"}],
            "dump": f"shots/{name}.txt",
            "desc": prev.get("desc", ""),
            "result": prev.get("result", "未判定"),
        })
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"title": title, "meta": meta, "sections": sections},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    blank = sum(1 for s in sections if not s["flow"])
    empty = sum(1 for s in sections if not s["title"] or not s["expect"])
    print(out)
    print(f"  {len(sections)}セクション。")
    if carried:
        print(f"  {carried}件は前のマニフェストから title / expect を引き継いだ")
    if empty:
        print(f"  {empty}件は title か expect が空。埋める")
    nochk = sum(1 for s in sections if s["flow"] and not s["checked"])
    if nochk:
        print(f"  {nochk}件はフローに機械判定が無い（証跡だけが根拠）")
    if blank:
        print(f"  {blank}件はフローが無い（探索で撮る。sim-driver に渡す）")


main()
