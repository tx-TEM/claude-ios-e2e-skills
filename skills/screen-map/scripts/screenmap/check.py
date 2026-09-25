"""画面マップの自己テスト（mapctl.py check）。不整合・経路が切れる箇所・書き足すもの・鮮度・ID の生存。"""
import subprocess

from .screen import (ACTION_KEYS, BACKWARD, ELEMENT_KEYS, EXPECT_KEYS, FORWARD, GESTURE_KEYS,
                        GESTURES, KINDS, OPS, SCREEN_KEYS, SYSTEM_IDS, expect_kind, is_pattern,
                        pattern_prefix)
from .map import old_schema


def last_commit(repo, paths):
    """そのパス群を最後に触ったコミットの時刻（epoch秒）。無ければ None。

    複数渡すと**いちばん新しいもの**が返る（git log -1 の仕様）。ファイルごとに
    呼ばずに済む。
    """
    if not paths:
        return None
    try:
        r = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ct", "--"] + list(paths),
                           capture_output=True, text=True)
    except OSError:
        return None
    out = r.stdout.strip()
    return int(out) if r.returncode == 0 and out.isdigit() else None


def staleness(mp):
    """マップが実装に追いついていないかを、gitのコミット日時で見る。

    **判定はファイルシステムの mtime ではなく git のコミット日時で行う。**
    mtime は clone や checkout で全ファイルが同じ時刻になるので、リポジトリでは
    意味を持たない。

    `screens/<id>.yaml` より新しく `files` が触られていたら、その画面の
    `actions` と `result` は実装とずれている可能性がある。**ずれていても
    フローは通る**（到達判定は anchor しか見ない）ので、古い `result` を
    信じて期待値を立てると、違う期待で撮った証跡がそのまま通ってしまう。

    **リファクタやコメントの修正でも「新しい」と出る。** 警告であって
    不整合ではない。読む側が `files` を確かめる合図として使う。
    """
    repo = mp.root.parent
    if not (repo / ".git").exists():
        return None
    out = []
    for sid in sorted(mp.screens):
        files = mp.screens[sid].files
        y = last_commit(repo, [str(mp.root / "screens" / (sid + ".yaml"))])
        f = last_commit(repo, [str(repo / str(x)) for x in files])
        if y is None or f is None or not files:
            continue
        if f > y:
            out.append((sid, y, f))
    return out


def screen_ids(scr):
    """その画面が持つ、ソースに書いてあるはずの ID。anchor と要素の id（ラベル指定は除く）。"""
    out = [str(scr.anchor)] if scr.anchor else []
    out += [str(el["id"]) for el in scr.elements if el.get("id") and el.get("by") != "label"]
    return list(dict.fromkeys(out))


def liveness(mp):
    """マップの ID がソースに残っているか。(dead, elsewhere, unchecked, missing)。

    **ID をリテラルとして、その画面の `files` から探す。** ID はリテラルで画面のファイルに
    1か所だけ書く決まり（reference/ids.md）なので、合成していなければ文字列で当たる。
    パターン（`list.row.*`）は `*` の前の固定の部分で探す。

    - dead       どの画面の files にも無い。消えたか名前が変わった。不整合
    - elsewhere  その画面の files には無いが、ほかの画面の files にある。ID が別の画面に
                 移ったか、その画面の files が足りない。警告
    - unchecked  files が無いか、1つも読めない画面（stub など）。確かめられない
    - missing    files に書いてあるのにファイルが無い。マップが古い。警告（鮮度と同じ）

    OS が持つ ID（共通の SYSTEM_IDS と config.yaml の `system_ids`）は探さない。**振る舞いのずれ（押した先が
    変わった）は分からない。** 分かるのは ID の文字列が残っているかだけ。
    """
    repo = mp.root.parent
    texts, missing = {}, []
    for sid in sorted(mp.screens):
        buf = []
        for f in mp.screens[sid].files:
            path = repo / str(f)
            try:
                buf.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                missing.append((sid, str(f)))
        if buf:
            texts[sid] = "\n".join(buf)
    everything = "\n".join(texts.values())
    dead, elsewhere, unchecked = [], [], []
    for sid in sorted(mp.screens):
        ids = [i for i in screen_ids(mp.screens[sid]) if i not in mp.system_ids]
        if sid not in texts:
            if ids:
                unchecked.append(sid)
            continue
        for i in ids:
            lit = pattern_prefix(i) if is_pattern(i) else i
            if lit in texts[sid]:
                continue
            if lit in everything:
                others = [o for o in sorted(texts) if o != sid and lit in texts[o]]
                elsewhere.append((sid, i, others))
            else:
                dead.append((sid, i))
    return dead, elsewhere, unchecked, missing


def defined_ids(mp):
    """どこかの画面に要素として定義されている ID と、画面の anchor。"""
    ids = set()
    for sid in mp.screens:
        a = mp.screens[sid].anchor
        if a:
            ids.add(str(a))
        ids.update(str(el.get("id")) for el in mp.screens[sid].elements if el.get("id"))
    return ids


def is_defined(ids, ref):
    """ref が定義済みの ID か。パターンの要素（`list.row.*`）の具体的な1つも定義済みとみなす。"""
    ref = str(ref)
    return ref in ids or any(is_pattern(i) and ref.startswith(pattern_prefix(i)) for i in ids)


class Findings:
    """検査で見つかったもの。直す先の重さで3つに分ける。

      bad     不整合。経路が組めない、黙って飛ばされる。直す
      breaks  経路が切れる／弱い箇所。事実として出す（直す対象とは限らない）
      todo    書き足すもの（name / summary / expect）。無くても経路は組める
    """

    def __init__(self):
        self.bad, self.breaks, self.todo = [], [], []


def unknown_keys(d, allowed):
    return ", ".join(sorted(set(d) - allowed))


def check_screen(mp, sid, ids):
    """1画面ぶんの (bad, breaks, todo)。"""
    scr = mp.screens[sid]
    s = scr.raw or {}
    f = Findings()
    if not isinstance(s, dict):
        return ["{}: 画面の中身が辞書になっていない".format(sid)], [], []
    check_screen_keys(sid, s, f)
    check_ready(sid, s, ids, f)
    check_elements(sid, s, f)
    for a in scr.actions:
        check_action(mp, sid, a, ids, f)
    check_auto_shows(mp, sid, f)
    return f.bad, f.breaks, f.todo


def check_screen_keys(sid, s, f):
    if s.get("stub"):
        f.breaks.append("{}: stub（操作は網羅ではない）".format(sid))
    if not s.get("anchor"):
        f.bad.append("{}: anchor が無い".format(sid))
    if unknown_keys(s, SCREEN_KEYS):
        f.bad.append("{}: 知らない鍵 {}（使えるのは {}）".format(
            sid, unknown_keys(s, SCREEN_KEYS), ", ".join(sorted(SCREEN_KEYS))))


def check_ready(sid, s, ids, f):
    r = s.get("ready")
    if r is None:
        return
    if not isinstance(r, dict) or not (set(r) <= {"any", "all"}) or not r:
        f.bad.append("{}: ready は any か all に ID の並びを書く".format(sid))
        return
    for k in r:
        for ref in r[k] or []:
            if not is_defined(ids, ref):
                f.bad.append("{}: ready の {} がどの画面の要素にも無い".format(sid, ref))


def check_elements(sid, s, f):
    seen = set()
    for el in s.get("elements") or []:
        if not isinstance(el, dict) or not el.get("id"):
            f.bad.append("{}: id の無い要素がある".format(sid))
            continue
        eid = str(el["id"])
        if eid in seen:
            f.bad.append("{}: 要素 {} が2回ある（アクションは1つの要素にまとめる）".format(sid, eid))
        seen.add(eid)
        if "*" in eid[:-1]:
            f.bad.append("{}: {} の * は末尾にだけ書ける".format(sid, eid))
        if unknown_keys(el, ELEMENT_KEYS):
            f.bad.append("{}: 要素 {} に知らない鍵 {}（使えるのは {}）".format(
                sid, eid, unknown_keys(el, ELEMENT_KEYS), ", ".join(sorted(ELEMENT_KEYS))))
        if not el.get("name"):
            f.todo.append("{}: 要素 {} に name が無い".format(sid, eid))
        if el.get("in_tree") is False:
            f.breaks.append("{}: {} は in_tree: false（座標が要る）".format(sid, eid))
        if el.get("by") == "label":
            f.breaks.append("{}: {} はラベル指定（ローカライズで壊れる）".format(sid, eid))
            if is_pattern(eid):
                f.bad.append("{}: {} はラベル指定なのでパターンにできない".format(sid, eid))
        if el.get("when") and el.get("actions"):
            f.breaks.append("{}: {} は条件つき（when: {}）。前提に書かない限り経路に使わない"
                            .format(sid, eid, el["when"]))


def check_action(mp, sid, a, ids, f):
    """要素のアクションか、画面の gestures の1項目。"""
    where = "{}: 「{}」".format(sid, a.label())
    on_element = a.element is not None
    kinds = OPS if on_element else GESTURES
    keys = ACTION_KEYS if on_element else GESTURE_KEYS
    if len([k for k in kinds if k in a.raw]) != 1:
        f.bad.append("{}: {} のどれか1つだけを持つ操作にする（{}）".format(
            sid, " / ".join(kinds), a.element.get("id") if on_element else a.raw))
        return
    if on_element and a.raw.get(a.op) is not None:
        f.bad.append("{} は値を持たない（`- {}:` だけ書く。対象は要素の id）".format(where, a.op))
    if not on_element and a.target not in ("down", "up"):
        f.bad.append("{} の向きは down / up".format(where))
    if unknown_keys(a.raw, keys):
        f.bad.append("{} に知らない鍵 {}（使えるのは {}）".format(
            where, unknown_keys(a.raw, keys), ", ".join(sorted(keys))))
    if not a.summary:
        f.todo.append("{} に summary が無い".format(where))

    exp = a.raw.get("expect")
    items = exp if isinstance(exp, list) else ([exp] if exp is not None else [])
    if not items:
        f.todo.append("{} に expect が無い（結果を自動で確かめられない）".format(where))
    whens = [isinstance(e, dict) and bool(e.get("when")) for e in items]
    if any(whens) and not all(whens):
        f.bad.append("{} の expect で when のある項目と無い項目が混ざっている"
                     "（分かれるなら全部に when を書く）".format(where))
    screens = sum(check_expect(mp, a, where, e, ids, f) for e in items)
    if screens > 1 and not any(whens):
        f.bad.append("{} に移る先が2つある（状態で分かれるなら when を書く）".format(where))


def check_expect(mp, a, where, e, ids, f):
    """expect の1項目。画面を移る項目なら 1 を返す（移る先が2つあるかを数えるため）。"""
    if not isinstance(e, dict):
        f.bad.append("{} の expect が読めない: {}".format(where, e))
        return 0
    if unknown_keys(e, EXPECT_KEYS):
        f.bad.append("{} の expect に知らない鍵 {}".format(where, unknown_keys(e, EXPECT_KEYS)))
    kind = expect_kind(e)
    if kind is None:
        f.bad.append("{} の expect は {} のどれか1つを持つ".format(where, " / ".join(KINDS)))
        return 0
    if kind == "screen":
        dest, via = e.get("screen"), e.get("via")
        if dest == "back":
            if via not in BACKWARD:
                f.bad.append("{} は screen: back なので via は {}".format(where, " / ".join(BACKWARD)))
        else:
            if via not in FORWARD:
                f.bad.append("{} の via は {}（戻る操作は screen: back）".format(where, " / ".join(FORWARD)))
            if dest not in mp.screens:
                f.bad.append("{} の遷移先 {} のファイルが無い".format(where, dest))
        if a.element is None:
            f.bad.append("{} は要素に紐づかないので画面を移れない".format(where))
        return 1
    if e.get("via"):
        f.bad.append("{} の via は screen と一緒にだけ書く".format(where))
    elif kind != "external":
        ref = e[kind]
        if ref == "self" and a.element is None:
            f.bad.append("{} の self は要素のアクションでだけ使える".format(where))
        elif ref != "self" and not is_defined(ids, ref):
            f.bad.append("{} の expect が指す {} がどの画面の要素にも無い".format(where, ref))
    return 0


def check_auto_shows(mp, sid, f):
    for iid, after in mp.screens[sid].auto_items():
        # 欠けていると、確かめも閉じもせずに黙って飛ばす（sim-test-report の flowgen/maestro.py の auto_checks）
        if iid not in mp.screens:
            f.bad.append("{}: 自動表示 {} のファイルが無い（screens/{}.yaml）".format(sid, iid, iid))
        elif mp.screens[iid].auto_close() is None:
            f.bad.append("{}: 自動表示 {} に anchor と閉じる操作（screen: back）の両方が要る"
                         .format(sid, iid))
        for x in after:
            if x not in mp.screens:
                f.bad.append("{}: 自動表示 {} の after {} の画面が無い".format(sid, iid, x))
        if after:
            f.breaks.append("{}: 自動表示 {} を {} から戻るたびに確かめる（出ていないとき約7秒）"
                            .format(sid, iid, " / ".join(after)))
        else:
            f.breaks.append("{}: 自動表示 {} を着くたびに確かめる（出ていないとき約7秒）"
                            .format(sid, iid))


def cmd_check(mp):
    bad = []
    old = old_schema(mp)
    if old:
        print("古い形（actions / states を画面に持つ）のマップ: {}".format(" ".join(old)))
        print("migrate_map.py で要素中心の形に移す（screen-map スキルの reference/schema.md）")
        return 1
    if mp.start not in mp.screens:
        bad.append("起点 {} のファイルが無い".format(mp.start))
    reach = mp.reachable()
    # 自動表示の画面は遷移で入らない（こちらの操作と関係なく被さる）。被さる先の画面に
    # 着けるなら、出会いうる画面として数える
    reach |= {i for sid in reach if sid in mp.screens for i in mp.screens[sid].auto_shows() if i in mp.screens}
    print("起点: {}".format(mp.start))
    print("到達できる: {}".format(" ".join(sorted(reach))))
    lost = sorted(set(mp.screens) - reach)
    print("到達できない: {}".format(" ".join(lost) if lost else "なし"))

    ids = defined_ids(mp)
    breaks, todo = [], []
    for sid in sorted(mp.screens):
        b, br, td = check_screen(mp, sid, ids)
        bad += b
        breaks += br
        todo += td

    stale = staleness(mp)
    if stale is None:
        print("\nマップの鮮度: git が無いので測れない")
    else:
        print("\nマップの鮮度（git のコミット日時。mtime は clone で揃うので使わない）:")
        import time as _t
        for sid, y, f in stale:
            print("  {}: 実装のほうが新しい（files {} > マップ {}）"
                  .format(sid, _t.strftime("%m/%d %H:%M", _t.localtime(f)),
                          _t.strftime("%m/%d %H:%M", _t.localtime(y))))
        if not stale:
            print("  全画面、マップが実装に追いついている")
        else:
            print("  **この画面の summary と expect は実装とずれている可能性がある。**"
                  "期待値を立てる前に files を読む。\n"
                  "  リファクタやコメントの修正でもここに出るので、不整合ではなく警告。")

    dead, elsewhere, unchecked, missing = liveness(mp)
    print("\nID の生存（files の中をリテラルで探す。OS の ID は外す: {}）:".format(" / ".join(mp.system_ids)))
    for sid, i, others in elsewhere:
        print("  {}: {} はこの画面の files に無く、{} の files にある"
              "（別の画面に移ったか、files が足りない）".format(sid, i, " / ".join(others)))
    if unchecked:
        print("  files が無い（読めない）ので確かめられない: {}".format(" ".join(unchecked)))
    if dead:
        print("  {}件が実装に見つからない（下の不整合）。OS が持つ ID なら config.yaml の"
              " system_ids に足す（{} は足さなくても外れる）".format(len(dead), " / ".join(SYSTEM_IDS)))
    if not (dead or elsewhere or unchecked or missing):
        print("  全画面、マップの ID がソースにある")
    for sid, f in missing:
        print("  {}: files の {} が無い（消えたか移った。files を直す）".format(sid, f))
    for sid, i in dead:
        files = mp.screens[sid].files
        bad.append("{}: {} が実装に見つからない（dead。files: {}{}）".format(
            sid, i, files[0], " ほか{}件".format(len(files) - 1) if len(files) > 1 else ""))

    print("\n経路が切れる／弱い箇所:")
    for b in breaks:
        print("  " + b)
    if not breaks:
        print("  （なし）")
    if todo:
        print("\n書き足すもの（{}件）:".format(len(todo)))
        for t in todo:
            print("  " + t)
    if bad:
        print("\nマップの不整合:")
        for b in bad:
            print("  " + b)
        return 1
    return 0
