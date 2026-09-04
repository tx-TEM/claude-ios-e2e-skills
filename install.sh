#!/usr/bin/env bash
#
# ~/.claude/ から、このリポジトリのスキルとサブエージェントへシンボリックリンクを張る。
# 何度実行してもよい。リンク先に実体がある場合は、動かさずその場で中断する。
#
#   ./install.sh              差し替えを実行する
#   ./install.sh --dry-run    何が起きるかだけ表示する
#
# clone したリポジトリの中から実行する。
#
# 環境変数で上書きできる。
#   CLAUDE_CONFIG_DIR    Claudeの設定     既定 ~/.claude

set -euo pipefail

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

DRY_RUN=0
case "${1:-}" in
  "")        ;;
  --dry-run) DRY_RUN=1 ;;
  *) echo "使い方: $0 [--dry-run]" >&2; exit 64 ;;
esac

# --dry-run のときは実行せず、走るはずのコマンドを表示する
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

# スクリプトはリポジトリの中に置かれている前提
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -d "$REPO_DIR/skills" ] || [ ! -d "$REPO_DIR/agents" ]; then
  echo "$REPO_DIR に skills/ と agents/ が無い。cloneしたリポジトリの中から実行する。" >&2
  exit 1
fi
echo "リポジトリ: $REPO_DIR"

# $1 リンク元  $2 リンク先  $3 表示名
link() {
  local src="$1" dest="$2" label="$3"

  if [ ! -e "$src" ]; then
    echo "    リンク元が無い: $label"
    return
  fi

  if [ -L "$dest" ]; then
    if [ "$(readlink "$dest")" = "$src" ]; then
      echo "    設定済み: $label"
      return
    fi
    # リンクは中身を持たないので、向き先が違うだけなら張り替えてよい
    run rm "$dest"
  elif [ -e "$dest" ]; then
    # 実体がある。勝手に動かさず、退けるかどうかは人に決めてもらう
    echo >&2
    echo "$dest に実体がある。中身を確認して、退けるか消してから再実行する。" >&2
    exit 1
  fi

  echo "    リンク: $label"
  run ln -s "$src" "$dest"
}

run mkdir -p "$CLAUDE_DIR/skills" "$CLAUDE_DIR/agents"

echo "スキル:"
for d in "$REPO_DIR"/skills/*/; do
  [ -d "$d" ] || continue
  link "${d%/}" "$CLAUDE_DIR/skills/$(basename "$d")" "$(basename "$d")"
done

echo "サブエージェント:"
for f in "$REPO_DIR"/agents/*.md; do
  [ -f "$f" ] || continue
  link "$f" "$CLAUDE_DIR/agents/$(basename "$f")" "$(basename "$f")"
done

echo
echo "セッションを開き直すと反映される。"
