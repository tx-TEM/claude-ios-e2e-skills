#!/usr/bin/env python3
"""古い形の画面マップ（`actions` / `states` を画面に持つ）を、要素中心の形に移す。

  migrate_map.py --repo <アプリのリポジトリ>            移したあとの yaml を出すだけ（書かない）
  migrate_map.py --repo <アプリのリポジトリ> --write    screens/*.yaml を書き換える

**変換は機械的にできるところまで。** 読めば分かるが機械には決められないもの
（`result` に書いてある条件、要素の `name`、遷移の `summary`）は、変換後に
「人が直すもの」として一覧で出す。**一覧が空になるまでがマップの移行。**

  - `actions` の `tap` / `text` を ID ごとにまとめて `elements` にする（出てきた順）
  - `to` + `kind` は `expect: {screen, via}` に。`kind: back` / `dismiss` は `screen: back`
  - `result` はアクションの `summary` に。`expect: <ID>` は `expect: {visible: <ID>}`
    （操作した要素そのものなら `self`。入力欄への text は `value: self`）
  - `states` は見るだけの要素（`when` つき）に。`ready` に足すかは人が決める
  - `expect` が指していた観測点の ID も、見るだけの要素として足す
  - `scroll: <ID>` は見るだけの要素に。`scroll: down` / `up` は `gestures` に
  - `select`（`index` / `capture`）は捨てる。どれを押すかはスクリプトが実行時に決める
  - `auto_shows` はそのまま（画面 ID だけの項目になる）

**yaml のコメントは消える**（読んだ中身から書き直すため）。書き換える前に
コメントに書いてあったことを確かめる。git の差分で見られるよう、コミット済みの
状態で叩く。
"""
import re
import sys
from pathlib import Path

from mini_yaml import load_yaml

KEEP = ("anchor", "names", "summary", "files", "stub")
CONDITION = re.compile(r"とき|場合|なら|ログイン|未登録|0件|権限")


def convert(sid, s):
    """1画面ぶんを移す。(新しい辞書, 人が直すものの並び)。"""
    todo = []
    out = {k: s[k] for k in KEEP if k in s}
    elements, by_id, gestures = [], {}, []

    def element(eid, a=None):
        if eid not in by_id:
            el = {"id": eid, "name": ""}
            if a is not None and a.get("by") == "label":
                el["by"] = "label"
            if a is not None and a.get("in_tree") is False:
                el["in_tree"] = False
            by_id[eid] = el
            elements.append(el)
        return by_id[eid]

    for a in s.get("actions") or []:
        op = next((k for k in ("tap", "text", "scroll") if k in a), None)
        if op is None:
            todo.append("{}: 操作の種類が分からないエントリを捨てた: {}".format(sid, a))
            continue
        target = str(a[op])
        result = a.get("result")
        if op == "scroll":
            if target in ("down", "up"):
                g = {"scroll": target}
                if result:
                    g["summary"] = result
                if a.get("expect"):
                    g["expect"] = {"visible": a["expect"]}
                gestures.append(g)
            else:
                el = element(target)
                if result:
                    el["summary"] = result
                todo.append("{}: scroll: {} は見るだけの要素にした。result「{}」がスクロールで"
                            "起きる操作（ページ送りなど）なら gestures に移す"
                            .format(sid, target, result or ""))
            continue

        el = element(target, a)
        act = {op: None}
        kind = a.get("kind")
        if kind in ("back", "dismiss"):
            act["summary"] = result or ("前の画面に戻る" if kind == "back" else "閉じる")
            act["expect"] = {"screen": "back", "via": kind}
            if not result:
                todo.append("{}: {} の summary を仮に「{}」にした".format(sid, target, act["summary"]))
        elif a.get("to"):
            act["summary"] = result or "{} を開く".format(a["to"])
            act["expect"] = {"screen": a["to"], "via": kind or "push"}
            if not result:
                todo.append("{}: {} の summary を仮に「{}」にした。何が起きるかを書く"
                            .format(sid, target, act["summary"]))
        else:
            if result:
                act["summary"] = result
            else:
                todo.append("{}: {} に summary が無い（result が無かった）".format(sid, target))
            if a.get("expect"):
                ref = "self" if a["expect"] == target else a["expect"]
                act["expect"] = {"value" if op == "text" and ref == "self" else "visible": ref}
        if result and CONDITION.search(str(result)):
            todo.append("{}: {} の result「{}」に条件が書いてある。出る条件なら要素の when、"
                        "結果が分かれるなら when つきの expect にする".format(sid, target, result))
        sel = a.get("select") or {}
        if sel.get("capture") or sel.get("index") not in (None, 0):
            todo.append("{}: {} の select（{}）を捨てた。どれを押すかはスクリプトが実行時に決める。"
                        "条件で選ぶなら plan の do に pick を書く".format(
                            sid, target, ", ".join("{}: {}".format(k, v) for k, v in sel.items())))
        el.setdefault("actions", []).append(act)

    for st in s.get("states") or []:
        ref = st.get("expect")
        if not ref:
            continue
        el = element(str(ref))
        if st.get("when") and not el.get("when"):
            el["when"] = st["when"]
        todo.append("{}: states の {} を見るだけの要素にした。読み込み完了の目印なら ready に足す"
                    .format(sid, ref))

    # 古い形の expect は観測点の ID で、要素として定義されていなかった。見るだけの要素にする
    refs = [e.get("visible") for el in list(elements) for a in el.get("actions") or []
            for e in [a.get("expect") or {}]] + [(g.get("expect") or {}).get("visible") for g in gestures]
    for ref in refs:
        if ref and ref != "self" and ref not in by_id:
            element(ref)
    nameless = [el["id"] for el in elements if not el.get("name")]
    if nameless:
        todo.append("{}: name を書く要素（{}件）: {}".format(sid, len(nameless), ", ".join(nameless)))
    if elements:
        out["elements"] = elements
    if gestures:
        out["gestures"] = gestures
    if s.get("auto_shows"):
        out["auto_shows"] = s["auto_shows"]
    return out, todo


# ---------- yaml に書く ----------

PLAIN = re.compile(r"^[^\s\[\]{}&*!|>'\"%@`#,?:-][^#]*$")


def scalar(v, flow=False):
    if v is None:
        return ""
    if v is True or v is False:
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v)
    risky = (not PLAIN.match(s) or ": " in s or s.endswith(":") or s in ("true", "false", "True", "False")
             or re.match(r"^-?\d+$", s) or s != s.strip() or (flow and re.search(r"[,\[\]{}]", s)))
    if not risky:
        return s
    return '"{}"'.format(s) if '"' not in s else "'{}'".format(s)


def flow_value(v):
    if isinstance(v, dict):
        return "{" + ", ".join("{}: {}".format(k, flow_value(x)) for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(flow_value(x) for x in v) + "]"
    return scalar(v, flow=True)


def dump_screen(d):
    out = []
    for k in KEEP:
        if k not in d:
            continue
        v = d[k]
        if k == "files" and isinstance(v, list):
            out.append("files:")
            out.extend("  - " + scalar(f) for f in v)
        elif isinstance(v, list):
            out.append("{}: {}".format(k, flow_value(v)))
        else:
            out.append("{}: {}".format(k, scalar(v)))
    if d.get("elements"):
        out.append("elements:")
        for el in d["elements"]:
            out.append("  - id: " + scalar(el["id"]))
            for k in ("name", "summary", "when", "by", "in_tree"):
                if k in el:
                    out.append("    {}: {}".format(k, scalar(el[k])).rstrip())
            if el.get("actions"):
                out.append("    actions:")
                for a in el["actions"]:
                    op = next(k for k in ("tap", "text") if k in a)
                    out.append("      - {}:".format(op))
                    for k in ("summary", "expect"):
                        if k in a:
                            v = a[k]
                            out.append("        {}: {}".format(k, flow_value(v) if isinstance(v, dict) else scalar(v)))
    if d.get("gestures"):
        out.append("gestures:")
        for g in d["gestures"]:
            out.append("  - scroll: " + scalar(g["scroll"]))
            for k in ("summary", "expect"):
                if k in g:
                    v = g[k]
                    out.append("    {}: {}".format(k, flow_value(v) if isinstance(v, dict) else scalar(v)))
    if d.get("auto_shows"):
        out.append("auto_shows: " + flow_value(d["auto_shows"]))
    return "\n".join(out) + "\n"


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    repo, write = None, False
    i = 0
    while i < len(argv):
        if argv[i] == "--repo":
            repo = argv[i + 1]; i += 2
        elif argv[i] == "--write":
            write = True; i += 1
        else:
            sys.exit("知らない引数: " + argv[i] + "（--help）")
    if not repo:
        sys.exit("--repo <アプリのリポジトリ> が要る")
    screens = Path(repo).expanduser().resolve() / "screen-map" / "screens"
    if not screens.is_dir():
        sys.exit("{} が無い".format(screens))

    todo, moved, skipped = [], [], []
    for f in sorted(screens.glob("*.yaml")):
        s = load_yaml(f) or {}
        if not ("actions" in s or "states" in s):
            skipped.append(f.stem)
            continue
        new, td = convert(f.stem, s)
        todo += td
        text = dump_screen(new)
        # 書いたものを読み戻して、同じ中身になるかを確かめる。ずれたら書かない
        tmp = f.with_suffix(".migrate.tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            back = load_yaml(tmp)
        finally:
            tmp.unlink()
        if back != new:
            sys.exit("{}: 書き出した yaml を読み戻すと中身がずれる。変換を直す".format(f.name))
        moved.append(f.stem)
        if write:
            f.write_text(text, encoding="utf-8")
        else:
            print("# ---- screens/{}".format(f.name))
            print(text)

    print("移した画面: {}".format(" ".join(moved) if moved else "なし"))
    if skipped:
        print("もう新しい形の画面: {}".format(" ".join(skipped)))
    if moved and not write:
        print("（書いていない。--write で screens/*.yaml を書き換える）")
    if write and moved:
        print("yaml のコメントは消えている。git の差分で、消えたコメントに書いてあったことを確かめる")
    if todo:
        print("\n人が直すもの（{}件）:".format(len(todo)))
        for t in todo:
            print("  " + t)
    if moved:
        print("\n直したら route.py check --repo <アプリ> で確かめる")


if __name__ == "__main__":
    main()
