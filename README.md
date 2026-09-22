# claude-skills

Claude Code の個人用スキル・サブエージェント置き場。マシンをまたいで使い回すためにバージョン管理している。

## 収録

| 名前 | 種類 | 概要 |
| --- | --- | --- |
| `sim-test-report` | Skill | iOSシミュレーターでの動作確認を、テストケースのレビュー → 実施 → 証跡レポートまで通して進める。成果物は画像をbase64で埋め込んだ単一HTMLと、PRコメント貼り付け用の1枚PNG |
| `screen-map` | Skill | iOSアプリの画面マップを画面単位で作る。ソースを読んで画面・遷移・確認箇所を特定し、`accessibilityIdentifier` を実装に振って `screen-map/screens/*.yaml` に落とす。`scripts/route.py` がそのマップから目的の画面までの経路を組み、`sim-test-report` が動作確認のフローとして使う |
| `test-case-builder` | Agent | 変更から確認項目を立て、画面マップがあれば実行できるフローまで組んで返す。レビューを受けるのも実施も判定もしない。`sim-test-report` の手順0から呼ばれる |
| `sim-driver` | Agent | シミュレーターを操作して証跡スクリーンショットを撮る。判定はせず観測した事実だけ返す。`sim-test-report` の手順1から呼ばれる |

## セットアップ

clone したディレクトリで `./install.sh` を実行する。`~/.claude/` から `skills/*/` と `agents/*.md` へシンボリックリンクを張る。何度実行してもよく、リンク先に実体がある場合は動かさずに中断する。`--dry-run` を付けると何が起きるかだけ表示する。反映されるのはセッションを開き直したタイミング。

## 前提

- macOS — 証跡の撮影に `xcrun simctl`、画像の縮小に `sips` を使う
- iOSシミュレーターMCP（`mcp__Claude_Code_iOS_Simulator__control`）
- `python3` — レポート生成と、ビュー階層ダンプの抽出に使う。**Xcode 同梱の 3.9 で動く**ので、スクリプトに 3.10 以降の構文を持ち込まない
- Google Chrome — 1枚PNGの描画に使う。無い場合はHTMLのみ生成される
- Homebrew — `install.sh` が Maestro と openjdk を入れるのに使う。無い場合、`install.sh` はリンクを張ったあと失敗して止まる
- Maestro — ビュー階層のダンプとスクロールに使う。`install.sh` が mobile-dev-inc のタップから入れる（素の `brew install maestro` は別物が入るので注意）
- Java 17以上 — Maestro が使う。`install.sh` が openjdk も入れる。**Homebrew の openjdk は keg-only でシステムからは見えない**が、`maestrod.py` と `dump.sh` が `/usr/libexec/java_home` → `brew --prefix openjdk` の順で探して補うので、通常は手当て不要。どちらでも見つからない場所に JDK がある場合だけ `JAVA_HOME` を自分で設定する（未設定のままだと `Unable to locate a Java Runtime` で maestro が起動しない）

## 経路を組む

画面マップから、起点から目的の画面までの経路を組み立てる。**マップのあるアプリのリポジトリで**実行する（カレントから上へ `screen-map/` を探す。`--map <dir>` でも渡せる）。

```bash
python3 ~/.claude/skills/screen-map/scripts/route.py check
```

```
screens                        画面の一覧（呼び名・できること）
path  <セグメント...>          人が読む経路
flow  <セグメント...> --app X  Maestro のフロー。maestrod.py run にそのまま渡せる
check                          マップ全体の自己テスト（到達可否と切れている箇所）
```

経路はセグメントを並べて組む。`--goto <画面id>`（いま居る画面からそこまで計算して繋ぐ）/ `--do <操作id>`（その場で操作）/ `--shot <パス>`（撮る）で、並び順がそのまま実行順になる。`--goto` はナビゲーションの履歴を持っていて、深く入った先から戻る経路も組む。

組めなかった場合は理由を返して終了コード 2 で終わる。理由はマップの穴（未マップの画面、`in_tree: false`、`to` の先が無い）か、呼び方の間違い（`text` の値が未指定）のどちらか。

## マニフェストの骨組みを作る

`route.py --out-dir` が置いた `index.json` から、`manifest.json` の骨組みを作る。証跡1枚＝1セクションで、`title` と `expect` だけ空になる。

```bash
python3 skills/sim-test-report/scripts/manifest.py <索引.json> <出力先> --title "…" --meta "…"
```

## フローを走らせる

マニフェストに載っているフローを順に走らせ、証跡と同名のダンプを撮る。`flow` を持たないセクション（探索で撮るもの）は飛ばす。

```bash
python3 skills/sim-test-report/scripts/run_flows.py <manifest.json> <フローのディレクトリ> <UDID>
```

## レポート単体で生成する

スキルを経由せずスクリプトだけ使うこともできる。マニフェストの形式は `skills/sim-test-report/scripts/build_report.py` 冒頭のdocstringを参照。

```bash
python3 skills/sim-test-report/scripts/build_report.py <manifest.json>
```
