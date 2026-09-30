#!/usr/bin/env python3
"""test-case-builder の plan.json から、経路が組めるかを確かめて manifest.json を作る。

  manifest.py <plan.json> <出力先ディレクトリ> --device <端末>=<UDID> [--device …]

**どのアプリの画面マップで経路を組むかは plan の `repo` で決まる。** plan の画面 id と要素 id は
そのマップを前提に書いたものなので、plan が持つ（引数では渡さない）。

証跡は `<出力先>/shots/<端末>/<名前>.png` に撮る。名前は項目の並び順から振る
（`test_01`, `test_02`, …。テストケースをまたいで通しで、explore のテストケースもその場の番号）。
**同じ項目を複数の端末で撮れる**（`--device iphone=<UDID> --device ipad=<UDID>`）。フローは端末によらず1組で、撮影先だけを
`${SHOTS}` のまま書き、run_flows.py が端末に合わせて埋める。どの端末で撮るか、どこに出すかは
撮る側の設定で、テストケース（plan）は持たない。

**どのシミュレーターで撮るかもここで決める。** UDID から機種名と OS を引いて、マニフェストの
`devices` に書く。run_flows.py と sim-driver はそこから UDID を読み、build_report.py は確認環境を
そこから出す。撮るときに手で渡し直さない。

1. テストケースごとに、**起動直後の画面から**経路が組めるかを確かめる（flowgen/flow.py の
   `plan_rows()`）。組めなければ全部のテストケースの理由を出して止まる（manifest は
   書かない）。組めたらテストケースごとに読める経路を出す
2. 返ってきた項目ごとの行から manifest.json を組む。**plan と同じテストケースの入れ子**
   （`cases` → `items`）で、項目1つ＝証跡1枚。項目の中身はテストケースの下に置く

**フローはここでは書かない。** run_flows.py が撮るときに、前のテストケースが終わった画面から
テストケースごとに組む。繋げなければ起動し直して起点から組むので、ここでは起点から組めることを
確かめれば足りる。

plan.json の形。**plan はテストケースの集合で、項目は必ずどれかのテストケースに属する。**
**項目1つ＝ from から do を順に叩いて、1枚撮る。**

    {"app": "<bundle id>",
     "repo": "<アプリのリポジトリ>",
     "clear_state": false,
     "cases": [
       {"title": "さがす画面を開くと一覧が出る",
        "items": [{"from": "browse", "title": "…", "expect": "…"}]},
       {"title": "キーワードで絞り込める",
        "items": [
          {"from": "browse",
           "do": [{"op": "text:browse.search_field", "runtime": true}]},
          {"from": "browse",
           "do": [{"op": "text:browse.search_field", "input": "zzzz"}]}]},
       {"title": "一覧から詳細を開ける",
        "items": [
          {"id": "open", "from": "browse", "do": ["tap:browse.book_row.*"]},
          {"from": "book_detail", "do": ["see:book_detail.section.related"],
           "expect": "{open} で開いた作品の関連作品が並ぶ"}]},
       {"title": "条件に合う行を開ける",
        "items": [
          {"from": "browse",
           "do": [{"op": "tap:browse.book_row.*", "pick": "貸出中の本"}]}]},
       {"title": "語を含む行だけが残る",
        "items": [
          {"from": "browse",
           "do": [{"op": "text:browse.search_field", "input": "猫"},
                  {"op": "see:browse.book_row.*", "input": "猫"}]}]},
       {"title": "図書カードを開ける",
        "items": [
          {"from": "book_detail",
           "do": ["tap:book_detail.card_link", "see:text:図書カード"]}]},
       {"title": "ログイン中に登録できる",
        "items": [
          {"from": "book_detail", "when": ["ログイン中"],
           "do": ["tap:book_detail.register_button"]}]},
       {"title": "起動するとお知らせが出る", "launch": true,
        "items": [{"from": "notice_sheet"}]},
       {"title": "履歴から開き直せる", "explore": "画面 history がマップに無い",
        "items": [{"from": "browse", "title": "…", "expect": "…"}]}]}

  repo      アプリのリポジトリ（必須）。画面マップはその下の screen-map/。
            相対パスなら plan の置き場所から読む。マニフェストにも写す

テストケース（cases の要素）:
  title     何の機能を確かめるまとまりか（必須。plan の中で重ねない）。
            マニフェストのテストケースの題と、レポートの見出しになる
  items     項目の並び（必須）。**テストケースの中の項目は前の項目に依存して
            よく、テストケースどうしは依存しない。** まとめるのは、後ろの
            項目の期待が前の項目の結果を指すときと、後ろの項目の結果が
            どこから来たかで変わるとき（戻り先など）だけ。それ以外は分ける
  launch    このテストケースはアプリを起動し直してから始める。**起動した
            ときの表示そのもの**（起動するとお知らせが出る）を確かめるとき
            だけ。きれいな状態から始めたいだけなら付けない

  **後に残る状態（入力欄の語、絞り込み、セグメントの選択）の後始末は plan に
  書かない。** 画面マップの操作の `leaves`（残る状態）と `reset`（既定に戻す
  操作）から、テストケースの後にスクリプトが reset を叩くか、起動し直す
  explore   経路が組めなかった理由（「画面 history がマップに無い」）。
            付いたテストケースはフローを持たず、その場に項目として
            並ぶ（sim-driver が探索で撮る）。1項目でも組めなければ付ける

項目（items の要素）:
  from      その項目の操作を始める画面。**項目は経路を持たない** —
            前の項目が終わった画面から from までは、ここで計算して
            繋ぐ（すでに居れば何もしない）
  do        from から撮る地点までの操作。`tap:<id>` `text:<id>` `see:<id>` `scroll:down` の
            ように種類を頭に付けて指す。**並び順がそのまま実行順。** 確かめる操作の
            手前で状態を作る準備（語を打つ、特定の行を開く）も並べてよい。撮るのは
            最後の操作のあとの1枚。画面に着くためだけの移動は書かない（from で足りる）。
            遷移する操作も書いてよく、行き先はマップの `expect` で追う。
            `see:<id>` は「その要素を見る」（見えるまでスクロールして確かめる）。
            **撮る前に何かが出るのを待つのも see。** 見えるまで最大60秒待つ。
            パターンの要素には、その語を含む行を待つ語を添えられる
              {"op": "see:<パターン>", "input": 語}     その語を含む行（絞り込みの結果など）
              {"op": "see:<パターン>", "runtime": true} 含む語を撮るときに決める
                   （打った語と同じとは限らない。作者で絞り込めば行の ID は作品名）
            `see:text:<文言>` はマップに無い文言が出るまで待つ（スクロールしない）。
            アプリの外（Safari など）はマップに無いので、ここでしか待てない。
            文言はローカライズや表記揺れで壊れるので、ID で書けるときは使わない。
            着いた状態を見るだけの項目は空。
            **パターンの要素（ID の末尾が *）は、どれを押すかをスクリプトが決める。**
            何も添えなければ画面に見えている1件目。選ぶ条件があるときだけ
              {"op": 操作id, "pick": 条件}  止めて、ダンプと条件から選ばせる
            具体的な1つを決め打つなら ID まで書く（`tap:browse.book_row.吾輩は猫である`）。
            text は打つ文字を {"op": 操作id, …} で添える
              "runtime": true  **値を実行時に決める。** フローには値を
                   焼き込まず、`env` の未定のまま残す。着いた画面を
                   見ないと決まらないときに。焼き込むと、データが
                   変わっても古い値で黙って走る
              "input": 値      データに依らない値（一致しない語など）
            from までの経路の途中でパターンの要素を押すときも、見えている1件目。
            どれを選ぶかを気にするなら、それは確かめる操作なので do に書く
            **子の要素（マップの children。横スクロールの中など）は、親の中でする。**
            スクリプトが親を縦に寄せ、子が見えるまで親を送る。パターンの親
            （`recommend.carousel.*`）のどれの中でするかは、何も添えなければ
            画面に見えている1件目。決めるときは in を添える（ほかと一緒に書ける）
              {"op": 操作id, "in": 親の ID}          その親の中（recommend.carousel.9）
              {"op": 操作id, "in": [外の親, 内の親]}  パターンの親が2段なら外から順に
              {"op": 操作id, "in": {"pick": 条件}}   止めて、ダンプと条件から親を選ばせる
  when      項目の前提。マップの `when` の文言をそのまま写す（["ログイン中"]）。
            条件つきの要素と、結果が分かれる操作の枝は、ここに同じ文言が
            あるときだけ使う。意味は読まない — 文字列が一致するかだけ
  id        同じテストケースの後ろの項目が、期待の中で `{id}` と書いて
            この項目を指すための名前（英数字と _ . -）。要るときだけ
  title / expect  確認項目と期待。そのままマニフェストに入る。expect の
            `{id}` は「test_04（その項目の title）」に展開される。番号で
            書かない — 並べ替えや削除でずれる

**フローの置き場はスキル側の `.work/flows/<出力先の名前>/` に決め、マニフェストの `flows` に
記録する。** アプリのリポジトリには何も作らない。フローを組むのに要るもの（`app` /
`clear_state` / `repo`）もマニフェストに写す。run_flows.py は plan.json を読まない — レビューの
あとに plan を直していると、合意したものと違うフローを走らせることになる。

**写さない。** 証跡の名前・撮った画面・自動確認のIDは経路を計算した結果から、
`title` / `expect` / `from` は plan から、どちらもスクリプトが1行にする。

**ヘッダの題と meta（ブランチ・確認環境・実施日）は持たない。** レポートを組むときに
`build_report.py` へ直に渡す。確認環境は撮影する端末を決めるまで決まらない。

**フローを組んだ結果（割ったフロー、押す行の選び方、値の入る先）はマニフェストに載せない。**
run_flows.py が撮るときに組み、その場で使う。

**`inputs` は撮影する側（LLM）が決める値。** plan で `runtime` を書いた打つ文字と、
`pick`（条件つきで選ぶ）の行に付く。値が空のうちはその本を走らせられない（`run_flows.py`
がそこで止まる）。着いた画面を見ないと決まらないものなので、埋めるのは撮影する側。
**条件の無いパターンの要素は `inputs` に入らない。** `run_flows.py` が画面に見えている1件目を
選んで `devices.<端末>.picked` に書く。`picked` には、条件つきで選んだ行も含めて、押した行の
値と、同じ ID の行のうち何番目か（`<変数>_INDEX`。Maestro の `index` に入る）が入る。
同じ名前の行を区別するため。**埋める側は正規表現のエスケープをかけない。** 各値がセレクタ
（正規表現）に入るか inputText に入るかは、フローを組むときに決まり、エスケープは
run_flows.py がする。

plan で `explore` の付いたテストケースは**経路が組めなかったもの**。その項目は `flow` を
持たないので `run_flows.py` は飛ばし、sim-driver が探索で撮る。**plan の並びのまま、その場に
並ぶ** — 証跡の番号とレビューの並びを、探索に回したかどうかで動かさない。

**マニフェストの形は plan と同じテストケースの入れ子。** `cases` の要素がテストケースで、
その `items` に項目の中身（`name` / `title` / `from` / `do` / `when` / `screen` /
`expect` / `checked` / `flow` / `devices` / `images` と判定の欄）が並ぶ。同じテストケースの
項目は、後ろの項目が前の項目の結果を当てにしている。判定は期待に出てくる前の項目の証跡も読み、
sim-driver は同じテストケースの中で起動し直さない。**項目に属するテストケースの題を持たせない**
— 親がテストケースなので、題の文字列で紐づけ直さない。名前で1項目を引く、全項目を順に
並べるときは `manifest_items.py`（`walk()` / `find()`）を通す。

テストケースの `launch` / `explore` は、レビューの「ここでアプリを起動し直す」や sim-driver の
起動に、題はレポートの見出しに使う。`after` はテストケースの後始末で、
画面マップの `leaves` / `reset` から flowgen が決めたもの（`relaunch`: 次の頭で起動し直す、
`resets`: 叩いて戻す操作、`leaves`: 残る状態の文）。何も要らなければ null（#83）。
走らせるときに `reset` の画面まで繋げなければ、そこで起動し直す。

なぜスクリプトなのか。一覧の中身（証跡の名前、画面、自動確認のID、フローの
ファイル名）は flowgen が計算したもので、**手で写すとタイポの余地ができる。**
撮影の名前と manifest の `src` がずれても、走らせるまで誰も気づかない。

**撮れなかったことは `devices.<端末>.unexpected` に run_flows.py が書く**（判定ではないので
`result` には入れない。`result` は `PENDING` のまま）。フローで撮る予定だった項目だけに付く。

  {"kind": "failed", "reason": "フローが失敗（2本目）"}   その項目が撮れなかった（フローが落ちた、
        押す行や親が画面に無い、manifest.py の後で画面マップが変わって組めない）
  {"kind": "skipped", "reason": "同じテストケースの test_10 が落ちた"}   前の項目が落ちたので走らせていない

端末ごとに持つ（落ちるのは端末ごと）。run_flows.py がその項目を走らせ直すと消える。探索
（sim-driver）で撮っても消えない — フローでは撮れなかった記録として残る。

`desc` / `result` / `note` は手順2で埋める。`result` を `PENDING` で置くのは、
build_report.py が result の無い項目を拒むため（判定していない項目が
黙って OK で出ないように）。

**同じ場所に既にマニフェストがあれば、判定の欄（`desc` / `note` / `result`）を
引き継ぐ。** 引き継ぎのキーは証跡の名前。`title` / `expect` は plan が正で、
直すなら plan を直して叩き直す。

**出力先の名前は `sim-test-report-<日付>-<テーマ>`**（`sim-test-report-20260928-search`）。
並べれば時系列になり、スキル側の `.work/flows/` とダンプの置き場も同じ名前になる。
形が違えば警告を出す（止めはしない）。

**実行時に決めた値（`inputs`）も、同じ変数名のものは引き継ぐ。** 撮り直し
（`run_flows.py --only`）は手前の項目をなぞるので、前に撮ったときの値が要る。
消すと、手前の項目の判断を撮り直しのたびにやり直すことになる。plan を直して
変数が変わった（別の入力欄になった）ものは空に戻す。引き継いだものは出力に出す
— データが変わっていれば古い値で走るので、見て直せるように。
"""
import json
import re
import sys
from pathlib import Path

import manifest_items
from device import simulators
from flowgen import flow as flows_of   # plan からフローを作る


LABELS = {"iphone": "iPhone", "ipad": "iPad"}
FLOWS = Path(__file__).resolve().parents[1] / ".work" / "flows"   # スキル側の作業ディレクトリ


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    if len(argv) < 2:
        sys.exit(__doc__)
    plan_path, out_dir = Path(argv[0]).expanduser(), Path(argv[1]).expanduser()
    devices = []
    i = 2
    while i < len(argv):
        if argv[i] == "--repo":
            sys.exit("--repo は渡さない。アプリのリポジトリは plan の repo に書く")
        elif argv[i] == "--device":
            name, _, udid = argv[i + 1].partition("=")
            if not udid:
                sys.exit(f"--device は <端末>=<UDID> で渡す: {argv[i + 1]}")
            devices.append((name, udid)); i += 2
        else:
            sys.exit("知らない引数: " + argv[i] + "（題と meta は build_report.py に渡す）")
    if not devices:
        sys.exit("--device <端末>=<UDID> が要る（iphone / ipad。複数の端末で撮るなら並べる）")
    names = [n for n, _ in devices]
    if len(set(names)) != len(names):
        sys.exit("--device の端末が重なっている: " + ", ".join(names))
    # 機種名と OS は UDID から引く。撮った端末として、確認環境にそのまま出る
    info = {}
    for n, u in devices:
        sim = simulators.lookup(u)
        info[n] = {"udid": u, "model": sim["model"], "os": sim["os"]}
    devices = names

    plan = flows_of.load_plan(plan_path)
    cases = flows_of.read_cases(plan)
    flowed = any(not c["explore"] for c in cases)

    # フローは run_flows.py が走らせるときに組んで、端末ごとに <flows>/<端末>/ に書く。
    # **置き場はスキル側の .work に固定する。** 呼ぶ側のカレント（アプリのリポジトリ）に
    # 作ると、誰も片付けない。スキル側なら maestrod.py sweep が古いものを消す
    flows = FLOWS / out_dir.resolve().name
    rows, afters = flows_of.plan_rows(plan) if flowed else ({}, {})
    if flowed:
        print()

    out = out_dir / "manifest.json"

    # 判定の欄は、作り直しても消さない。footer など判定側が足した欄も残す
    kept, top = {}, {}
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
            # 古い形（項目が名前だけ）のものは引き継がない
            kept = {it.get("name"): it for _, it in manifest_items.walk(old)
                    if isinstance(it, dict) and it.get("name")}
            top = {k: v for k, v in old.items()
                   if k not in ("sections", "cases", "title", "meta", "resume", "app", "clear_state")}
        except Exception:
            pass

    def item_of(r):
        """plan の項目1つを、マニフェストの項目にする。"""
        e, it, name = rows.get(r["name"]) or {}, r["item"], r["name"]
        prev = kept.get(name, {})
        return {
            "name": name,                      # 証跡・ダンプ・フローのファイル名。引き継ぎの鍵
            "title": it.get("title", ""),      # 確認項目。plan が正
            "from": it.get("from"),            # 操作を始める画面（plan）
            "do": it.get("do") or [],          # 確かめる操作（plan）。レビューで読み上げる
            "when": it.get("when") or [],      # 項目の前提（plan）
            "screen": e.get("screen"),         # 撮った画面（経路の計算）。探索は撮るまで決まらない
            "expect": r["expect"],             # 証跡の中で何を確かめるか。plan が正
            "checked": e.get("checked"),       # None なら証跡だけが根拠
            # 撮るフローのファイル名（<flows>/<端末>/ の下）。run_flows.py が走らせるときに書く。
            # 探索で撮る項目は None
            "flow": e.get("flow"),
            # 実行時に決める値は端末ごと（その端末の画面を見て決める）。picked は
            # run_flows.py が見えている1件目を選んだ結果で、撮るたびに選び直す。
            "devices": {d: {"inputs": {k: ((prev.get("devices") or {}).get(d) or {})
                                           .get("inputs", {}).get(k, "")
                                       for k in (e.get("inputs") or {})},
                            "picked": {}}
                        for d in devices},
            # 証跡は端末ごとに1枚。ダンプは同名の .txt
            "images": [{"src": f"shots/{d}/{name}.png", "label": LABELS.get(d, d)}
                       for d in devices],
            "desc": prev.get("desc", ""),
            "note": prev.get("note", ""),      # この項目だけの但し書き。判定で埋める
            "result": prev.get("result", "PENDING"),
        }

    # plan の並びのまま、項目はテストケースの下に置く。探索のテストケースもその場に置く（フローは持たない）。
    # after はテストケースの後始末（画面マップの leaves / reset から flowgen が決めたもの。#83）
    case_list = [{"title": c["title"], "launch": c["launch"], "explore": c["explore"],
                  "after": afters.get(c["title"]),
                  "items": [item_of(r) for r in c["items"]]} for c in cases]
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = dict(top, app=plan.get("app"), clear_state=bool(plan.get("clear_state")),
                    repo=plan["repo"], devices=info, flows=str(flows), cases=case_list)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    items = [it for _, it in manifest_items.walk(manifest)]
    carried = []
    for it in items:
        for d, dev in it["devices"].items():
            kept_inputs = {k: v for k, v in dev["inputs"].items() if v}
            if kept_inputs:
                carried.append("{} {}: {}".format(
                    it["name"], d, ", ".join(f"{k}={v}" for k, v in kept_inputs.items())))
    blank = sum(1 for s in items if not s["flow"])
    empty = [s["name"] for s in items if not s["title"] or not s["expect"]]
    print(out)
    if not re.fullmatch(r"sim-test-report-\d{8}-.+", out_dir.resolve().name):
        print(f"  出力先の名前 {out_dir.resolve().name} が sim-test-report-<日付>-<テーマ> の形でない"
              "（例 sim-test-report-20260928-search）。回ごとに分かれるように付け直すとよい")
    print(f"  {len(case_list)}テストケース・{len(items)}項目 × {len(devices)}端末")
    for n in devices:
        print(f"    {n}: {info[n]['model']} ({info[n]['os']})  {info[n]['udid']}")
    if empty:
        print(f"  title か expect が空: {', '.join(empty)}。plan.json を埋めて叩き直す")
    if carried:
        print("  前のマニフェストから引き継いだ実行時の値（データが変わっていれば直す）:")
        for c in carried:
            print("    " + c)
    nochk = sum(1 for s in items if s["flow"] and not s["checked"])
    if nochk:
        print(f"  {nochk}件はフローに自動確認が無い（証跡だけが根拠）")
    if blank:
        print(f"  {blank}件はフローが無い（探索で撮る。sim-driver に渡す）")

main()
