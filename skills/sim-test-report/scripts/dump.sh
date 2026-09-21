#!/usr/bin/env bash
#
# ビュー階層のダンプを取り、生JSONと抽出結果の両方を .work/ に残す。
#
#   dump.sh <UDID> <名前> [画面幅 画面高]
#
# 生成物
#   .work/<名前>.json   maestro hierarchy の出力そのまま
#   .work/<名前>.txt    elements.py の出力（画面外の行も含む）
#
# 標準出力には画面内の行だけを出す。
#
# 生と抽出後の両方を残すのは、どちらかだけでは後から追えないため。
# 生が無いと抽出スクリプトを直しても検証し直せず、抽出後が無いと
# エージェントが何を見てその座標を選んだのかが分からない。
#
# 名前は証跡と揃える（例: iphone_03_before_tap）。同じ項番で複数回
# 取るときは何をした後かを足す。付けないと上書きされる。

set -euo pipefail

UDID="${1:?UDID を渡す}"
NAME="${2:?名前を渡す（例: iphone_03_before_tap）}"
PT_W="${3:-390}"
PT_H="${4:-844}"

SCRIPTS="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(cd -- "$SCRIPTS/.." && pwd)/.work"
mkdir -p "$WORK"

# Maestro は JVM で動く。macOS の /usr/bin/java は Java 未導入だとスタブで、
# 叩いても「Unable to locate a Java Runtime」が出るだけ。Homebrew の openjdk は
# keg-only なので PATH にも出ない。JAVA_HOME が未設定ならここで補う。
# maestrod.py の java_env() と同じ順で探す。補えなければそのまま進め、
# maestro 自身のエラーを見せる。
if [ -z "${JAVA_HOME:-}" ]; then
  _jh="$(/usr/libexec/java_home 2>/dev/null || true)"
  _bp="$(brew --prefix openjdk 2>/dev/null || true)"
  for base in "$_jh" "$_bp"; do
    [ -n "$base" ] || continue
    for home in "$base/libexec/openjdk.jdk/Contents/Home" "$base"; do
      if [ -x "$home/bin/java" ]; then
        export JAVA_HOME="$home"
        export PATH="$home/bin:$PATH"
        break 2
      fi
    done
  done
  unset _jh _bp
fi

# JVMの警告が毎回3行出るので隠す。本当のエラーだけ見せる。
if ! maestro --udid "$UDID" hierarchy > "$WORK/$NAME.raw" 2> "$WORK/$NAME.err"; then
  echo "maestro hierarchy が失敗した:" >&2
  grep -v "^WARNING" "$WORK/$NAME.err" >&2 || true
  exit 1
fi

# maestro は JSON の前に標準出力へアナリティクスの通知を出す（既定で有効）。
# そのまま保存すると生ダンプが壊れ、elements.py が JSONDecodeError で落ちる。
# MAESTRO_CLI_NO_ANALYTICS を切ってある環境では何も落ちないので、
# 切っていない環境ぶんの保険。別のバナーが増えても効く。
sed -n '/^[[{]/,$p' "$WORK/$NAME.raw" > "$WORK/$NAME.json"
if [ ! -s "$WORK/$NAME.json" ]; then
  echo "maestro hierarchy の出力にJSONが無い。先頭を出す:" >&2
  head -5 "$WORK/$NAME.raw" >&2
  exit 1
fi
rm -f "$WORK/$NAME.raw" "$WORK/$NAME.err"
python3 "$SCRIPTS/elements.py" "$WORK/$NAME.json" "$PT_W" "$PT_H" > "$WORK/$NAME.txt"

grep -v '×' "$WORK/$NAME.txt" || true
echo "生: $WORK/$NAME.json / 全行: $WORK/$NAME.txt" >&2
