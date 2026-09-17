#!/usr/bin/env python3
"""Maestro の MCP サーバーを常駐させ、細いクライアントから叩く。

  maestrod.py inspect <UDID> <名前> [幅 高さ] [bundle id]   画面を読む
  maestrod.py run     <UDID> '<flow yaml>'      操作する
  maestrod.py stop                              デーモンを止める

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
SOCK = WORK / "maestrod.sock"
IDLE_EXIT = 1800          # これだけ無操作なら自分で終わる。残骸を残さないため
CONNECT_TIMEOUT = 180

# ---------- デーモン ----------

def drivers():
    """このマシンで動いている XCUITest ドライバのPID。"""
    r = subprocess.run(["pgrep", "-f", "test-without-building"],
                       capture_output=True, text=True)
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]

def serve():
    WORK.mkdir(parents=True, exist_ok=True)
    # 前の実行が残したドライバを落としてから始める。
    # 1台のデバイスに2本繋がると両方が壊れ、以降すべての操作が
    # "Device became unreachable" で落ちる。実測で確認済み。
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

def call(tool, args, autostart=True):
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
            spawn()
    raise RuntimeError("接続できない")

def spawn():
    WORK.mkdir(parents=True, exist_ok=True)
    if SOCK.exists():
        SOCK.unlink()
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "__serve__"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    end = time.time() + 60
    while time.time() < end:
        if SOCK.exists():
            return
        time.sleep(0.2)
    raise RuntimeError("デーモンが立ち上がらない")

def show_cache(bundle, text):
    """画面が変わったときだけ、その画面の攻略メモを出す。

    引くかどうかをエージェントの判断に任せると、実測で一度も引かれ
    なかった。ダンプは必ず取るので、ここに同梱すれば引き忘れが
    起きない。毎回出すと同じ文面が積み上がるので、画面が変わった
    ときに限る。
    """
    if not bundle:
        return
    screen = next((l.split("画面: ", 1)[1].strip()
                   for l in text.splitlines() if l.startswith("画面: ")), None)
    if not screen:
        return
    marker = WORK / ".last_screen"
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
        return serve()
    if cmd == "stop":
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(10); s.connect(str(SOCK))
            s.sendall(b'{"op":"stop"}\n'); s.close()
            print("止めた")
        except Exception:
            print("動いていない")
        return
    if cmd == "inspect":
        udid, name = sys.argv[2], sys.argv[3]
        w, h = (sys.argv[4], sys.argv[5]) if len(sys.argv) > 5 else ("390", "844")
        bundle = sys.argv[6] if len(sys.argv) > 6 else None
        r = call("inspect_screen", {"device_id": udid})
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
        show_cache(bundle, out.stdout)
        return
    if cmd == "run":
        udid, yaml = sys.argv[2], sys.argv[3]
        r = call("run", {"device_id": udid, "yaml": yaml})
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
