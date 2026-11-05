"""GitHub Actions 用：本日の全レース場・全レースのデータを集めて静的サイトを作る。

    python build_data.py <出力フォルダ> [前回データのURL]

- <出力フォルダ>/index.html と <出力フォルダ>/data/today.json を作る。
- 前回データ（公開中の today.json）があれば読み込み、締切が近い／データ未取得のレースだけ取り直す。
  （各レース場サイトへのアクセスを最小限にするため）
"""
import datetime
import json
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scraper  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))
WINDOW_BEFORE = 90   # 締切の何分前から取り直すか
WINDOW_AFTER = 30    # 締切の何分後まで取り直すか
WAIT = 0.5           # リクエスト間隔（秒）


def load_previous(url, day):
    if not url:
        return {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": scraper.UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            prev = json.loads(r.read().decode("utf-8"))
        if prev.get("day") == day:
            return prev.get("races", {})
    except Exception as e:  # noqa: BLE001
        print("前回データなし:", e)
    return {}


def complete(res):
    """取り直し不要なほどデータがそろっているか。"""
    b = res.get("boats") or []
    if len(b) != 6:
        return False
    if res.get("original"):
        return all(x.get("tenji") and x.get("mawari") for x in b)
    return all(x.get("tenji") for x in b)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "site"
    prev_url = sys.argv[2] if len(sys.argv) > 2 else ""
    now = datetime.datetime.now(JST)
    day = now.strftime("%Y%m%d")
    sched = scraper.get_schedule(day)
    prev = load_previous(prev_url, day)
    # 手動更新：指定レース（"場-レース" のカンマ区切り）は時間に関係なく取り直す
    force = {x.strip() for x in os.environ.get("FORCE_RACES", "").split(",") if x.strip()}
    if force:
        print("手動更新:", ",".join(sorted(force)))

    venues, races = [], {}
    fetched = 0
    lock = threading.Lock()

    def do_venue(jcd, s):
        nonlocal fetched
        for rno in sorted(s["races"]):
            key = f"{jcd}-{rno}"
            old = prev.get(key)
            t = s["races"][rno].get("closed_at") or ""
            near = True
            if t and key not in force:
                hh, mm = map(int, t.split(":"))
                closed = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                mins = (closed - now).total_seconds() / 60
                near = -WINDOW_AFTER <= mins <= WINDOW_BEFORE
                if mins > WINDOW_BEFORE:      # まだ展示前：取得しない
                    if old:
                        races[key] = old
                    continue
            if old and complete(old) and not near:
                races[key] = old
                continue
            try:
                res = scraper.get_race(jcd, rno, day)
                res["fetched_at"] = datetime.datetime.now(JST).strftime("%H:%M")
                races[key] = res
                with lock:
                    fetched += 1
            except Exception as e:  # noqa: BLE001
                print("取得失敗", key, e)
                if old:
                    races[key] = old
            time.sleep(WAIT)

    jobs = []
    for jcd, v in scraper.VENUES.items():
        s = sched.get(jcd)
        venues.append({"jcd": jcd, "name": v["name"], "original": v["kind"] != "official",
                       "open": s is not None, "title": s["title"] if s else "",
                       "races": s["races"] if s else {}})
        if s:
            jobs.append((jcd, s))
    # レース場ごとに並列（同じサイトへは順番に1件ずつ）
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda a: do_venue(*a), jobs))

    os.makedirs(os.path.join(out, "data"), exist_ok=True)
    races = dict(sorted(races.items(), key=lambda kv: tuple(map(int, kv[0].split("-")))))
    data = {"day": day, "updated_at": now.strftime("%Y-%m-%d %H:%M"),
            "schedule_known": bool(sched), "venues": venues, "races": races}
    with open(os.path.join(out, "data", "today.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    here = os.path.dirname(os.path.abspath(__file__))
    shutil.copy(os.path.join(here, "index.html"), os.path.join(out, "index.html"))
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"{day} {now:%H:%M} 取得 {fetched} レース / 保存 {len(races)} レース")


if __name__ == "__main__":
    main()
