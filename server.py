"""オリジナル展示タイム ビューア — ローカルサーバ

起動:  python server.py        →  http://localhost:8765/
"""
import json
import os
import sys
import time
import datetime
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scraper  # noqa: E402

PORT = int(os.environ.get("PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))
JST = datetime.timezone(datetime.timedelta(hours=9))


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
            if u.path == "/api/race":
                jcd, rno = int(q["jcd"]), int(q["rno"])
                if jcd not in scraper.VENUES or not 1 <= rno <= 12:
                    return self._send(400, {"error": "invalid jcd/rno"})
                return self._send(200, scraper.get_race(jcd, rno, today()))
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}/"
    print(f"オリジナル展示タイム ビューア: {url}  (Ctrl+C で終了)")
    if "--no-browser" not in sys.argv:
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
