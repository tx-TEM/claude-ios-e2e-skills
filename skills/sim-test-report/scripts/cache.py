#!/usr/bin/env python3
"""画面マップのキャッシュ。観測を追記し、引くときに畳む。

  cache.py add    <bundle> '<json1行>'      変化があったときだけ追記する
  cache.py screen <bundle> <画面識別子>      その画面の selector・重複・罠
  cache.py find   <bundle> <語>...           その操作ができる画面を探す
  cache.py path   <bundle> <起点> <行き先>   経路を出す
  cache.py sweep  [日数] [残す数]            後片付け。実行の前に呼ぶ

kind で3つのファイルに振り分ける。アクセスの仕方が違うため。

  selector / dup / note  screens/<画面識別子>.jsonl  いまここで何が使えるか
  transition             transitions.jsonl           AからBへどう行くか
  capability             capabilities.jsonl          Zをやりたい、どこで？

**追記専用。過去の行は書き換えない。** アプリが変わったことは、古い行を
消さずに新しい行を足して表す。上書きすると「変わった」という事実自体が
消え、キャッシュが古いことを検出できなくなる。

同じ内容が既に最新なら追記しない。ファイルの大きさが実行回数ではなく
知識の量に比例するようにするため。
"""
import collections, json, shutil, sys, time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / ".cache"
WORK = Path(__file__).resolve().parent.parent / ".work"

# add のたびに行数を見て、これを超えたら勝手に畳む。手で呼ばなくても
# 溜まり続けないようにするため。畳んでも fold の結果は変わらない。
AUTO_COMPACT_LINES = 200

# kind ごとの同一性の判定に使うフィールド。ここが同じなら同じ事実とみなす。
KEYS = {
    "selector":   ("kind", "sel"),
    "dup":        ("kind", "sel"),
    "note":       ("kind", "text"),
    "transition": ("kind", "from", "to"),
    "capability": ("kind", "screen", "what"),
}

def die(msg):
    sys.exit(f"cache.py: {msg}")

def path_for(bundle, kind, screen=None):
    base = ROOT / bundle
    if kind in ("selector", "dup", "note"):
        if not screen:
            die(f"{kind} には screen が要る")
        return base / "screens" / (screen.replace("/", "_") + ".jsonl")
    if kind == "transition":
        return base / "transitions.jsonl"
    if kind == "capability":
        return base / "capabilities.jsonl"
    die(f"不明な kind: {kind}")

def load(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out

def key(rec):
    k = rec.get("kind")
    if k not in KEYS:
        die(f"不明な kind: {k}")
    return tuple(json.dumps(rec.get(f), ensure_ascii=False, sort_keys=True) for f in KEYS[k])

def fold(recs):
    """同じ key の最新だけを残す。これが「現在の最良の姿」。"""
    cur = {}
    for r in recs:
        cur[key(r)] = r
    return cur

def payload(rec):
    return {k: v for k, v in rec.items() if k != "at"}

def cmd_add(bundle, raw):
    rec = json.loads(raw)
    kind = rec.get("kind") or die("kind が無い")
    p = path_for(bundle, kind, rec.get("screen"))
    stored = dict(rec)
    if p.parent.name == "screens":
        stored.pop("screen", None)   # ファイル名が持っているので落とす
    stored.setdefault("at", date.today().isoformat())

    cur = fold(load(p)).get(key(stored))
    if cur and payload(cur) == payload(stored):
        print(f"変化なし。追記しない（前回 {cur.get('at')}）")
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(stored, ensure_ascii=False) + "\n")
    print(("更新" if cur else "新規") + f": {p}")
    if len(load(p)) > AUTO_COMPACT_LINES:
        before, after = compact(p, 3)
        print(f"  溜まったので畳んだ: {before} → {after}行")

def cmd_screen(bundle, screen):
    recs = fold(load(path_for(bundle, "selector", screen))).values()
    if not recs:
        print(f"{screen} の記録は無い")
        return
    print(f"# {screen}")
    for k, head in (("selector", "安定セレクタ"), ("dup", "重複"), ("note", "罠")):
        rows = [r for r in recs if r.get("kind") == k]
        if not rows:
            continue
        print(f"\n## {head}")
        for r in rows:
            if k == "selector":
                mark = "" if r.get("ok", True) else "  ← 前回は見つからなかった"
                print(f"  {r['sel']}  ({r.get('type', '?')}, {r['at']}){mark}")
            elif k == "dup":
                print(f"  {r['sel']}  {r['n']}個。テキストでは特定できない")
            else:
                print(f"  {r['text']}")

def cmd_find(bundle, words):
    recs = fold(load(path_for(bundle, "capability"))).values()
    hit = [r for r in recs
           if all(w in json.dumps(r, ensure_ascii=False) for w in words)]
    if not hit:
        print("該当なし。探索して記録する")
        return
    for r in sorted(hit, key=lambda r: not r.get("ok", True)):
        if r.get("ok", True):
            print(f"[{r['screen']}] {r['what']}")
            if r.get("how"):
                print(f"    手段: {json.dumps(r['how'], ensure_ascii=False)}")
            if r.get("result"):
                print(f"    結果: {r['result']}")
        else:
            print(f"[{r['screen']}] {r['what']}  ← できない: {r.get('why', '理由の記録なし')}")

def cmd_path(bundle, src, dst):
    edges = {}
    for r in fold(load(path_for(bundle, "transition"))).values():
        if r.get("ok", True):
            edges.setdefault(r["from"], []).append(r)
    # 幅優先。辺は画面数ぶんしか無いので全部読んでよい
    seen, queue = {src: None}, [src]
    while queue:
        cur = queue.pop(0)
        if cur == dst:
            break
        for e in edges.get(cur, []):
            if e["to"] not in seen:
                seen[e["to"]] = e
                queue.append(e["to"])
    if dst not in seen:
        print(f"{src} から {dst} への経路は記録に無い。探索して記録する")
        return
    steps = []
    cur = dst
    while seen[cur] is not None:
        e = seen[cur]
        steps.append(e)
        cur = e["from"]
    for i, e in enumerate(reversed(steps), 1):
        print(f"{i}. {e['from']} → {e['to']}  {json.dumps(e['how'], ensure_ascii=False)}")
    print("\n1ホップごとにダンプを取り、画面識別子が次のノードと一致するか確かめる。")
    print("一致しなければ ok:false を追記して探索に落ちる。粘らない。")

def compact(p, keep):
    """同じ key の古い行を落とす。fold が見るのは最新なので結果は変わらない。

    keep を2以上にしておくと「いつ変わったか」が1世代ぶん残る。
    """
    recs = load(p)
    pos = collections.defaultdict(list)
    for i, r in enumerate(recs):
        pos[key(r)].append(i)
    survive = set()
    for ps in pos.values():
        survive.update(ps[-keep:])
    out = [recs[i] for i in sorted(survive)]
    if len(out) != len(recs):
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
        tmp.replace(p)
    return len(recs), len(out)

def cmd_sweep(days, keep):
    """.work は古いものから消し、.cache は消さずに畳む。

    性質が逆なため。.work の生JSONは1実行で約3MBになるが判定が済めば
    用済み。.cache は小さいうえ、消すと実機を動かさないと作り直せない。
    """
    cut = time.time() - days * 86400
    n = freed = 0
    md = WORK / "maestro"
    if md.exists():
        for d in md.iterdir():
            if d.is_dir() and d.stat().st_mtime < cut:
                freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d)
                n += 1
    if WORK.exists():
        # .txt は残す。2KBしかなく、何を見て判断したかの記録になる
        for f in WORK.iterdir():
            if f.is_file() and f.suffix in (".json", ".yaml", ".err") and f.stat().st_mtime < cut:
                freed += f.stat().st_size
                f.unlink()
                n += 1
    print(f".work: {days}日より古い {n}件 / {freed // 1024}KB を消した（.txt は残す）")

    for f in sorted(ROOT.rglob("*.jsonl")) if ROOT.exists() else []:
        before, after = compact(f, keep)
        if before != after:
            print(f".cache: {f.relative_to(ROOT)}  {before} → {after}行")

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "sweep":
        a = sys.argv[2:]
        return cmd_sweep(int(a[0]) if a else 14, int(a[1]) if len(a) > 1 else 3)
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, bundle, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
    if cmd == "add":
        cmd_add(bundle, rest[0])
    elif cmd == "screen":
        cmd_screen(bundle, rest[0])
    elif cmd == "find":
        cmd_find(bundle, rest)
    elif cmd == "path":
        cmd_path(bundle, rest[0], rest[1])
    else:
        sys.exit(__doc__)

main()
