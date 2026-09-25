"""画面マップと経路の部品。叩くのは scripts/ 直下の route.py / manifest.py / migrate_map.py。

  mini_yaml.py  screen-map が書く yaml だけを読む最小のパーサ
  model.py      マップを読む（画面・要素・操作と、そこから引ける辺）
  walk.py       マップの上を歩いて、経路をステップ列にする（build）
  flow.py       ステップ列から Maestro のフローと、人が読む経路を書く
  check.py      マップの自己テスト（route.py check）
  plans.py      plan.json を読み、項目ごとのフローを書く（manifest.py が呼ぶ）
"""
