# 実際のやり取りの例

検証用のアプリ（青空文庫の作品を探して読む iOS アプリ。画面マップあり）で、`sim-test-report` を通しで動かしたときの記録。載せているのは、依頼・テストケースのレビュー（全文）・報告のやり取りと、plan と manifest の先頭の部分、1つのテストケースの後始末と Safari に出る項目の Maestro のフロー、進捗ログ。

- 端末: iPhone 17 Pro（iOS 26.5）のシミュレーター
- 入口: 差分なし（アプリ全体の主な導線の疎通確認）
- 結果: 14項目すべて OK（エラー系の4項目はユーザーの判断で外した）

## 1. 依頼

> **ユーザー:** シミュレーターでテストして。テストケースは一般的なもので

メインのエージェントは `test-case-builder` を呼び、画面マップ（さがす・作品詳細・リーダー・おすすめの4画面）からテストケースを立てさせる。経路が組めることは `manifest.py` がこの時点で確かめる。

## 2. テストケースのレビュー

撮る前に、テストケースをユーザーに見せて合意をとる。以下はレビューに出したものの全文。テストケースの題が見出し、`［］` は撮る画面。`→ ここで〜で戻す` は、後に状態を残すテストケースの後始末で、画面マップの `leaves`（後に残る状態）と `reset`（既定に戻す操作）からスクリプトが決めたもの。

> **Claude:** テストケース案ができました。撮影の前に確認をお願いします。
>
> iPhone 17 Pro (iOS 26.5) で撮ります。
>
> **起動すると一覧画面が出る**
>
> 1. 起動するとさがす画面が出て、作品の一覧が表示される［さがす］
>    - 操作: なし（着いた状態を見る）
>    - 期待: さがす画面が出て、作品の行が複数並んでいる
>
> **キーワードで絞り込んだ一覧から詳細を開き、戻ると絞り込んだままの一覧に戻る**
>
> 2. 作品名を入力すると、入力が止まってから絞り込みが走る［さがす］
>    - 操作: 検索欄に入力する（打つ語は撮るときに決める）
>    - 期待: 入力欄に出ている語を、一覧に残っている行がすべて作品名に含んでいる
> 3. 絞り込んだ一覧から行をタップすると詳細に移る［作品詳細］
>    - 操作: 見えている1件目の行をタップする
>    - 期待: 詳細が開き、作品名がタップした行と一致する
> 4. 詳細から戻ると、絞り込んだままの一覧に戻る［さがす］
>    - 操作: 戻るボタンを押す
>    - 期待: さがす画面が出て、test_03 でタップした行が一覧に見えている
>
> → ここでクリアボタンで戻す（入力欄の語と絞り込みの結果が残るため）
>
> **検索キーを押すとデバウンスを待たずに絞り込みが確定し、キーボードが閉じる**
>
> 5. 検索キーを押すと絞り込みが確定し、キーボードが閉じる［さがす］
>    - 操作: 検索欄に入力して（打つ語は撮るときに決める）、検索キーを押す
>    - 期待: キーボードが閉じていて、入力欄に出ている語を一覧に残っている行がすべて作品名に含んでいる
>
> → ここでクリアボタンで戻す（入力欄の語と絞り込みの結果が残るため）
>
> **絞り込み対象を作者に切り替えられる**
>
> 6. 「作者」を選ぶと絞り込み対象が作者に切り替わる［さがす］
>    - 操作: 絞り込み対象の「作者」を押す
>    - 期待: 作者のセグメントが選ばれた状態になっている
>
> → ここで絞り込み対象の「作品名」を押して戻す（対象が作者のまま残るため）
>
> **おすすめタブに切り替え、作品を開いて戻るとおすすめの一覧に戻る**
>
> 7. おすすめタブに切り替えると、カテゴリーごとのカルーセルが出る［おすすめ］
>    - 操作: 「おすすめ」タブを押す
>    - 期待: おすすめ画面が出て、カテゴリーの見出しと作品のカードが横に並んでいる
> 8. 作品のカードをタップすると詳細に移る［作品詳細］
>    - 操作: 見えている1件目のカルーセルで、見えている1件目のカードをタップする
>    - 期待: 詳細が開き、作品名がタップしたカードと一致する
> 9. おすすめから開いた詳細から戻ると、おすすめの一覧に戻る［おすすめ］
>    - 操作: 戻るボタンを押す
>    - 期待: おすすめ画面が出て、test_08 でタップしたカードが見えている
>
> **さがすタブに戻れる**
>
> 10. さがすタブに切り替えると一覧画面に戻る［さがす］
>     - 操作: 「さがす」タブを押す
>     - 期待: さがす画面が出て、作品の行が並んでいる
>
> **詳細から本文を読め、リーダーから戻ると詳細に戻る**
>
> 11. 一覧の作品行をタップすると詳細に移る［作品詳細］
>     - 操作: さがす画面で見えている1件目の行をタップする
>     - 期待: 詳細が開き、書誌情報とリンクのセクションが見えている
> 12. 本文を持つ作品は「読む」からリーダーが開く［リーダー］
>     - 前提: 本文の HTML を持つ作品のとき（11 で開いた1件目の作品がこれに当たる必要がある）
>     - 操作: 「読む」を押す
>     - 期待: リーダーが開き、本文が出ていて、タブバーが隠れている
> 13. リーダーから戻ると詳細に戻る［作品詳細］
>     - 操作: 戻るボタンを押す
>     - 期待: 詳細が出て、test_11 で開いた作品の書誌情報が見えている
>
> **詳細から図書カードを Safari で開ける**
>
> 14. 図書カードを開くと、青空文庫のページが Safari で開く［作品詳細］
>     - 操作: 図書カードのリンクを押し、「図書カード」の文字が出るまで待つ
>     - 期待: Safari が開き、図書カードのページが出ている
>
> **一覧の取得に失敗したときは再読み込みで取り直せる**
>
> 15. 一覧の取得に失敗したときは再読み込みで取り直せる［さがす］
>     - 前提: 取得に失敗したとき
>     - 操作: 再読み込みボタンを押す
>     - 期待: さがす画面に作品の行が並ぶ
>     - ⚠ 一時コードが要る（BrowseViewModel の取得を失敗させる）
>
> **詳細の取得に失敗したときは再読み込みで取り直せる**
>
> 16. 詳細の取得に失敗したときは再読み込みで取り直せる［作品詳細］
>     - 前提: 取得に失敗したとき
>     - 操作: さがす画面で見えている1件目の行から詳細を開き、再読み込みボタンを押す
>     - 期待: 詳細が出て、書誌情報とリンクのセクションが見えている
>     - ⚠ 一時コードが要る（DetailViewModel の取得を失敗させる）
>
> **本文の取得に失敗したときは再読み込みで取り直せる**
>
> 17. 本文の取得に失敗したときは再読み込みで取り直せる［リーダー］
>     - 前提: 本文の HTML を持つ作品のとき、本文の取得に失敗したとき
>     - 操作: 再読み込みボタンを押す
>     - 期待: エラー表示が消え、本文が出ている
>     - ⚠ 一時コードが要る（リーダーの本文取得を失敗させる）
>
> **おすすめの取得に失敗したときは再読み込みで取り直せる**
>
> 18. おすすめの取得に失敗したときは再読み込みで取り直せる［おすすめ］
>     - 前提: 取得に失敗したとき
>     - 操作: 再読み込みボタンを押す
>     - 期待: おすすめ画面が出て、カテゴリーのカルーセルが並んでいる
>     - ⚠ 一時コードが要る（RecommendViewModel の取得を失敗させる）
>
> 入れていないもの:
> - **無限スクロールと本文のスクロール**: 静止画1枚では合否が決められないため
> - **「もっと見る」**: まだ何も起きない作りのため
> - **作者で絞り込んだ結果**: 絞り込んだ結果が合っているかは機械的に待てないので、6 では選択が切り替わることだけを見ます
>
> 決めてほしいことが2つあります。
>
> 1. **15〜18 のエラー系をどうするか。** 仕込む場合は、失敗させる一時コードを入れてビルドし、撮ったあとに元に戻します。外す場合はこの4件をスコープ外としてレポートに書きます。
> 2. **12 の前提。** さがす画面の1件目が本文の HTML を持つ作品でないと、12〜13 は通りません。1件目のまま進めて、通らなかったらそのとき対処する形でよいですか。

> **ユーザー:** エラー系は外して、12は1件目のままで進めて

## 3. plan.json（先頭のテストケース3つ）

レビューに出した内容の元。`test-case-builder` が書き、`manifest.py` がここからフローとマニフェストを作る。項目は経路を持たず、`from`（始める画面）と `do`（叩く操作）だけを書く。前の画面から `from` まではスクリプトが画面マップで繋ぐ。全8テストケース（エラー系を外したあと）のうち先頭の3つ。

```json
{
  "app": "tx-tem.AozoraReaderClient",
  "repo": "/Users/yoshiki-tx/Program/AozoraReader",
  "cases": [
    {
      "title": "起動すると一覧画面が出る",
      "launch": true,
      "items": [
        {
          "from": "browse",
          "title": "起動するとさがす画面が出て、作品の一覧が表示される",
          "expect": "さがす画面が出て、作品の行が複数並んでいる"
        }
      ]
    },
    {
      "title": "キーワードで絞り込んだ一覧から詳細を開き、戻ると絞り込んだままの一覧に戻る",
      "items": [
        {
          "from": "browse",
          "do": [
            {
              "op": "text:browse.search_field",
              "runtime": true
            },
            {
              "op": "see:browse.book_row.*",
              "runtime": true
            }
          ],
          "title": "作品名を入力すると、入力が止まってから絞り込みが走る",
          "expect": "入力欄に出ている語を、一覧に残っている行がすべて作品名に含む"
        },
        {
          "id": "open",
          "from": "browse",
          "do": [
            "tap:browse.book_row.*"
          ],
          "title": "絞り込んだ一覧から行をタップすると詳細に移る",
          "expect": "詳細が開き、作品名がタップした行と一致する"
        },
        {
          "from": "detail",
          "do": [
            "tap:BackButton"
          ],
          "title": "詳細から戻ると、絞り込んだままの一覧に戻る",
          "expect": "さがす画面が出て、{open} でタップした行が一覧に見えている"
        }
      ]
    },
    {
      "title": "検索キーを押すとデバウンスを待たずに絞り込みが確定し、キーボードが閉じる",
      "items": [
        {
          "from": "browse",
          "do": [
            {
              "op": "text:browse.search_field",
              "runtime": true
            },
            "tap:Search",
            {
              "op": "see:browse.book_row.*",
              "runtime": true
            }
          ],
          "title": "検索キーを押すと絞り込みが確定し、キーボードが閉じる",
          "expect": "キーボードが閉じていて、入力欄に出ている語を一覧に残っている行がすべて作品名に含む"
        }
      ]
    }
  ]
}
```

- **テストケース（`cases`）の中の項目は、前の項目の結果を当てにしてよい。** 期待の `{open}` は同じテストケースの前の項目を指し、マニフェストでは `test_03（…）` に展開される
- **打つ語と、待つ行の語は `runtime`。** データに依る値は plan に書かず、撮影のときに画面を見て決める
- **検索キーの項目は、打つ操作から `do` に並べる。** 準備のためだけに項目を立てない
- **後に残る状態の後始末は plan に書かない。** 画面マップの `leaves` / `reset` からスクリプトが決める
- **`launch`** は、起動したときの表示そのものを確かめるテストケースに付ける

## 4. manifest.json（先頭の部分）

`manifest.py` が plan から作る定義ファイル。撮影（`run_flows.py`）、判定（`evidence-judge`）、レポート（`build_report.py`）はここだけを読む。下は判定まで終わったあとのもので、`cases` は先頭4つ、`sections` は先頭2つ。

```json
{
  "repo": "/Users/yoshiki-tx/Program/AozoraReader",
  "devices": {
    "iphone": {
      "udid": "0606D63A-C712-4026-B9E8-06A55AB4B7EF",
      "model": "iPhone 17 Pro",
      "os": "iOS 26.5"
    }
  },
  "flows": "/Users/yoshiki-tx/Program/claude-ios-e2e-skills/skills/sim-test-report/.work/flows/sim-test-report-general",
  "cases": [
    {
      "title": "起動すると一覧画面が出る",
      "launch": true,
      "explore": null,
      "after": null,
      "items": [
        "test_01"
      ]
    },
    {
      "title": "キーワードで絞り込んだ一覧から詳細を開き、戻ると絞り込んだままの一覧に戻る",
      "launch": false,
      "explore": null,
      "after": {
        "relaunch": false,
        "resets": [
          "tap:Clear text"
        ],
        "leaves": [
          "入力欄の語と絞り込みの結果が残る"
        ]
      },
      "items": [
        "test_02",
        "test_03",
        "test_04"
      ]
    },
    {
      "title": "検索キーを押すとデバウンスを待たずに絞り込みが確定し、キーボードが閉じる",
      "launch": false,
      "explore": null,
      "after": {
        "relaunch": false,
        "resets": [
          "tap:Clear text"
        ],
        "leaves": [
          "入力欄の語と絞り込みの結果が残る"
        ]
      },
      "items": [
        "test_05"
      ]
    },
    {
      "title": "絞り込み対象を作者に切り替えられる",
      "launch": false,
      "explore": null,
      "after": {
        "relaunch": false,
        "resets": [
          "tap:browse.target_picker.title"
        ],
        "leaves": [
          "絞り込みの対象が作者のまま残る"
        ]
      },
      "items": [
        "test_06"
      ]
    }
  ],
  "sections": [
    {
      "name": "test_01",
      "title": "起動するとさがす画面が出て、作品の一覧が表示される",
      "from": "browse",
      "case": "起動すると一覧画面が出る",
      "do": [],
      "screen": "browse",
      "expect": "さがす画面が出て、作品の行が複数並んでいる",
      "checked": "browse",
      "launch": true,
      "when": [],
      "parts": [
        {
          "flow": "test_01.yaml",
          "decide": null
        }
      ],
      "flow": "test_01.yaml",
      "picks": {},
      "sees": {},
      "input_use": {},
      "devices": {
        "iphone": {
          "inputs": {},
          "picked": {}
        }
      },
      "images": [
        {
          "src": "shots/iphone/test_01.png",
          "label": "iPhone"
        }
      ],
      "desc": "さがす画面が起動し、BOITEUX・BOITEUSE、LE URINE、AU MAGASIN DE NOUVEAUTES など作品の行が複数並んでいる。",
      "note": "",
      "result": "OK"
    },
    {
      "name": "test_02",
      "title": "作品名を入力すると、入力が止まってから絞り込みが走る",
      "from": "browse",
      "case": "キーワードで絞り込んだ一覧から詳細を開き、戻ると絞り込んだままの一覧に戻る",
      "do": [
        {
          "op": "text:browse.search_field",
          "runtime": true
        },
        {
          "op": "see:browse.book_row.*",
          "runtime": true
        }
      ],
      "screen": "browse",
      "expect": "入力欄に出ている語を、一覧に残っている行がすべて作品名に含む",
      "checked": "browse.book_row.*",
      "launch": false,
      "when": [],
      "parts": [
        {
          "flow": "test_02.1.yaml",
          "decide": null
        },
        {
          "flow": "test_02.2.yaml",
          "decide": "BROWSE_SEARCH_FIELD"
        },
        {
          "flow": "test_02.yaml",
          "decide": "BROWSE_BOOK_ROW"
        }
      ],
      "flow": "test_02.yaml",
      "picks": {},
      "sees": {
        "BROWSE_BOOK_ROW": {
          "pattern": "browse.book_row.*",
          "exclude": []
        }
      },
      "input_use": {
        "BROWSE_SEARCH_FIELD": "text",
        "BROWSE_BOOK_ROW": "selector"
      },
      "devices": {
        "iphone": {
          "inputs": {
            "BROWSE_SEARCH_FIELD": "春",
            "BROWSE_BOOK_ROW": "春"
          },
          "picked": {}
        }
      },
      "images": [
        {
          "src": "shots/iphone/test_02.png",
          "label": "iPhone"
        }
      ],
      "desc": "「春」と入力すると入力が止まってから絞り込みが走り、一覧に残った行はすべて作品名に「春」を含む（熱海の春、天草の春、田舎の新春、イーハトーボ農学校の春、Ｆ村での春、絵本の春、演劇的青春への釈明、帰らぬ春、春日若宮御祭の研究、北国の春）。",
      "note": "",
      "result": "OK"
    }
  ]
}
```

- **`cases` の `after`** はテストケースの後始末。画面マップの `leaves` / `reset` から flowgen が決めたもので、`resets` は叩いて戻す操作、`relaunch` は次の頭で起動し直すか。レビューの「→ ここで〜で戻す」はここから読み上げる
- **`parts`** は撮影時に値を決めるために割ったフロー。`decide` がその本の前に決める値
- **`devices.iphone.inputs`** は撮影時に決めた値（打った語「春」と、待った行の語）
- **`desc` / `result`** は `evidence-judge` が証跡とダンプを読んで埋めたもの

## 5. Maestro のフロー（2つの項目）

`manifest.py` が plan から項目ごとに書く。撮影ごとに1本で、撮る地点でフローが切れる（証跡と同名のダンプをその場で取るため）。

### 後始末のあとに、打って検索キーを押す（test_05）

前のテストケース（絞り込んで詳細を開いて戻る）の後始末が、このテストケースの最初のフローの頭に入る。クリアボタン（画面マップの `reset`）で戻し、打ったテストケースだったのでキーボードを閉じる。そのあと、打つ語と待つ行の語を撮影時に決めるので、フローは3本に割れる。

```yaml
# test_05.1.yaml — 前のテストケースの後始末をして、検索欄が見えるところで止まる（ここで打つ語を決める）
# 続き: browse から
- extendedWaitUntil:
    visible:
      id: '^browse$'
    timeout: 10000
# browse: tap Clear text [ラベル] — 入力を消し、絞り込みを解除して全作品に戻す
- scrollUntilVisible:
    element:
      text: '.*Clear\ text.*'
    direction: DOWN
    timeout: 60000
    optional: true
- scrollUntilVisible:
    element:
      text: '.*Clear\ text.*'
    direction: UP
    timeout: 60000
- tapOn:
    text: '.*Clear\ text.*'
- extendedWaitUntil:
    visible:
      id: '^browse\.search_field$'
    timeout: 10000
# browse: hideKeyboard — 打ったあとに開いたキーボードを閉じる
- hideKeyboard
# 次で使う browse.search_field が見えるまでスクロールして止める（ここで打つ文字を決める）
- scrollUntilVisible:
    element:
      id: '^browse\.search_field$'
    direction: DOWN
    timeout: 60000
    optional: true
- scrollUntilVisible:
    element:
      id: '^browse\.search_field$'
    direction: UP
    timeout: 60000
```

```yaml
# test_05.2.yaml — 決めた語を打って検索キーを押す（ここで待つ行の語を決める）
# 続き: browse から
- extendedWaitUntil:
    visible:
      id: '^browse$'
    timeout: 10000
# browse: text browse.search_field — 入力が 300ms 止まるか変換が確定すると絞り込みを送り、一覧が入れ替わる
- tapOn:
    id: '^browse\.search_field$'
- eraseText
- inputText: ${BROWSE_SEARCH_FIELD}
- extendedWaitUntil:
    visible:
      id: '^browse\.search_field$'
    timeout: 10000
# browse: tap Search — デバウンスを待たずに絞り込みを送り、キーボードを閉じる
- scrollUntilVisible:
    element:
      id: '^Search$'
    direction: DOWN
    timeout: 60000
    optional: true
- scrollUntilVisible:
    element:
      id: '^Search$'
    direction: UP
    timeout: 60000
- tapOn:
    id: '^Search$'

# 補足: 「tap Search」の結果を確かめる expect がマップに無い
```

```yaml
# test_05.yaml — その語を含む行が出るまで待って撮る
# 続き: browse から
- extendedWaitUntil:
    visible:
      id: '^browse$'
    timeout: 10000
# browse: see browse.book_row.*（${BROWSE_BOOK_ROW} を含む行） — 作品の行（ID は表示中の作品名）
- scrollUntilVisible:
    element:
      id: '^browse\.book_row\..*${BROWSE_BOOK_ROW}.*'
    direction: DOWN
    centerElement: true
    timeout: 60000
    optional: true
- scrollUntilVisible:
    element:
      id: '^browse\.book_row\..*${BROWSE_BOOK_ROW}.*'
    direction: UP
    timeout: 60000
- takeScreenshot: '${SHOTS}/test_05'
```

止まるたびに、メインのエージェントが画面のダンプを見て値を書き、同じコマンドを叩き直すと続きから走る。今回は `BROWSE_SEARCH_FIELD` = `夜`、`BROWSE_BOOK_ROW` = `夜`。

### アプリの外に出る（test_14）

図書カードは詳細から始める1項目。Safari に出たまま、ページの文言を待って撮る。

```yaml
# 続き: detail から
- extendedWaitUntil:
    visible:
      id: '^detail$'
    timeout: 10000
# detail: tap detail.card_link — 青空文庫の図書カードを Safari で開く
- scrollUntilVisible:
    element:
      id: '^detail\.card_link$'
    direction: DOWN
    timeout: 60000
    optional: true
- scrollUntilVisible:
    element:
      id: '^detail\.card_link$'
    direction: UP
    timeout: 60000
- tapOn:
    id: '^detail\.card_link$'
# アプリの外（safari）に出る。確かめずに、外に居るまま撮る
- waitForAnimationToEnd:
    timeout: 3000
# detail: 「図書カード」が出るまで待つ
- extendedWaitUntil:
    visible:
      text: '.*図書カード.*'
    timeout: 10000
- takeScreenshot: '${SHOTS}/test_14'
```

## 6. 撮影と判定

撮影は `run_flows.py` を1回叩くだけ。進捗ログ:

```
test_01 撮影済み
test_02 撮影せず 入力が未定（BROWSE_SEARCH_FIELD）
test_02 撮影済み
test_03 撮影済み（選んだ: BROWSE_BOOK_ROW=browse.book_row.熱海の春）
test_04 撮影済み
test_05 撮影済み
test_06 撮影済み
test_07 撮影済み
test_08 撮影済み（選んだ: RECOMMEND_CAROUSEL=recommend.carousel.総記, RECOMMEND_BOOK=recommend.book.読書法）
test_09 撮影済み
test_10 撮影済み
test_11 撮影済み（選んだ: BROWSE_BOOK_ROW=browse.book_row.BOITEUX ・ BOITEUSE）
test_12 撮影済み
test_13 撮影済み
test_14 撮影済み
```

撮り終えたら `evidence-judge` が証跡とダンプを読んで OK / NG を付け、`build_report.py` がレポートを組む。

## 7. 報告

> **Claude:** iPhone 17 Pro (iOS 26.5) で14件テストし、すべて期待どおりに動きました（NG なし）。エラー系4件は指示どおり外し、レポートの「確認していないこと」に書いてあります。
>
> HTML のレポートと、PR に貼る用の PNG を送りました。PNG はアップロードが 400 で失敗したので、スマホやブラウザからは見えず、このデスクトップアプリの中でだけ見えます。ファイルはすべて `~/Desktop/sim-test-report-general/` にあります。
>
> **確認できたこと**
> - **起動:** さがす画面に作品の一覧が出ます。
> - **キーワード検索:** 「春」「夜」で絞り込むと、残った行はすべて作品名にその語を含んでいました。検索キーを押すとキーボードも閉じます。
> - **作品名/作者の切り替え:** 「作者」を選ぶと、選んだ状態になります。
> - **一覧→詳細→戻る:** 「熱海の春」の詳細を開いて戻ると、絞り込んだままの一覧に戻ります。
> - **おすすめ:** カルーセルから「読書法」の詳細を開き、戻るとおすすめ画面に戻ります。さがすタブへの切り替えもできます。
> - **本文を読む:** さがす画面の1件目「BOITEUX・BOITEUSE」で試しました。本文があったので「読む」でリーダーが開き、タブバーは隠れていました。戻ると詳細に戻ります。
> - **図書カード:** リンクを押すと Safari で青空文庫のページが開きます。
>
> **画面マップに足す候補**
> - 詳細からさがす画面に戻ると、検索欄にカーソルが戻り、キーボードがまた開いていました。マップには「入力した語と絞り込みの結果が残る」としか書いていません。不具合ではなさそうですが、意図どおりなら書き足す候補です。
> - 次の操作には、たどり着いたことを自動で確かめる手がかりがありません: さがす画面の検索キーとスクロール、リーダーのスクロール、おすすめの「もっと見る」。
> - （中略）
> - 絞り込んで0件になったときの表示がマップに無いので、テストできませんでした。

このとき届いたレポートの1枚画像（PR に貼る用）の先頭。全14項目のうち、最初の3項目まで:

![このセッションの動作確認レポートの先頭（3項目まで）](example-session-report.png)
