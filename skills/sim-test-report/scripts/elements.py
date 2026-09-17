#!/usr/bin/env python3
"""Maestroの階層ダンプから、操作に使える要素だけを1行1要素で出す。

usage: elements.py <dump.json> [画面幅 画面高]
出力: tap座標 / 画面内か / テキスト / id
"""
import json, re, sys

W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (390, 844)
B = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

rows = []
def walk(x):
    a = x.get("attributes", {})
    t = (a.get("accessibilityText") or a.get("text") or "").strip().replace("\n", " ")
    r = (a.get("resource-id") or "").strip()
    m = B.match(a.get("bounds") or "")
    if (t or r) and m:
        x0, y0, x1, y1 = map(int, m.groups())
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            return walk_children(x)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        onscreen = 0 <= cx <= W and 0 <= cy <= H
        rows.append((cx, cy, onscreen, t, r))
    walk_children(x)

def walk_children(x):
    for c in x.get("children") or []:
        walk(c)

d = json.load(open(sys.argv[1]))
walk(d[0] if isinstance(d, list) else d)

seen = set()
print(f"{'tap':>12}  {'画面内':<5} テキスト / id")
for cx, cy, on, t, r in sorted(rows, key=lambda v: (v[1], v[0])):
    key = (t, r, cx, cy)
    if key in seen:
        continue
    seen.add(key)
    label = t if t else f"#{r}"
    if t and r:
        label = f"{t}  #{r}"
    print(f"{f'({cx},{cy})':>12}  {'○' if on else '×  ':<5} {label[:60]}")
