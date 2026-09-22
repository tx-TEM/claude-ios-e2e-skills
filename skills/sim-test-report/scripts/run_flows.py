#!/usr/bin/env python3
"""マニフェストに載っているフローを順に走らせ、証跡と同名のダンプを撮る。

  run_flows.py <manifest.json> <フローのディレクトリ> <UDID>

`flow` を持つセクションだけを対象にする。持たないセクション（経路が組めず探索で
撮るもの）は飛ばすので、**そちらは sim-driver に任せる。**

**フローを一息に走らせる。** 続きのフローは前のフローが終わった画面から始まるので、
間にアプリの画面を動かすものが入ると次の到達判定が落ちる。ここが最後まで
走り切ってから sim-driver に渡せば、そうならない。マニフェストの並びに探索の
セクションが混ざっていてもよい（実行しないだけ）。

やることは1セクションにつき2つだけ。

  maestrod.py run     <UDID> @<フロー> <名前> <出力先>/shots  操作して撮る
  maestrod.py inspect <UDID> <名前> <出力先>/shots  同名のダンプを置く

**これをモデルに打たせない。** 導線はフローの中にあり、座標の判断も要らないので、
打つ以外の仕事が無い。人（モデル）が組み立てると書式が崩れ、打ち間違いの余地が
残る。進捗ログもここで固定の書式で書く。

**落ちたら、次に起動し直すフローまで飛ばす。** 続きのフロー（`launch` が偽）は
前のフローが終わった画面から始まるので、1本落ちたあとを走らせても意味がない。
**`launch` が真のフローからは走らせる** — 自分で `stopApp` / `launchApp` するので、
前が落ちたことに影響されない。1回で取れる証跡は取っておいたほうが、直して
走らせ直すときの手がかりが増える。落ちた地点の画面は maestrod.py run が出す。
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAESTROD = HERE / "maestrod.py"


def sh(args, quiet=True):
    """出力は捨てる。ダンプを読むのは判定する側で、ここではない。

    `quiet=False` でも、**落ちたときだけ**出す。通ったフローの `OK {...}` は
    1本ごとに出ると読むものが増えるだけで、判断には使わない。
    """
    r = subprocess.run([sys.executable, str(MAESTROD)] + args,
                       capture_output=True, text=True)
    if r.returncode != 0 and not quiet:
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
    return r.returncode


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    manifest_path, flow_dir, udid = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out = manifest_path.parent
    shots, log = out / "shots", out / "progress.log"
    shots.mkdir(parents=True, exist_ok=True)

    targets = [(i, s) for i, s in enumerate(manifest["sections"], 1) if s.get("flow")]
    skipped = [s.get("name") for s in manifest["sections"] if not s.get("flow")]
    if not targets:
        sys.exit("flow を持つセクションが無い。全部探索なので sim-driver に渡す。")

    done, lost, broken = 0, [], False
    for i, sec in targets:
        name = sec["name"]
        line = f"{i:02d} {name}"
        if sec.get("launch"):
            broken = False          # ここから鎖が切り替わる
        if broken:
            lost.append(line)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{line} 撮影できず 続きなので、前のフローの失敗で飛ばした\n")
            continue
        flow = flow_dir / sec["flow"]
        if not flow.exists():
            sys.exit(f"{i:02d} フローが無い: {flow}")
        # 落ちたら、その run をもう一度は走らせない。1回目の出力をそのまま見せる
        if sh(["run", udid, "@" + str(flow), name, str(shots)], quiet=False) != 0:
            broken = True
            lost.append(line)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{line} 撮影できず フローが失敗\n")
            print(f"{line} 失敗。次に起動し直すフローまで飛ばす", file=sys.stderr)
            continue
        sh(["inspect", udid, name, str(shots)])   # 出力は捨てる
        done += 1
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{line} 撮影済み\n")
        print(line + " 撮影済み")

    print(f"\n{done}件を撮った: {shots}")
    if skipped:
        print(f"飛ばした（フローが無い。探索で撮る）: {', '.join(x or '?' for x in skipped)}")
    if lost:
        print(f"\n撮れなかった {len(lost)}件:", file=sys.stderr)
        for line in lost:
            print("  " + line, file=sys.stderr)
        sys.exit(1)


main()
