# IDの振り方

手順4で ID を振るときに読む。SKILL.md の手順4にある規則の、書き方の実例と理由。

## anchor

**`anchor` は画面自体に振る。画面内の要素を借りない。**

```swift
public var body: some View {
    VStack { ... }
        .accessibilityIdentifier("browse")     // 画面id と同じ
}
```

借りると意味が二重になる。借りた要素を別コンポーネントに替えたり消したりすると、**画面は存在するのに「着いていない」判定**になる。どの要素を借りるかの判断も要らなくなる。

SwiftUIのコンテナに振ったIDは要素として出る（実測で確認済み）。出ない場合は `.accessibilityElement(children: .contain)` を併せる。それでも出なければ常在要素を借りるしかないが、**借りた理由を手順6で報告する。**

## IDの書き方

**IDはリテラルで1箇所に書く。接頭辞と役割名を分けて合成しない。** 合成すると、完成したIDがソースのどこにも文字列として存在しなくなり、**ID生存チェックのgrepが生きているIDを `dead` と誤判定する。**

```swift
// 悪い: "browse.error.reload_button" がどこにも literal で無い
ErrorView(idPrefix: "browse.error") { ... }
```

接頭辞を渡して中で組み立てる形にしない。IDは完成した文字列で、画面のファイルに1箇所だけ書く。

**自分で振れないIDは、そのまま書く。** ナビゲーションの戻る（`BackButton`）やキーボードの検索キー（`Search`）はOSが持つIDで、接頭辞の規則に従えない。そのまま要素の `id` に書き、手順6で**lintの例外として報告する**。

**`accessibilityIdentifier` は読み上げられない。** VoiceOver が読むのは `accessibilityLabel` / `value` / `hint` のほうで、identifier は自動化専用。**identifier に何を入れてもユーザー体験は変わらない**ので、命名を実利用者に配慮して曲げる必要はない。

逆に、**テストの都合で `label` に情報を足さない。** そちらは読み上げられる。

**コードに書いてもビュー階層に出ないことがある。** コンテナに付けて子要素がまとめられていない、`isAccessibilityElement = false`、SwiftUIでmodifierの付け場所が違う、などが典型。ここは手順5で実測して直すのが前提で、**コードに書いた時点では未確定として扱う。**

## 共有コンポーネント

**共有コンポーネントには、まず呼び出し側の modifier で振る。** コンポーネントを触らずに済むので、これが既定。

```swift
SearchBar(text: $model.keyword, ...)
    .accessibilityIdentifier("browse.search_field")
```

**振ってから実測で確かめる。届かないことがある。**

| | 呼び出し側の modifier で届くか |
|---|---|
| 単一要素のコンポーネント（ボタン1つ、ラベル1つ） | たぶん届く |
| `UIViewRepresentable` のラッパー | **届くことがある。** ラッパー自体が要素としてツリーに出て、内側のサブビューは同じ位置の別要素として並ぶ（`UISearchBar` にIDが乗り、入力値は隣の行に `プレースホルダ = 打った文字` で出る）。タップも入力もIDで通る |
| 複数要素のコンテナ | **全体に1つまで。** 中のボタンには届かない |

**届かなかった場合だけ、コンポーネントがIDを受け取る形にする。** 順序を逆にしない。**「このコンポーネントは届かないだろう」と予測してパラメータを足さない** — 実測すると届くことがある。

```swift
ErrorView(reloadIdentifier: "browse.error.reload_button") { ... }
```

**渡すIDは最小限にする。** タップ対象にだけ渡し、状態の観測はそのIDで兼ねる（再読み込みボタンが見えていればエラー状態なので、メッセージ用のIDは要らない）。役割ごとに配るとパラメータが増えるだけになる。

どちらの形でも、**IDのリテラルは画面のファイル側に置く。** コンポーネント側に書くと全画面で同じIDになって区別できない。画面側に置けば `files` に共有コンポーネントを載せる必要もない（載せると、それを触ったPRで全画面がヒットする）。

**IDを受け取る引数に空文字のデフォルトを置かない。** 渡し忘れたときに空のIDが振られ、ツリーにどう出るかが不定になる。`String?` にして、nil のときは modifier を適用しない。

```swift
private let reloadIdentifier: String?
...
if let reloadIdentifier {
    button.accessibilityIdentifier(reloadIdentifier)
} else {
    button
}
```

## 一覧の行

**`ForEach` で繰り返す要素のIDは、画面側で `switch` して完成形を返す。** ドメインの型に画面固有のIDを持たせると、その型を別の画面で使ったときに他画面のIDが振られる。呼び出し側で組み立てると合成になって grep で引けない。

```swift
// 悪い: 画面固有のIDがドメインの enum に漏れる
extension FilterTarget {
    var identifier: String {
        switch self {
        case .title: "browse.target_picker.title"
```

```swift
// 悪い: 合成なので "browse.target_picker.title" が literal で無い
.accessibilityIdentifier("browse.target_picker.\(target.identifier)")
```

```swift
// 良い: 画面のファイルで switch して完成形を返す
private func identifier(for target: FilterTarget) -> String {
    switch target {
    case .title: "browse.target_picker.title"
    case .author: "browse.target_picker.author"
    }
}
```

**動的な一覧の行は、表示テキストで補間する。** 画面に出ている文言をそのまま入れる。

```swift
// 悪い: 画面に出ていない値。正しいかを画面から確かめられない
.accessibilityIdentifier("item_list.cell.\(item.id)")

// 良い: ダンプの中だけで対応が取れる
.accessibilityIdentifier("item_list.cell.\(item.title)")
```

```
(201,707)  ○  牛乳, 1,000ml  #item_list.cell.牛乳
```

**ここでの「表示テキスト」はデータ由来の文言**（商品名、ユーザー名、件名）。ボタンやラベルのような**ローカライズされるUI文言とは別物**で、そちらは従来どおり静的な役割名を振る。

### なぜ item.id を入れないか

**画面にもダンプにも、その値の正しさを確かめる材料が無い。** 表示は A なのにIDは B のもの、という状態になっても、データソースを引くまで気づけない。フローは存在する別の行を叩き、ダンプは正しそうに見えたまま通る。

**名指しにも使えない。** `cell.55453` と書くには誰かが `55453` を知っている必要があり、動的な一覧ではデータソースを引くしかない。**テストケースを作る側がAPIを叩く前提になる。**

表示テキストならそうならない。テストケースが「牛乳で絞り込んでタップ」と書く時点で、`牛乳` は分かっている。

```json
{"from": "item_list", "do": ["tap:item_list.cell.牛乳"]}
```

plan の `do` にこう書ける。データソースを引かずに済む。

「何番目の行」より強い。**一覧が想定と違えばそこで落ちる。** `item.id` だと黙って別の行を叩いて通る。

### 同名は普通にある

一意にはならない。同じ名前の項目は普通にある。**IDの役目は表示と対応を取ることで、一意にすることではない。** 同名を区別するために `item.id` や index を足すと、上の問題がそのまま戻ってくる。

同名のどれを押すかは**実行時の選び方**で、識別子に焼き込むのとは別物。マップにも書かない（どれを押すかはスクリプトが実行時に決める。`reference/route.md`）。

文言が変われば落ちるが、**それは欠点ではない。** 表示が変わったなら、それを期待していたテストは落ちるべき。

### index を焼き込まない

```swift
.accessibilityIdentifier("item_list.cell.\(index)")   // 悪い
```

並び替えや上への1件挿入で全部ずれ、**「IDはデータで動かない」という前提が崩れる。** ダンプは上から順に並んでいるので何番目かは数えれば分かり、識別子に入れても同じ情報が二重になるだけで、**食い違う余地が増える**（2行目に `cell.5` が付いていても、画面からは正誤を決められない）。

| 軸 | 振るもの | 例 |
|---|---|---|
| どの行か | **表示テキスト。** 絞りきれないぶんは実行時に選ぶ（マップに書かない） | `item_list.cell.<表示名>` |
| 行の中のどの要素か | 静的な役割名 | `item_list.cell.title` / `.subtitle` |

マップ側は、**行をパターンで書く。** 何番目か、どの行かは書かない — どの行を押すかはテストのときの選び方で、画面の事実ではない。

```yaml
elements:
  - id: item_list.cell.*
    name: アイテムの行（ID は表示中の名前）
    actions:
      - tap:
        summary: アイテムの詳細を開く
        expect: {screen: item_detail, via: push}
```

どの行を押すかはスクリプトが実行時に決める。条件が無ければ画面に見えている1件目、条件があれば plan の `do` に `pick` で書く（sim-test-report の test-case-builder）。**特定の行を名指しするのはテストケースの側で、表示テキストまで書く**（`tap:item_list.cell.牛乳`）。

末尾の `*` がパターンの印。接頭辞（`item_list.cell.`）は静的なので、それでlintの存在確認ができる。**接頭辞を補間の中に散らさない。**
