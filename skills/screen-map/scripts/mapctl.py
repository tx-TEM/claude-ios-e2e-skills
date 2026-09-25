#!/usr/bin/env python3
"""画面マップ（screen-map/）を引く・確かめる CLI。

  mapctl.py screens                    画面の一覧（id / 呼び名 / できること）
  mapctl.py which <パス...>            変更したファイルから対象画面を引く（`-` で標準入力）
  mapctl.py check                      マップ全体の自己テスト

  --repo <dir>       アプリのリポジトリ。画面マップはその下の screen-map/。必ず渡す

**画面マップを、フローを作る以外の目的で読むための道具。** 使うのは2者。

  テストケースを考える（sim-test-report の test-case-builder）
      which で差分から対象の画面を引き、screens で機能名から画面を探す。check の鮮度で、
      その画面のマップを信用してよいか（ソースを読むべきか）を決める
  マップを作ったあとの検査（screen-map スキルの手順5、migrate_map.py のあと）
      check で、書いたマップが経路として成り立つかを確かめる。テストケースとは関係ない

  フローを作って走らせる流れ（manifest.py → run_flows.py）はこれを通らない。

**ここにあるのは CLI だけ。** 中身は screenmap/ にある（一覧は screenmap/__init__.py）。

**経路もフローもここでは組まない。** 画面マップから経路を組み、項目ごとのフローを書くのは
sim-test-report の `manifest.py`（中身は flowgen/）。plan の形は `manifest.py --help`。

**どの画面が目標かを決めるのはここの仕事ではない。** `screens` が出すのは
一覧で、絞るのは読む側。ここにキーワード一致を足さない。**文字列の一致は
`summary` を読む判断より粗いのに、決定的なツールの見た目をまとう。**
柔らかい判断は柔らかいまま人のレビューに出す（sim-test-report の手順0）。
"""
import sys

from screenmap.check import cmd_check
from screenmap.map import load_map


def cmd_screens(mp):
    for sid in sorted(mp.screens):
        s = mp.screens[sid]
        mark = " [stub]" if s.stub else ""
        names = s.names
        print("{}{}".format(sid, mark))
        if names:
            print("    呼び名: {}".format(" / ".join(str(n) for n in names)))
        print("    {}".format(s.summary or "(summary 無し)"))


def cmd_which(mp, paths):
    """変更したファイルが、どの画面のものかを引く。

    **コードを開かずに対象画面を決めるための入り口。** 画面が決まれば
    `actions` と `result` に期待動作が書いてあるので、そこから確認項目を
    立てられる。マップを作った目的の半分はこれ。

    **当たらなかったファイルを出すのが、もう半分の仕事。** `files` は
    網羅ではない（共有コンポーネントは載せない決まりだし、新しく足した
    画面はまだマップに無い）。当たらなかったことを「無関係」と読むと
    確認が漏れるので、**そこはコードを読む必要がある**と名指しする。
    """
    owner = {}
    for sid in sorted(mp.screens):
        for f in mp.screens[sid].files:
            owner.setdefault(str(f), []).append(sid)
    # 完全一致で引けなければファイル名で引く。リポジトリ相対かどうかの
    # 食い違いで黙って0件になる方が怖い
    by_base = {}
    for f, sids in owner.items():
        by_base.setdefault(f.rsplit("/", 1)[-1], []).append((f, sids))

    hit, miss = {}, []
    for path in paths:
        sids = owner.get(path)
        how = ""
        if sids is None:
            cands = by_base.get(path.rsplit("/", 1)[-1] or path)
            if cands and len(cands) == 1:
                sids, how = cands[0][1], "（ファイル名で一致）"
        if sids:
            for sid in sids:
                hit.setdefault(sid, []).append(path + how)
        else:
            miss.append(path)

    for sid in sorted(hit):
        print("{}  — {}".format(sid, mp.screens[sid].summary or ""))
        for f in hit[sid]:
            print("    " + f)
    if not hit:
        print("どの画面にも当たらなかった")
    if miss:
        print("\nどの画面にも載っていない（マップでは決められない）")
        for f in miss:
            print("    " + f)
        print("\n共有コンポーネント、モデル、API層は `files` に載せない決まりなので、"
              "ここに出る。\nまだマップに無い画面のファイルもここに出る。"
              "**当たらなかったことを「無関係」と読まない。**")
    return 0


def main():
    argv = sys.argv[1:]
    # 使い方を訊かれたのは失敗ではない。標準出力に出して 0 で終わる
    # （`&&` で繋いだ先が続けられるように）。引数が無いのは失敗なので 1
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    if not argv:
        sys.exit(__doc__)
    cmd, argv = argv[0], argv[1:]

    repo, rest = None, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--repo":
            repo = argv[i + 1]; i += 2
        elif a.startswith("--"):
            sys.exit("知らない引数: " + a + "（--help）")
        else:
            rest.append(a); i += 1
    mp = load_map(repo)

    if cmd == "screens":
        return cmd_screens(mp)
    if cmd == "which":
        if not rest:
            sys.exit("ファイルのパスが要る（`git diff --name-only ...` の出力、"
                     "または `-` で標準入力）")
        paths = rest
        if rest == ["-"]:
            paths = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
        sys.exit(cmd_which(mp, paths))
    if cmd == "check":
        sys.exit(cmd_check(mp))
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
