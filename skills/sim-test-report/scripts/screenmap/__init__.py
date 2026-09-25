"""画面マップと経路の部品。叩くのは scripts/ 直下の route.py / manifest.py / migrate_map.py。

  flow.py       plan からフローを作る主体（manifest.py が呼ぶ）。項目の do をステップにし、
                項目の間は route.py で繋ぎ、maestro.py で書く
  route.py      画面から画面への経路（項目と項目の橋渡し）。人が読む経路の表示もここ
  maestro.py    ステップを Maestro のフロー（yaml）にする
  model.py      マップを読む（画面・要素・操作と、そこから引ける辺）
  check.py      マップの自己テスト（route.py check）
  mini_yaml.py  screen-map が書く yaml だけを読む最小のパーサ
"""
