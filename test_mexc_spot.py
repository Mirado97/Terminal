"""Быстрый тест MEXC Spot API: купить 10 USDT → USDC."""
import hmac
import hashlib
import json
import time
import urllib.request
from pathlib import Path

env = dict(
    l.split("=", 1)
    for l in Path(".env").read_text().splitlines()
    if "=" in l and not l.startswith("#")
)
key = env.get("MEXC_API_KEY", "").strip()
sec = env.get("MEXC_API_SECRET", "").strip()

ts     = str(int(time.time() * 1000))
params = f"symbol=USDCUSDT&side=BUY&type=MARKET&quoteOrderQty=10&timestamp={ts}"
sig    = hmac.new(sec.encode(), params.encode(), hashlib.sha256).hexdigest()
url    = f"https://api.mexc.com/api/v3/order?{params}&signature={sig}"

req = urllib.request.Request(url, method="POST", headers={"X-MEXC-APIKEY": key})
try:
    with urllib.request.urlopen(req) as r:
        print(json.dumps(json.loads(r.read()), indent=2))
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())
