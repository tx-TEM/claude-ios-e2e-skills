"""画面マップの部品。叩くのは scripts/ 直下の map.py / migrate_map.py。

  model.py      マップを読む（画面・要素・操作と、そこから引ける辺）
  bridge.py     画面から画面への経路（項目と項目の橋渡し）。人が読む経路の表示もここ
  steps.py      ステップの型（Act / See / Await / Shot / Restart）
  results.py    操作の結果の型（Arrive / Visible / …）と、マップの expect からそれを作る resolve_result
  check.py      マップの自己テスト（map.py check）
  mini_yaml.py  screen-map が書く yaml だけを読む最小のパーサ

sim-test-report の testflow/ がこれを import してフローを作る（依存は sim-test-report → screen-map の向きだけ）。
"""
