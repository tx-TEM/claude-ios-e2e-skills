#!/usr/bin/env python3
"""Maestro の MCP サーバーを常駐させ、細いクライアントから叩く。

  maestrod.py inspect <UDID> <名前> [幅 高さ] [bundle id]   画面を読む
  maestrod.py run     <UDID> '<flow yaml>'      操作する
  maestrod.py stop    <UDID>                    そのデバイスのデーモンを止める

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
import json, os, socket, subprocess, sys, threading, time, queue
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

def stop_others(udid):
    """別のデバイスのデーモンを止める。2本同時に立つと階層が混ざる。"""
    for sock in Path("/tmp").glob(f"maestrod-{os.getuid()}-*.sock"):
        if sock.name == sock_for(udid).name:
            continue
        try:
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.settimeout(10); c.connect(str(sock))
            c.sendall(b'{"op":"stop"}\n'); c.close()
            print(f"別デバイスのデーモンを止めた: {sock.name}", file=sys.stderr)
        except Exception:
            pass
        try: sock.unlink()
        except Exception: pass

def spawn(udid):
    WORK.mkdir(parents=True, exist_ok=True)
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

def show_cache(bundle, udid, text):
    """画面が変わったときだけ、その画面の攻略メモを出す。

    引くかどうかをエージェントの判断に任せると、実測で一度も引かれ
    なかった。ダンプは必ず取るので、ここに同梱すれば引き忘れが
    起きない。毎回出すと同じ文面が積み上がるので、画面が変わった
    ときに限る。

    マーカーはデバイスごとに分ける。iPhoneとiPadを並行で走らせると
    共有マーカーを奪い合い、片方が「変わっていない」と誤判定して
    記録が出なくなる。
    """
    if not bundle:
        return
    screen = next((l.split("画面: ", 1)[1].strip()
                   for l in text.splitlines() if l.startswith("画面: ")), None)
    if not screen:
        return
    marker = WORK / f".last_screen_{udid}"
    prev = marker.read_text().strip() if marker.exists() else ""
    if prev == screen:
        return
    marker.write_text(screen)
    r = subprocess.run([sys.executable, str(HERE / "cache.py"), "screen", bundle, screen],
                       capture_output=True, text=True)
    body = r.stdout.strip()
    if body and "の記録は無い" not in body:
        print("\n--- この画面の記録 ---")
        print(body)

def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "__serve__":
        return serve(sys.argv[2])
    if cmd == "stop":
        if len(sys.argv) < 3:
            sys.exit("stop には UDID が要る（他のデバイスを巻き添えにしないため）")
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(10); s.connect(str(sock_for(sys.argv[2])))
            s.sendall(b'{"op":"stop"}\n'); s.close()
            print("止めた")
        except Exception:
            print("動いていない")
        return
    if cmd == "inspect":
        udid, name = sys.argv[2], sys.argv[3]
        w, h = (sys.argv[4], sys.argv[5]) if len(sys.argv) > 5 else ("390", "844")
        bundle = sys.argv[6] if len(sys.argv) > 6 else None
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
        print("\n".join(l for l in out.stdout.splitlines() if "×" not in l))
        print(f"生: {raw} / 全行: {WORK / (name + '.txt')}", file=sys.stderr)
        show_cache(bundle, udid, out.stdout)
        return
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
