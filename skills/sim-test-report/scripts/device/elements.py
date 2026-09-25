#!/usr/bin/env python3
"""ビュー階層から、操作に使える要素だけを1行1要素で出す。

usage: elements.py <dump.json>
出力: 1行目に画面の識別子、2行目に欄の名前、3行目から1行1要素

  tap        画面内  上端  id                         テキスト       状態
  (201,241)  ○       208   browse.book_row.こころ     こころ, 夏目漱石
  (108,142)  ○       126   browse.target_picker.title 作品名         選択

**欄はタブで区切る。** id にもテキストにも空白が入る（`browse.book_row.BOITEUX ・ BOITEUSE`）
ので、空白で割ると id がどこで終わるか決まらない。タブで割れば、どの欄も1回で取れる。
無い欄は空。状態は `選択` / `非活性` / `チェック` をカンマで並べる。
**画面内** は、中心が画面の外なら `×`、画面の中でも前面の要素（固定された検索欄、
バー、ステータスバー、キーボード）の裏にあれば `裏`、それ以外は `○`（covered()）。
行は中心の y、次に x の順（上から）。**上端** は要素の上端の y で、Maestro の `index` が
当たった要素を並べる基準（上端の y、次に x）と同じものを数えるために出す。

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

# ツリーの全ノード（行を持たないものも）。重なりの判定に使う（covered()）
nodes = []

def walk(node, compact, parent=None, win=(0, 0)):
    global screen
    d = norm(node, compact)
    t = d["text"].strip().replace("\n", " ").replace("\t", " ")   # タブは欄の区切り
    r = d["rid"].strip()
    m = B.match(d["bounds"] or "")
    me = {"i": len(nodes), "end": None, "parent": parent, "box": None,
          "cover": False, "row": None, "text": t, "win": win,
          "front": bool(parent and parent["front"])}
    nodes.append(me)
    if m:
        me["box"] = tuple(map(int, m.groups()))
        # 前に被さりうるもの。文言か id を持つ要素と、ステータスバーの窓
        me["cover"] = bool(t or r) or (parent is None and win[0] > 0 and is_status_bar(me["box"]))
        me["front"] = me["front"] or (me["cover"] and is_bar(me["box"]))
    if (t or r) and m:
        x0, y0, x1, y1 = me["box"]
        if r.startswith(SCREEN_ID):
            explicit.append(r)
        elif (r and x1 - x0 >= W * 0.95 and y0 <= H * 0.08
                and 30 <= y1 - y0 <= 120):
            screen_cands.append(r)
        if x1 - x0 > 0 and y1 - y0 > 0:
            st = []
            if d["selected"]: st.append("選択")
            if not d["enabled"]: st.append("非活性")
            if d["checked"]: st.append("チェック")
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            on = 0 <= cx <= W and 0 <= cy <= H
            row = [cx, cy, "○" if on else "×", t, r, ",".join(st), y0]
            me["row"] = row
            rows.append(row)
    for j, c in enumerate(d["children"]):
        # アプリのルートの子が窓（アプリ本体、キーボード）。後ろの窓ほど前面
        walk(c, compact, me, (win[0], j + 1) if parent is None else win)
    me["end"] = len(nodes)


def is_status_bar(box):
    """画面の上端の細い帯（ステータスバー）か。アプリの外の窓のうち、これだけが被さる。"""
    x0, y0, x1, y1 = box
    return y0 <= 0 and y1 - y0 <= H * 0.1


def is_bar(box):
    """画面の上端か下端に張り付いた、左右の端から端までの帯（ナビゲーションバー、タブバー）か。

    「幅の95%以上」では足りない。iPad の一覧の行は左右に余白を持っても幅の96%あり、
    上端近くの行がバーに見えた（実測）。バーは余白を持たない。
    """
    x0, y0, x1, y1 = box
    return (x0 <= 0 and x1 >= W and 30 <= y1 - y0 <= 120
            and (y0 <= H * 0.08 or y1 >= H * 0.92))


def area(box):
    x0, y0, x1, y1 = box
    return max(0, x1 - x0) * max(0, y1 - y0)


def cell_of(n):
    """n が載っているセル。n を含み、面積が n の2倍までの、いちばん外側の祖先（無ければ n）。

    行の id はセルの中の1つのビューに付いていて、同じセルの中の文言（タイトルや
    バッジ）はその兄弟として並ぶことがある。それを「被さっている」と読まないために、
    セルの中のものは重なりから外す。一覧の外枠（コレクションビュー）は行よりずっと
    大きいので、ここで止まる。
    """
    cell, a = n, area(n["box"])
    p = n["parent"]
    while p is not None and p["box"] and area(p["box"]) <= 2 * a:
        cell = p
        p = p["parent"]
    return cell


def covered(n):
    """画面内の要素の中心に、前面に描かれる別の要素が被さっているか。

    スクロールした一覧では、行の中心が、一覧の上に固定された検索欄や絞り込みのタブ、
    ステータスバーの裏に入る（#41。1件目の行が検索欄の裏にあり、タップがステータスバーに
    当たった）。どちらが前面かは、次の順で決める。

    1. **窓が違えば、後ろの窓が前面。** アプリのルートの子が窓（アプリ本体、キーボード）で、
       ステータスバーはルートの外の窓
    2. **同じ窓なら、上下のバー（`front`）が前面。** SwiftUI の NavigationStack では、
       ナビゲーションバーが中身より前に並ぶのに前面に描かれる（実測）
    3. **どちらでもなければ、ツリーで後ろにあるほうが前面**

    被さる側に数えるのは、文言か id を持つ要素と、ステータスバーの窓だけ。文言も id も
    無い入れ物は画面いっぱいに広がっていることが多く、数えると全部が裏になる。画面の
    半分より大きいものも入れ物として外す。**文言も id も無い不透明な板が前面にあっても
    分からない**ので、ここで裏にならない要素が見えているとは限らない。

    同じものの別の姿（same()）と、同じセルの中のもの（cell_of()）は外す。
    """
    cx, cy = n["row"][0], n["row"][1]
    cell = cell_of(n)
    for o in nodes:
        if cell["i"] <= o["i"] < cell["end"] or (o["i"] < cell["i"] < o["end"]):
            continue   # 同じセルの中か、祖先
        if not o["cover"] or not o["box"] or area(o["box"]) > W * H / 2 or same(n, o):
            continue
        if o["win"] != n["win"]:
            front = o["win"] > n["win"]
        elif o["front"] != n["front"]:
            front = o["front"]
        else:
            front = o["i"] > n["i"]
        x0, y0, x1, y1 = o["box"]
        if front and x0 <= cx <= x1 and y0 <= cy <= y1:
            return True
    return False


def same(n, o):
    """o が n の別の姿か。被さっているとは数えない。

    - 枠がまったく同じ（スクロールバーが2つ並んで出る）
    - o が n を丸ごと含み、n の文言を o の文言が含む。SwiftUI が行の文言をまとめた
      要素（`作品名, BOITEUX ・ BOITEUSE`）は、中のラベル（`作品名`）より後ろに並ぶ
    - 同じ窓で、o が n を丸ごと含み、o に文言が無い。バーの背景のような入れ物で、
      中のボタン（iPad のタブ）より後ろに並ぶことがある（実測）
    """
    if o["box"] == n["box"]:
        return True
    a0, b0, a1, b1 = n["box"]
    x0, y0, x1, y1 = o["box"]
    inside = x0 <= a0 and y0 <= b0 and a1 <= x1 and b1 <= y1
    if not inside:
        return False
    if n["text"] and n["text"] in o["text"]:
        return True
    return o["win"] == n["win"] and not o["text"]

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

for k, root in enumerate(roots):
    # 2つ目からのルートはアプリの外の窓（ステータスバー）。アプリの上に被さる
    walk(root, compact, win=(k, 0))

for n in nodes:
    if n["row"] and n["row"][2] == "○" and covered(n):
        n["row"][2] = "裏"

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
print("\t".join(["tap", "画面内", "上端", "id", "テキスト", "状態"]))
for cx, cy, on, t, r, st, top in sorted(rows, key=lambda v: (v[1], v[0])):
    key = (t, r, cx, cy, st)
    if key in seen:
        continue
    seen.add(key)
    # **id は切らない。** 切れた断片が別のidとして読めてしまい、
    # 読む側は切れたことに気づけない（実際、長い行のidが途中で切れ、
    # その断片が実在する別レコードのidと一致して誤読された）。
    # テキストは切るが、切ったことが分かるように印を残す。
    text = t if len(t) <= 60 else t[:59] + "…"
    print("\t".join([f"({cx},{cy})", on, str(top), r, text, st]))
