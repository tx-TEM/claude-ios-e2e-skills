"""画面マップの部品。叩くのは scripts/ 直下の mapctl.py / migrate_map.py。

  map.py        画面マップを読む（画面を引く、画面から画面への行き方を探す）
  screen.py     1つの画面を読む（要素と操作、そこから進める先、戻る操作、自動表示）
  bridge.py     画面から画面への経路（項目と項目の橋渡し）。人が読む経路の表示もここ
  steps.py      ステップの型（Act / See / Await / Shot / Restart）
  actions.py    画面でする操作を、画面仕様から読み解く（Tap / Input / InputLater / Scroll と resolve_action）
  results.py    起きることを、画面仕様から読み解く（Arrive / Visible / … と resolve_result）
  check.py      マップの自己テスト（mapctl.py check）
  mini_yaml.py  screen-map が書く yaml だけを読む最小のパーサ

sim-test-report の testflow/ がこれを import してフローを作る（依存は sim-test-report → screen-map の向きだけ）。
"""
