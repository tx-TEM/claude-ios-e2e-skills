#!/usr/bin/env python3
"""マニフェストの項目を引く。

  manifest_items.py <manifest.json>              全項目を順に1行ずつ（名前・テストケース・題）
  manifest_items.py <manifest.json> test_05 […]  その項目を、属するテストケースと一緒に JSON で

**マニフェストはテストケースの入れ子**（`cases` → `items`）で、項目の中身はテストケースの
下にある。証跡1枚＝1項目の単位で引きたい側（run_flows.py の `--only`、build_report.py の
カード、evidence-judge / retaker / sim-driver）は、ここの `walk()` と `find()` を通す。
属するテストケースは、項目の中に題の文字列で持たせず、入れ子の親として返す。

名前で引いて出すときは、テストケースの `items` を省く（ほかの項目の中身まで出さない）。
同じテストケースのほかの項目の名前は `siblings` に並べる — 期待に出てくる前の項目の
証跡を読むときに使う。
"""
import json
import sys
from pathlib import Path


def walk(manifest):
    """全項目を並び順に。[(テストケース, 項目)]。"""
    return [(case, item) for case in manifest.get("cases") or [] for item in case.get("items") or []]


def find(manifest, name):
    """名前で1項目を引く。(テストケース, 項目)。無ければ (None, None)。"""
    return next(((c, it) for c, it in walk(manifest) if it.get("name") == name), (None, None))


def names(manifest):
    return [it.get("name") for _, it in walk(manifest)]


def main():
    argv = sys.argv[1:]
    if not argv or "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0 if argv else 1)
    manifest = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    if "sections" in manifest:
        sys.exit("古いマニフェスト（sections が平らに並んでいる）。manifest.py で作り直す")
    if len(argv) == 1:
        for case, item in walk(manifest):
            print("\t".join([item.get("name", ""), case.get("title", ""), item.get("title", "")]))
        return
    out, missing = [], []
    for n in argv[1:]:
        case, item = find(manifest, n)
        if item is None:
            missing.append(n)
            continue
        head = {k: v for k, v in case.items() if k != "items"}
        head["siblings"] = [it.get("name") for it in case.get("items") or []]
        out.append({"case": head, "item": item})
    if missing:
        sys.exit("{} という項目が無い。ある名前: {}".format(", ".join(missing), ", ".join(names(manifest))))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
