"""画面マップを読む。

アプリの screen-map/ を全部読み、画面（Screen）を引いたり、画面から画面への行き方を探したりできるようにする。
"""
import heapq
import sys
from pathlib import Path

# PyYAML を入れさせないための最小パーサ（理由は mini_yaml.py）
from .mini_yaml import load_yaml
from .screen import SYSTEM_IDS, Screen


class ScreenMap:
    """画面マップ全体。起点と、{画面 id: Screen}。"""

    def __init__(self, root):
        self.root = root
        cfg = load_yaml(root / "config.yaml") or {}
        self.start = cfg.get("start")
        # OS が持つ ID。ソースに無いので生存チェックから外す。共通のもの（SYSTEM_IDS）に、
        # アプリの config.yaml の system_ids を足す
        self.system_ids = list(SYSTEM_IDS) + [str(x) for x in (cfg.get("system_ids") or [])]
        self.screens = {f.stem: Screen(f.stem, load_yaml(f) or {})
                        for f in sorted((root / "screens").glob("*.yaml"))}

    def edges(self, sid):
        """その画面から進む辺（Screen.edges）。画面が無ければ空。"""
        s = self.screens.get(sid)
        return s.edges() if s else []

    def anchor(self, sid):
        """その画面の anchor。画面が無いか anchor が無ければ None。"""
        s = self.screens.get(sid)
        return s.anchor if s else None

    def blocked(self, edge, given=()):
        """往路の辺として使えない理由。使えるなら None。"""
        a, _, dest, _, conds = edge
        if not a.in_tree():
            return "in_tree: false（座標が要る）"
        if dest not in self.screens:
            return "遷移先 {} のファイルが無い".format(dest)
        missing = [c for c in conds if c not in given]
        if missing:
            return "条件つき（when: {}）".format(" / ".join(missing))
        return None

    def path_from(self, src, goal, given=(), relaxed=False):
        """src から goal までの最短経路。[(画面id, 辺), ...] か None。

        **同じ長さなら、タブの切り替え（`via: tab`）を多く通るほうを採る。** タブバーは
        いつも同じ位置にあり、画面の下の方のカードを押すより確実。

        **起点固定にしない。** セグメントを繋ぐとき、2本目以降は
        いま居る画面から引くことになる。

        relaxed=True では使えない辺も通す。**経路が無いのか、切れた辺の
        先にあるのかを言い分けるため**だけに使う。
        """
        if src == goal:
            return []
        best = {src: (0, 0)}
        prev = {src: None}
        heap = [(0, 0, 0, src)]
        n = 0
        while heap:
            hops, other, _, cur = heapq.heappop(heap)
            if (hops, other) > best.get(cur, (hops, other)):
                continue
            if cur == goal:
                out, at = [], goal
                while prev[at]:
                    out.append(prev[at])
                    at = prev[at][0]
                return list(reversed(out))
            for edge in self.edges(cur):
                if self.blocked(edge, given) and not (relaxed and edge[2] in self.screens):
                    continue
                nxt = edge[2]
                cost = (hops + 1, other + (0 if edge[3] == "tab" else 1))
                if nxt not in best or cost < best[nxt]:
                    best[nxt] = cost
                    prev[nxt] = (cur, edge)
                    n += 1
                    heapq.heappush(heap, (cost[0], cost[1], n, nxt))
        return None

    def auto_hosts(self, sid):
        """sid を自動で出すことがある画面（被さる先）と、その after。[(画面, after)]。"""
        return sorted((h, after) for h, s in self.screens.items()
                      for i, after in s.auto_items() if i == sid)

    def reachable(self):
        """起点から、何かの条件の下で辿れる画面。"""
        out = {self.start}
        queue = [self.start]
        while queue:
            cur = queue.pop(0)
            for edge in self.edges(cur):
                if edge[0].in_tree() and edge[2] in self.screens and edge[2] not in out:
                    out.add(edge[2])
                    queue.append(edge[2])
        return out


def find_map(repo):
    """アプリのリポジトリから画面マップ（screen-map/）を引く。**呼ぶ側が必ず渡す**
    （カレントからは探さない — どこで叩いたかで結果が変わらないように）。"""
    if not repo:
        sys.exit("--repo <アプリのリポジトリ> が要る")
    p = Path(repo).expanduser().resolve() / "screen-map"
    if (p / "config.yaml").exists():
        return p
    sys.exit("{} に画面マップが無い（config.yaml を探した）。\n"
             "このアプリにはまだ画面マップが無いのかもしれない（screen-map スキルで作る）".format(p))


def old_schema(mp):
    """古い形（`actions` / `states` を画面に持つ）の画面。移行前のマップを黙って空として読まない。"""
    return sorted(sid for sid, s in mp.screens.items()
                  if isinstance(s.raw, dict) and ("actions" in s.raw or "states" in s.raw))


def load_map(repo):
    mp = ScreenMap(find_map(repo))
    old = old_schema(mp)
    if old:
        sys.exit("画面マップが古い形（actions / states）のまま: {}。\n"
                 "migrate_map.py --repo <アプリ> で要素中心の形に移す".format(" ".join(old)))
    return mp
