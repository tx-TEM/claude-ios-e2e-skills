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

# JVMの警告が毎回3行出るので隠す。本当のエラーだけ見せる。
if ! maestro --udid "$UDID" hierarchy > "$WORK/$NAME.json" 2> "$WORK/$NAME.err"; then
  echo "maestro hierarchy が失敗した:" >&2
  grep -v "^WARNING" "$WORK/$NAME.err" >&2 || true
  exit 1
fi
rm -f "$WORK/$NAME.err"
python3 "$SCRIPTS/elements.py" "$WORK/$NAME.json" "$PT_W" "$PT_H" > "$WORK/$NAME.txt"

grep -v '×' "$WORK/$NAME.txt" || true
echo "生: $WORK/$NAME.json / 全行: $WORK/$NAME.txt" >&2
