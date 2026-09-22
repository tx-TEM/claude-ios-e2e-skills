#!/usr/bin/env python3
"""ビュー階層から、操作に使える要素だけを1行1要素で出す。

usage: elements.py <dump.json>
出力: 画面の識別子 / tap座標 / 画面内か / テキスト / id / 状態

入力は2種類を自動判別する。

  MCP の inspect_screen   {"ui_schema": {...}, "elements": [...]}
      属性名が略号（b/txt/rid/a11y）で、既定値のものは省かれている
  maestro hierarchy       {"attributes": {...}, "children": [...]}
      生の形。MCPが使えないときの退避路
"""
import json, re, sys

# 画面の寸法はダンプのルート要素から取る。外すと端の要素が「画面外」として
# 出力から落ち、落ちたことは行からは分からない。ルートの bounds は必ず画面
# いっぱいなので、ここから取れば外さない。
W = H = None
B = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

def labeled(label, value):
    """ラベルと値が食い違うなら両方出す。

    入力欄はラベルがプレースホルダーのままで、打った文字は値の側に入る。
    ラベルだけ出すと、入力が効いたのかが行から読めない。
    食い違うのは実測で190ノード中5個（入力欄・スクロール位置・ステータスバー）で、
    どれも状態そのものなので、出して困る行は無い。
    """
    label = (label or "").strip()
    value = (value or "").strip()
    if not label:
        return value
    if not value or value == label:
        return label
    return f"{label} = {value}"

def norm(n, compact):
    """どちらの形式も同じ辞書に均す。"""
    if compact:
        return {
            "bounds": n.get("b", ""),
            "text": labeled(n.get("a11y"), n.get("txt") or n.get("val")),
            "rid": n.get("rid", ""),
            # 既定値は省かれているので、無ければ既定
            "selected": n.get("selected", False) is True,
            "enabled": n.get("enabled", True) is not False,
            "checked": n.get("checked", False) is True,
            "children": n.get("c") or [],
        }
    a = n.get("attributes", {})
    def flag(k, default):
        v = a.get(k, n.get(k, default))
        return str(v).lower() == "true"
    return {
        "bounds": a.get("bounds", ""),
        "text": labeled(a.get("accessibilityText"), a.get("text")),
        "rid": a.get("resource-id", ""),
        "selected": flag("selected", False),
        "enabled": not (str(a.get("enabled", n.get("enabled", "true"))).lower() == "false"),
        "checked": flag("checked", False),
        "children": n.get("children") or [],
    }

rows = []
# ナビゲーションバーのid。これが画面のキーになる。
#
# 条件を緩くすると、ナビゲーションバーを持たない画面（地図など）で
# 別物を掴む。実測では地図を含む画面で AnnotationContainer が選ばれて
# いた。アノテーションはデータで変わるので、日によってキーが変わる。
# **キーが取れないより悪い。**
#
# ナビゲーションバーは全幅・上端（iPhone y0=47 / iPad y0=32）・高さ54pt。
# 高さの条件が要る。地図画面の AnnotationContainer は [0,0][390,844] で
# 全幅かつ上端なので、高さを見ないと通ってしまう。
# アプリが明示的に付けた画面idがあれば、位置に関係なくそれを使う。
# 規約は「screen.」始まり。ルートビューに付けても拾えるようにするため。
# ナビゲーションバーが無い画面（地図など）は、この手段でしかキーを
# 持てない。
SCREEN_ID = "screen."
screen = None
screen_cands = []
explicit = []

def walk(node, compact):
    global screen
    d = norm(node, compact)
    t = d["text"].strip().replace("\n", " ")
    r = d["rid"].strip()
    m = B.match(d["bounds"] or "")
    if (t or r) and m:
        x0, y0, x1, y1 = map(int, m.groups())
        if r.startswith(SCREEN_ID):
            explicit.append(r)
        elif (r and x1 - x0 >= W * 0.95 and y0 <= H * 0.08
                and 30 <= y1 - y0 <= 120):
            screen_cands.append(r)
        if x1 - x0 > 0 and y1 - y0 > 0:
            st = ""
            if d["selected"]: st += " [選択]"
            if not d["enabled"]: st += " [非活性]"
            if d["checked"]: st += " [チェック]"
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            rows.append((cx, cy, 0 <= cx <= W and 0 <= cy <= H, t, r, st))
    for c in d["children"]:
        walk(c, compact)

d = json.load(open(sys.argv[1]))
compact = isinstance(d, dict) and "ui_schema" in d and "elements" in d
roots = d["elements"] if compact else [d[0] if isinstance(d, list) else d]

for root in roots:
    m = B.search((root.get("b") if compact else root.get("attributes", {}).get("bounds")) or "")
    if m:
        x0, y0, x1, y1 = map(int, m.groups())
        W, H = max(W or 0, x1), max(H or 0, y1)
if W is None:
    sys.exit("画面の寸法が分からない。ルート要素に bounds が無い。")

for root in roots:
    walk(root, compact)

# 複数あれば型名（<モジュール>.<型名>）を優先する。コード由来で安定するため。
if explicit:
    screen = explicit[0]
elif screen_cands:
    typed = [c for c in screen_cands if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_.]+$", c)]
    screen = (typed or screen_cands)[0]
if screen:
    print(f"画面: {screen}")
else:
    print("画面: 【不明】ナビゲーションバーが無い。遷移したかの判定には使えない")
seen = set()
print(f"{'tap':>12}  {'画面内':<5} テキスト / id")
for cx, cy, on, t, r, st in sorted(rows, key=lambda v: (v[1], v[0])):
    key = (t, r, cx, cy, st)
    if key in seen:
        continue
    seen.add(key)
    label = f"{t}  #{r}" if (t and r) else (t or f"#{r}")
    print(f"{f'({cx},{cy})':>12}  {'○' if on else '×  ':<5} {label[:60]}{st}")
