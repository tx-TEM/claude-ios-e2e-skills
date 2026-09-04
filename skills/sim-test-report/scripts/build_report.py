#!/usr/bin/env python3
"""スクリーンショット付き動作確認レポート（単一HTML）を生成する。

使い方:
    python3 build_report.py <manifest.json>

マニフェスト形式:
{
  "title": "一覧からお気に入り登録できるようにする — 動作確認レポート",
  "meta": [
    "Issue: example-org/SampleApp-iOS#123",
    "ブランチ: issues/123-favorite-from-list ／ 確認環境: iPhone 16 シミュレーター (iOS 26.0) ／ 実施日: 2026-01-15"
  ],
  "sections": [
    {
      "title": "一覧のセルにお気に入りボタンが表示される",
      "images": [
        {"src": "shots/iphone_01_list_favorite_button.png", "label": "iPhone"},
        {"src": "shots/ipad_01_list_favorite_button.png", "label": "iPad"}
      ],
      "desc": "アイテム一覧画面。各セルの右端に星アイコンのボタンが表示される。",
      "result": "OK"
    }
  ],
  "footer": "補足事項（画面で確認できなかった項目、作成したテストデータなど）",
  "output": "verification_report.html"
}

- 1セクションに複数の画像を並べられる。同じ確認項目をiPhoneとiPadで撮った場合など
- images の要素は {"src": ..., "label": ...} か、ラベル不要なら文字列だけでもよい
- 画像が1枚なら "image": "shots/01_foo.png" と書いてもよい（images 1件と等価）
- 複数端末を撮った項目は、全ての端末で確認できたときだけ result を "OK" にする
- src はマニフェストからの相対パスまたは絶対パス
- 画像は sips があれば --width（デフォルト750px）に縮小してから埋め込む
- output 省略時は manifest と同じディレクトリに verification_report.html を出力
- HTMLと同時に、PRコメント貼り付け用の1枚画像（<output>.png）も生成する
  （headless Chromeを使用。--no-png で抑止、Chrome不在時はスキップ）
"""

import base64
import html
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import time

DEFAULT_WIDTH = 750
PNG_PAGE_WIDTH = 900
# PRコメントで拡大しても文字が読めるようRetina相当で描画する
PNG_SCALE = 2
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_TIMEOUT = 180


def stable_file(path: pathlib.Path):
    """サイズが前回と変わらなくなったらTrueを返す判定関数を作る。"""
    last = -1

    def check() -> bool:
        nonlocal last
        if not path.exists():
            return False
        size = path.stat().st_size
        done = size > 0 and size == last
        last = size
        return done

    return check


def run_chrome(args: list[str], stdout=subprocess.DEVNULL, done=None) -> None:
    """Chromeを別セッションで起動し、終わらなければプロセスグループごと落とす。

    --headless=new は成果物を書き出したあとも終了しないことがある。capture_output で
    待つと、残った孫プロセスがパイプを掴んだままになり永久に返らない。パイプを使わず、
    done() が成果物の完成を告げた時点で打ち切る。
    """
    proc = subprocess.Popen(
        [CHROME, *args], stdout=stdout, stderr=subprocess.DEVNULL, start_new_session=True
    )
    deadline = time.monotonic() + CHROME_TIMEOUT
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            if done is not None and done():
                return
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()


def load_image_b64(path: pathlib.Path, width: int) -> str:
    """画像を必要なら縮小してbase64文字列にする。sipsが無ければ原寸で埋め込む。"""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            resized = pathlib.Path(tmp) / path.name
            subprocess.run(
                ["sips", "--resampleWidth", str(width), str(path), "--out", str(resized)],
                check=True,
                capture_output=True,
            )
            data = resized.read_bytes()
    except (subprocess.CalledProcessError, FileNotFoundError):
        data = path.read_bytes()
    return base64.b64encode(data).decode()


def section_images(section: dict, base_dir: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """images / image のどちらの書き方でも (パス, ラベル) の一覧にして返す。"""
    raw = section.get("images") or [section["image"]]
    items = []
    for item in raw:
        if isinstance(item, str):
            item = {"src": item}
        path = pathlib.Path(item["src"])
        if not path.is_absolute():
            path = base_dir / path
        items.append((path, item.get("label", "")))
    return items


def build(manifest_path: pathlib.Path, width: int) -> pathlib.Path:
    manifest = json.loads(manifest_path.read_text())
    base_dir = manifest_path.parent

    cards = ""
    for i, section in enumerate(manifest["sections"], start=1):
        shots = ""
        for path, label in section_images(section, base_dir):
            caption = f'<figcaption>{html.escape(label)}</figcaption>' if label else ""
            shots += (f'<figure>{caption}<img src="data:image/png;base64,'
                      f'{load_image_b64(path, width)}" alt="{html.escape(label) or f"screenshot {i}"}" /></figure>')
        multi = " multi" if len(section_images(section, base_dir)) > 1 else ""
        result = section.get("result", "OK")
        result_class = "result" if result == "OK" else "result ng"
        mark = "✓" if result == "OK" else "✗"
        cards += f'''
    <section class="card">
      <h2><span class="badge">{i}</span>{html.escape(section["title"])}<span class="{result_class}">{mark} {html.escape(result)}</span></h2>
      <div class="body">
        <div class="shots{multi}">{shots}</div>
        <p>{html.escape(section["desc"])}</p>
      </div>
    </section>'''

    meta_lines = "".join(f"<p>{html.escape(m)}</p>" for m in manifest.get("meta", []))
    footer = manifest.get("footer", "")
    footer_html = f"<footer>{html.escape(footer)}</footer>" if footer else ""

    doc = f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(manifest["title"])}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, "Hiragino Sans", sans-serif; margin: 0; padding: 24px; background: #f5f6f7; color: #222; line-height: 1.7; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #1c1e21; color: #e4e6e8; }} .card {{ background: #26282c !important; }} header p, .body p {{ color: #b6bac0 !important; }} .shots figcaption {{ color: #9aa0a6 !important; }} }}
  .wrap {{ max-width: 880px; margin: 0 auto; }}
  header h1 {{ font-size: 22px; margin: 0 0 4px; }}
  header p {{ margin: 2px 0; color: #555; font-size: 13px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 20px 24px; margin-top: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .card h2 {{ font-size: 16px; margin: 0 0 12px; display: flex; align-items: center; gap: 10px; }}
  .badge {{ background: #2f9e63; color: #fff; border-radius: 50%; width: 26px; height: 26px; display: inline-flex; align-items: center; justify-content: center; font-size: 14px; flex: none; }}
  .result {{ margin-left: auto; color: #2f9e63; font-size: 14px; flex: none; }}
  .result.ng {{ color: #d64545; }}
  .body {{ display: block; }}
  .shots {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 0 0 14px; }}
  .shots figure {{ margin: 0; }}
  .shots img {{ width: 400px; max-width: 100%; border-radius: 10px; border: 1px solid rgba(128,128,128,.35); display: block; }}
  /* 複数端末を並べたときはカード幅を分け合う。1枚だけのときは伸ばさない */
  .shots.multi figure {{ flex: 1 1 0; min-width: 0; }}
  .shots.multi img {{ width: 100%; }}
  .shots figcaption {{ margin-bottom: 6px; font-size: 12px; color: #777; text-align: center; }}
  .body p {{ margin: 0; font-size: 14px; color: #444; white-space: pre-wrap; }}
  @media (max-width: 640px) {{ .shots {{ flex-direction: column; }} .shots.multi img {{ width: 100%; }} }}
  footer {{ margin-top: 24px; font-size: 12px; color: #888; white-space: pre-wrap; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{html.escape(manifest["title"])}</h1>
    {meta_lines}
  </header>
  {cards}
  {footer_html}
</div>
</body>
</html>'''

    out = manifest.get("output", "verification_report.html")
    out_path = pathlib.Path(out)
    if not out_path.is_absolute():
        out_path = base_dir / out_path
    out_path.write_text(doc)
    return out_path


def measure_page_height(html_path: pathlib.Path) -> int:
    """高さ計測用スクリプトを足したコピーをdump-domし、titleに書かれた実高さを読む。"""
    probe = html_path.read_text().replace(
        "</body>",
        "<script>document.title = document.documentElement.scrollHeight;</script></body>",
    )
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(probe)
        probe_path = f.name
    with tempfile.NamedTemporaryFile("w+", suffix=".html", delete=False) as out:
        dom_path = out.name
    try:
        with open(dom_path, "w") as sink:
            run_chrome(
                ["--headless=new", "--disable-gpu", "--hide-scrollbars",
                 f"--window-size={PNG_PAGE_WIDTH},1000", "--dump-dom", f"file://{probe_path}"],
                stdout=sink,
            )
        match = re.search(r"<title>(\d+)</title>", pathlib.Path(dom_path).read_text())
        if match is None:
            raise RuntimeError("ページ高さを計測できなかった")
        return int(match.group(1))
    finally:
        pathlib.Path(probe_path).unlink(missing_ok=True)
        pathlib.Path(dom_path).unlink(missing_ok=True)


def render_png(html_path: pathlib.Path) -> pathlib.Path | None:
    """HTMLレポートをPRコメント貼り付け用の1枚PNGにする。Chrome不在ならNone。"""
    if not pathlib.Path(CHROME).exists():
        print("Chromeが見つからないためPNG生成をスキップしました。", file=sys.stderr)
        return None
    height = measure_page_height(html_path)
    png_path = html_path.with_suffix(".png")
    png_path.unlink(missing_ok=True)
    run_chrome(
        ["--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--force-device-scale-factor={PNG_SCALE}", f"--window-size={PNG_PAGE_WIDTH},{height}",
         f"--screenshot={png_path}", f"file://{html_path}"],
        done=stable_file(png_path),
    )
    if not png_path.exists():
        print("PNGを書き出せませんでした。", file=sys.stderr)
        return None
    return png_path


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    width = DEFAULT_WIDTH
    make_png = "--no-png" not in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--width="):
            width = int(a.split("=", 1)[1])
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    out_path = build(pathlib.Path(args[0]), width)
    size = out_path.stat().st_size
    print(f"{out_path} ({size / 1024 / 1024:.2f} MB)")
    if make_png:
        png_path = render_png(out_path)
        if png_path is not None:
            print(f"{png_path} ({png_path.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
