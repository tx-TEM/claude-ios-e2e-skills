"""Maestro のフローを、組み立てたデータから yaml の文字列に書き出す。

maestro.py はコマンドを dict で組み立てる（`{"tapOn": {"id": "^x$"}}`）。インデントと
引用符の付け方はここでだけ決める。**文字列を手で継ぎ足さない** — インデントや引用符を
コマンドごとに合わせると、読むときに yaml の形を頭の中で組み直すことになる。

  コマンド   "stopApp"（引数なし） / {"名前": 値} / {"名前": {鍵: 値, ...}}
  値         文字列はシングルクォートで包む。数と真偽はそのまま。Raw はそのまま
  Comment    コマンドの並びに混ぜると `# …` の行になる（フローを読む人のための注記）

PyYAML を使わないのは、スキルを使うだけで環境に手を入れさせないため（mini_yaml.py と同じ）。
書く形はこのスキルのフローに要るものだけに絞る。
"""


class Raw(str):
    """引用符を付けずに書く値。env の参照（`${LIST_ROW}`）や、`DOWN` のような語。"""


class Comment(str):
    """フローに残すコメントの1行。書くと `# ` が付く。"""


def scalar(v):
    """値を1つ書く。文字列はシングルクォート（正規表現の `\\` を素通しするためダブルにしない）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, Raw)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def command(cmd, indent=0):
    """コマンド1つ（リストの1項目）の行。中身の鍵は `- ` の後ろから4字下げる。"""
    pad = " " * indent
    if isinstance(cmd, str):
        return [pad + "- " + cmd]
    (name, body), = cmd.items()
    if not isinstance(body, dict):
        return [pad + "- {}: {}".format(name, scalar(body))]
    return [pad + "- {}:".format(name)] + mapping(body, indent + 4)


def mapping(d, indent):
    """鍵と値の並び。入れ子の dict は2字、コマンドの並び（runFlow の commands）も2字下げる。"""
    pad, out = " " * indent, []
    for k, v in d.items():
        if isinstance(v, dict):
            out += [pad + k + ":"] + mapping(v, indent + 2)
        elif isinstance(v, list):
            out += [pad + k + ":"] + [line for c in v for line in command(c, indent + 2)]
        else:
            out.append("{}{}: {}".format(pad, k, scalar(v)))
    return out


def render(app, env, commands, notes=()):
    """フロー1本。`env` は未定のまま置く変数名の並び、`notes` は末尾に付ける補足。"""
    out = ["appId: " + app]
    if env:
        out += ["env:"] + ["  {}: ''".format(v) for v in env]
    out.append("---")
    for c in commands:
        out += ["# " + c] if isinstance(c, Comment) else command(c)
    if notes:
        out += [""] + ["# 補足: " + n for n in notes]
    return "\n".join(out) + "\n"
