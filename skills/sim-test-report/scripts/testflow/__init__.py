"""plan から Maestro のフローを作る部品。叩くのは scripts/ 直下の manifest.py。

  flow.py      plan からフローを作る主体。項目の do をステップにし、項目の間は
               screen-map の screenmap/route.py で繋ぎ、maestro.py で書く
  maestro.py   ステップを Maestro のフロー（yaml）にする

**画面マップを読む部品は screen-map スキルにある**（screen-map/scripts/screenmap/）。
マップの形を知っているのはマップを作る側なので、そちらに置いて、ここから import する。
install.sh は両方のスキルを必ずまとめてリンクするので、隣のスキルのディレクトリを
import のパスに足して読む（シンボリックリンクは実体のリポジトリまで辿る）。
"""
import sys
from pathlib import Path

_SCREEN_MAP = Path(__file__).resolve().parents[3] / "screen-map" / "scripts"
if str(_SCREEN_MAP) not in sys.path:
    sys.path.insert(0, str(_SCREEN_MAP))
