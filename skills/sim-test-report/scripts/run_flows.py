#!/usr/bin/env python3
"""マニフェストに載っているフローを順に走らせ、証跡と同名のダンプを撮る。

  run_flows.py <manifest.json> <フローのディレクトリ> --device <端末>=<UDID> [--device …]
  run_flows.py <manifest.json> <フローのディレクトリ> <UDID>      （端末が1つのマニフェスト）
  run_flows.py … --from <名前>                                    （端末を1つだけ渡すとき）

**1回で全端末を撮る。** 渡した順に1台ずつ撮り切ってから次の端末へ移る（Maestro の
デーモンが握れるのは1台で、行き来させると切り替えのたびに約10秒かかる）。フローは
全端末で同じで、撮影先の `${SHOTS}` にここで `<出力先>/shots/<端末>` を入れる。
進捗ログは `<出力先>/progress_<端末>.log`。実行時に決める値は端末ごと — その端末の
画面を見て決める。

**未定で止まったら、叩き直すと続きから走る。** 撮り終えた端末は飛ばし、止まった端末の
止まった項目から走る。状態はマニフェストの `resume` に書き、走り切ったら消す。

`flow` を持つセクションだけを対象にする。持たないセクション（経路が組めず探索で
撮るもの）は飛ばすので、**そちらは sim-driver に任せる。**

**フローを一息に走らせる。** 続きのフローは前のフローが終わった画面から始まるので、
間にアプリの画面を動かすものが入ると次の到達判定が落ちる。ここが最後まで
走り切ってから sim-driver に渡せば、そうならない。マニフェストの並びに探索の
セクションが混ざっていてもよい（実行しないだけ）。

やることは1セクションにつき2つだけ。

  maestrod.py run     <UDID> @<フロー> <名前> <出力先>/shots/<端末>  操作して撮る
  maestrod.py inspect <UDID> <名前> <出力先>/shots/<端末>  同名のダンプを置く

**これをモデルに打たせない。** 導線はフローの中にあり、座標の判断も要らないので、
打つ以外の仕事が無い。人（モデル）が組み立てると書式が崩れ、打ち間違いの余地が
残る。進捗ログもここで固定の書式で書く。

**撮った項目の判定は捨てる。** 画像とダンプが入れ替わった以上、前の `desc` と
`note` は別の証跡についての文章になる。残すと、古い説明が新しい画像の隣で `OK` のまま
レポートに出る。`build_report.py` は `PENDING` を弾くので、そこで止まる。
初回は元から `PENDING` なので何も起きない。

**実行時に決める値があるフローは2本に割れている。** 前半（`pre_flow`）が目的の
画面まで運び、後半が値を使う。**前半を走らせてから止まる** — 着いていないと、
値を決めるために画面を見ることができない。再開のときは前半を飛ばす（もうそこに居る）。

**値はフローに書き戻さない。** マニフェストの `devices.<端末>.inputs` を走らせる直前に env へ
入れる。フローに残すと、次の実行で「もう埋まっている」ことになり、データが
変わっても古い値で走る。


**続きから走るとき、前を撮り直さない** — 止まった時点でアプリはその画面に居るので、
続きのフローはそのまま走る。手前からやり直すと、撮れている証跡を捨てて撮り直すことになる。
`--from` は記録を無視して、1台の決まった項目から走らせたいときだけ使う。

**入力が未定ならそこで終える。** 落ちたときは次の鎖へ進むが、こちらは進めない
— 先へ走らせると画面が変わり、値を決めるために見ることができなくなる。

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


def save(manifest_path, manifest):
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")


def run_device(manifest, manifest_path, flow_dir, device, udid, resume):
    """1台ぶんのフローを走らせる。(止まったか, 撮った数, 撮れなかった行) を返す。

    入力が未定で止まったら、その項目の名前を返す（呼ぶ側が再開位置に書く）。
    """
    out = manifest_path.parent
    shots, log = out / "shots" / device, out / f"progress_{device}.log"
    shots.mkdir(parents=True, exist_ok=True)

    targets = [(i, s) for i, s in enumerate(manifest["sections"], 1) if s.get("flow")]
    if resume:
        names = [s["name"] for _, s in targets]
        if resume not in names:
            sys.exit(f"再開先 {resume} が見つからない。ある名前: {', '.join(names)}")
        targets = targets[names.index(resume):]

    print(f"--- {device}（{udid}）")
    done, lost, broken = 0, [], False
    for i, sec in targets:
        name = sec["name"]
        line = name
        if sec.get("launch"):
            broken = False          # ここから鎖が切り替わる
        if broken:
            lost.append(f"{device} {line}")
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{line} 撮影できず 続きなので、前のフローの失敗で飛ばした\n")
            continue
        # 値を使う操作の手前までを先に走らせる。**決めるには着いていないといけない。**
        # 再開のときは飛ばす（前の実行で走っていて、アプリはもうそこに居る）。
        if sec.get("pre_flow") and not (resume and name == resume):
            pre = flow_dir / sec["pre_flow"]
            if not pre.exists():
                sys.exit(f"{name} 前半のフローが無い: {pre}")
            if sh(["run", udid, "@" + str(pre), name + ".pre", str(shots)], quiet=False) != 0:
                broken = True
                lost.append(f"{device} {line}")
                with log.open("a", encoding="utf-8") as f:
                    f.write(f"{line} 撮影できず 前半のフローが失敗\n")
                print(f"{line} 前半で失敗。次に起動し直すフローまで飛ばす", file=sys.stderr)
                continue

        flow = flow_dir / sec["flow"]
        if not flow.exists():
            sys.exit(f"{name} フローが無い: {flow}")
        body = flow.read_text(encoding="utf-8")
        need = required_env(body)
        # 撮影先は端末で決まる。決めるのはそれ以外の値
        inputs = dict(sec["devices"][device].get("inputs") or {}, SHOTS=str(shots.resolve()))
        # **未定ならそこで止まる。** 落ちたときは次の鎖へ進むが、こちらは進めない。
        # 先へ走らせると画面が変わってしまい、値を決めるために見ることができない。
        # **止まった時点でアプリはその画面に居る。** 値を埋めて叩き直せば続きから走る。
        undecided = [k for k in need if not inputs.get(k)]
        if undecided:
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{line} 撮影せず 入力が未定（{', '.join(undecided)}）\n")
            rest = [n for n, _ in targets[targets.index((i, sec)):]]
            print(f"\n{device} {line} 入力が未定（{', '.join(undecided)}）。")
            print(f"いまこの画面に居る。見て {', '.join(undecided)} を決めて "
                  f"devices.{device}.inputs に書き、同じコマンドをもう一度叩けば続きから走る。")
            print(f"{device} のここから先の {len(rest)}件はまだ撮っていない。")
            return name, done, lost

        target = "@" + str(flow)
        for k in need:
            body = re.sub(r"^(\s*{}:\s*).*$".format(re.escape(k)),
                          lambda m, v=inputs[k]: m.group(1) + yaml_quote(v),
                          body, count=1, flags=re.M)
            target = body

        # 落ちたら、その run をもう一度は走らせない。1回目の出力をそのまま見せる
        if sh(["run", udid, target, name, str(shots)], quiet=False) != 0:
            broken = True
            lost.append(f"{device} {line}")
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{line} 撮影できず フローが失敗\n")
            print(f"{line} 失敗。次に起動し直すフローまで飛ばす", file=sys.stderr)
            continue
        sh(["inspect", udid, name, str(shots)])   # 出力は捨てる
        sec["desc"], sec["note"], sec["result"] = "", "", "PENDING"   # 証跡が入れ替わったので判定も捨てる
        done += 1
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{line} 撮影済み\n")
        print(line + " 撮影済み")
    print(f"{done}件を撮った: {shots}")
    return None, done, lost


def main():
    # 標準出力を行ごとに流す。既定のバッファのままだと、即時に出る標準エラーと
    # 混ざったときに順番が入れ替わり、失敗の行が撮影済みの行より前に出る
    sys.stdout.reconfigure(line_buffering=True)

    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    pairs, resume_arg, rest = [], None, []
    i = 0
    while i < len(argv):
        if argv[i] == "--device":
            d, _, u = argv[i + 1].partition("=")
            if not u:
                sys.exit(f"--device は <端末>=<UDID> で渡す: {argv[i + 1]}")
            pairs.append((d, u)); i += 2
        elif argv[i] == "--from":
            resume_arg = argv[i + 1]; i += 2
        elif argv[i].startswith("--"):
            sys.exit("知らない引数: " + argv[i])
        else:
            rest.append(argv[i]); i += 1
    if len(rest) not in (2, 3):
        sys.exit(__doc__)
    manifest_path, flow_dir = Path(rest[0]), Path(rest[1])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    known = list((manifest["sections"][0].get("devices") or {}) if manifest["sections"] else {})
    if len(rest) == 3:
        # UDID だけ渡す書き方は、端末が1つのマニフェストに限る
        if pairs or len(known) != 1:
            sys.exit(f"端末ごとに --device <端末>=<UDID> で渡す（このマニフェストの端末: {', '.join(known)}）")
        pairs = [(known[0], rest[2])]
    if not pairs:
        sys.exit(f"--device <端末>=<UDID> が要る（このマニフェストの端末: {', '.join(known)}）")
    for d, _ in pairs:
        if d not in known:
            sys.exit(f"端末 {d} はこのマニフェストに無い（ある端末: {', '.join(known)}）")
    if resume_arg and len(pairs) != 1:
        sys.exit("--from は端末を1つだけ渡すときに使う（どの端末のどこからか決まらない）")

    if not any(s.get("flow") for s in manifest["sections"]):
        sys.exit("flow を持つセクションが無い。全部探索なので sim-driver に渡す。")

    # 前の実行が未定で止まっていれば、撮り終えた端末は飛ばし、止まった端末の続きから走る
    state = manifest.get("resume") or {}
    finished = list(state.get("done") or [])
    total, lost_all = 0, []
    for d, u in pairs:
        if d in finished and not resume_arg:
            print(f"--- {d} は撮り終えている。飛ばす")
            continue
        resume = resume_arg or (state.get("from") if state.get("device") == d else None)
        stopped, done, lost = run_device(manifest, manifest_path, flow_dir, d, u, resume)
        total += done
        lost_all += lost
        if stopped:
            manifest["resume"] = {"done": finished, "device": d, "from": stopped}
            save(manifest_path, manifest)
            sys.exit(1)
        finished.append(d)
    manifest.pop("resume", None)
    save(manifest_path, manifest)

    skipped = [s.get("name") for s in manifest["sections"] if not s.get("flow")]
    print(f"\nこの実行で {total}件を撮った")
    if skipped:
        print(f"飛ばした（フローが無い。探索で撮る）: {', '.join(x or '?' for x in skipped)}")
    if lost_all:
        print(f"\n撮れなかった {len(lost_all)}件:", file=sys.stderr)
        for line in lost_all:
            print("  " + line, file=sys.stderr)
        sys.exit(1)

main()
