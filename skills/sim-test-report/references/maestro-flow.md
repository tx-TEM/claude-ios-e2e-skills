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

## どこに置くか

**共有する部品と、確認ごとのフローで置き場所が違う。**

| | 置き場所 | 理由 |
|---|---|---|
| `common/` と `README.md` | アプリのリポジトリの `maestro/`（無ければキャッシュ） | 汎用の導線はアプリの実装に紐づく。セレクタの出どころと同じリポジトリで版管理し、UIを変えた人が直せる状態にする |
| ケースごとのフロー | 常に端末ローカルのキャッシュ | 確認のたびに増えて肥大化する。その場限りのものなので共有しない |

解決先は `config.json` の `shared_dirs` と `cases_dir` で決まる。

ケース側は端末ローカルなので他の端末には無い。消えても作り直せる前提で使う。
UIが変わってフローが古くなっても、assert が失敗して落ちるだけで、
間違った証跡が残ることはない（「遷移ごとに assert を置く」を参照）。

## 2層に分ける

**PRごとに使い捨てにするのは末端だけにする。**

```
<共有フローのディレクトリ>/        # アプリのリポジトリの maestro/ など
  common/
    launch.yaml                 # 起動して初期画面まで。launchApp を含む
    goto_<画面名>.yaml            # 目的の画面まで。launchApp を含めない
    <汎用操作>.yaml               # env で引数を受ける
  README.md

<ケースフローのディレクトリ>/      # 端末ローカル
  <日付>-<テーマ>.yaml             # common を runFlow で呼び、固有部分だけ書く
```

ケース側は過去ログとして残す。同じ画面をまた撮ることになったとき、前回どう辿ったかが
そのまま動く形で残っているのが効く。共有側と別の場所に置くのは、再利用する部品と
その場限りのものが混ざると、どれを直せばいいか分からなくなるため。

ケースフローから共有フローを呼ぶときは**絶対パス**で参照する。`runFlow` は絶対パスを受ける。

```yaml
- runFlow: common/launch.yaml
- runFlow:
    file: common/goto_search_result.yaml
    env:
      KEYWORD: "<検索語>"
```

**`launchApp` を含めるかどうかで用途が変わる。** 含むフローはアプリを再起動して最初の画面に戻す。
sim-driver が途中から遷移だけさせたいときに使えないので、`README.md` にどちらかを必ず書く。

## 後から合成しやすい粒度で切る

`common/` のフローは、撮影時に別々の組み合わせで呼ばれる。そのつもりで切る。

- **1フロー1目的。** 「記録画面へ行く」と「記録を作る」は分ける。まとめて1本にすると、
  画面まで行きたいだけのときに使えない
- **終わりの状態を README に書く。** 次にどのフローを繋げられるかが決まる
- **引数は `env` で受ける。** 金額や検索語を埋め込むと、その値でしか使えなくなる
- **`launchApp` は起動用のフローだけに入れる。** 途中のフローに入れると、繋いだときにそこで
  最初の画面に戻ってしまう
- **開始時に必要な状態を仮定しすぎない。** 「ホームから」なのか「どの画面からでも」なのかを
  README に明記する。曖昧だと繋げられない

## セレクタの出典をコメントに残す

UIが変わったときに、どこを見直せばいいか分からなくなる。ファイル名と行番号を書く。

```yaml
# タブの id は MainTabViewController.swift:84 の
# "tab-" + item.name 規則から。name は MainTabViewModel.swift:20
- tapOn:
    id: "tab-search"
```

## 遷移ごとに assert を置く

フロー自身が「遷移できたか」を判定できるようにする。**これがあって初めて、撮影までLLMが画面を見ずに済む。**

```yaml
- tapOn:
    id: "tab-search"
- extendedWaitUntil:
    visible: "<その画面に必ず出る文言>"
    timeout: 20000
```

失敗すれば Maestro が非ゼロで終了し、どのセレクタが見つからなかったかをテキストで出す。
スクリーンショットを撮ってLLMに判定させる必要がない。成功時の出力も数十行のテキストで、
画像1枚（約2,000トークン）より桁で安い。

**`tapOn` の `COMPLETED` は到達を意味しない。**
「セレクタに一致する何かをタップした」というだけで、意図した画面に着いたかは別の話。
テキストセレクタが画面内の想定外の要素に当たっていても `COMPLETED` と出る。

そのため assert の無いフローを盲目実行すると、**撮れないのではなく、間違った画面の証跡が撮れる。**
撮り直しにも気づけないので、都度スクリーンショットを見て進むより結果が悪い。省略しないこと。

判定の分担はこうなる。

| 何を判定するか | 誰が | 形式 |
|---|---|---|
| 遷移できたか | フロー内の assert | テキスト・決定的・LLM不要 |
| 確認項目がOKか | 呼び出し元が証跡PNGを見る | 画像・最後に1回だけ |

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
    text: "<ダイアログのボタン>"
    waitToSettleTimeoutMs: 0
```

**テキストセレクタは部分一致する。**
`設定` は `設定変更` にも当たる。画面内の別要素に当たって想定外の画面へ飛ぶこともある
（タブ名のつもりで書いた文字列が、本文中のリンクに当たって Web ビューが開くことがある）。
完全一致は `^...$`、識別子があるなら `id:` を優先する。

**大量のデータを持つ一覧画面では Maestro は動かない。**
アクセシビリティの階層を読んで操作するため、階層が大きいとスナップショットの取得自体に失敗する。
数千件規模の一覧画面で `maestro hierarchy` が120秒でタイムアウトし
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
