"""シミュレーターを UDID から引く。機種名・OS・起動しているかを返す。

  from simulators import lookup
  lookup("0606D63A-…")  # {"udid": …, "model": "iPhone 17 Pro", "os": "iOS 26.5", "booted": True}
"""
import json
import re
import subprocess
import sys


def lookup(udid):
    r = subprocess.run(["xcrun", "simctl", "list", "devices", "-j"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("シミュレーターの一覧が取れない（xcrun simctl list devices）: " + r.stderr.strip())
    for runtime, devices in json.loads(r.stdout)["devices"].items():
        for d in devices:
            if d["udid"] == udid:
                # com.apple.CoreSimulator.SimRuntime.iOS-26-5 → iOS 26.5
                m = re.search(r"\.([A-Za-z]+)-([\d-]+)$", runtime)
                os_name = "{} {}".format(m.group(1), m.group(2).replace("-", ".")) if m else runtime
                return {"udid": udid, "model": d["name"], "os": os_name,
                        "booted": d["state"] == "Booted"}
    sys.exit(f"UDID {udid} のシミュレーターが無い（xcrun simctl list devices で確かめる）")
