#!/usr/bin/env python3
"""Maestro の MCP サーバーを常駐させ、細いクライアントから叩く。

  maestrod.py inspect <UDID> <名前> [幅 高さ]                   画面を読む
  maestrod.py run     <UDID> '<flow yaml>'                      操作する
  maestrod.py tap     <UDID> <x> <y> <名前> [幅 高さ] [bundle]  タップ→確認
  maestrod.py stop    [UDID]                                    止める（省くと全部）
  maestrod.py sweep   [日数]                                    .work の後片付け

なぜデーモンを挟むのか、理由が2つある。

1. **サーバーを立て直すとドライバが壊れる。** 実測で、maestro mcp を
   起動・終了するたびに XCUITest ドライバが不安定になり、次の接続が
   swipeV2 / deviceInfo / isScreenStatic のどこかで落ちた。1本を維持
   すれば19操作連続で1回も落ちない。Bash は毎回プロセスが終わるので、
   stdio を握り続ける役が別に要る。

2. **MCPツールの結果はそのままコンテキストに載る。** inspect_screen の
   ペイロードは約10KBあり、1回3〜4kトークン。57回なら200kトークンで
   破綻する。デーモンを挟めば、削った約600トークンだけを渡せる。

速度（実測）
  初回の接続  約10秒（1回だけ）
  inspect     0.3秒   （maestro hierarchy は16.6秒）
  run         0.3秒   （maestro test は17.7〜25秒）
  実ジェスチャーを伴う scroll は5〜8秒。これは操作そのものの時間
"""
import json, os, re, shutil, socket, subprocess, sys, threading, time, queue
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE.parent / ".work"
# ソケットはデバイスごとに分けるが、**同時に生かすのは1本だけ**。
#
# 1つのMCPサーバーが握れるドライバは1台ぶんで、別のデバイスを要求すると
# Device became unreachable で落ちる。かといって2本同時に立てると、今度は
# 互いに干渉して「iPhoneを要求したのにiPadの階層が返る」が起きる。実測で
# 両方を確認した。1本だけなら device_id は正しく効く。
#
# 端末ごとに順番に撮る運用（スキルの手順もそう）とは合う。端末を
# 切り替えるたびに初回の約10秒を払い直す。
#
# ソケットは /tmp に置く。AF_UNIX のパス上限は約104バイトで、
# リポジトリ配下（.work/）にUDID付きで置くと超える。
def sock_for(udid):
    return Path(f"/tmp/maestrod-{os.getuid()}-{udid[:8]}.sock")
# 画面識別子が取れなかったときのマーカー値。空文字だと「マーカーが無い」と
# 区別が付かず、前後比較が「変わっていない」に倒れる。
UNKNOWN = "【不明】"
IDLE_EXIT = 1800          # これだけ無操作なら自分で終わる。残骸を残さないため
CONNECT_TIMEOUT = 180

# ---------- デーモン ----------

def drivers(udid=None):
    """XCUITest ドライバのPID。udid を省くと全部。

    起動時は**全部**落とす。残った別デバイスのドライバがあると、
    新しいサーバーがそれに接続してしまい、iPadを要求したのに
    iPhoneの階層が返る。実測で確認した（新規セットアップの時間が
    かからないのが傍証になる）。同時に1本しか立てない前提なので、
    巻き添えにする相手はいない。
    """
    r = subprocess.run(["ps", "-ww", "-A", "-o", "pid=,command="],
                       capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        if "test-without-building" in line and (udid is None or f"id={udid}" in line):
            pid = line.strip().split(None, 1)[0]
            if pid.isdigit():
                out.append(int(pid))
    return out

def serve(udid):
    WORK.mkdir(parents=True, exist_ok=True)
    SOCK = sock_for(udid)
    # 残っているドライバを全部落としてから始める。
    # 1台のデバイスに2本繋がると両方が壊れ、別デバイスのが残っていると
    # そちらに繋がって別の端末の階層が返る。どちらも実測で確認済み。
    for pid in drivers():
        try: os.kill(pid, 15)
        except Exception: pass
    if SOCK.exists():
        SOCK.unlink()
    proc = subprocess.Popen(["maestro", "mcp", "--no-viewer"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)
    inbox = queue.Queue()

    def reader():
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("{"):
                try: inbox.put(json.loads(line))
                except Exception: pass
    threading.Thread(target=reader, daemon=True).start()

    state = {"rid": 0}
    lock = threading.Lock()

    def rpc(method, params, timeout=CONNECT_TIMEOUT):
        with lock:
            state["rid"] += 1
            rid = state["rid"]
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            end = time.time() + timeout
            while time.time() < end:
                try: m = inbox.get(timeout=1)
                except queue.Empty: continue
                if m.get("id") == rid:
                    return m
            return {"error": {"message": "タイムアウト"}}

    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "maestrod", "version": "1"}})
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK))
    srv.listen(8)
    srv.settimeout(60)
    last = time.time()
    while True:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            if time.time() - last > IDLE_EXIT:
                break
            continue
        last = time.time()
        try:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk: break
                data += chunk
            req = json.loads(data.decode())
            if req.get("op") == "stop":
                conn.sendall(b'{"ok":true}\n'); conn.close(); break
            r = rpc("tools/call", {"name": req["tool"], "arguments": req["args"]})
            body = "".join(c.get("text", "") for c in (r.get("result") or {}).get("content", []))
            if "error" in r:
                body = json.dumps(r["error"], ensure_ascii=False)
            conn.sendall((json.dumps({"ok": "error" not in r, "text": body}) + "\n").encode())
        except Exception as e:
            try: conn.sendall((json.dumps({"ok": False, "text": str(e)}) + "\n").encode())
            except Exception: pass
        finally:
            try: conn.close()
            except Exception: pass
    proc.terminate()
    try: proc.wait(timeout=10)
    except Exception: pass
    # maestro を殺してもドライバは孤児として残ることがある。
    # 残しても速度は戻らず、次の起動を壊すだけなので必ず落とす。
    for pid in drivers():
        try: os.kill(pid, 15)
        except Exception: pass
    if SOCK.exists():
        SOCK.unlink()

# ---------- クライアント ----------

def call(udid, tool, args, autostart=True):
    SOCK = sock_for(udid)
    for attempt in (1, 2):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(CONNECT_TIMEOUT)
            s.connect(str(SOCK))
            s.sendall((json.dumps({"tool": tool, "args": args}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk: break
                buf += chunk
            s.close()
            return json.loads(buf.decode())
        except (FileNotFoundError, ConnectionRefusedError):
            if not autostart or attempt == 2:
                raise
            spawn(udid)
    raise RuntimeError("接続できない")

def socks():
    return sorted(Path("/tmp").glob(f"maestrod-{os.getuid()}-*.sock"))

def stop_one(sock):
    """1本止める。返り値は実際に生きていたか。"""
    alive = False
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(10); c.connect(str(sock))
        c.sendall(b'{"op":"stop"}\n'); c.close()
        alive = True
    except Exception:
        pass
    try: sock.unlink()
    except Exception: pass
    return alive

def stop_others(udid):
    """別のデバイスのデーモンを止める。2本同時に立つと階層が混ざる。"""
    for sock in socks():
        if sock.name == sock_for(udid).name:
            continue
        if stop_one(sock):
            print(f"別デバイスのデーモンを止めた: {sock.name}", file=sys.stderr)

def spawn(udid):
    WORK.mkdir(parents=True, exist_ok=True)
    # デーモンは起動時に XCUITest ドライバを**全部**落とす（別デバイスのが
    # 残っていると、そちらに繋がって別の端末の階層が返るため）。Xcode などで
    # 自分のUIテストを走らせていると巻き添えになるので、黙って殺さない。
    left = drivers()
    if left:
        print(f"注意: 起動中の XCUITest ドライバ {len(left)}件 (pid "
              f"{', '.join(map(str, left))}) を落とす。Xcode でUIテストを"
              "走らせているなら、先に止めてから実行する。", file=sys.stderr)
    stop_others(udid)
    SOCK = sock_for(udid)
    if SOCK.exists():
        SOCK.unlink()
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "__serve__", udid],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    end = time.time() + 60
    while time.time() < end:
        if SOCK.exists():
            return
        time.sleep(0.2)
    raise RuntimeError("デーモンが立ち上がらない")

def screen_of(text):
    """抽出結果の先頭行から画面識別子を取る。取れなければ None。"""
    s = next((l.split("画面: ", 1)[1].strip()
              for l in text.splitlines() if l.startswith("画面: ")), None)
    return None if (s is None or s.startswith("【不明】")) else s

def cmd_inspect(udid, name, w, h):
    r = call(udid, "inspect_screen", {"device_id": udid})
    if not r["ok"] or not r["text"].lstrip().startswith('{"ui_schema"'):
        sys.exit(f"画面を読めなかった: {r['text'][:200]}\n"
                 "ドライバが壊れている可能性がある。maestrod.py stop してやり直す。")
    WORK.mkdir(parents=True, exist_ok=True)
    raw = WORK / f"{name}.json"
    raw.write_text(r["text"])
    out = subprocess.run([sys.executable, str(HERE / "elements.py"), str(raw), w, h],
                         capture_output=True, text=True)
    (WORK / f"{name}.txt").write_text(out.stdout)
    # タップ時に「その座標に何があったか」「画面が変わったか」を見るために、
    # 端末ごとの直近ぶんを固定名で置く。名前は毎回変わるので追えないため。
    #
    # マーカーはデバイスごとに分ける。iPhoneとiPadを並行で走らせると
    # 共有マーカーを奪い合い、片方が「変わっていない」と誤判定する。
    (WORK / f".last_dump_{udid}.txt").write_text(out.stdout)
    (WORK / f".last_screen_{udid}").write_text(screen_of(out.stdout) or UNKNOWN)
    print("\n".join(l for l in out.stdout.splitlines() if "×" not in l))
    print(f"生: {raw} / 全行: {WORK / (name + '.txt')}", file=sys.stderr)

def label_at(udid, x, y, tol=40):
    """直前のダンプで、その座標にいちばん近い要素のラベル。"""
    f = WORK / f".last_dump_{udid}.txt"
    if not f.exists():
        return None
    best = None
    for line in f.read_text().splitlines():
        m = re.match(r"\s*\((-?\d+),(-?\d+)\)\s+\S+\s+(.*)", line)
        if not m:
            continue
        cx, cy, lab = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        d = abs(cx - x) + abs(cy - y)
        if d <= tol and (best is None or d < best[0]):
            best = (d, lab)
    return best[1] if best else None

def cmd_tap(udid, x, y, name, w, h, bundle):
    """タップし、画面が変わったかまで見て返す。

    タップの前後はどのみちダンプを取るので、前後の画面識別子を
    突き合わせる手間はここで吸収できる。「効いたが遷移しない」と
    「そもそも効いていない」の区別は判断が要るので、そこはしない。
    """
    before = (WORK / f".last_screen_{udid}").read_text().strip() \
        if (WORK / f".last_screen_{udid}").exists() else None
    on = label_at(udid, x, y)

    r = call(udid, "run", {"device_id": udid,
                           "yaml": f"appId: {bundle or 'x'}\n---\n- tapOn:\n    point: {x},{y}\n"})
    if not (r["ok"] and r["text"].lstrip().startswith('{"success":true')):
        sys.exit(f"タップできなかった: {r['text'][:200]}")

    cmd_inspect(udid, name, w, h)

    after = (WORK / f".last_screen_{udid}").read_text().strip() \
        if (WORK / f".last_screen_{udid}").exists() else None
    # 識別子が取れない画面を挟むと、前後が同じに見えても同じ画面とは限らない。
    # 「変わっていない」と言い切らず、判定できないことをそのまま出す。
    if UNKNOWN in (before, after) or before is None:
        moved = " → 画面識別子が取れないので、遷移したかは判定できない"
    elif before == after:
        moved = f" → {before} のまま"
    else:
        moved = f" → {before} から {after} へ"
    print(f"\nタップ ({x},{y})" + (f" 「{on}」" if on else "") + moved)

def cmd_sweep(days):
    """.work の古いものを消す。`.txt` だけは残す。

    生JSONは1実行で約3MBになるが、判定が済めば用済み。`.txt` は2KBしか
    なく、過去の実行で何を見て判断したかの記録になるので残す。
    """
    cut = time.time() - days * 86400
    n = freed = 0
    md = WORK / "maestro"
    if md.exists():
        for d in md.iterdir():
            if d.is_dir() and d.stat().st_mtime < cut:
                freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d)
                n += 1
    if WORK.exists():
        for f in WORK.iterdir():
            if f.is_file() and f.suffix in (".json", ".yaml", ".err") and f.stat().st_mtime < cut:
                freed += f.stat().st_size
                f.unlink()
                n += 1
    print(f".work: {days}日より古い {n}件 / {freed // 1024}KB を消した（.txt は残す）")

def cmd_stop(udid=None):
    """デーモンを止め、残ったドライバも落とす。UDID を省くと全部。

    デーモンは終了時に自分でドライバを片付けるが、ソケットだけ死んで
    ドライバが孤児として残ることがある。残すと次の起動を壊すだけなので、
    デーモンが居なくても必ず見に行く。
    """
    targets = [sock_for(udid)] if udid else socks()
    stopped = sum(1 for t in targets if stop_one(t))
    print(f"デーモンを止めた: {stopped}件" if stopped else "デーモンは動いていない")
    left = drivers(udid)
    for pid in left:
        try: os.kill(pid, 15)
        except Exception: pass
    if left:
        print(f"残っていた XCUITest ドライバ {len(left)}件を落とした")

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "__serve__":
        return serve(sys.argv[2])
    if cmd == "stop":
        return cmd_stop(sys.argv[2] if len(sys.argv) > 2 else None)
    if cmd == "sweep":
        return cmd_sweep(int(sys.argv[2]) if len(sys.argv) > 2 else 14)
    if cmd == "inspect":
        udid, name = sys.argv[2], sys.argv[3]
        w, h = (sys.argv[4], sys.argv[5]) if len(sys.argv) > 5 else ("390", "844")
        return cmd_inspect(udid, name, w, h)
    if cmd == "tap":
        udid, x, y, name = sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
        w, h = (sys.argv[6], sys.argv[7]) if len(sys.argv) > 7 else ("390", "844")
        bundle = sys.argv[8] if len(sys.argv) > 8 else None
        return cmd_tap(udid, x, y, name, w, h, bundle)
    if cmd == "run":
        udid, yaml = sys.argv[2], sys.argv[3]
        r = call(udid, "run", {"device_id": udid, "yaml": yaml})
        # JSON-RPCが成功でも、ツールの本文が失敗を伝えていることがある。
        # 両方見ないと、落ちた操作を成功として報告してしまう。
        body = r["text"]
        good = r["ok"] and body.lstrip().startswith('{"success":true')
        print(("OK " if good else "失敗 ") + body[:300])
        if not good:
            sys.exit(1)
        return
    sys.exit(__doc__)

main()
