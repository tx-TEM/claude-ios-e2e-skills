# 実際のやり取りの例

検証用のアプリ（青空文庫の作品を探して読む iOS アプリ。画面マップあり）で、`sim-test-report` を通しで動かしたときの記録。載せているのは、依頼・テストケースのレビュー（全文）・報告のやり取りと、plan と manifest の先頭の部分、テストケースの境目で走った Maestro のフロー2本、進捗ログ。

- 端末: iPhone 17 Pro（iOS 26.5）のシミュレーター
- 入口: 差分なし（アプリ全体の主な導線の疎通確認）
- 結果: 15項目すべて OK（通信エラーの再読み込みは、サーバーの状態を変えないと出せないので入れていない）

## 1. 依頼

> **ユーザー:** シミュレーターでテストして。テストケースは一般的なもので。コード書き換えとかサーバーの状態を変えるものは無しでお願い。

メインのエージェントは `test-case-builder` を呼び、画面マップ（さがす・作品詳細・リーダー・おすすめの4画面）からテストケースを立てさせる。テストケースごとに、起動直後の画面から経路が組めることを `manifest.py` がこの時点で確かめる。今回は全部組めた（探索で撮る項目は無し）。

## 2. テストケースのレビュー

撮る前に、テストケースをユーザーに見せて合意をとる。以下はレビューに出したものの全文。テストケースの題が見出し、`［］` は撮る画面。`→ ここで〜で戻す` は、後に状態を残すテストケースの後始末で、画面マップの `leaves`（後に残る状態）と `reset`（既定に戻す操作）からスクリプトが決めたもの。

> **Claude:** テストケースの案です。iPhone 17 Pro (iOS 26.5) で撮ります。サーバーの状態やデータを変える操作はこのアプリに無いので、全項目が見るだけの操作です。コードに手を入れる項目もありません。
>
> **起動するとさがす画面に一覧が出る**
>
> 1. 起動するとさがす画面に一覧が出る［さがす］
>    - 操作: なし（着いた状態を見る）
>    - 期待: さがす画面が出て、作品の行が複数並んでいる
>
> **作品名で絞り込むと一覧が絞られ、クリアすると全件に戻る**
>
> 2. 作品名で絞り込むと、入力欄に出ている語を含む行だけが残る［さがす］
>    - 操作: 検索欄に入力する（打つ語は撮るときに決める）。その語を含む行が見えるまでスクロールする
>    - 期待: 一覧に残っている行が、どれも入力欄の語を作品名に含む
> 3. クリアすると入力欄が空になり全件に戻る［さがす］
>    - 操作: クリアボタンを押す
>    - 期待: 入力欄が空になる。一覧は test_02 より行が多いか、絞り込む前の全件に戻っている
>
> **作者で絞り込みの対象を切り替えると、その対象で絞り込める**
>
> 4. 作者のセグメントを選ぶとプレースホルダが作者向けに変わる［さがす］
>    - 操作: 絞り込み対象の「作者」を押す
>    - 期待: 作者のセグメントが選ばれ、入力欄のプレースホルダが「作者で絞り込む」になる
> 5. 作者で絞り込むと、入力欄の語を名前に含む作者の作品だけが残る［さがす］
>    - 操作: 検索欄に作者名を入力する（打つ語は撮るときに決める）
>    - 期待: 一覧に残っている行が、どれも入力欄の語を名前に含む作者の作品になっている
>
> → ここでクリアボタンと「作品名」セグメントで戻す（絞り込み対象が作者のままになり、入力欄の語と絞り込みの結果も残るため）
>
> **一覧から開いた詳細は内容が一致し、戻ると一覧に戻る**
>
> 6. 作品の行をタップすると詳細に移る［作品詳細］
>    - 操作: さがす画面で見えている1件目の行をタップする
>    - 期待: 作品詳細が開き、作品名がタップした行と一致する
> 7. 詳細から戻るとさがす画面に戻る［さがす］
>    - 操作: 戻るボタンを押す
>    - 期待: さがす画面に戻り、test_06 でタップした行が一覧に見えている
>
> **詳細から図書カードを開くと Safari が開く**
>
> 8. 図書カードを開くと Safari で青空文庫の図書カードのページが開く［作品詳細］
>    - 操作: さがす画面で見えている1件目の作品を開き、「図書カード」のリンクを押す
>    - 期待: Safari に切り替わり、開いたページに「図書カード」の文字が見えている
>
> **詳細から本文を開いて読み進め、戻れる**
>
> 9. 読むを押すと本文がリーダーで開く［リーダー］
>    - 操作: さがす画面で見えている1件目の作品を開き、「読む」を押す
>    - 期待: リーダー画面が開き、タブバーが隠れて本文が出ている
>    - 前提: 本文の HTML を持つ作品であること
> 10. 本文をスクロールすると続きが表示される［リーダー］
>     - 操作: 下へスクロールする
>     - 期待: スクロール前とは違う本文の行が出ている
>     - ⚠ 画面の自動確認なし。証跡だけで判定
> 11. リーダーから戻ると詳細に戻る［作品詳細］
>     - 操作: 戻るボタンを押す
>     - 期待: 作品詳細の画面が出て、タブバーが戻っている
>
> **おすすめタブに切り替えるとカテゴリーごとの一覧が出る**
>
> 12. おすすめタブに切り替えるとカテゴリーごとのカルーセルが出る［おすすめ］
>     - 操作: 「おすすめ」タブを押す
>     - 期待: おすすめ画面が出て、カテゴリーの見出しとカルーセルが複数並んでいる
>
> **おすすめの作品を開くと詳細に遷移する**
>
> 13. おすすめの作品カードをタップすると詳細に移る［作品詳細］
>     - 操作: おすすめ画面で、見えている1つ目のカルーセルの1件目のカードをタップする
>     - 期待: 作品詳細が開き、作品名がタップしたカードと一致する
>
> **もっと見るをタップしても画面は変わらない**
>
> 14. もっと見るをタップしても画面が変わらない［おすすめ］
>     - 操作: おすすめ画面で、見えている1つ目のカルーセルの「もっと見る」を押す
>     - 期待: おすすめ画面のまま変わらず、カルーセルも見えている
>     - ⚠ 画面の自動確認なし。証跡だけで判定
>
> **おすすめからさがすタブに戻れる**
>
> 15. さがすタブに切り替えると、さがす画面が出る［さがす］
>     - 操作: おすすめ画面で「さがす」タブを押す
>     - 期待: さがす画面が出て、作品の行が並んでいる
>
> 入っていないもの: 通信エラーのときの「再読み込み」ボタン。モックサーバーを止めるなど、サーバーの状態を変えないと出せないためです。
>
> 出力先は `~/Desktop/sim-test-report-20260928-general-2/` です。同じ名前のフォルダが今日の前の回ですでにあり、前回の判定が混ざるため `-2` にしました。この内容で撮影に進めてよいですか？

> **ユーザー:** それで進めて

## 3. plan.json（先頭のテストケース3つ）

レビューに出した内容の元。`test-case-builder` が書き、`manifest.py` がここからマニフェストを作る。項目は経路を持たず、`from`（始める画面）と `do`（叩く操作）だけを書く。前の画面から `from` まではスクリプトが画面マップで繋ぐ。全10テストケースのうち先頭の3つ。

```json
{
  "app": "tx-tem.AozoraReaderClient",
  "repo": "/Users/yoshiki-tx/Program/AozoraReader",
  "cases": [
    {
      "title": "起動するとさがす画面に一覧が出る",
      "launch": true,
      "items": [
        {"from": "browse",
         "title": "起動するとさがす画面に一覧が出る",
         "expect": "さがす画面が出て、作品の行が複数並んでいる"}
      ]
    },
    {
      "title": "作品名で絞り込むと一覧が絞られ、クリアすると全件に戻る",
      "items": [
        {"id": "search",
         "from": "browse",
         "do": [
           {"op": "text:browse.search_field", "runtime": true},
           {"op": "see:browse.book_row.*", "runtime": true}
         ],
         "title": "作品名で絞り込むと、入力欄に出ている語を含む行だけが残る",
         "expect": "入力欄に出ている語を、一覧に残っている行がすべて作品名に含む"},
        {"from": "browse",
         "do": ["tap:Clear text"],
         "title": "クリアすると入力欄が空になり全件に戻る",
         "expect": "入力欄が空になり、{search} より多い、または元の全件数の行が並んでいる"}
      ]
    },
    {
      "title": "作者で絞り込みの対象を切り替えると、その対象で絞り込める",
      "items": [
        {"from": "browse",
         "do": ["tap:browse.target_picker.author"],
         "title": "作者のセグメントを選ぶとプレースホルダが作者向けに変わる",
         "expect": "作者のセグメントが選ばれた状態になり、入力欄のプレースホルダが「作者で絞り込む」に変わる"},
        {"from": "browse",
         "do": [
           {"op": "text:browse.search_field", "runtime": true},
           {"op": "see:browse.book_row.*", "runtime": true}
         ],
         "title": "作者で絞り込むと、入力欄に出ている語を含む作者の作品だけが残る",
         "expect": "一覧に残っている行がすべて、入力欄に出ている語を含む作者の作品になっている"}
      ]
    }
  ]
}
```

- **テストケース（`cases`）の中の項目は、前の項目の結果を当てにしてよい。** 期待の `{search}` は同じテストケースの前の項目を指し、マニフェストでは `test_02（…）` に展開される
- **打つ語と、待つ行の語は `runtime`。** データに依る値は plan に書かず、撮影のときに画面を見て決める
- **後に残る状態の後始末は plan に書かない。** 画面マップの `leaves` / `reset` からスクリプトが決める
- **`launch`** は、起動したときの表示そのものを確かめるテストケースに付ける

## 4. manifest.json（先頭の部分）

`manifest.py` が plan から作る定義ファイル。撮影（`run_flows.py`）、判定（`evidence-judge`）、レポート（`build_report.py`）はここだけを読む。下は判定まで終わったあとのもので、`cases` は先頭3つ、`sections` は先頭2つ。

```json
{
  "app": "tx-tem.AozoraReaderClient",
  "clear_state": false,
  "repo": "/Users/yoshiki-tx/Program/AozoraReader",
  "devices": {
    "iphone": {
      "udid": "0606D63A-C712-4026-B9E8-06A55AB4B7EF",
      "model": "iPhone 17 Pro",
      "os": "iOS 26.5"
    }
  },
  "flows": "/Users/yoshiki-tx/Program/claude-ios-e2e-skills/skills/sim-test-report/.work/flows/sim-test-report-20260928-general-2",
  "cases": [
    {
      "title": "起動するとさがす画面に一覧が出る",
      "launch": true,
      "explore": null,
      "after": null,
      "items": [
        "test_01"
      ]
    },
    {
      "title": "作品名で絞り込むと一覧が絞られ、クリアすると全件に戻る",
      "launch": false,
      "explore": null,
      "after": null,
      "items": [
        "test_02",
        "test_03"
      ]
    },
    {
      "title": "作者で絞り込みの対象を切り替えると、その対象で絞り込める",
      "launch": false,
      "explore": null,
      "after": {
        "relaunch": false,
        "resets": [
          "tap:Clear text",
          "tap:browse.target_picker.title"
        ],
        "leaves": [
          "絞り込みの対象が作者のまま残る",
          "入力欄の語と絞り込みの結果が残る"
        ]
      },
      "items": [
        "test_04",
        "test_05"
      ]
    }
  ],
  "sections": [
    {
      "name": "test_01",
      "title": "起動するとさがす画面に一覧が出る",
      "from": "browse",
      "case": "起動するとさがす画面に一覧が出る",
      "do": [],
      "screen": "browse",
      "expect": "さがす画面が出て、作品の行が複数並んでいる",
      "checked": "browse",
      "when": [],
      "flow": "test_01.yaml",
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
      "desc": "起動直後のさがす画面に、「BOITEUX・BOITEUSE」から「AU RIMBAUD」まで作品の行が複数並んでいる。対象は作品名タブが選ばれている。",
      "note": "",
      "result": "OK"
    },
    {
      "name": "test_02",
      "title": "作品名で絞り込むと、入力欄に出ている語を含む行だけが残る",
      "from": "browse",
      "case": "作品名で絞り込むと一覧が絞られ、クリアすると全件に戻る",
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
      "when": [],
      "flow": "test_02.yaml",
      "devices": {
        "iphone": {
          "inputs": {
            "BROWSE_SEARCH_FIELD": "博士",
            "BROWSE_BOOK_ROW": "博士"
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
      "desc": "入力欄に「博士」を確定すると、一覧に残った行はすべて作品名に「博士」を含む（「ソーンダイク博士」序文、アインシュタイン博士のこと、鴎外博士の追憶、風博士、カライ博士の臨終、冠婚葬祭博士、工学博士末広恭二君など）。",
      "note": "",
      "result": "OK"
    }
  ]
}
```

- **`cases` の `after`** はテストケースの後始末。画面マップの `leaves` / `reset` から flowgen が決めたもので、`resets` は叩いて戻す操作、`relaunch` は次の頭で起動し直すか。レビューの「→ ここで〜で戻す」はここから読み上げる。2つ目のテストケースはクリアボタン（`reset` の操作）をテストケースの中で押しているので、後始末は無い
- **`flow`** は撮るフローのファイル名。フローはここでは書かず、撮るときに `run_flows.py` がテストケースごとに組んで `<flows>/<端末>/` に書く
- **`devices.iphone.inputs`** は撮影時に決めた値（打った語「博士」と、待った行の語）
- **`desc` / `result`** は `evidence-judge` が証跡とダンプを読んで埋めたもの

## 5. Maestro のフロー（テストケースの境目の2本）

`run_flows.py` が撮るときに、テストケースごとに、前のテストケースが終わった画面から組んで項目ごとに書く。撮影ごとに1本で、撮る地点でフローが切れる（証跡と同名のダンプをその場で取るため）。値を撮影時に決める項目は、その手前でさらに割れる。

### 前のテストケースの後始末をしてから、押す行が見えるところで止まる（test_06.1）

前のテストケース（作者で絞り込む）は、絞り込み対象を作者にし、語を打ったまま終わっている。次のテストケースの最初のフローの頭で、クリアボタンと「作品名」セグメント（どちらも画面マップの `reset`）で戻し、キーボードを閉じる。そのあと押す行が見えるところで止まり、`run_flows.py` が画面を読んで見えている1件目を選ぶ。

```yaml
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
# browse: tap browse.target_picker.title — 作品名で絞り込むように切り替え、その場で送り直す。プレースホルダが「作品名で絞り込む」に変わる
- scrollUntilVisible:
    element:
      id: '^browse\.target_picker\.title$'
    direction: DOWN
    timeout: 60000
    optional: true
- scrollUntilVisible:
    element:
      id: '^browse\.target_picker\.title$'
    direction: UP
    timeout: 60000
- tapOn:
    id: '^browse\.target_picker\.title$'
- extendedWaitUntil:
    visible:
      id: '^browse\.target_picker\.title$'
      selected: true
    timeout: 10000
# browse: hideKeyboard — 打ったあとに開いたキーボードを閉じる
- hideKeyboard
# 次で使う browse.book_row.* が見えるまでスクロールして止める（ここで押すものを決める）
- scrollUntilVisible:
    element:
      id: '^browse\.book_row\..*'
    direction: DOWN
    timeout: 60000
    optional: true
- scrollUntilVisible:
    element:
      id: '^browse\.book_row\..*'
    direction: UP
    timeout: 60000
```

### Safari から戻して、次のテストケースを始める（test_09）

1つ前のテストケース（test_08）は、図書カードを Safari で開いたまま撮って終わっている。次のテストケースの最初のフローの頭で、アプリを終了させずに前面に戻し、詳細画面に居ることを確かめてから「読む」を押す。

```yaml
# 続き: アプリの外から
# アプリの外から detail に戻す
- launchApp:
    stopApp: false
- extendedWaitUntil:
    visible:
      id: '^detail$'
    timeout: 10000
# detail: tap detail.read_button — 本文をリーダーで開く → reader
- scrollUntilVisible:
    element:
      id: '^detail\.read_button$'
    direction: DOWN
    timeout: 60000
    optional: true
- scrollUntilVisible:
    element:
      id: '^detail\.read_button$'
    direction: UP
    timeout: 60000
- tapOn:
    id: '^detail\.read_button$'
- extendedWaitUntil:
    visible:
      id: '^reader$'
    timeout: 10000
- waitForAnimationToEnd:
    timeout: 3000
- takeScreenshot: '${SHOTS}/test_09'
```

## 6. 撮影と判定

撮影は `run_flows.py` を叩くだけ。打つ語が未定のところで一度止まり、メインのエージェントが画面のダンプを見て値を書き、同じコマンドを叩き直すと続きから走る。

> **Claude:** 前回と同じデータ状況なので、作品名は複数作品に共通する「博士」、作者は複数作品を持つ「菊池」（菊池 寛）にします。

進捗ログ:

```
test_01 撮影済み
test_02 撮影せず 入力が未定（BROWSE_SEARCH_FIELD）
--- 再開: test_02 の 2本目から
test_02 撮影済み
test_03 撮影済み
test_04 撮影済み
test_05 撮影済み
test_06 撮影済み（選んだ: BROWSE_BOOK_ROW=browse.book_row.BOITEUX ・ BOITEUSE）
test_07 撮影済み
test_08 撮影済み（選んだ: BROWSE_BOOK_ROW=browse.book_row.BOITEUX ・ BOITEUSE）
test_09 撮影済み
test_10 撮影済み
test_11 撮影済み
test_12 撮影済み
test_13 撮影済み（選んだ: RECOMMEND_CAROUSEL=recommend.carousel.総記, RECOMMEND_BOOK=recommend.book.書斎）
test_14 撮影済み（選んだ: RECOMMEND_CAROUSEL=recommend.carousel.総記）
test_15 撮影済み
```

撮り終えたら `evidence-judge` に判定させる。渡すのはマニフェストのパスと、メインしか知らないことだけ。

```
~/Desktop/sim-test-report-20260928-general-2/manifest.json

一時コード: 無し
スコープ外: 通信エラー時の再読み込みボタン。サーバーの状態を変えないと出せず、ユーザーの指示（サーバーの状態を変えない）で外した
作成したデータ: 無し
フローが落ちて探索で撮った項目: 無し
実行時に決めた値:
- test_02: 入力「博士」、見る行の語「博士」。作品名に「博士」を含む作品が複数あり、絞り込みの結果が複数行になるため
- test_05: 入力「菊池」（作者 菊池 寛）、見る行の語「芥川」（菊池 寛の作品「芥川の印象」「芥川の事ども」など）。作品を複数持つ作者で、絞り込み後の行がすべて同じ作者になるかを見られるため
```

判定は15件すべて OK で返った。ただし test_14（もっと見る）の説明には「カルーセルが次のページの作品に入れ替わった」とあり、画面マップに書き足す候補まで挙がっていた。メインが証跡を見直すと、「もっと見る」を押すために末尾まで横スクロールした位置のままなだけだったので、説明を直し、その候補も消した。そのあと `build_report.py` でレポートを組んだ。

## 7. 報告

> **Claude:** iPhone 17 Pro (iOS 26.5) で15項目を撮り、15件すべてOK（期待どおり）でした。コードにもサーバーの状態にも手を入れておらず、データも作っていません。
>
> - **見たこと:** 起動、作品名と作者での絞り込みとクリア、一覧から詳細への移動と戻り、図書カードが Safari で開くこと、リーダーで本文を開いてスクロールして戻ること、おすすめタブの表示と作品を開くこと、「もっと見る」、タブの切り替え。
> - **打った語:** 作品名の絞り込みは「博士」、作者の絞り込みは「菊池」（菊池 寛）です。どちらも複数行に絞られ、残った行がすべて条件に合っていることを確かめました。
> - **判定を1件直しました:** test_14（もっと見る）で、判定役のエージェントが「カルーセルが次のページの作品に入れ替わった」と書いていました。証跡を自分で見ると、「もっと見る」を押すために末尾まで横スクロールした位置のままなだけでした。何も起きないという画面マップの説明どおりです。レポートの説明を直し、この見立てから出ていた画面マップの修正案も消しました。
> - **確認していないこと:** 通信エラーのときの再読み込みボタン。サーバーを止めるなど、状態を変えないと出せないためです。
> - **画面マップに足す候補:** 次の操作に期待する結果が書かれておらず、自動確認ができないため、証跡の画像だけで判定しました。
>   - さがす画面の検索キーとスクロール
>   - リーダーのスクロール
>   - おすすめの「もっと見る」
>
> 出力先は `~/Desktop/sim-test-report-20260928-general-2/` です。同じ名前のフォルダが今日の前の回ですでにあったので `-2` を付けています。HTML レポートはお送りしました。PR 貼り付け用の PNG は3.4MBあり、アップロードが400エラーで失敗したため、スマホやWebからは見えず、このMacのデスクトップアプリでだけ開けます。ファイルはフォルダに入っています。

このとき届いたレポートの1枚画像（PR に貼る用）の先頭。全15項目のうち、最初の3項目まで:

![このセッションの動作確認レポートの先頭（3項目まで）](example-session-report.png)
