#!/usr/bin/env python3
"""確かめる変更の範囲（差分のファイル）を決める。**plan を作る側の道具**で、フローを組む
manifest.py とは別。test-case-builder が差分を読むときと、呼び出し元がレビューで項目と
見比べるときに使う。

  diffscope.py <リポジトリ> [--base <ブランチ>] [--head <コミット>] [--names]

## 範囲

**今回の変更は、今のブランチのコミットのうち、既定ブランチにも、ほかのオープン中の PR にも
入っていないもの。** 起点はその手前（候補との分岐点のうち、いちばん新しいもの）。

    master ── A ── B                      既定ブランチに入っている
                    └── C ── D            ほかのオープン PR #11 に入っている
                              └── E ── F  今のブランチ。どの PR にも無い → 起点は D、範囲は E, F

  候補     既定ブランチ（`origin/HEAD` が指すもの）と、オープン中の PR の先頭（`gh pr list`）。
           どちらも取る前に fetch する（手元が古いと起点がずれる）。PR の先頭が手元に無ければ
           `git fetch origin pull/<番号>/head` で取る
  除く PR  **今のブランチ自身の PR**（先頭が今のブランチと同じ）と、**今のブランチを含む PR**
           （今のブランチの上に積んだ PR）。含めると今回の変更が全部「PR にある」ことになる
  --base   起点の候補をそのブランチだけにする。自動の決め方が合わないときに
  --head   省けば**作業ツリーまで**。コミットしていない変更と、未追跡のファイル
           （`.gitignore` で外れるものを除く）も入る。実装を終えて確かめるときは、まだ
           コミットしていないことが多い。コミットで区切るときだけ書く
  --names  差分のファイルだけを1行ずつ出す（`mapctl.py which -` に流す）

`gh` が使えない（入っていない、GitHub でない）ときは既定ブランチだけで決め、そう出す。

範囲はここでしか決めない。builder・呼び出し元で起点が食い違わないように。
"""
import json
import subprocess
import sys


class ScopeError(Exception):
    pass


def run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def git(repo, *args):
    got = run(repo, *args)
    if got.returncode != 0:
        raise ScopeError("git {}: {}".format(" ".join(args), got.stderr.strip()))
    return got.stdout


def lines(text):
    return [s for s in text.splitlines() if s]


def open_prs(repo):
    """オープン中の PR の [{number, headRefName, headRefOid}]。gh が使えなければ None。"""
    try:
        got = subprocess.run(["gh", "pr", "list", "--state", "open", "--limit", "200",
                              "--json", "number,headRefName,headRefOid"],
                             cwd=str(repo), capture_output=True, text=True)
    except OSError:
        return None
    if got.returncode != 0:
        return None
    return json.loads(got.stdout)


def default_branch(repo):
    """既定ブランチの名前（origin/HEAD が指すもの）。"""
    got = run(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if got.returncode != 0:
        raise ScopeError("既定ブランチが分からない（origin/HEAD が無い）。`git remote set-head origin --auto` "
                         "で取るか、--base にブランチを書く")
    return got.stdout.strip().split("/", 1)[1]


def fetch(repo, *refspec):
    got = run(repo, "fetch", "--quiet", "origin", *refspec)
    return got.returncode == 0, got.stderr.strip()


def has_commit(repo, oid):
    return run(repo, "cat-file", "-e", oid + "^{commit}").returncode == 0


def is_ancestor(repo, a, b):
    return run(repo, "merge-base", "--is-ancestor", a, b).returncode == 0


def candidates(repo, head_rev):
    """起点の候補 [(説明, 参照)] と注記の並び。今のブランチ自身の PR は候補にしない。"""
    notes = []
    name = default_branch(repo)
    ok, err = fetch(repo, name)
    if not ok:
        notes.append("origin/{} を fetch できなかった（手元のまま使う）: {}".format(name, err))
    cands = [("既定ブランチ origin/{}".format(name), "origin/" + name)]
    prs = open_prs(repo)
    if prs is None:
        notes.append("gh でオープン中の PR を引けなかった。既定ブランチだけで起点を決めた")
        return cands, notes
    branch = run(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    for pr in prs:
        if pr["headRefName"] == branch:
            continue
        oid = pr["headRefOid"]
        if not has_commit(repo, oid):
            ok, err = fetch(repo, "pull/{}/head".format(pr["number"]))
            if not ok or not has_commit(repo, oid):
                notes.append("PR #{}（{}）の先頭を取れなかった。候補から外した".format(
                    pr["number"], pr["headRefName"]))
                continue
        if is_ancestor(repo, head_rev, oid):
            notes.append("PR #{}（{}）は今のブランチの上に積んだもの。候補から外した".format(
                pr["number"], pr["headRefName"]))
            continue
        cands.append(("PR #{}（{}）の先頭".format(pr["number"], pr["headRefName"]), oid))
    return cands, notes


def scope(repo, base=None, head=None):
    """差分の範囲。{via, point, head, files, untracked, notes}。

    `untracked` は `files` のうち、まだ `git add` していない新しいファイル。`git diff` には出ない。

    `base` を省けば、既定ブランチとほかのオープン中の PR の先頭のうち、今のブランチとの
    分岐点がいちばん新しいものを起点にする。`head` が None なら作業ツリーまで。
    """
    head_rev = head or "HEAD"
    if base:
        cands, notes = [(base, base)], []
    else:
        cands, notes = candidates(repo, head_rev)
    via, point = None, None
    for label, ref in cands:
        mb = run(repo, "merge-base", ref, head_rev)
        if mb.returncode != 0:
            if base:
                raise ScopeError("{} との分岐点が無い: {}".format(base, mb.stderr.strip()))
            notes.append("{} とは分岐点が無い。候補から外した".format(label))
            continue
        mb = mb.stdout.strip()
        if point is None or is_ancestor(repo, point, mb):
            via, point = label, mb
    if point is None:
        raise ScopeError("起点が決まらない（候補: {}）".format(", ".join(l for l, _ in cands)))
    if head:
        files, untracked = lines(git(repo, "diff", "--name-only", point, head)), []
    else:
        files = lines(git(repo, "diff", "--name-only", point))
        untracked = [f for f in lines(git(repo, "ls-files", "--others", "--exclude-standard"))
                     if f not in files]
        files += untracked
    return {"via": via, "point": point, "head": head, "files": files, "untracked": untracked,
            "notes": notes}


def describe(sc):
    """起点と範囲を1行で。"""
    return "{}（分岐点 {}）から{}まで".format(sc["via"], sc["point"][:7], sc["head"] or "作業ツリー")


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv or not argv:
        print(__doc__)
        sys.exit(0 if argv else 2)
    repo, opts, names, rest = argv[0], {}, False, argv[1:]
    while rest:
        a = rest.pop(0)
        if a == "--names":
            names = True
        elif a in ("--base", "--head") and rest:
            opts[a[2:]] = rest.pop(0)
        else:
            sys.exit("知らない引数: {}\n{}".format(a, __doc__))
    try:
        sc = scope(repo, opts.get("base"), opts.get("head"))
    except ScopeError as e:
        sys.exit(str(e))
    if names:
        print("\n".join(sc["files"]))
        return
    print("差分: " + describe(sc))
    print("  {}ファイル".format(len(sc["files"])))
    for f in sc["files"]:
        print("    " + f + ("  （新規・git add 前）" if f in sc["untracked"] else ""))
    for n in sc["notes"]:
        print("注: " + n)
    print("変更の中身: git -C {} diff {}{}".format(repo, sc["point"][:12], " " + sc["head"] if sc["head"] else ""))
    if sc["untracked"]:
        print("「新規・git add 前」のファイルは git diff に出ない。ファイルをそのまま読む")


if __name__ == "__main__":
    main()
