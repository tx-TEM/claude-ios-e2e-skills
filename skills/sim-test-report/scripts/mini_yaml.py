"""screen-map が書く yaml だけを読む、最小の yaml パーサ。

PyYAML は Xcode 同梱の python3.9 に入っていない。入れさせると、このスキルを
使うだけで環境に手を入れることになる。読む相手は screen-map が書く yaml だけ
なので、その範囲に限った最小のパーサを持つ。**読めない行は例外にする。**
黙って None を返すと、経路が組めないのかマップが間違っているのか分からなくなる。

  from mini_yaml import load_yaml
  load_yaml(Path("screen-map/screens/browse.yaml"))  # dict / list
"""
import re

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*:(\s|$)")


def _strip_comment(s):
    """引用符の外にある ` #` から先を落とす。"""
    quote = None
    for i, c in enumerate(s):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i]
    return s


def _scalar(s):
    if s.startswith("[") and s.endswith("]"):
        body = s[1:-1].strip()
        return [_scalar(p.strip()) for p in body.split(",")] if body else []
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if re.match(r"^-?\d+$", s):
        return int(s)
    return s


def _lines(text):
    out = []
    for raw in text.splitlines():
        s = _strip_comment(raw.rstrip())
        if s.strip():
            out.append((len(s) - len(s.lstrip(" ")), s.strip()))
    return out


def _parse(ls, i, indent):
    if ls[i][1] == "-" or ls[i][1].startswith("- "):
        return _seq(ls, i, indent)
    return _map(ls, i, indent)


def _seq(ls, i, indent):
    out = []
    while i < len(ls) and ls[i][0] == indent and (ls[i][1] == "-" or ls[i][1].startswith("- ")):
        head = ls[i][1][1:].strip()
        i += 1
        sub = []
        while i < len(ls) and ls[i][0] > indent:
            sub.append(ls[i])
            i += 1
        if head and _KEY.match(head):
            # `- tap: x` の形。続く行は "- " のぶん右に揃っている
            sub = [(indent + 2, head)] + sub
            out.append(_parse(sub, 0, indent + 2)[0])
        elif head:
            out.append(_scalar(head))
        elif sub:
            out.append(_parse(sub, 0, sub[0][0])[0])
        else:
            out.append(None)
    return out, i


def _map(ls, i, indent):
    out = {}
    while i < len(ls) and ls[i][0] == indent:
        s = ls[i][1]
        if s == "-" or s.startswith("- "):
            break
        if ":" not in s:
            raise ValueError("読めない行: " + s)
        k, _, rest = s.partition(":")
        k, rest = k.strip(), rest.strip()
        i += 1
        if rest:
            out[k] = _scalar(rest)
            continue
        sub = []
        while i < len(ls) and ls[i][0] > indent:
            sub.append(ls[i])
            i += 1
        if sub:
            out[k] = _parse(sub, 0, sub[0][0])[0]
        elif i < len(ls) and ls[i][0] == indent and (ls[i][1] == "-" or ls[i][1].startswith("- ")):
            out[k], i = _seq(ls, i, indent)   # `-` をキーと同じ字下げで書く形
        else:
            out[k] = None
    return out, i


def load_yaml(path):
    ls = _lines(path.read_text(encoding="utf-8"))
    if not ls:
        return {}
    try:
        return _parse(ls, 0, ls[0][0])[0]
    except ValueError as e:
        raise ValueError("{}: {}".format(path, e))
