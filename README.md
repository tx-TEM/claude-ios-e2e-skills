# claude-skills

## 収録

| 名前 | 種類 | 概要 |
| --- | --- | --- |
| `sim-test-report` | Skill | iOSシミュレーターでの動作確認を、テストケースのレビュー → 実施 → 証跡レポートまで通して進める。成果物は画像をbase64で埋め込んだ単一HTMLと、PRコメント貼り付け用の1枚PNG |
| `screen-map` | Skill | iOSアプリの画面マップを画面単位で作る。ソースを読んで画面・遷移・確認箇所を特定し、`accessibilityIdentifier` を実装に振って `screen-map/screens/*.yaml` に落とす。`scripts/route.py` がそのマップから目的の画面までの経路を組み、`sim-test-report` が動作確認のフローとして使う |
| `test-case-builder` | Agent | 確認項目を立て、画面マップがあれば実行できるフローまで組んで返す。コードの差分と画面マップを参照する。レビューを受けるのも実施も判定もしない。`sim-test-report` から呼ばれる |
| `sim-driver` | Agent | シミュレーターを操作して証跡スクリーンショットを撮る。判定はせず観測した事実だけ返す。`sim-test-report` から呼ばれる |

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

## 流れ

### 1. テストケースを立てる（LLM / `test-case-builder`）

ユーザーの指示から確認項目を立てる。コードの差分と、事前に用意した[画面マップ](skills/screen-map/SKILL.md#スキーマ)を参照する。

```
1. さがす画面の初期表示で作品一覧が出る [browse]
   期待: 未絞り込みの状態で、一覧の先頭が「BOITEUX・BOITEUSE」（李 箱）になる
   証跡: iphone_01_browse_initial

2. キーワード入力でデバウンス絞り込みが走る [browse]
   期待: 「夏目」を打って 300ms 待つと 6 件に絞り込まれ、先頭行が
         「温情の裕かな夏目さん」（内田 魯庵）になる。キーボードは開いたまま
   証跡: iphone_02_debounce_filtered

3. 検索キーで絞り込みが即時確定する [browse]
   期待: 直前と同じ 6 件のまま、キーボードが閉じる
   証跡: iphone_03_search_key_filtered

…
```

### 2. 経路を計算して Maestro のフローを書く（`route.py`）

テストケースを元に、たどる経路を画面マップから計算し、Maestro のフローとして書き出す。

```bash
python3 ~/.claude/skills/screen-map/scripts/route.py flow --app <bundle id> \
  --out-dir ~/.claude/skills/sim-test-report/.work/flows/<slug> \
  --goto browse --shot <出力先>/shots/iphone_01_browse_initial \
  --do text:browse.searchField --input browse.searchField=夏目 --shot … \
  --restart --goto browse --do scroll:down --shot …
```

```
  browse  text browse.searchField "夏目"    ✓ browse.searchField が出ている
          撮影 iphone_02_debounce_filtered
  browse  tap Search                      — 機械判定なし。証跡で見る
  …
  機械判定 12件 / 証跡でしか見られない 6件

  フロー（10本）: …/.work/flows/general-flow
    01_iphone_01_browse_initial.yaml ★起動し直す  →  機械判定 browse
    02_iphone_02_debounce_filtered.yaml          →  機械判定 browse.searchField
```

画面マップが無いアプリでは、この手順を飛ばす。マップはあっても経路が組めない項目（未マップの画面、座標が要る操作）は理由つきで返る。どちらも LLM（`sim-driver`）が画面を見ながら探索して撮る。

### 3. テストの定義ファイルを作る（`manifest.py`）

手順1のテストケースと、手順2で書き出したフローを1つのファイルにまとめる。以降はこのファイルだけで動く。

```bash
python3 ~/.claude/skills/sim-test-report/scripts/manifest.py \
  ~/.claude/skills/sim-test-report/.work/flows/<slug> <出力先> --title "…" --meta "ブランチ: …"
```

```json
{
  "name": "iphone_02_debounce_filtered",
  "title": "",
  "screen": "browse",
  "expect": "",
  "checked": "browse.searchField",
  "launch": false,
  "flow": "02_iphone_02_debounce_filtered.yaml",
  "images": [{ "src": "shots/iphone_02_debounce_filtered.png" }],
  "dump": "shots/iphone_02_debounce_filtered.txt",
  "desc": "",
  "result": "未判定"
}
```

空いた `title` と `expect` をテストケースで埋める。

```json
"title":  "キーワードを打つと入力が止まってから絞り込みが走る",
"expect": "「夏目」を打って 300ms 待つと 6 件に絞り込まれ、先頭行が
           「温情の裕かな夏目さん」（内田 魯庵）になる。キーボードは開いたまま"
```

経路が組めなかった項目も、フローを持たないセクションとして同じファイルに並べる。

このファイルを読み上げてレビューを受ける。合意してから撮影に入る。

### 4. フローを走らせる（`run_flows.py`）

フローを順に走らせ、証跡と同名のダンプを撮る。フローを持たない項目は LLM（`sim-driver`）が撮る。

```bash
python3 ~/.claude/skills/sim-test-report/scripts/run_flows.py \
  <出力先>/manifest.json ~/.claude/skills/sim-test-report/.work/flows/<slug> <UDID>
```

### 5. 判定を書き込む（LLM）

撮影したスクリーンショットとダンプを見て、結果を記録する。

### 6. レポートを組む（`build_report.py`）

```bash
python3 ~/.claude/skills/sim-test-report/scripts/build_report.py <出力先>/manifest.json
```

画像を base64 で埋め込んだ単一HTMLと、それを1枚に描画したPNGが出る。

![動作確認レポートの先頭部分](docs/report-example.png)

スキルを経由せずここだけ使ってもよく、定義ファイルの形式は `build_report.py` 冒頭のdocstringを参照。
