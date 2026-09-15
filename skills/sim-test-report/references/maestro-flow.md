# Maestro のフローを作る

手順0で「フロー化する価値がある」と判断し、ユーザーの合意が取れたときに読む。

## 原則: 画面を突っつかず、ソースから確定させる

シミュレーターを操作しながらセレクタを探すと、LLMが画面を見る往復が発生して、
Maestro を使う利点がその場で消える。**アプリのソースを読んで導線とセレクタを先に確定させ、
YAMLを一気に書く。** 実行は確認のためで、試行錯誤の手段ではない。

画面を見ても得られないが、コードには書いてあるものが多い。実例:

- タブの識別子の命名規則（`vc.tabBarItem.accessibilityIdentifier = "tabbar-button-" + item.imageName`）
- 一覧のどのセクションが1タップで目的画面へ飛ぶか（`didSelectRowAt` の分岐）
- ボタンの実際のラベル（storyboard の `title` と、実機に出る文字列が違うことがある）
- 消えるUIや広告の有無（後述のハングの原因になる）

## 調べる場所

| 欲しいもの | 見る場所 |
|---|---|
| 安定した識別子 | `grep -rn 'accessibilityIdentifier' --include='*.swift'` |
| ボタン・画面の文言 | `Localizable.xcstrings` / `.strings` を `tr("キー")` から逆引き |
| 画像ボタンの名前 | `UIImage(resource:)` / `UIImage(named:)` のアセット名。階層にはこの名前で出る |
| 遷移の分岐 | `didSelectRowAt` / `performSegue` / `pushViewController` |
| ボタンの並びと題字 | `.storyboard` / `.xib` の `title=` |

`String(localized: .fooBar)` 形式は `.xcstrings` のキーが `fooBar` ではなくキャメルケース変換前の文字列のことがある。
日本語側から逆引きすると確実。

## 2層に分ける

**PRごとに使い捨てにするのは末端だけにする。**

```
<flow_root>/
  common/
    launch.yaml              # 起動してホームまで。launchApp を含む
    goto_<画面名>.yaml        # 目的の画面まで。launchApp を含めない
    <汎用操作>.yaml           # env で引数を受ける
  README.md
  <ケース名>.yaml             # 使い捨て。common を runFlow で呼び、固有部分だけ書く
```

```yaml
- runFlow: common/launch.yaml
- runFlow:
    file: common/goto_product_find.yaml
    env:
      KEYWORD: "納豆"
```

**`launchApp` を含めるかどうかで用途が変わる。** 含むフローはアプリを再起動して最初の画面に戻す。
sim-driver が途中から遷移だけさせたいときに使えないので、`README.md` にどちらかを必ず書く。

## セレクタの出典をコメントに残す

UIが変わったときに、どこを見直せばいいか分からなくなる。ファイル名と行番号を書く。

```yaml
# タブの id は RootTabViewController.swift:218 の
# "tabbar-button-" + item.imageName 規則から。imageName は RootTabViewModel.swift:42
- tapOn:
    id: "tabbar-button-tabbar_input_off"
```

## 落とし穴

**`launchApp` は起動完了を待たない。**
待ちを入れないとスプラッシュ表示中にタップが走る。しかもタップは `COMPLETED` と報告されるので、
ログだけ見ても失敗に見えない。起動直後は必ず既知の要素を待つ。

```yaml
- launchApp
- extendedWaitUntil:
    visible: "<ホームに必ず出る文言>"
    timeout: 60000
```

**タップ後の「画面が静止するまで待つ」がハングの原因になる。**
広告や常時アニメーションがある画面では静止しない。`waitToSettleTimeoutMs` を切る。

```yaml
- tapOn:
    text: "履歴を確認"
    waitToSettleTimeoutMs: 0
```

**テキストセレクタは部分一致する。**
`食料品` は `食料品品` にも当たる。画面内の別要素に当たって想定外の画面へ飛ぶこともある
（タブ名のつもりで書いた文字列が、本文中のリンクに当たって Web ビューが開いた実例がある）。
完全一致は `^...$`、識別子があるなら `id:` を優先する。

**大量のデータを持つ一覧画面では Maestro は動かない。**
アクセシビリティの階層を読んで操作するため、階層が大きいとスナップショットの取得自体に失敗する。
実測では記録9,868件の履歴画面で `maestro hierarchy` が120秒でタイムアウトし
`Device became unreachable during viewHierarchy` になった。
Maestro 側では回避できないので、この画面を通る確認はフローにせず sim-driver に任せる。

## 確認のしかた

書いたら1回走らせる。

```bash
maestro test --udid <UDID> --test-output-dir <出力先> <flow.yaml>
```

失敗したときは**再操作せずに成果物を読む。** 失敗した瞬間の画面階層とスクリーンショットが残っている。

```
<出力先>/<日時>/<フロー名>/screen-hierarchy/step-NNN-....json
<出力先>/<日時>/<フロー名>/screenshots/step-NNN-....png
```

階層のJSONは1画面で約6,000トークンあり、そのまま読むとスクリーンショットより高い。
ラベルだけに絞ると約200トークンになる。

```bash
python3 - <hierarchy.json> <<'PY'
import json, sys
def walk(n, out):
    a = n.get("attributes", {})
    label = a.get("accessibilityText") or a.get("text") or a.get("title") or ""
    if label.strip():
        out.append((label.strip(), a.get("bounds", "")))
    for c in n.get("children", []) or []:
        walk(c, out)
out = []
walk(json.load(open(sys.argv[1])), out)
seen = set()
for l, b in out:
    if (l, b) in seen: continue
    seen.add((l, b))
    print(f"{l}\t{b}")
PY
```

`bounds` はポイント座標で出るので、スクリーンショットのピクセル解像度との換算が要らない。

## README.md に書くこと

sim-driver が最初に読む。次を落とさない。

- 対象の bundle id、確認した端末とOS、確認した日
- フロー一覧と、それぞれ `launchApp` を含むかどうか
- そのアプリで踏んだ落とし穴
- **Maestro が動かない画面**（sim-driver がそこを自分で撮る判断に使う）
- 検証済みの範囲と、書いてあるが未検証のもの
