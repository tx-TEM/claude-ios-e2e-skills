#!/usr/bin/env python3
"""スクリーンショット付き動作確認レポート（単一HTML）を生成する。

使い方:
    python3 build_report.py <manifest.json> [--title <題>]
    python3 build_report.py <manifest.json> --check     形だけ確かめる（レポートは作らない）

**`--check` は判定を書き終えた側（evidence-judge）が叩く。** 判定の欄は LLM が書くので、
形が揺れる（footer を見出しごとの辞書で書く、など）。レポートを作る段で止まると、
直すのが判定を書いた本人ではなくなる。書いた直後に確かめて、その場で直させる。
`RETAKE` はここでは通す（撮り直しを待っている項目で、判定の書き忘れではない）。
`SKIP` は理由（`desc`）が空なら止める。

    python3 build_report.py manifest.json \
      --title "一覧からお気に入り登録できるようにする — 動作確認レポート"

**ヘッダは題だけ渡す。** ほかは事実から出す。

- 確認環境: マニフェストの `devices`（manifest.py が UDID から引いた機種名と OS）から
- 実施日: 組んだ日

マニフェスト形式:
{
  "devices": {
    "iphone": {"udid": "…", "model": "iPhone 17 Pro", "os": "iOS 26.5"},
    "ipad": {"udid": "…", "model": "iPad Air 13-inch (M4)", "os": "iOS 26.5"}
  },
  "cases": [
    {
      "title": "一覧からお気に入り登録できる",
      "items": [
        {
          "name": "test_01",
          "title": "一覧のセルにお気に入りボタンが表示される",
          "images": [
            {"src": "shots/iphone/test_01.png", "label": "iPhone"},
            {"src": "shots/ipad/test_01.png", "label": "iPad"}
          ],
          "expect": "各セルの右端に星アイコンのボタンが出る",
          "desc": "アイテム一覧画面。各セルの右端に星アイコンのボタンが表示される。",
          "note": "",
          "result": "OK"
        }
      ]
    }
  ],
  "footer": "実行全体の補足（確認していない項目、一時コード、作成したテストデータなど）"
}

- **「実施日」だけは件数の行の右端に出す。** 他の項目（確認環境）は何で確かめたかで、
  いつの結果かはそれと性格が違う。他の項目は件数の行の下に1行ずつ並ぶ
- 1項目に複数の画像を並べられる。同じ確認項目をiPhoneとiPadで撮った場合など
- 複数端末を撮った項目は、全ての端末で確認できたときだけ result を "OK" にする
- **result の `SKIP` は「撮れなかった。成否を確かめていない」。** OK でも NG でもないので、
  どちらにも数えず「撮れなかった」として別に数える。マニフェストから外して footer に書くと、
  項目の話が footer に回り、「全 N 項目」からも消える。理由は desc に書き（空なら止まる）、
  カードでは「撮れなかった理由」の帯になる。画像は撮れたぶんだけ出す（無いファイルは飛ばす。
  1枚も無くてよい）。複数端末で1台だけ撮れたときは、その端末の画像が出る
- **result は省略できず、`PENDING` や `RETAKE` のままでも止まる。** title が空のときも止まる（骨組みのまま生成しようとしている）
- カードは「期待 → 結果 → 証跡 → 注記」の順。**2つを続けて証跡の上に置くのは、
  OK の根拠をその場で読めるようにするため。** 結果の帯は OK / NG と同じ色にする。 desc は観測した事実なので、それだけでは
  何を期待していたかが分からない。枚数でレイアウトは変えない。どの欄も省略でき、
  無い欄は行ごと出さない
    expect  期待（レビューで合意したもの）
    desc    結果（観測した事実）
    note    その項目だけの但し書き（期待の訂正、証跡の読み方の注意）
- **自動確認（checked）は出さない。** 撮影のフローが何を待ったかは作る側の話で、
  読む側は全項目で画像と判定を見る。確認として弱い項目は手順0のレビューで扱う
- **項目ごとの話はカードに書き、footer は実行全体の話だけにする。** footer に項目番号つきで
  書くと、読む側がカードと行き来して突き合わせることになる
- **カードはテストケース（`cases` の要素）ごとにまとめ、頭にその題の見出しを置く。** 同じ機能を
  順に確かめる項目のまとまりが読める。1項目だけで題が項目と同じなら置かない
- テストケースと項目には他の欄を持たせてよい。**知らない欄は無視する** — このマニフェストは
  手順0で作って工程ごとに埋めていくので、screen / flow なども載っている
- 項目を引くのは manifest_items.py（`walk()`）
- src はマニフェストからの相対パスまたは絶対パス
- 画像は sips があれば750px幅に縮小してから埋め込む
- manifest と同じディレクトリに verification_report.html を出力する
- HTMLと同時に、PRコメント貼り付け用の1枚画像（verification_report.png）も生成する
  （headless Chromeを使用。Chrome不在時はスキップ）
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
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifest_items import walk   # noqa: E402  項目を並び順に引く

SHOT_WIDTH = 750   # 埋め込む画像の幅（px）
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


def load_image_b64(path: pathlib.Path) -> str:
    """画像を必要なら縮小してbase64文字列にする。sipsが無ければ原寸で埋め込む。"""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            resized = pathlib.Path(tmp) / path.name
            subprocess.run(
                ["sips", "--resampleWidth", str(SHOT_WIDTH), str(path), "--out", str(resized)],
                check=True,
                capture_output=True,
            )
            data = resized.read_bytes()
    except (subprocess.CalledProcessError, FileNotFoundError):
        data = path.read_bytes()
    return base64.b64encode(data).decode()


def section_images(section: dict, base_dir: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """images を (パス, ラベル) の一覧にして返す。

    `SKIP` の項目は、撮れたぶん（ファイルがあるもの）だけ。撮れなかった端末の証跡は無い。
    """
    skip = section.get("result") == "SKIP"
    items = []
    for item in section.get("images") or []:
        path = pathlib.Path(item["src"])
        if not path.is_absolute():
            path = base_dir / path
        if skip and not path.exists():
            continue
        items.append((path, item.get("label", "")))
    return items


def case_heads(cases: list) -> dict:
    """テストケースの見出しを置く位置。{項目の番号（1始まり、通し）: 見出し}。

    各テストケースの最初の項目の上に置く。**1項目だけで、題が項目と同じテストケースには
    置かない** — 同じ文がカードのすぐ上に2回並ぶだけになる。
    """
    heads, i = {}, 1
    for case in cases:
        items = case.get("items") or []
        title = case.get("title")
        if items and title and not (len(items) == 1 and title == items[0].get("title")):
            heads[i] = title
        i += len(items)
    return heads


def section_rows(section: dict) -> tuple[list[tuple[str, str]], str]:
    """証跡の上に出す期待と結果、下に出す注記。値の無いものは出さない。

    **2つを続けて証跡の上に出す。** 何を期待し何が起きたかを突き合わせてから
    画像を見る。枚数でレイアウトを変えないので、1枚でも複数端末でも同じ順に並ぶ。
    `SKIP` の項目の desc は観測した結果ではなく、撮れなかった理由。
    """
    outcome = "撮れなかった理由" if section.get("result") == "SKIP" else "結果"
    top = [(label, section[key])
           for label, key in (("期待", "expect"), (outcome, "desc"))
           if (section.get(key) or "").strip()]
    return top, (section.get("note") or "").strip()


TEXT_FIELDS = ("title", "expect", "desc", "note")
# レポートに出せる result。SKIP は撮れなかった項目（成否を確かめていない）
RESULTS = ("OK", "NG", "SKIP")
# カードの帯（記号, 見せる言葉）と、カードの class
BADGES = {"OK": ("✓", "OK", ""), "NG": ("✗", "NG", " ng"), "SKIP": ("—", "撮れなかった", " skip")}


def shape_problems(manifest: dict) -> list:
    """判定の欄の形の崩れ。[説明, ...]。**文字列で書く欄に、文字列以外が入っていないか。**

    footer も desc も、レポートにはそのまま文として出す。辞書や配列で書かれると
    出しようがない（見出しを付けたいなら、文字列の中に改行で書く）。
    """
    out = []
    footer = manifest.get("footer", "")
    if not isinstance(footer, str):
        out.append(f"footer が文字列ではない（{type(footer).__name__}）。"
                   "見出しごとに改行した1つの文字列で書く（例: \"確認していないこと: …\\n作成したデータ: 無し\"）")
    for _, item in walk(manifest):
        name = item.get("name", "")
        for key in TEXT_FIELDS:
            v = item.get(key, "")
            if v is not None and not isinstance(v, str):
                out.append(f"{name} の {key} が文字列ではない"
                           f"（{type(v).__name__}）。1つの文字列で書く")
        if item.get("result") not in RESULTS + ("RETAKE",):
            out.append(f"{name} の result が "
                       f"{item.get('result')!r}。OK / NG / SKIP / RETAKE のどれかを書く")
        elif item.get("result") == "SKIP" and not str(item.get("desc") or "").strip():
            out.append(f"{name} は SKIP なのに desc が空。撮れなかった理由（何を試して、どこで止まったか。"
                       "端末ごとに違えば端末も）を書く")
    return out


def build(manifest_path: pathlib.Path, title: str, env: str = "", day: str = "") -> pathlib.Path:
    manifest = json.loads(manifest_path.read_text())
    base_dir = manifest_path.parent
    # 形の崩れはここでも止める（--check を通さずに叩かれたとき）。result の未判定は下で見る
    bad = [p for p in shape_problems(manifest) if "の result が" not in p]
    if bad:
        raise SystemExit("マニフェストの形が崩れている:\n  " + "\n  ".join(bad))

    cards = ""
    counts = dict.fromkeys(RESULTS, 0)
    heads = case_heads(manifest.get("cases") or [])
    for i, (_, section) in enumerate(walk(manifest), start=1):
        # **既定を OK にしない。** このマニフェストは工程ごとに埋めていくので、
        # 判定を書き忘れた項目が黙って OK で出ると、確かめていないものを
        # 確かめたことにしてしまう。画像を読む前に見る。
        if section.get("result") not in RESULTS:
            raise SystemExit(f'{section.get("name", i)} "{section.get("title", "")}" の result が'
                             f' {section.get("result")!r}。判定していない項目と'
                             '撮り直しが要る項目を、レポートに出せない。')
        if not (section.get("title") or "").strip():
            raise SystemExit(f'{section.get("name", i)} に title が無い。'
                             'マニフェストの骨組みのまま生成しようとしている。')
        shots = ""
        for path, label in section_images(section, base_dir):
            caption = f'<figcaption>{html.escape(label)}</figcaption>' if label else ""
            shots += (f'<figure>{caption}<img src="data:image/png;base64,'
                      f'{load_image_b64(path)}" alt="{html.escape(label) or f"screenshot {i}"}" /></figure>')
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
        mark, word, kind = BADGES[result]
        counts[result] += 1
        if i in heads:
            cards += f'''
    <h2 class="case">{html.escape(heads[i])}</h2>'''
        cards += f'''
    <section class="card{kind}">
      <h2><span class="badge">{i}</span><span class="title">{html.escape(section["title"])}</span><span class="result">{mark} {html.escape(word)}</span></h2>
      {expect_html}
      {f'<div class="shots{multi}">{shots}</div>' if shots else ""}
      {rows_html}
    </section>'''

    meta_lines = (f'<div class="meta"><p><span>確認環境</span>{html.escape(env)}</p></div>' if env else "")
    summary = (f'<div class="summary"><span class="ok">OK {counts["OK"]}</span>'
               f'<span class="ng">NG {counts["NG"]}</span>'
               # 撮れなかった項目が無い回は出さない。OK と NG は0件でも出す（0件であることが結果）
               + (f'<span class="skip">撮れなかった {counts["SKIP"]}</span>' if counts["SKIP"] else "")
               + f'<span class="all">全 {sum(counts.values())} 項目</span>'
               + (f'<span class="date">実施日 {html.escape(day)}</span>' if day else "")
               + '</div>')
    footer = manifest.get("footer", "")
    footer_html = (f'<footer class="card"><h2>補足</h2><p>{html.escape(footer)}</p></footer>'
                   if footer else "")

    doc = f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #f4f5f7; --card: #fff; --text: #1f2328; --sub: #59636e; --faint: #8c959f;
    --line: #e3e6ea; --ok: #1a7f37; --ok-bg: #dafbe1; --ng: #cf222e; --ng-bg: #ffebe9;
    --expect-bg: #f0f4fa; --expect-line: #6e8fb8; --note-bg: #fff8c5; --badge: #57606a;
    --skip: #59636e; --skip-bg: #eaeef2;
  }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg: #16181b; --card: #22252a; --text: #e6e8eb; --sub: #aab1b9; --faint: #7d858f;
    --line: #33373d; --ok: #4ac26b; --ok-bg: #12311d; --ng: #ff6b6b; --ng-bg: #3d1618;
    --expect-bg: #1c2633; --expect-line: #6e8fb8; --note-bg: #3a3212; --badge: #6e7781;
    --skip: #aab1b9; --skip-bg: #2d3137;
  }} }}
  body {{ font-family: -apple-system, "Hiragino Sans", sans-serif; margin: 0; padding: 32px 24px; background: var(--bg); color: var(--text); line-height: 1.7; }}
  .wrap {{ max-width: 880px; margin: 0 auto; }}
  header h1 {{ font-size: 22px; line-height: 1.4; margin: 0 0 8px; }}
  .meta {{ margin: 10px 0 0; font-size: 13px; color: var(--sub); }}
  .meta p {{ margin: 0; }}
  .meta span {{ color: var(--faint); margin-right: 10px; }}
  .summary {{ display: flex; gap: 8px; margin-top: 14px; font-size: 13px; font-weight: 600; }}
  .summary span {{ padding: 3px 12px; border-radius: 999px; }}
  .summary .ok {{ color: var(--ok); background: var(--ok-bg); }}
  .summary .ng {{ color: var(--ng); background: var(--ng-bg); }}
  .summary .skip {{ color: var(--skip); background: var(--skip-bg); }}
  .summary .all {{ color: var(--sub); background: var(--card); border: 1px solid var(--line); }}
  .summary {{ align-items: center; flex-wrap: wrap; }}
  .summary .date {{ margin-left: auto; padding: 0; color: var(--sub); font-weight: 400; }}
  h2.case {{ font-size: 15px; line-height: 1.5; margin: 32px 0 0; padding-left: 10px; border-left: 4px solid var(--badge); color: var(--sub); }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 20px 24px; margin-top: 16px; }}
  .card.ng {{ border-left: 4px solid var(--ng); }}
  .card h2 {{ font-size: 16px; line-height: 1.5; margin: 0; display: flex; align-items: flex-start; gap: 10px; }}
  .card h2 .title {{ flex: 1; padding-top: 1px; }}
  .badge {{ background: var(--badge); color: #fff; border-radius: 50%; width: 26px; height: 26px; display: inline-flex; align-items: center; justify-content: center; font-size: 13px; flex: none; }}
  .result {{ flex: none; font-size: 13px; font-weight: 700; padding: 2px 12px; border-radius: 999px; color: var(--ok); background: var(--ok-bg); }}
  .card.ng .result {{ color: var(--ng); background: var(--ng-bg); }}
  .card.skip {{ border-left: 4px solid var(--skip); }}
  .card.skip .result {{ color: var(--skip); background: var(--skip-bg); }}
  .block {{ margin: 12px 0 0; padding: 8px 14px 10px; border-left: 3px solid; border-radius: 4px; font-size: 14px; }}
  .block span {{ display: block; font-size: 12px; font-weight: 700; margin-bottom: 2px; }}
  .block p {{ margin: 0; white-space: pre-wrap; }}
  .block.expect {{ background: var(--expect-bg); border-color: var(--expect-line); }}
  .block.expect span {{ color: var(--expect-line); }}
  .block.outcome {{ background: var(--ok-bg); border-color: var(--ok); }}
  .block.outcome span {{ color: var(--ok); }}
  .card.ng .block.outcome {{ background: var(--ng-bg); border-color: var(--ng); }}
  .card.ng .block.outcome span {{ color: var(--ng); }}
  .card.skip .block.outcome {{ background: var(--skip-bg); border-color: var(--skip); }}
  .card.skip .block.outcome span {{ color: var(--skip); }}
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
    <h1>{html.escape(title)}</h1>
    {summary}
    {meta_lines}
  </header>
  {cards}
  {footer_html}
</div>
</body>
</html>'''

    out_path = base_dir / "verification_report.html"
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
    # file:// の後ろは絶対パスでないと読めない。マニフェストを相対パスで渡すと
    # HTML の出力先も相対になり、Chrome はエラーページを撮ってそのまま PNG にする
    html_path = html_path.resolve()
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
    argv = sys.argv[1:]
    args, check = [], False
    title = "動作確認レポート"
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--title":
            title = argv[i + 1]; i += 2
        elif a == "--check":
            check = True; i += 1
        elif a.startswith("--"):
            sys.exit("知らない引数: " + a)
        else:
            args.append(a); i += 1
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    manifest_path = pathlib.Path(args[0])
    if check:
        bad = shape_problems(json.loads(manifest_path.read_text()))
        if bad:
            print("形が崩れている。直してからもう一度 --check を叩く:")
            for p in bad:
                print("  " + p)
            sys.exit(1)
        print("形は揃っている")
        return
    devices = json.loads(manifest_path.read_text()).get("devices") or {}
    env = "、".join(f"{v['model']} シミュレーター ({v['os']})" for v in devices.values())
    out_path = build(manifest_path, title, env, date.today().isoformat())
    size = out_path.stat().st_size
    print(f"{out_path} ({size / 1024 / 1024:.2f} MB)")
    png_path = render_png(out_path)
    if png_path is not None:
        print(f"{png_path} ({png_path.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
