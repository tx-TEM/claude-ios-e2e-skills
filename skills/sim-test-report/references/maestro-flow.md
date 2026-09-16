# Maestro のフローを作る

`flow-author` がこの手順で作る。

## 原則: 画面を突っつかず、ソースから確定させる

シミュレーターを操作しながらセレクタを探すと、画面を見る往復が発生して Maestro を使う利点が消える。**アプリのソースを読んで導線とセレクタを先に確定させ、YAMLを一気に書く。** 実行は確認のためで、試行錯誤の手段ではない。

## 調べる場所

| 欲しいもの | 見る場所 |
|---|---|
| 安定した識別子 | `grep -rn 'accessibilityIdentifier' --include='*.swift'` |
| ボタン・画面の文言 | `Localizable.xcstrings` / `.strings` を `tr("キー")` から逆引き |
| 画像ボタンの名前 | `UIImage(resource:)` / `UIImage(named:)` のアセット名。階層にはこの名前で出る |
| 遷移の分岐 | `didSelectRowAt` / `performSegue` / `pushViewController` |
| ボタンの並びと題字 | `.storyboard` / `.xib` の `title=` |

`String(localized: .fooBar)` 形式は `.xcstrings` のキーが `fooBar` ではなくキャメルケース変換前の文字列のことがある。日本語側から逆引きすると確実。

**逆引きは必要な文言をまとめて1回で引く。** 1語ずつ引かない。検索そのものは1秒もかからず、かかるのは往復のほう。

```bash
python3 - <<'PY'
import json, glob, sys
targets = {"<文言1>", "<文言2>", "<文言3>"}          # 要るものを全部並べる
for f in glob.glob("**/*.xcstrings", recursive=True):
    if any(x in f for x in ("Pods", "worktrees", ".bundle")): continue
    try: d = json.load(open(f))
    except Exception: continue
    for k, v in (d.get("strings") or {}).items():
        ja = (((v.get("localizations") or {}).get("ja") or {}).get("stringUnit") or {}).get("value")
        if ja and ja.strip() in targets:
            print(f"{ja}\t{k}\t{f}")
PY
```

出たキーも、使用箇所をまとめて1回で引く。

```bash
grep -rnE 'tr\("(<キー1>|<キー2>|<キー3>)"\)' --include='*.swift' <ソース>
```

storyboard の `title` と実機に出る文字列が違うことがある。最後は実行して確かめる。

## どこに置くか

**共有する部品と、確認ごとのフローで置き場所が違う。**

| | 置き場所 |
|---|---|
| `common/` と `README.md` | アプリのリポジトリの `maestro/`（無ければキャッシュ） |
| ケースごとのフロー | 常に端末ローカルのキャッシュ |

解決先は `config.json` の `shared_dirs` と `cases_dir` で決まる。

ケース側は端末ローカルなので他の端末には無い。消えても作り直せる前提で使う。

## 2層に分ける

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

ケース側は過去ログとして残す。同じ画面をまた撮ることになったとき、前回どう辿ったかがそのまま動く形で残っているのが効く。

ケースフローから共有フローを呼ぶときは**絶対パス**で参照する。`runFlow` は絶対パスを受ける。

```yaml
- runFlow: <共有フローのディレクトリ>/common/launch.yaml
- runFlow:
    file: <共有フローのディレクトリ>/common/goto_search_result.yaml
    env:
      KEYWORD: "<検索語>"
```

**`launchApp` を含めるかどうかで用途が変わる。** 含むフローはアプリを再起動して最初の画面に戻す。sim-driver が途中から遷移だけさせたいときに使えないので、`README.md` にどちらかを必ず書く。

## 後から合成しやすい粒度で切る

`common/` のフローは、撮影時に別々の組み合わせで呼ばれる。そのつもりで切る。

- **1フロー1目的。** 「ある画面へ行く」と「そこで操作する」は分ける。まとめて1本にすると、画面まで行きたいだけのときに使えない
- **終わりの状態を README に書く。** 次にどのフローを繋げられるかが決まる
- **引数は `env` で受ける。** 値を埋め込むと、その値でしか使えなくなる
- **`launchApp` は起動用のフローだけに入れる**
- **開始時に必要な状態を仮定しすぎない。** 「ホームから」なのか「どの画面からでも」なのかを README に明記する。曖昧だと繋げられない

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

**`tapOn` の `COMPLETED` は到達を意味しない。** 「セレクタに一致する何かをタップした」というだけで、意図した画面に着いたかは別。想定外の要素に当たっていても `COMPLETED` と出る。

そのため assert の無いフローを盲目実行すると、**撮れないのではなく、間違った画面の証跡が撮れる。** 撮り直しにも気づけない。省略しないこと。

短時間で消えるUIにも効く。`assertVisible` はその瞬間の画面に対する判定なので、トーストのように数秒で消えるものでも直後に置けば捉えられる。表示時間を延ばす一時コードは要らない。

| 何を判定するか | 誰が | 形式 |
|---|---|---|
| 遷移できたか | フロー内の assert | テキスト・決定的・LLM不要 |
| 確認項目がOKか | 呼び出し元が証跡PNGを見る | 画像・最後に1回だけ |

## セレクタが安定しないなら、アプリ側に識別子を足す

テキストはローカライズで変わり、件数や記号が付き、同じ文言が複数ある。`accessibilityIdentifier` はそのどれにも影響されない。**VoiceOver は読まない**ので、付けても実際のアクセシビリティ体験は変わらない。

```swift
button.accessibilityIdentifier = "<画面>-<役割>"
```

```yaml
- tapOn:
    id: "<識別子>"
```

**ただし全面的に振らない。** アプリ側のコード変更なのでチームの合意が要る。**実際に壊れた場所にだけ足す。** 撮影で落ちた記録が `README.md` に溜まるので、それが候補のリストになる。

**付けても効かない要素がある。** `UIBarButtonItem` のようにビューを直接持たないものは、識別子が内部のビューに伝わらず階層に現れないことがある。その場合は画像のアセット名など、実際に階層へ出ている値を使う。

## 落とし穴

**`launchApp` は起動完了を待たない。** 待ちを入れないとスプラッシュ表示中にタップが走る。しかもタップは `COMPLETED` と報告されるので、ログだけ見ても失敗に見えない。

**`launchApp` は直前に開いていた画面を復元する。** 「起動すれば初期画面に出る」は初回起動でしか成り立たない。起動用のフローでは、初期画面へ明示的に移動してから待つ。

```yaml
- launchApp
- extendedWaitUntil:          # タブバーが出れば起動は完了している
    visible:
      id: "<初期画面のタブのid>"
    timeout: 60000
- tapOn:
    id: "<初期画面のタブのid>"
- extendedWaitUntil:
    visible: "<その画面に必ず出る文言>"
    timeout: 30000
```

**タップ後の「画面が静止するまで待つ」がハングの原因になる。** 広告や常時アニメーションがある画面では静止しない。`waitToSettleTimeoutMs` を切る。

```yaml
- tapOn:
    text: "<ダイアログのボタン>"
    waitToSettleTimeoutMs: 0
```

**テキストセレクタは部分一致しない。全文一致の正規表現として扱われる。** 画面に「予定（2）」と出ているとき、`予定` では一致しない。前後を `.*` で囲む。

```yaml
- assertVisible: "予定.*"      # 前方一致させたいとき
- assertVisible: ".*予定.*"    # 部分一致させたいとき
```

`^...$` を付ける必要はない。既に全文一致なので冗長になるだけ。

**同じ文言の要素が複数あると、意図しないほうに当たる。** タブ名のつもりで書いた文字列が本文中の同じ文言に当たって別の画面が開くことがある。`id:` があるならそちらを優先し、無ければ `index:` で絞る。

**大量のデータを持つ一覧画面では Maestro は動かない。** 階層が大きいとスナップショットの取得自体に失敗する。症状は2つあり、どちらも同じ原因。

```
Device became unreachable during viewHierarchy
App crashed or stopped while executing flow     ← クラッシュログは残らない
```

後者はアプリの不具合に見えるが、`~/Library/Logs/DiagnosticReports` と
シミュレーターのコンテナ内を見てもクラッシュログが無ければ階層取得の失敗。**リトライしない。**

**制約は画面ではなくデータ量に付く。** 件数が少なければ同じ画面でも通る。そして
**その画面を経由して到達する画面も踏めない。** 一覧から切り替えるサブページなどが該当する。

1回試すのに数分かかるので、`README.md` の記録を必ず先に読む。

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

階層のJSONは1画面で約6,000トークンあり、そのまま読むとスクリーンショットより高い。ラベルだけに絞ると約200トークンになる。

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

## 実行ごとの出力の扱い

`--test-output-dir` を指定すると、実行のたびに `<日時>/` が積まれる。指定しないと `~/.maestro/tests/` に溜まり続ける。

**撮り終えた直後には消さない。** 失敗の原因を後から追うのはこの成果物で、撮り直しの判断にも要る。代わりに**実行のはじめに古い出力を消す。**

```bash
rm -rf <出力先>
maestro test --udid <UDID> --test-output-dir <出力先> <flow.yaml>
```

証跡は証跡ディレクトリへコピーした時点で独立しているので、出力先を消しても残る。

## README.md に書くこと

sim-driver が最初に読む。次を落とさない。

- 対象の bundle id、確認した端末とOS、確認した日
- フロー一覧と、それぞれ `launchApp` を含むかどうか、開始時に想定する状態、終わったときの画面
- そのアプリで踏んだ落とし穴
- **Maestro が動かない画面。** 撮影で落ちるたびに書き足す。何をしようとして、どのエラーで落ちたかまで書く。**「この画面が読めなかった」と書き、「この種の画面は読めない」と一般化しない**
- 検証済みの範囲と、書いてあるが未検証のもの
