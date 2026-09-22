#!/usr/bin/env python3
"""route.py の索引から manifest.json の骨組みを作る。

  manifest.py <索引.json> <出力先ディレクトリ> [--title <題>] [--meta <行>]...

`route.py --out-dir` が置いた `index.json` を読み、証跡1枚＝1セクションの manifest を書く。
**索引から導ける欄だけ埋め、判断が要る欄（`title` / `expect`）は空で残す。**
呼ぶ側（test-case-builder の項目）がそこを書く。

なぜスクリプトなのか。索引の中身（証跡の名前、画面、機械判定のID、フローの
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


def main():
    argv = sys.argv[1:]
    if len(argv) < 2:
        sys.exit(__doc__)
    index_path, out_dir = argv[0], argv[1]
    title, meta = "動作確認レポート", []
    i = 2
    while i < len(argv):
        if argv[i] == "--title":
            title = argv[i + 1]; i += 2
        elif argv[i] == "--meta":
            meta.append(argv[i + 1]); i += 2
        else:
            sys.exit("知らない引数: " + argv[i])

    entries = json.loads(Path(index_path).read_text(encoding="utf-8"))
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

    sections, carried = [], 0
    for e in entries:
        name = e["name"]
        prev = kept.get(name, {})
        carried += 1 if (prev.get("title") or prev.get("expect")) else 0
        sections.append({
            "name": name,                      # 引き継ぎと突き合わせのキー
            "title": prev.get("title", ""),    # 確認項目。索引からは導けない
            "screen": e.get("screen"),
            "expect": prev.get("expect", ""),  # 渡したデータで何が起きるか。同上
            "checked": e.get("checked"),       # None なら証跡だけが根拠
            "flow": e.get("flow"),
            "images": [{"src": f"shots/{name}.png"}],
            "dump": f"shots/{name}.txt",
            "desc": prev.get("desc", ""),
            "result": prev.get("result", "未判定"),
        })
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"title": title, "meta": meta, "sections": sections},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    blank = sum(1 for s in sections if not s["checked"])
    empty = sum(1 for s in sections if not s["title"] or not s["expect"])
    print(out)
    print(f"  {len(sections)}セクション。")
    if carried:
        print(f"  {carried}件は前のマニフェストから title / expect を引き継いだ")
    if empty:
        print(f"  {empty}件は title か expect が空。埋める")
    if blank:
        print(f"  {blank}件は機械判定なし（証跡だけが根拠）")


main()
