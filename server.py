"""オリジナル展示タイム ビューア — ローカルサーバ

起動:  python server.py        →  http://localhost:8765/
"""
import json
import os
import sys
import re
import time
import threading
import datetime
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scraper  # noqa: E402

PORT = int(os.environ.get("PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))
JST = datetime.timezone(datetime.timedelta(hours=9))
# アイコンなど、そのまま配る静的ファイル
STATIC_FILES = {
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/icon-180.png": ("icon-180.png", "image/png"),
    "/favicon.ico": ("icon-180.png", "image/png"),
    "/apple-touch-icon.png": ("icon-180.png", "image/png"),
}


# 6艇分のタイムがそろったレースは保存し、以後は取り直さない（「更新」ボタンのときだけ取り直す）
CACHE_DIR = os.path.join(HERE, "cache")
FINAL = {}
FINAL_LOCK = threading.Lock()


def _cache_file(day):
    return os.path.join(CACHE_DIR, f"{day}.json")


def _load_final(day):
    if day in FINAL:
        return FINAL[day]
    data = {}
    try:
        with open(_cache_file(day), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        pass
    FINAL.clear()          # 前日分はメモリから捨てる
    FINAL[day] = data
    return data


def get_race_cached(jcd, rno, day, refresh=False):
    key = f"{jcd}-{rno}"
    with FINAL_LOCK:
        hit = _load_final(day).get(key)
    if hit and not refresh:
        return hit
    res = scraper.get_race(jcd, rno, day)
    res["complete"] = scraper.is_complete(res)
    if res["complete"]:
        with FINAL_LOCK:
            data = _load_final(day)
            data[key] = res
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                tmp = _cache_file(day) + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, _cache_file(day))
            except Exception:  # noqa: BLE001
                pass
    return res


# レース場を選んだら、展示済みのレースを裏で順番に取得して保存しておく
PREFETCH_AHEAD_MIN = 30      # 締切30分前を過ぎたレース（展示航走が済んでいる見込み）を対象にする
PREFETCHING = set()
PREFETCH_LOCK = threading.Lock()


def _prefetch_targets(jcd, day):
    races = (scraper.get_schedule(day).get(jcd) or {}).get("races", {})
    now = datetime.datetime.now(JST)
    now_min = now.hour * 60 + now.minute
    with FINAL_LOCK:
        done = set(_load_final(day))
    out = []
    for rno, info in sorted(races.items()):
        t = info.get("closed_at") or ""
        if not re.fullmatch(r"\d{1,2}:\d{2}", t):
            continue
        h, m = map(int, t.split(":"))
        if h * 60 + m - now_min <= PREFETCH_AHEAD_MIN and f"{jcd}-{rno}" not in done:
            out.append(int(rno))
    return out


def _prefetch_worker(jcd, day):
    try:
        for rno in _prefetch_targets(jcd, day):
            try:
                get_race_cached(jcd, rno, day)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)  # 相手サイトへの負担を抑える
    finally:
        with PREFETCH_LOCK:
            PREFETCHING.discard(jcd)


def start_prefetch(jcd, day):
    with PREFETCH_LOCK:
        if jcd in PREFETCHING:
            return False
        PREFETCHING.add(jcd)
    threading.Thread(target=_prefetch_worker, args=(jcd, day), daemon=True).start()
    return True


def today():
    return datetime.datetime.now(JST).strftime("%Y%m%d")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path in STATIC_FILES:
                name, ctype = STATIC_FILES[u.path]
                with open(os.path.join(HERE, name), "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                return self.wfile.write(body)
            if u.path == "/api/venues":
                day = today()
                sched = scraper.get_schedule(day)
                venues = []
                for jcd, v in scraper.VENUES.items():
                    s = sched.get(jcd)
                    venues.append({
                        "jcd": jcd, "name": v["name"],
                        "original": v["kind"] != "official",
                        "open": s is not None,
                        "title": s["title"] if s else "",
                        "races": s["races"] if s else {},
                    })
                return self._send(200, {"day": day, "schedule_known": bool(sched), "venues": venues})
            if u.path == "/api/prefetch":
                jcd = int(q["jcd"])
                if jcd not in scraper.VENUES:
                    return self._send(400, {"error": "invalid jcd"})
                return self._send(200, {"started": start_prefetch(jcd, today())})
            if u.path == "/api/race":
                jcd, rno = int(q["jcd"]), int(q["rno"])
                if jcd not in scraper.VENUES or not 1 <= rno <= 12:
                    return self._send(400, {"error": "invalid jcd/rno"})
                return self._send(200, get_race_cached(jcd, rno, today(), refresh=q.get("refresh") == "1"))
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})


def main():
    # Render などのクラウドでは PORT が渡されるので、外部から受け付ける
    hosted = "RENDER" in os.environ or "--public" in sys.argv
    host = "0.0.0.0" if hosted else "127.0.0.1"
    srv = ThreadingHTTPServer((host, PORT), Handler)
    url = f"http://localhost:{PORT}/"
    print(f"新加速タイム: {url}  (Ctrl+C で終了)", flush=True)
    if not hosted and "--no-browser" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
