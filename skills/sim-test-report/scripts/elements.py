#!/usr/bin/env python3
"""ビュー階層から、操作に使える要素だけを1行1要素で出す。

usage: elements.py <dump.json> [画面幅 画面高]
出力: 画面の識別子 / tap座標 / 画面内か / テキスト / id / 状態

入力は2種類を自動判別する。

  MCP の inspect_screen   {"ui_schema": {...}, "elements": [...]}
      属性名が略号（b/txt/rid/a11y）で、既定値のものは省かれている
  maestro hierarchy       {"attributes": {...}, "children": [...]}
      生の形。MCPが使えないときの退避路
"""
import json, re, sys

W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (390, 844)
B = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

def norm(n, compact):
    """どちらの形式も同じ辞書に均す。"""
    if compact:
        return {
            "bounds": n.get("b", ""),
            "text": (n.get("a11y") or n.get("txt") or ""),
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
        "text": (a.get("accessibilityText") or a.get("text") or ""),
        "rid": a.get("resource-id", ""),
        "selected": flag("selected", False),
        "enabled": not (str(a.get("enabled", n.get("enabled", "true"))).lower() == "false"),
        "checked": flag("checked", False),
        "children": n.get("children") or [],
    }

rows = []
screen = None  # 画面上部の全幅要素（ナビゲーションバー）のid。APIデータでは変わらない

def walk(node, compact):
    global screen
    d = norm(node, compact)
    t = d["text"].strip().replace("\n", " ")
    r = d["rid"].strip()
    m = B.match(d["bounds"] or "")
    if (t or r) and m:
        x0, y0, x1, y1 = map(int, m.groups())
        if screen is None and r and x1 - x0 >= W * 0.95 and y0 < H * 0.2:
            screen = r
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
if isinstance(d, dict) and "ui_schema" in d and "elements" in d:
    for e in d["elements"]:
        walk(e, True)
else:
    walk(d[0] if isinstance(d, list) else d, False)

if screen:
    print(f"画面: {screen}")
seen = set()
print(f"{'tap':>12}  {'画面内':<5} テキスト / id")
for cx, cy, on, t, r, st in sorted(rows, key=lambda v: (v[1], v[0])):
    key = (t, r, cx, cy, st)
    if key in seen:
        continue
    seen.add(key)
    label = f"{t}  #{r}" if (t and r) else (t or f"#{r}")
    print(f"{f'({cx},{cy})':>12}  {'○' if on else '×  ':<5} {label[:60]}{st}")
