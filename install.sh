#!/usr/bin/env bash
#
# ~/.claude/ から、このリポジトリのスキルとサブエージェントへシンボリックリンクを張る。
# 何度実行してもよい。リンク先に実体がある場合は、動かさずその場で中断する。
#
#   ./install.sh              差し替えを実行する
#   ./install.sh --dry-run    何が起きるかだけ表示する
#
# 環境変数で上書きできる。
#   CLAUDE_SKILLS_DIR    clone先          既定 ~/Program/claude-skills
#   CLAUDE_CONFIG_DIR    Claudeの設定     既定 ~/.claude
#   CLAUDE_SKILLS_REPO_URL clone元       既定 SSHが通ればSSH、駄目ならHTTPS

set -euo pipefail

REPO_SSH="git@github.com:tx-TEM/claude-skills.git"
REPO_HTTPS="https://github.com/tx-TEM/claude-skills.git"
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

# スクリプト自身がリポジトリの中にあるなら、それを使う。単体で置かれた場合だけcloneする
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/skills" ] && [ -d "$SCRIPT_DIR/agents" ]; then
  REPO_DIR="$SCRIPT_DIR"
else
  REPO_DIR="${CLAUDE_SKILLS_DIR:-$HOME/Program/claude-skills}"
fi

# SSH鍵をGitHubに登録済みの端末はSSH、そうでなければHTTPSでcloneする。
# HTTPSはprivateリポジトリなので資格情報が要る。gh をインストール済みなら
# `gh auth setup-git` を一度流しておけば通る
pick_repo_url() {
  if [ -n "${CLAUDE_SKILLS_REPO_URL:-}" ]; then
    echo "$CLAUDE_SKILLS_REPO_URL"
    return
  fi
  if GIT_TERMINAL_PROMPT=0 \
     GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new" \
     git ls-remote "$REPO_SSH" >/dev/null 2>&1; then
    echo "$REPO_SSH"
  else
    echo "$REPO_HTTPS"
  fi
}

if [ -d "$REPO_DIR/.git" ]; then
  echo "リポジトリ: $REPO_DIR"
else
  REPO_URL="$(pick_repo_url)"
  echo "clone: $REPO_URL -> $REPO_DIR"
  run mkdir -p "$(dirname "$REPO_DIR")"
  if ! GIT_TERMINAL_PROMPT=0 run git clone "$REPO_URL" "$REPO_DIR"; then
    echo >&2
    echo "cloneに失敗した。privateリポジトリなので認証が要る。次のどちらかを済ませてから再実行する。" >&2
    echo "  SSH   : この端末の公開鍵をGitHubに登録する (https://github.com/settings/keys)" >&2
    echo "  HTTPS : gh auth login してから gh auth setup-git" >&2
    exit 1
  fi
fi

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
