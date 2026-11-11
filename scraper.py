"""各ボートレース場のオリジナル展示データ（展示・一周・まわり足・直線）を取得する。

標準ライブラリのみで動作します。
"""
import re
import time
import json
import html
import urllib.error
import urllib.request
import urllib.parse
from html.parser import HTMLParser

UA = "BoatTenjiViewer/1.0 (personal use)"
TIMEOUT = 20

# ---------------------------------------------------------------------------
# 会場設定
#   kind:
#     jlc       : {base}/modules/yosou/<path>  (day, race を埋め込む)
#     asp       : {base}/asp/kyogi/<jcd>/pc/<page><rr>.htm  (当日のみ・日付指定不可)
#     miyajima  : POST race_common/require/kaisai_reload.php
#     toda      : {base}/xml/kaisai/<YYYYMMDD>/race_table_original_<rr>.xml
#                 (ttime=展示, rnd=一周, cnr=まわり足, str=直線)
#     gamagori  : {base}/asp/gamagori/kyogi/kyogihtml/time/time<YYYYMMDD>07<rr>.htm
#     official  : 独自計測なし。boatrace.jp の直前情報から展示タイムのみ
# ---------------------------------------------------------------------------
VENUES = {
    1:  dict(name="桐生", kind="jlc", base="https://www.kiryu-kyotei.com",
             path="cyokuzen.php?day={d}&race={r}&if=1"),
    2:  dict(name="戸田", kind="toda", base="https://www.boatrace-toda.jp"),
    3:  dict(name="江戸川", kind="official"),
    4:  dict(name="平和島", kind="asp", base="https://www.heiwajima.gr.jp", page="yoso05"),
    5:  dict(name="多摩川", kind="jlc", base="https://www.boatrace-tamagawa.com",
             path="oriten.php?day={d}&race={r}&jo=05&if=1"),
    6:  dict(name="浜名湖", kind="jlc", base="https://www.boatrace-hamanako.jp",
             path="group-cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    7:  dict(name="蒲郡", kind="gamagori", base="https://www.gamagori-kyotei.com"),
    8:  dict(name="常滑", kind="jlc", base="https://www.boatrace-tokoname.jp",
             path="group-cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    9:  dict(name="津", kind="jlc", base="https://www.boatrace-tsu.com",
             path="group-tenji.php?type=group-tenji&day={d}&race={r}&if=1&kind=1"),
    10: dict(name="三国", kind="jlc", base="https://www.boatrace-mikuni.jp",
             path="group-cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    11: dict(name="琵琶湖", kind="jlc", base="https://www.boatrace-biwako.jp",
             path="cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    12: dict(name="住之江", kind="asp", base="https://www.boatrace-suminoe.jp", page="st02"),
    13: dict(name="尼崎", kind="jlc", base="https://www.boatrace-amagasaki.jp",
             path="group-cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    14: dict(name="鳴門", kind="jlc", base="https://www.n14.jp",
             path="group-cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    15: dict(name="丸亀", kind="asp", base="https://www.marugameboat.jp", page="st02"),
    16: dict(name="児島", kind="asp", base="https://www.kojimaboat.jp", page="st02"),
    17: dict(name="宮島", kind="miyajima", base="https://www.boatrace-miyajima.com"),
    18: dict(name="徳山", kind="official"),
    19: dict(name="下関", kind="jlc", base="https://www.boatrace-shimonoseki.jp",
             path="group-cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    20: dict(name="若松", kind="jlc", base="https://www.wmb.jp",
             path="group-cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    21: dict(name="芦屋", kind="jlc", base="https://www.boatrace-ashiya.com",
             path="group-cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    22: dict(name="福岡", kind="jlc", base="https://www.boatrace-fukuoka.com",
             path="tenji_info.php?day={d}&race={r}&if=1"),
    23: dict(name="唐津", kind="jlc", base="https://www.boatrace-karatsu.jp",
             path="group-cyokuzen.php?day={d}&race={r}&if=1&kind=2"),
    24: dict(name="大村", kind="official"),
}


# ---------------------------------------------------------------------------
# HTTP（短時間キャッシュ付き）
# ---------------------------------------------------------------------------
_cache = {}


def fetch(url, data=None, ttl=20):
    key = (url, data)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": UA, "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        raw = res.read()
        charset = res.headers.get_content_charset()
    text = None
    for enc in [c for c in (charset, "utf-8", "cp932", "euc_jp") if c]:
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")
    _cache[key] = (time.time(), text)
    return text


# ---------------------------------------------------------------------------
# テーブルパーサ（rowspan / colspan を展開してグリッド化）
# ---------------------------------------------------------------------------
class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []      # 完成したテーブル: list[list[cell]]
        self._stack = []      # 構築中テーブル
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None})
        elif not self._stack:
            return
        elif tag == "tr":
            t = self._stack[-1]
            t["row"] = []
            t["rows"].append(t["row"])
        elif tag in ("td", "th"):
            t = self._stack[-1]
            if t["row"] is None:
                t["row"] = []
                t["rows"].append(t["row"])
            def _int(v):
                try:
                    return max(1, int(re.sub(r"\D", "", v or "1") or 1))
                except ValueError:
                    return 1
            t["cell"] = {"text": "", "rs": _int(a.get("rowspan")), "cs": _int(a.get("colspan")),
                         "cls": a.get("class", "")}
            t["row"].append(t["cell"])
        elif tag == "br":
            self._data(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "table" and self._stack:
            t = self._stack.pop()
            self.tables.append(t["rows"])
        elif tag in ("td", "th") and self._stack:
            self._stack[-1]["cell"] = None

    def handle_data(self, data):
        self._data(data)

    def _data(self, data):
        if self._skip or not self._stack:
            return
        c = self._stack[-1]["cell"]
        if c is not None:
            c["text"] += data


def _grid(rows):
    grid, pending = [], {}
    for r, row in enumerate(rows):
        line, c, it = [], 0, iter(row)
        cells = list(row)
        i = 0
        while i < len(cells) or (r, c) in pending:
            if (r, c) in pending:
                line.append(pending.pop((r, c)))
                c += 1
                continue
            cell = cells[i]
            i += 1
            for dc in range(cell["cs"]):
                for dr in range(cell["rs"]):
                    if dr == 0:
                        continue
                    pending[(r + dr, c + dc)] = cell
                line.append(cell)
            c += cell["cs"]
        grid.append(line)
    return grid


def _norm(s):
    s = html.unescape(s or "").replace("　", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


LAP_KEYS = ("一周", "1周", "１周", "半周")
MAWARI_KEYS = ("まわり足", "回り足", "周り足")
CHOKU_KEYS = ("直線",)
NAME_KEYS = ("選手名", "レーサー名")
NUM_RE = re.compile(r"^-?\d{1,3}\.\d{1,3}$")


def _clean_name(s):
    s = re.sub(r"[AB][12]\s*/?|\d+|/[^ ]*", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()[:12]


def _val(text):
    t = _norm(text).replace(" ", "")
    m = re.search(r"\d{1,3}\.\d{2}", t)
    return m.group(0) if m else None


def parse_original_table(page):
    """ページ内から「まわり足」列を持つテーブルを探し、艇ごとの値を返す。"""
    p = _TableParser()
    p.feed(page)
    best = None
    for rows in p.tables:
        flat = " ".join(_norm(c["text"]) for row in rows for c in row)
        if not any(k in flat for k in MAWARI_KEYS + LAP_KEYS):
            continue
        # 最初のデータ行（先頭セルが 1〜6）
        first = None
        for ri, row in enumerate(rows):
            if row and re.fullmatch(r"[1-6１-６]", _norm(row[0]["text"])):
                first = ri
                break
        if first is None or first == 0:
            continue
        # ヘッダ部とデータ部は別々にグリッド化（ヘッダの rowspan がデータ行へはみ出すサイトがあるため）
        hg = _grid(rows[:first])
        g = _grid(rows[first:])
        ncol = max(len(l) for l in hg + g)
        heads = []
        for c in range(ncol):
            parts = []
            for line in hg:
                if c < len(line):
                    t = _norm(line[c]["text"])
                    if t and (not parts or parts[-1] != t):
                        parts.append(t)
            heads.append(" ".join(parts))
        first = 0

        def find(keys, exclude=()):
            for c, h in enumerate(heads):
                if c in exclude:
                    continue
                if any(k in h for k in keys):
                    return c
            return None

        lap = find(LAP_KEYS)
        maw = find(MAWARI_KEYS)
        cho = find(CHOKU_KEYS)
        used = {x for x in (lap, maw, cho) if x is not None}
        ten = None
        for c, h in enumerate(heads):
            if c in used:
                continue
            hh = h.replace("オリジナル展示データ", "").replace("オリジナル展示", "")
            if "展示" in hh and not any(x in hh for x in ("スタート", "評価", "リプレイ", "ST")):
                ten = c
                break
        if maw is None and lap is None:
            continue
        name_c = find(NAME_KEYS)
        lap_label = "一周"
        if lap is not None and "半周" in heads[lap]:
            lap_label = "半周"

        boats = {}
        for line in g[first:]:
            if not line:
                continue
            w = _norm(line[0]["text"]).translate(str.maketrans("１２３４５６", "123456"))
            if not re.fullmatch(r"[1-6]", w) or int(w) in boats:
                continue
            def cell(c):
                return line[c]["text"] if c is not None and c < len(line) else ""
            row_text = " ".join(_norm(x["text"]) for x in line)
            toban = re.search(r"(?<!\d)(\d{4})(?!\d)", row_text)
            name = _norm(cell(name_c)) if name_c is not None else ""
            name = re.sub(r"[\s]+", " ", name)
            boats[int(w)] = {
                "waku": int(w),
                "name": name,
                "toban": toban.group(1) if toban else "",
                "tenji": _val(cell(ten)),
                "lap": _val(cell(lap)),
                "mawari": _val(cell(maw)),
                "choku": _val(cell(cho)),
            }
        if not boats:
            continue
        cand = {"lap_label": lap_label, "boats": [boats[k] for k in sorted(boats)],
                "has": {"tenji": ten is not None, "lap": lap is not None,
                        "mawari": maw is not None, "choku": cho is not None}}
        score = sum(1 for b in cand["boats"] for k in ("tenji", "lap", "mawari", "choku") if b[k])
        score += sum(1 for b in cand["boats"] if b["name"])  # 名前ありを優先
        if best is None or score > best[0]:
            best = (score, cand)
    return best[1] if best else None


def parse_toda_xml(text):
    """戸田の race_table_original_XX.xml（record ごとに1艇）を解析する。"""
    def tag(rec, name):
        m = re.search(rf"<{name}>(.*?)</{name}>", rec, re.S)
        return _norm(m.group(1)) if m else ""

    boats = {}
    for rec in re.findall(r"<record>(.*?)</record>", text, re.S):
        w = tag(rec, "teiban")
        if not re.fullmatch(r"[1-6]", w) or int(w) in boats:
            continue
        boats[int(w)] = {
            "waku": int(w), "name": tag(rec, "name"), "toban": "",
            "tenji": _val(tag(rec, "ttime")),
            "lap": _val(tag(rec, "rnd")),
            "mawari": _val(tag(rec, "cnr")),
            "choku": _val(tag(rec, "str")),
        }
    if not boats:
        return None
    return {"lap_label": "一周", "boats": [boats[k] for k in sorted(boats)],
            "has": {"tenji": True, "lap": True, "mawari": True, "choku": True}}


def parse_gamagori(page):
    """蒲郡のオリジナル展示ページ（展示・一周・まわり足・直線）を解析する。"""
    boats = {}
    parts = re.split(r'<td class="waku w([1-6])">', page)
    for w, seg in zip(parts[1::2], parts[2::2]):
        seg = seg.split("</tr>")[0]
        m = re.search(r'<div class="name">.*?<a[^>]*>(.*?)</a>', seg, re.S)
        vals = [_val(_norm(re.sub(r"<[^>]+>", "", x)))
                for x in re.findall(r'<td class="score[^"]*">(.*?)</td>', seg, re.S)]
        if len(vals) < 4 or int(w) in boats:
            continue
        boats[int(w)] = {"waku": int(w), "name": _norm(m.group(1)) if m else "", "toban": "",
                         "tenji": vals[0], "lap": vals[1], "mawari": vals[2], "choku": vals[3]}
    if not boats:
        return None
    return {"lap_label": "一周", "boats": [boats[k] for k in sorted(boats)],
            "has": {"tenji": True, "lap": True, "mawari": True, "choku": True}}


# ---------------------------------------------------------------------------
# 公式サイト（展示タイムのみ）
# ---------------------------------------------------------------------------
def fetch_official(jcd, rno, day):
    url = f"https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={rno}&jcd={jcd:02d}&hd={day}"
    page = fetch(url)
    boats = []
    # tbody ごとに1艇
    for tb in re.findall(r'<tbody class="is-fs12[^"]*">(.*?)</tbody>', page, re.S):
        w = re.search(r'is-boatColor(\d)', tb)
        if not w:
            continue
        links = re.findall(r'<a href="/owpc/pc/data/racersearch/profile\?toban=(\d+)">(.*?)</a>', tb, re.S)
        links = [(t, _norm(re.sub(r"<[^>]+>", "", n))) for t, n in links]
        name = next((x for x in links if x[1]), None)
        tds = re.findall(r'<td[^>]*rowspan="4"[^>]*>(.*?)</td>', tb, re.S)
        vals = [_norm(re.sub(r"<[^>]+>", " ", t)) for t in tds]
        tenji = next((v for v in vals if re.fullmatch(r"\d\.\d{2}", v)), None)
        boats.append({"waku": int(w.group(1)),
                      "name": name[1] if name else "",
                      "toban": name[0] if name else "",
                      "tenji": tenji, "lap": None, "mawari": None, "choku": None})
    # スタート展示の進入コース（表はコース順に艇番が並ぶ）
    courses = {}
    i = page.find("スタート展示</th>")
    if i >= 0:
        seg = page[i:i + 20000].split("</table>")[0]
        order = [int(x) for x in re.findall(r'table1_boatImage1Number is-type\d">\s*(\d)\s*<', seg)]
        if sorted(order) == [1, 2, 3, 4, 5, 6]:
            courses = {w: c + 1 for c, w in enumerate(order)}
    return url, boats, courses


# ---------------------------------------------------------------------------
# 取得のエントリポイント
# ---------------------------------------------------------------------------
def get_race(jcd, rno, day):
    v = VENUES[jcd]
    res = {"jcd": jcd, "venue": v["name"], "rno": rno, "day": day,
           "lap_label": "一周", "boats": [], "source": None, "original": False,
           "note": "", "has": {}, "courses": {}}
    kind = v["kind"]
    data = None
    try:
        if kind == "jlc":
            url = v["base"] + "/modules/yosou/" + v["path"].format(d=day, r=rno)
            res["source"] = url
            data = parse_original_table(fetch(url))
        elif kind == "asp":
            url = f'{v["base"]}/asp/kyogi/{jcd:02d}/pc/{v["page"]}{rno:02d}.htm'
            res["source"] = url
            data = parse_original_table(fetch(url))
            res["note"] = "このレース場のページは当日分のみ公開されています。"
        elif kind == "toda":
            url = f'{v["base"]}/xml/kaisai/{day}/race_table_original_{rno:02d}.xml'
            res["source"] = v["base"] + "/"
            try:
                data = parse_toda_xml(fetch(url))
            except urllib.error.HTTPError as e:
                if e.code != 404:  # 404 = 展示前でまだファイルが無い
                    raise
        elif kind == "gamagori":
            url = f'{v["base"]}/asp/gamagori/kyogi/kyogihtml/time/time{day}{jcd:02d}{rno:02d}.htm'
            res["source"] = url
            try:
                data = parse_gamagori(fetch(url))
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
        elif kind == "miyajima":
            url = v["base"] + "/race_common/require/kaisai_reload.php"
            res["source"] = v["base"] + "/"
            page = fetch(url, data=(("race", str(rno)), ("date", day)))
            for seg in page.split("####"):
                if any(k in seg for k in MAWARI_KEYS):
                    d = parse_original_table(seg)
                    if d and (data is None or sum(1 for b in d["boats"] if b["name"]) >
                              sum(1 for b in data["boats"] if b["name"])):
                        data = d
    except Exception as e:  # noqa: BLE001
        res["note"] = f"独自データの取得に失敗しました: {e}"

    if data and any(b["tenji"] or b["lap"] or b["mawari"] or b["choku"] for b in data["boats"]):
        res.update(boats=data["boats"], lap_label=data["lap_label"], original=True, has=data["has"])
        # 選手名は表記ゆれが大きいので公式サイトの表記で統一（失敗時は会場ページの表記を整形）
        names = {}
        try:
            _, ob, res["courses"] = fetch_official(jcd, rno, day)
            names = {b["waku"]: b for b in ob if b["name"]}
        except Exception:  # noqa: BLE001
            pass
        for b in res["boats"]:
            if b["waku"] in names:
                b["name"] = names[b["waku"]]["name"]
                b["toban"] = names[b["waku"]]["toban"] or b["toban"]
            else:
                b["name"] = _clean_name(b["name"])
        return res

    # フォールバック：公式の展示タイムのみ
    if kind == "official":
        res["note"] = "このレース場は独自計測（一周・まわり足・直線）を公開していません。展示タイムのみ表示します。"
    elif not res["note"]:
        res["note"] = "独自計測データがまだ公開されていません（展示航走前の可能性）。公式の展示タイムを表示します。"
    try:
        url, boats, res["courses"] = fetch_official(jcd, rno, day)
        res["boats"] = boats
        res["has"] = {"tenji": True, "lap": False, "mawari": False, "choku": False}
        if kind == "official":
            res["source"] = url
        res["official_source"] = url
    except Exception as e:  # noqa: BLE001
        res["note"] += f"（公式サイトの取得にも失敗: {e}）"
    return res


def is_complete(res):
    """6艇すべての取得対象タイムがそろっていれば True（以後は取り直さない）。"""
    boats = res.get("boats") or []
    if len(boats) != 6:
        return False
    keys = [k for k in ("tenji", "lap", "mawari", "choku") if (res.get("has") or {}).get(k)]
    if not keys or (VENUES[res["jcd"]]["kind"] != "official" and not res.get("original")):
        return False
    return all(b.get(k) for b in boats for k in keys)


# ---------------------------------------------------------------------------
# 本日の開催一覧（Boatrace Open API）
# ---------------------------------------------------------------------------
def get_schedule(day):
    url = f"https://boatraceopenapi.github.io/programs/v2/{day[:4]}/{day}.json"
    out = {}
    try:
        j = json.loads(fetch(url, ttl=600))
        for p in j.get("programs", []):
            if p.get("race_date", "").replace("-", "") not in ("", day):
                continue
            s = out.setdefault(p["race_stadium_number"], {"title": p.get("race_title", ""), "races": {}})
            s["races"][p["race_number"]] = {
                "closed_at": (p.get("race_closed_at") or "")[11:16],
                "subtitle": p.get("race_subtitle", ""),
            }
    except Exception:  # noqa: BLE001
        pass
    if not out:
        out = get_schedule_official(day)
    return out


def get_schedule_official(day):
    """Open API が未更新のとき、boatrace.jp のレース一覧から開催場と締切予定時刻を取る。"""
    base = "https://www.boatrace.jp/owpc/pc/race"
    out = {}
    try:
        idx = fetch(f"{base}/index?hd={day}", ttl=600)
    except Exception:  # noqa: BLE001
        return out
    for jcd in sorted({int(j) for j in re.findall(rf"raceindex\?jcd=(\d+)&amp;hd={day}", idx)}):
        try:
            t = fetch(f"{base}/raceindex?jcd={jcd:02d}&hd={day}", ttl=600)
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r'class="heading2_titleName">(.*?)<', t)
        s = {"title": _norm(m.group(1)) if m else "", "races": {}}
        for rno, hm in re.findall(
                r'racelist\?rno=(\d+)&amp;jcd=\d+&amp;hd=\d+">\d+R</a>\s*</td>\s*<td>(\d{1,2}:\d{2})</td>', t):
            s["races"][int(rno)] = {"closed_at": hm.zfill(5), "subtitle": ""}
        if s["races"]:
            out[jcd] = s
    return out


if __name__ == "__main__":
    import sys
    jcd, rno = int(sys.argv[1]), int(sys.argv[2])
    day = sys.argv[3] if len(sys.argv) > 3 else time.strftime("%Y%m%d")
    print(json.dumps(get_race(jcd, rno, day), ensure_ascii=False, indent=1))
