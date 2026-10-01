# claude-ios-e2e-skills

個人用に作った Claude Code の Skill とサブエージェントのまとめ。

iOSアプリの動作確認を、テストケースのレビューからシミュレーターでの証跡撮影、PR に貼るレポートまで通して任せられるようにしている。

## 何を確かめるか

**操作とその結果。** 押したら遷移する、保存したら一覧に増える、一覧で選んだ行と詳細の中身が合う、実データの形で表示が分かれる、といった、アプリを通しで動かさないと分からないことを確かめる。スクリーンショットはその結果の証拠として撮る。

## 構成

| 名前 | 種類 | 概要 |
| --- | --- | --- |
| `sim-test-report` | Skill | iOSシミュレーターでの動作確認を、テストケースのレビュー → 実施 → 証跡レポートまで通して進める。成果物は画像をbase64で埋め込んだ単一HTMLと、PRコメント貼り付け用の1枚PNG |
| `screen-map` | Skill | iOSアプリの画面マップを画面単位で作る。ソースを読んで画面ごとの要素と、操作するとどうなるか（遷移も結果の1つ）を特定し、`accessibilityIdentifier` を実装に振って `screen-map/screens/*.yaml` に落とす。マップを引く・確かめる `scripts/mapctl.py`（check / screens / which / path）もここにある。`sim-test-report` はこのマップから目的の画面までの経路を組み、動作確認のフローにする |
| `test-case-builder` | Agent | コードの差分から確認項目を立て、画面マップがあれば実行できるフローまで組んで返す。レビューを受けるのも実施も判定もしない。`sim-test-report` から呼ばれる |
| `sim-driver` | Agent | シミュレーターを操作して証跡スクリーンショットを撮る。判定はせず観測した事実だけ返す。`sim-test-report` から呼ばれる |
| `evidence-judge` | Agent | 証跡を読んでOK/NGを判定し、定義ファイルに書き込む。撮影もレポート生成もしない。`sim-test-report` から呼ばれる |
| `retaker` | Agent | 判定で撮り直し（`RETAKE`）になった項目の原因を見立て、plan で直して `run_flows.py` で撮り直すか、画面マップに足す行を返す。判定はしない。`sim-test-report` から呼ばれる |

## セットアップ

clone したディレクトリで `./install.sh` を実行する。`~/.claude/` から `skills/*/` と `agents/*.md` へシンボリックリンクを張る。何度実行してもよく、リンク先に実体がある場合は動かさずに中断する。`--dry-run` を付けると何が起きるかだけ表示する。反映されるのはセッションを開き直したタイミング。

## 前提

- macOS — 証跡の撮影に `xcrun simctl`、画像の縮小に `sips` を使う
- iOSシミュレーターMCP（`mcp__Claude_Code_iOS_Simulator__control`）
- `python3` — レポート生成と、ビュー階層ダンプの抽出に使う。**Xcode 同梱の 3.9 で動く**ので、スクリプトに 3.10 以降の構文を持ち込まない
- Google Chrome — 1枚PNGの描画に使う。無い場合はHTMLのみ生成される
- Homebrew — `install.sh` が Maestro と openjdk を入れるのに使う。無い場合、`install.sh` はリンクを張ったあと失敗して止まる
- Maestro — ビュー階層のダンプとスクロールに使う。`install.sh` が mobile-dev-inc のタップから入れる（素の `brew install maestro` は別物が入るので注意）
- Java 17以上 — Maestro が使う。`install.sh` が openjdk も入れる。**Homebrew の openjdk は keg-only でシステムからは見えない**が、`maestrod.py` が `/usr/libexec/java_home` → `brew --prefix openjdk` の順で探して補うので、通常は手当て不要。どちらでも見つからない場所に JDK がある場合だけ `JAVA_HOME` を自分で設定する（未設定のままだと `Unable to locate a Java Runtime` で maestro が起動しない）

## 流れ

実際に通しで動かしたときのやり取り（依頼 → テストケースのレビュー → plan → Maestro のフロー → 報告）は [docs/example-session.md](docs/example-session.md) にある。

### 1. テストケースを立てる（LLM / `test-case-builder`）

ユーザーの指示とコードの差分から確認項目を立てる。**差分があれば必ず読み、何を確かめるかは差分から決める。** 事前に用意した[画面マップ](skills/screen-map/reference/schema.md)は、立てた項目を撮れる形にするために引く（操作するアクセシビリティ ID、始める画面、そこまでの経路）。差分が無いとき（「一通り見て」など）は、マップで画面を探し、その画面のソースを読んで項目を立てる。

項目はテストケース（1つの機能を確かめるまとまり）にまとめ、項目ごとに、どの画面で何を操作し、何が見えるはずかを **`plan.json` 1つ**に書く。そこまでの経路は書かない。以降のフローもマニフェストもここから作り、LLM が散文から写す箇所を残さない。

```json
{
  "app": "com.example.app",
  "repo": "~/Program/<アプリのリポジトリ>",
  "cases": [
    {"title": "キーワードで一覧を絞り込める",
     "items": [
       {"from": "browse",
        "title": "キーワード入力でデバウンス絞り込みが走る",
        "do": [{"op": "text:browse.search_field", "runtime": true}],
        "expect": "入力欄に出ている語を、一覧に残っている行がすべて品名に含む"}]},
    {"title": "一覧から開いた詳細から戻ると、開いた行の位置に戻る",
     "items": [
       {"id": "open", "from": "browse",
        "title": "一覧の行をタップすると詳細に移る",
        "do": ["tap:browse.item_row.*"],
        "expect": "詳細画面の品名が、タップした行の品名と一致する"},
       {"from": "item_detail",
        "title": "詳細から戻ると一覧に戻る",
        "do": ["tap:BackButton"],
        "expect": "一覧に戻り、{open} でタップした行が見えている"}]},
    {"title": "ログイン中に登録を押すと登録画面が開く",
     "items": [
       {"from": "item_detail", "when": ["ログイン中"],
        "title": "ログイン中に登録を押すと登録画面が開く",
        "do": ["tap:item_detail.register_button"],
        "expect": "…"}]}
  ]
}
```

書き方（テストケースの分け方、`launch`、`do` の操作、データに依る値の決め方、期待の書き方）は [test-case-builder.md](agents/test-case-builder.md) と `manifest.py --help` にある。

### 2. テストの定義ファイルを作る（`manifest.py`）

plan から、テストの定義ファイル（`manifest.json`）を作る。以降は定義ファイルだけで動く。フローはここでは書かず、撮るときに `run_flows.py` が組む（手順3）。

```bash
python3 ~/.claude/skills/sim-test-report/scripts/manifest.py \
  ~/.claude/skills/sim-test-report/.work/flows/<出力先の名前>/plan.json <出力先> \
  --device iphone=<iPhoneのUDID> --device ipad=<iPadのUDID>
```

経路は `scripts/flowgen/` が計算する（画面マップは screen-map の部品で読む）。plan の各項目について、前の項目が終わった画面から `from` までの経路を画面マップから計算し、`do` の操作と撮影を繋いで、項目ごとの手順にする。画面に着いたら anchor → 読み込み完了の目印（`ready`）→ アニメーションの落ち着きの順に待つ。テストケースの境目では、画面マップの `leaves`（後に残る状態）に `reset`（既定に戻す操作）があればそれを叩き、無ければアプリを起動し直す。`launch` の付いたテストケースも起動し直してから始める。

**1項目は「経路のフロー」と「`do` の1手ずつ」に分けて流す。** 経路（`from` まで）は1本の Maestro のフローで、押す前には見えるまでスクロールする（`scrollUntilVisible`）。経路の要素は画面マップに載っていて基本的に必ずあるので、それで困らない。経路の途中で一覧の行や親を選ぶ手だけは、`do` と同じく1手ずつ流す。

`do` の要素は、`run_flows.py` がダンプを読みながら探し、見つかってから1手ぶんのフロー（操作と、着いた確認）を流す。`from` に着いても、画面が意図した状態になっていない（ログインしていない、データが無い、絞り込みの結果が空）と、確かめたい要素がそもそも無い。Maestro の `scrollUntilVisible` はスクロールの端を検知しないので、無い要素では上限の60秒をまるごと払ってしまう。ダンプなら、1回送って読み直し、見えている ID が送る前と同じなら端と分かる。下の端 → 上の端まで探して無ければ、少し読み直してから（遅れて出るものを取りこぼさないように）その項目は撮れなかったとして次に進む。横スクロールの中の要素は、親を縦に寄せてから、親の枠の中を同じやり方で横に探す。

どの語を打つか、どの行を押す・見るかは、そのときの画面を見ないと決まらない。値はその手を流す直前に決め、フローに直接書く。値を決めるのは次のどちらか。

- **条件の無い行の選択**（画面に見えている1件目）は、`run_flows.py` がその場で画面を読んで決め、止まらずに続ける
- **打つ語と、条件つきで選ぶ行**は、`run_flows.py` がそこで止まって LLM に返す。LLM が値を決め、`--value '<値>'` を付けて叩き直すと、`run_flows.py` が値を定義ファイルに書いて、止まった手から続く。条件つきで選ぶ行は、いま画面に見えている行が候補として並ぶので、その中から選ぶ。無ければ `--next` を付けて叩き直すと、1画面送って候補を出し直す（「無い」は値ではないので、定義ファイルには残らない）。定義ファイルは LLM が手で書き換えない。値の鍵は plan の `do` に書いた操作そのもの（`text:list.search_field`）

流したものは、項目ごとに1本の記録（Maestro のフロー）として残る。

画面マップで経路が組めなかったテストケース（`explore`）はフローを作らない。LLM（`sim-driver`）が画面を見ながら操作して撮る。

定義ファイルは plan と同じテストケースの入れ子で、テストケースの下に項目ごとの題・期待・撮る画面・フロー・証跡の置き場と、判定の欄が並ぶ。形は `manifest.py` 冒頭の docstring を参照。

このファイルを読み上げてレビューを受ける。直すところがあれば plan を直して手順2を叩き直し、合意してから撮影に入る。

### 3. フローを走らせる（`run_flows.py`）

テストケースごとに、前のテストケースが終わった画面からフローを組んで走らせ、証跡と同名のダンプを撮る。1回で全端末を、1台ずつ撮り切りながら回る。フローを持たない項目は LLM（`sim-driver`）が撮る。

1本落ちても、巻き添えになるのはそのテストケースの残りの項目だけで、次のテストケースから続ける。

```bash
python3 ~/.claude/skills/sim-test-report/scripts/run_flows.py \
  <出力先>/manifest.json
```

LLM が値を決める操作で止まったら、値を書いて同じコマンドを叩けば続きを走る（手順2）。判定で撮り直し（`RETAKE`）が付いた項目があれば、同じコマンドでそれだけを撮り直す。詳しくは `run_flows.py` 冒頭の docstring を参照。

### 4. 判定を書き込む（LLM / `evidence-judge`）

撮影したスクリーンショットとダンプを見て、結果を記録する。

項目ごとに観測した事実（`desc`）と OK / NG（`result`）を書く。フローで撮れなかった項目は、判定の前に撮り直す。それでも撮れず、このまま出すと決めた項目は外さずに `SKIP` にし、撮れなかった理由を `desc` に書く（レポートに「撮れなかった」として出る）。画像以外を根拠にしたときは、その項目の注記（`note`）に残す。期待のほうが狭かったと思えても期待は直さず NG のまま返し、期待を直すかはユーザーが選ぶ。

### 5. レポートを組む（`build_report.py`）

```bash
python3 ~/.claude/skills/sim-test-report/scripts/build_report.py <出力先>/manifest.json \
  --title "…"
```

画像を base64 で埋め込んだ単一HTMLと、それを1枚に描画したPNGが出る。

項目ごとのカードには、レビューで合意した期待と、観測した結果が並び、その下に証跡が続く。レポートだけを見た人にも、何を期待して OK / NG にしたのかが読める。

![動作確認レポートの先頭部分](docs/report-example.png)

スキルを経由せずここだけ使ってもよく、定義ファイルの形式は `build_report.py` 冒頭のdocstringを参照。

## テスト

スクリプト（screen-map の `mapctl.py`、sim-test-report の `manifest.py` / `run_flows.py`）のテストは `tests/` にある。

```bash
python3 -m unittest discover tests
```
