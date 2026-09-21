#!/usr/bin/env bash
#
# ~/.claude/ から、このリポジトリのスキルとサブエージェントへシンボリックリンクを張る。
# 何度実行してもよい。リンク先に実体がある場合は、動かさずその場で中断する。
#
#   ./install.sh              差し替えとMaestroの導入を実行する
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

# ビュー階層のダンプとスクロールに使う Maestro を入れる。
#
# 注意点が2つある。
#  - brew が依存として openjdk を最新へ上げる。Javaのバージョンを固定している
#    プロジェクトがある端末では先に確認する
#  - 匿名アナリティクスが既定で有効。MAESTRO_CLI_NO_ANALYTICS に任意の値を入れると切れる。
#    このスクリプトでは触らない。~/.zshenv 側に置く
#
# 素の `brew install maestro` は別物（runmaestro.ai の macOS アプリ）が入る。
# 必ず mobile-dev-inc のタップを指定する。
install_maestro() {
  if command -v maestro >/dev/null 2>&1; then
    echo "    導入済み: maestro"
    return
  fi
  if ! command -v brew >/dev/null 2>&1; then
    echo "    brew が無い。https://docs.maestro.dev/maestro-cli/how-to-install-maestro-cli を参照" >&2
    return 1
  fi
  run brew tap mobile-dev-inc/tap
  run brew trust --formula mobile-dev-inc/tap/maestro
  run brew install mobile-dev-inc/tap/maestro
}

# Maestro が入らなければ失敗として終わる。brew があって tap や install が
# 失敗したときは set -e でここまで来ないので、扱いを揃える。前提に挙げている
# 以上、入らなかったことを成功として返すと、気づくのは動作確認の最中になる。
echo "Maestro:"
if ! install_maestro; then
  echo >&2
  echo "リンクは張れている。Maestro を入れてから ./install.sh をもう一度実行する。" >&2
  exit 1
fi

echo
echo "セッションを開き直すと反映される。"
