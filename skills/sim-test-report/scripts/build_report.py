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
      "expect": "各セルの右端に星アイコンのボタンが出る",
      "desc": "アイテム一覧画面。各セルの右端に星アイコンのボタンが表示される。",
      "note": "",
      "result": "OK"
    }
  ],
  "footer": "実行全体の補足（確認していない項目、一時コード、作成したテストデータなど）",
  "output": "verification_report.html"
}

- meta の各行は「ラベル: 値」で書くと、ヘッダでラベルと値に分けて並ぶ。1行に「／」で
  区切って複数書いてもよい。コロンの無い行はそのまま1項目になる
- 1セクションに複数の画像を並べられる。同じ確認項目をiPhoneとiPadで撮った場合など
- images の要素は {"src": ..., "label": ...} か、ラベル不要なら文字列だけでもよい
- 画像が1枚なら "image": "shots/01_foo.png" と書いてもよい（images 1件と等価）
- 複数端末を撮った項目は、全ての端末で確認できたときだけ result を "OK" にする
- **result は省略できず、`PENDING` や `RETAKE` のままでも止まる。** title が空のときも止まる（骨組みのまま生成しようとしている）
- カードは「期待 → 結果 → 証跡 → 注記」の順。**2つを続けて証跡の上に置くのは、
  OK の根拠をその場で読めるようにするため。** 結果の帯は OK / NG と同じ色にする。 desc は観測した事実なので、それだけでは
  何を期待していたかが分からない。枚数でレイアウトは変えない。どの欄も省略でき、
  無い欄は行ごと出さない
    expect  期待（レビューで合意したもの）
    desc    結果（観測した事実）
    note    その項目だけの但し書き（期待の訂正、証跡の読み方の注意）
- **機械判定（checked）は出さない。** 撮影のフローが何を待ったかは作る側の話で、
  読む側は全項目で画像と判定を見る。確認として弱い項目は手順0のレビューで扱う
- **項目ごとの話はカードに書き、footer は実行全体の話だけにする。** footer に項目番号つきで
  書くと、読む側がカードと行き来して突き合わせることになる
- sections には他の欄を持たせてよい。**知らない欄は無視する** — このマニフェストは
  手順0で作って工程ごとに埋めていくので、screen / flow / dump なども載っている
- src はマニフェストからの相対パスまたは絶対パス
- 画像は sips があれば --width（デフォルト750px）に縮小してから埋め込む
- output 省略時は manifest と同じディレクトリに verification_report.html を出力
- HTMLと同時に、PRコメント貼り付け用の1枚画像（<output>.png）も生成する
  （headless Chromeを使用。--no-png で抑止、Chrome不在時はスキップ）
"""

from __future__ import annotations

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
# PRコメントで拡大しても文字が読めるよう、等倍より大きく描画する。
# 上限を超えたら順に落とす。GitHubの画像添付は10MBまでで、超えると貼れない
PNG_SCALES = (1.5, 1.25, 1)
PNG_MAX_BYTES = 10 * 1024 * 1024
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


def meta_items(meta: list[str]) -> list[tuple[str, str]]:
    """meta の行を (ラベル, 値) にする。「／」で区切った行は複数の項目に分ける。"""
    items = []
    for line in meta:
        for part in re.split(r"\s*／\s*", line.strip()):
            m = re.match(r"([^:：]+?)\s*[:：]\s*(.+)", part)
            items.append((m.group(1), m.group(2)) if m else ("", part))
    return [(k, v) for k, v in items if v]


def section_rows(section: dict) -> tuple[list[tuple[str, str]], str]:
    """証跡の上に出す期待と結果、下に出す注記。値の無いものは出さない。

    **2つを続けて証跡の上に出す。** 何を期待し何が起きたかを突き合わせてから
    画像を見る。枚数でレイアウトを変えないので、1枚でも複数端末でも同じ順に並ぶ。
    """
    top = [(label, section[key])
           for label, key in (("期待", "expect"), ("結果", "desc"))
           if (section.get(key) or "").strip()]
    return top, (section.get("note") or "").strip()


def build(manifest_path: pathlib.Path, width: int) -> pathlib.Path:
    manifest = json.loads(manifest_path.read_text())
    base_dir = manifest_path.parent

    cards = ""
    counts = {"OK": 0, "NG": 0}
    for i, section in enumerate(manifest["sections"], start=1):
        # **既定を OK にしない。** このマニフェストは工程ごとに埋めていくので、
        # 判定を書き忘れた項目が黙って OK で出ると、確かめていないものを
        # 確かめたことにしてしまう。画像を読む前に見る。
        if section.get("result") in (None, "", "PENDING", "RETAKE"):
            raise SystemExit(f'sections[{i}] "{section.get("title", "")}" の result が'
                             f' {section.get("result")!r}。判定していない項目と'
                             '撮り直しが要る項目を、レポートに出せない。')
        if not (section.get("title") or "").strip():
            raise SystemExit(f'sections[{i}] に title が無い。'
                             'マニフェストの骨組みのまま生成しようとしている。')
        shots = ""
        for path, label in section_images(section, base_dir):
            caption = f'<figcaption>{html.escape(label)}</figcaption>' if label else ""
            shots += (f'<figure>{caption}<img src="data:image/png;base64,'
                      f'{load_image_b64(path, width)}" alt="{html.escape(label) or f"screenshot {i}"}" /></figure>')
        multi = " multi" if len(section_images(section, base_dir)) > 1 else ""
        top, note = section_rows(section)
        # 期待と結果は別の帯にする。1つの枠に並べると、どこまでが期待か読み分けにくい
        expect_html = "".join(
            f'<div class="block {"expect" if label == "期待" else "outcome"}">'
            f'<span>{label}</span><p>{html.escape(value)}</p></div>'
            for label, value in top)
        rows_html = (f'<div class="note"><span>注記</span><p>{html.escape(note)}</p></div>'
                     if note else "")
        result = section["result"]
        ok = result == "OK"
        counts["OK" if ok else "NG"] += 1
        cards += f'''
    <section class="card{"" if ok else " ng"}">
      <h2><span class="badge">{i}</span><span class="title">{html.escape(section["title"])}</span><span class="result">{"✓" if ok else "✗"} {html.escape(result)}</span></h2>
      {expect_html}
      <div class="shots{multi}">{shots}</div>
      {rows_html}
    </section>'''

    meta_lines = "".join(
        f'<div><dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd></div>'
        for k, v in meta_items(manifest.get("meta", [])))
    meta_lines = f'<dl class="meta">{meta_lines}</dl>' if meta_lines else ""
    summary = (f'<div class="summary"><span class="ok">OK {counts["OK"]}</span>'
               f'<span class="ng">NG {counts["NG"]}</span>'
               f'<span class="all">全 {counts["OK"] + counts["NG"]} 項目</span></div>')
    footer = manifest.get("footer", "")
    footer_html = (f'<footer class="card"><h2>補足</h2><p>{html.escape(footer)}</p></footer>'
                   if footer else "")

    doc = f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(manifest["title"])}</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #f4f5f7; --card: #fff; --text: #1f2328; --sub: #59636e; --faint: #8c959f;
    --line: #e3e6ea; --ok: #1a7f37; --ok-bg: #dafbe1; --ng: #cf222e; --ng-bg: #ffebe9;
    --expect-bg: #f0f4fa; --expect-line: #6e8fb8; --note-bg: #fff8c5; --badge: #57606a;
  }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg: #16181b; --card: #22252a; --text: #e6e8eb; --sub: #aab1b9; --faint: #7d858f;
    --line: #33373d; --ok: #4ac26b; --ok-bg: #12311d; --ng: #ff6b6b; --ng-bg: #3d1618;
    --expect-bg: #1c2633; --expect-line: #6e8fb8; --note-bg: #3a3212; --badge: #6e7781;
  }} }}
  body {{ font-family: -apple-system, "Hiragino Sans", sans-serif; margin: 0; padding: 32px 24px; background: var(--bg); color: var(--text); line-height: 1.7; }}
  .wrap {{ max-width: 880px; margin: 0 auto; }}
  header h1 {{ font-size: 22px; line-height: 1.4; margin: 0 0 8px; }}
  .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1px; margin: 12px 0 0; background: var(--line); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
  .meta div {{ background: var(--card); padding: 8px 14px; }}
  .meta dt {{ font-size: 11px; color: var(--faint); }}
  .meta dd {{ margin: 0; font-size: 13px; word-break: keep-all; overflow-wrap: anywhere; }}
  .summary {{ display: flex; gap: 8px; margin-top: 14px; font-size: 13px; font-weight: 600; }}
  .summary span {{ padding: 3px 12px; border-radius: 999px; }}
  .summary .ok {{ color: var(--ok); background: var(--ok-bg); }}
  .summary .ng {{ color: var(--ng); background: var(--ng-bg); }}
  .summary .all {{ color: var(--sub); background: var(--card); border: 1px solid var(--line); }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 20px 24px; margin-top: 16px; }}
  .card.ng {{ border-left: 4px solid var(--ng); }}
  .card h2 {{ font-size: 16px; line-height: 1.5; margin: 0; display: flex; align-items: flex-start; gap: 10px; }}
  .card h2 .title {{ flex: 1; padding-top: 1px; }}
  .badge {{ background: var(--badge); color: #fff; border-radius: 50%; width: 26px; height: 26px; display: inline-flex; align-items: center; justify-content: center; font-size: 13px; flex: none; }}
  .result {{ flex: none; font-size: 13px; font-weight: 700; padding: 2px 12px; border-radius: 999px; color: var(--ok); background: var(--ok-bg); }}
  .card.ng .result {{ color: var(--ng); background: var(--ng-bg); }}
  .block {{ margin: 12px 0 0; padding: 8px 14px 10px; border-left: 3px solid; border-radius: 4px; font-size: 14px; }}
  .block span {{ display: block; font-size: 12px; font-weight: 700; margin-bottom: 2px; }}
  .block p {{ margin: 0; white-space: pre-wrap; }}
  .block.expect {{ background: var(--expect-bg); border-color: var(--expect-line); }}
  .block.expect span {{ color: var(--expect-line); }}
  .block.outcome {{ background: var(--ok-bg); border-color: var(--ok); }}
  .block.outcome span {{ color: var(--ok); }}
  .card.ng .block.outcome {{ background: var(--ng-bg); border-color: var(--ng); }}
  .card.ng .block.outcome span {{ color: var(--ng); }}
  .shots {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0 0; }}
  .shots figure {{ margin: 0; }}
  .shots img {{ width: 360px; max-width: 100%; border-radius: 10px; border: 1px solid var(--line); display: block; }}
  /* 複数端末を並べたときはカード幅を分け合う */
  .shots.multi figure {{ flex: 1 1 0; min-width: 0; }}
  .shots.multi img {{ width: 100%; }}
  .shots figcaption {{ margin-bottom: 6px; font-size: 12px; color: var(--faint); text-align: center; }}
  .note {{ display: flex; gap: 12px; margin: 16px 0 0; padding: 8px 14px; background: var(--note-bg); border-radius: 4px; font-size: 14px; }}
  .note span {{ font-weight: 700; flex: none; font-size: 13px; padding-top: 1px; }}
  .note p {{ margin: 0; white-space: pre-wrap; }}
  footer.card h2 {{ font-size: 15px; margin-bottom: 8px; }}
  footer.card p {{ margin: 0; font-size: 13px; color: var(--sub); white-space: pre-wrap; }}
  @media (max-width: 640px) {{ body {{ padding: 20px 16px; }} .card {{ padding: 16px; }} .shots {{ flex-direction: column; }} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{html.escape(manifest["title"])}</h1>
    {meta_lines}
    {summary}
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
    for scale in PNG_SCALES:
        png_path.unlink(missing_ok=True)
        run_chrome(
            ["--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--force-device-scale-factor={scale}", f"--window-size={PNG_PAGE_WIDTH},{height}",
             f"--screenshot={png_path}", f"file://{html_path}"],
            done=stable_file(png_path),
        )
        if not png_path.exists():
            print("PNGを書き出せませんでした。", file=sys.stderr)
            return None
        if png_path.stat().st_size <= PNG_MAX_BYTES:
            return png_path
        print(f"PNGが{PNG_MAX_BYTES // 1024 // 1024}MBを超えたため倍率を下げて再描画します"
              f"（scale {scale}）。", file=sys.stderr)
    print("最小倍率でも上限を超えました。PRコメントには貼れない可能性があります。", file=sys.stderr)
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
