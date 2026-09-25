"""画面マップを読み、確かめる部品。叩くのは scripts/ 直下の mapctl.py / migrate_map.py。

  map.py        画面マップを読む（画面を引く、画面から画面への行き方を探す）
  screen.py     1つの画面を読む（要素と操作、そこから進める先、戻る操作、自動表示）
  mini_yaml.py  screen-map が書く yaml だけを読む最小のパーサ
  check.py      マップの自己テスト（mapctl.py check）

経路を組んでフローを作るのは sim-test-report の flowgen/。ここを import して画面マップを読む
（依存は sim-test-report → screen-map の向きだけ）。
"""
