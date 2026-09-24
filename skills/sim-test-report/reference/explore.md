# フローが無い項目を sim-driver に撮らせる

手順1で `run_flows.py` を走らせ切ったあと、`flow` の無いセクション（経路が組めず探索になった項目、フローが落ちて a を選ばれた項目）が残っているときに読む。

**こちらは sim-driver に任せる。** ダンプを見て座標を決め、タップして、また見る。往復が多いので、膨らんだ文脈のまま自分でやらない。

## 渡すもの

**端末ごとに呼ぶ。** 1台撮り切ってから次の端末の sim-driver を呼ぶ（`maestrod.py` が同時に握れるのは1台だけ）。`sim-driver` エージェントに次を渡す。

- **マニフェストのパス**と、撮らせる項目（`flow` が無いセクション）の名前（`test_11`）。項目の中身（`title` / `expect` / `from`）はマニフェストに入っている
- **端末名**（`iphone` / `ipad`）と、その **UDID**（マニフェストの `devices.<端末>.udid`）
- 証跡の出力先ディレクトリ。`~/Desktop/sim-test-report-<テーマのslug>/shots/<端末>/`。リポジトリ内には作らない
- 前提条件（アカウント、必要なデータ、事前設定）
- **対象アプリの bundle id**。手順0で plan の `app` に書いたものと同じ。探索で撮る項目でも Maestro のフローに要る
- **進捗ログのパス**。`~/Desktop/sim-test-report-<slug>/progress_<端末名>.log`（証跡ではないので `shots/` の外に置く）。**端末ごとに分ける。** 同じファイルに2台が書くと行が混ざる

## 進捗を見張る

呼ぶ前に進捗ログを作り、`Monitor` を張る。sim-driver は1項目終えるごとにここへ1行書き、その行がそのまま通知として届く。

```bash
python3 <このスキルのディレクトリ>/scripts/maestrod.py sweep
touch ~/Desktop/sim-test-report-<slug>/progress_<端末名>.log
```

`sweep` は14日より古い作業用ファイルを消す。

```
Monitor(command: "tail -f ~/Desktop/sim-test-report-<slug>/progress_<端末名>.log",
        description: "sim-driver の進捗", persistent: false, timeout_ms: 1800000)
```

**待つためではなく、早く止めるために張る。** 3項目目で導線を外しているのが見えた時点で sim-driver を `TaskStop` すれば、最後まで走らせてから全部撮り直すより早い。**サブエージェントの途中経過を見る手段はこれ以外に無い**（出力ファイルは会話トランスクリプトの実体なので、読むとコンテキストが溢れる）。

**`TaskStop` すると、撮れた証跡は残るがそこで終わる。** 止める前に `progress_<端末名>.log` を見て、どこまで進んだかを確かめる。

`tail -f` は自分では終わらないので、sim-driver の完了通知が来たら monitor も `TaskStop` で畳む。

## 終わったら

Maestro のデーモンを止める（`run_flows.py` も sim-driver も同じデーモンを使う）。

```bash
python3 <このスキルのディレクトリ>/scripts/maestrod.py stop
```

sim-driver 自身も終了時に止めるが、途中で `TaskStop` した場合は残る。**XCUITest ドライバが residual として残ると、次の実行で1台のデバイスに2本繋がり、全操作が `Device became unreachable` で落ちる。**

返ってくるのは項番とファイル名で、OK/NGの判定は含まれない。**判定は手順2で `evidence-judge` が下す。** 写っている情報で判断できなければ、追加の観点を伝えて撮り直させる。
