"""plan から Maestro のフローを作る部品。叩くのは scripts/ 直下の manifest.py。

  flow.py      plan からフローを作る主体。項目の do をステップにし、項目の間は
               bridge.py で繋ぎ、maestro.py で書く
  bridge.py    画面から画面への経路（項目と項目の橋渡し）。人が読む経路の表示もここ
  steps.py     ステップの型（Act / See / Await / Shot / Restart）
  actions.py   ステップでする操作（Tap / Input / InputLater / Scroll）
  results.py   操作をすると起きること（Arrive / Visible / …）
  maestro.py   ステップを Maestro のコマンドにする
  flowyaml.py  コマンドを yaml に書き出す

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
