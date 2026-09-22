#!/usr/bin/env python3
"""マニフェストに載っているフローを順に走らせ、証跡と同名のダンプを撮る。

  run_flows.py <manifest.json> <フローのディレクトリ> <UDID> [--from <証跡の名前>]

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

**撮った項目の判定は捨てる。** 画像とダンプが入れ替わった以上、前の `desc` は
別の証跡についての文章になる。残すと、古い説明が新しい画像の隣で `OK` のまま
レポートに出る。`build_report.py` は `PENDING` を弾くので、そこで止まる。
初回は元から `PENDING` なので何も起きない。

**実行時に決める値があるフローは2本に割れている。** 前半（`pre_flow`）が目的の
画面まで運び、後半が値を使う。**前半を走らせてから止まる** — 着いていないと、
値を決めるために画面を見ることができない。再開のときは前半を飛ばす（もうそこに居る）。

**値はフローに書き戻さない。** マニフェストの `inputs` を走らせる直前に env へ
入れる。フローに残すと、次の実行で「もう埋まっている」ことになり、データが
変わっても古い値で走る。

**`--from` はそこから再開する。** 入力を埋めたあとに使う。**前を撮り直さない** —
止まった時点でアプリはその画面に居るので、続きのフローはそのまま走る。手前から
やり直すと、撮れている証跡を捨てて撮り直すことになる。

**入力が埋まっていないセクションは走らせない。** `inputs` に空の値があるものは、
着いた画面を見ないと打つ文字が決まらない。そこは sim-driver が画面を見て埋めて
から走らせる。**その鎖の残りも飛ばす。**

**落ちたら、次に起動し直すフローまで飛ばす。** 続きのフロー（`launch` が偽）は
前のフローが終わった画面から始まるので、1本落ちたあとを走らせても意味がない。
**`launch` が真のフローからは走らせる** — 自分で `stopApp` / `launchApp` するので、
前が落ちたことに影響されない。1回で取れる証跡は取っておいたほうが、直して
走らせ直すときの手がかりが増える。落ちた地点の画面は maestrod.py run が出す。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAESTROD = HERE / "maestrod.py"


def required_env(body):
    """フローが要求している env の名前。`---` より前の env: ブロックから拾う。"""
    head = body.split("\n---", 1)[0].splitlines()
    out, inside = [], False
    for line in head:
        if re.match(r"^env:\s*$", line):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+([A-Za-z_][A-Za-z0-9_]*):", line)
            if not m:
                break
            out.append(m.group(1))
    return out


def yaml_quote(v):
    """env の値をシングルクォートで包む。正規表現の `\\` を素通しするため。"""
    return "'" + str(v).replace("'", "''") + "'"


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
    argv = sys.argv[1:]
    resume = None
    if "--from" in argv:
        i = argv.index("--from")
        resume = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if len(argv) < 3:
        sys.exit(__doc__)
    manifest_path, flow_dir, udid = Path(argv[0]), Path(argv[1]), argv[2]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out = manifest_path.parent
    shots, log = out / "shots", out / "progress.log"
    shots.mkdir(parents=True, exist_ok=True)

    targets = [(i, s) for i, s in enumerate(manifest["sections"], 1) if s.get("flow")]
    if resume:
        names = [s["name"] for _, s in targets]
        if resume not in names:
            sys.exit(f"--from の証跡 {resume} が見つからない。ある名前: {', '.join(names)}")
        targets = targets[names.index(resume):]
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
        # 埋まっていない入力があるセクションは走らせない。着いた画面を見ないと
        # 値が決まらない。**その鎖の残りも飛ばす**（続きのフローは、この
        # セクションが終わった画面から始まるため）。
        #
        # **止まった時点でアプリはその画面に居る。** 呼ぶ側はそれを見て値を決め、
        # `--from` で再開する。手前を撮り直さないので、鎖は一息のまま。
        # 値を使う操作の手前までを先に走らせる。**決めるには着いていないといけない。**
        # 再開のときは飛ばす（前の実行で走っていて、アプリはもうそこに居る）。
        if sec.get("pre_flow") and not (resume and name == resume):
            pre = flow_dir / sec["pre_flow"]
            if not pre.exists():
                sys.exit(f"{i:02d} 前半のフローが無い: {pre}")
            if sh(["run", udid, "@" + str(pre), name + ".pre", str(shots)], quiet=False) != 0:
                broken = True
                lost.append(line)
                with log.open("a", encoding="utf-8") as f:
                    f.write(f"{line} 撮影できず 前半のフローが失敗\n")
                print(f"{line} 前半で失敗。次に起動し直すフローまで飛ばす", file=sys.stderr)
                continue

        flow = flow_dir / sec["flow"]
        if not flow.exists():
            sys.exit(f"{i:02d} フローが無い: {flow}")
        body = flow.read_text(encoding="utf-8")
        need = required_env(body)
        undecided = [k for k in need if not (sec.get("inputs") or {}).get(k)]
        if undecided:
            broken = True
            lost.append(line)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{line} 撮影せず 入力が未定（{', '.join(undecided)}）\n")
            print(f"{line} 入力が未定（{', '.join(undecided)}）。いまこの画面に居るので、"
                  f"見て埋めてから --from {name} で再開する", file=sys.stderr)
            continue

        target = "@" + str(flow)
        for k in need:
            body = re.sub(r"^(\s*{}:\s*).*$".format(re.escape(k)),
                          lambda m, v=sec["inputs"][k]: m.group(1) + yaml_quote(v),
                          body, count=1, flags=re.M)
            target = body

        # 落ちたら、その run をもう一度は走らせない。1回目の出力をそのまま見せる
        if sh(["run", udid, target, name, str(shots)], quiet=False) != 0:
            broken = True
            lost.append(line)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{line} 撮影できず フローが失敗\n")
            print(f"{line} 失敗。次に起動し直すフローまで飛ばす", file=sys.stderr)
            continue
        sh(["inspect", udid, name, str(shots)])   # 出力は捨てる
        sec["desc"], sec["result"] = "", "PENDING"   # 証跡が入れ替わったので判定も捨てる
        done += 1
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{line} 撮影済み\n")
        print(line + " 撮影済み")

    if done:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n{done}件を撮った: {shots}")
    if skipped:
        print(f"飛ばした（フローが無い。探索で撮る）: {', '.join(x or '?' for x in skipped)}")
    if lost:
        print(f"\n撮れなかった {len(lost)}件:", file=sys.stderr)
        for line in lost:
            print("  " + line, file=sys.stderr)
        sys.exit(1)


main()
