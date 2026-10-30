#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台北電影時刻查詢
================
抓取台北地區（含新北）所有影城已公布的全部場次，產生一個乾淨的查詢頁面 movies.html。

用法：
    python3 taipei_movies.py            # 抓資料、產生 movies.html 並自動開啟
    python3 taipei_movies.py --days 3   # 只抓 3 天（預設：各影城公布多少就抓多少）
    python3 taipei_movies.py --no-open  # 不自動開瀏覽器
    python3 taipei_movies.py --debug    # 另存原始網頁，方便除錯

只用 Python 內建模組，不需要另外安裝套件。
資料來源：開眼電影網 各戲院時刻表頁面。
"""

import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

# ── 設定 ─────────────────────────────────────────────────────────────
# 常去的戲院（頁面上可以再用 ☆ 增減，這裡是預設值）
FAVORITES = {
    "t02b16",  # 光點華山電影館
    "t02c01",  # 百老匯影城公館店
    "t02a08",  # 誠品電影院
    "t02e21",  # 新店裕隆城威秀影城
}
DAYS = None       # 抓幾天；None = 各影城公布多少就抓多少
WORKERS = 4       # 同時連線數（不要調太高）

# 捷運路線（顯示順序、顏色）
LINES = [
    ("BL", "板南線", "#0070bd"), ("R", "淡水信義線", "#e3002c"),
    ("G", "松山新店線", "#008659"), ("O", "中和新蘆線", "#f8b61c"),
    ("BR", "文湖線", "#c48c31"), ("Y", "環狀線", "#e8c500"),
    ("A", "機場捷運", "#8246af"), ("X", "非捷運沿線", "#8a847c"),
]
# 各影城最近的捷運站：(站名, 第一條是分組用的主要路線, 其他經過的路線…)
MRT = {
    "t02a01": ("市政府", "BL", "R"),          # 信義威秀
    "t02a11": ("市政府", "BL", "R"),          # MUVIE 松仁
    "t02a12": ("南港", "BL"),                 # 南港 LaLaport
    "t02a09": ("南港", "BL"),                 # 喜樂時代 南港
    "t02a03": ("忠孝復興", "BL", "BR"),       # 微風
    "t02a08": ("國父紀念館", "BL"),           # 誠品電影院（松菸）
    "t02a13": ("國父紀念館", "BL"),           # 大巨蛋秀泰
    "t02b16": ("忠孝新生", "BL", "O"),        # 光點華山
    "t02b01": ("西門", "BL", "G"),            # 國賓戲院
    "t02b03": ("西門", "BL", "G"),            # 樂聲
    "t02b05": ("西門", "BL", "G"),            # 台北新光
    "t02b07": ("西門", "BL", "G"),            # 真善美
    "t02b06": ("西門", "BL", "G"),            # 喜樂時代 西門今日
    "t02b08": ("西門", "BL", "G"),            # 西門威秀
    "t02e07": ("板橋", "BL", "Y"),            # 板橋大遠百威秀
    "t02e08": ("府中", "BL"),                 # 府中15
    "t02e15": ("土城", "BL"),                 # 土城秀泰
    "t02b14": ("台北車站", "R", "BL", "A"),   # 京站威秀
    "t02d20": ("中山", "R", "G"),             # 光點台北
    "t02e18": ("紅樹林", "R"),                # 淡水禮萊
    "t02d04": ("芝山＋公車", "R"),            # 天母新光
    "t02c01": ("公館", "G"),                  # 公館百老匯
    "t02e21": ("七張", "G"),                  # 新店裕隆城威秀
    "t02f05": ("景美", "G"),                  # 景美佳佳
    "t02a05": ("南京復興", "G", "BR"),        # 總督
    "t02d02": ("松江南京", "G", "O"),         # 國賓長春
    "t02d03": ("松江南京", "G", "O"),         # 欣欣秀泰
    "t02e04": ("台北橋", "O"),                # 三重天台
    "t02e03": ("頭前庄", "O", "Y"),           # 新莊鴻金寶
    "t02e17": ("頂溪", "O"),                  # 永和喜樂時代
    "t02d05": ("劍南路", "BR"),               # 美麗華
    "t02d06": ("大直", "BR"),                 # 美麗新大直皇家
    "t02a06": ("東湖", "BR"),                 # 哈拉影城
    "t02e20": ("中和", "Y"),                  # 中和環球威秀
    "t02e11": ("新北產業園區", "A", "Y"),     # 國賓新莊晶冠
    "t02e22": ("新莊副都心", "A"),            # 國家影視聽中心
    "t02e12": ("林口", "A"),                  # 林口三井威秀
    "t02e10": ("林口", "A"),                  # 國賓林口昕境
    "t02e13": ("樹林火車站", "X"),            # 樹林秀泰
}

BASE = "https://www.atmovies.com.tw"
AREA_URL = BASE + "/showtime/a02/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


# 自動抓不到戲院清單時的備用名單
FALLBACK_THEATERS = [
    ("t02a01", "台北信義威秀"), ("t02a11", "MUVIE CINEMAS台北松仁威秀"),
    ("t02a12", "台北南港LaLaport威秀影城"), ("t02a09", "喜樂時代影城"),
    ("t02a03", "微風影城"), ("t02a08", "誠品電影院"), ("t02a05", "總督影城"),
    ("t02a13", "大巨蛋秀泰影城"), ("t02a06", "哈拉影城"),
    ("t02b01", "國賓戲院"), ("t02b03", "樂聲影城"), ("t02b05", "台北新光影城"),
    ("t02b14", "台北京站威秀"), ("t02b07", "真善美戲院"),
    ("t02b06", "喜樂時代影城西門今日店"), ("t02b08", "台北西門威秀影城"),
    ("t02b16", "光點華山電影館"),
    ("t02c01", "百老匯影城公館店"),
    ("t02d05", "美麗華大直"), ("t02d04", "台北天母新光影城"), ("t02d03", "欣欣秀泰影城"),
    ("t02d02", "國賓長春影城"), ("t02d06", "美麗新大直皇家影城"), ("t02d20", "光點台北電影院"),
    ("t02e07", "板橋大遠百威秀影城"), ("t02e12", "林口三井OUTLET威秀影城"),
    ("t02e03", "新莊鴻金寶麻吉影城"), ("t02e04", "三重天台戲院"),
    ("t02e10", "國賓林口昕境廣場"), ("t02e11", "國賓新莊晶冠廣場影城"),
    ("t02e13", "樹林秀泰影城"), ("t02e15", "土城秀泰影城"),
    ("t02e18", "國賓影城淡水禮萊廣場"), ("t02e17", "永和喜樂時代影城"),
    ("t02e20", "中和環球威秀影城"), ("t02e21", "新店裕隆城威秀影城"),
    ("t02e08", "府中15"), ("t02e22", "國家電影及視聽文化中心"),
    ("t02f05", "景美佳佳戲院"),
]

RATING = {"G": "普", "P": "護", "F2": "輔12", "F5": "輔15", "R": "限", "PG": "護",
          "PG12": "輔12", "PG15": "輔15"}

TIME_RE = re.compile(r"^(\d{1,2})\s*[:：]\s*(\d{2})$")
RUNTIME_RE = re.compile(r"片長\s*[:：]?\s*(\d+)\s*分")
MOVIE_HREF_RE = re.compile(r"/movie/([A-Za-z0-9]+)/?(?:[?#].*)?$")
THEATER_HREF_RE = re.compile(r"/showtime/(t02[a-z]\d+)/a02/?$")
DATE_HREF_RE = re.compile(r"/showtime/t02[a-z]\d+/a02/(\d{8})/?(?:[?#].*)?$")
SKIP_TEXT = ("其他戲院", "預告", "劇照", "購票", "訂票")


# ── 抓網頁 ───────────────────────────────────────────────────────────
def _ssl_ctx():
    try:
        import certifi  # 有裝就用（macOS 常需要）
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = _ssl_ctx()


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"})
            with urlopen(req, timeout=20, context=SSL_CTX) as r:
                raw = r.read()
                cs = r.headers.get_content_charset()
            for enc in filter(None, [cs, "utf-8", "big5"]):
                try:
                    return raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", "replace")
        except ssl.SSLCertVerificationError as e:
            raise SystemExit(
                "\n憑證驗證失敗。macOS 使用者請執行：\n"
                "  /Applications/Python\\ 3.*/Install\\ Certificates.command\n"
                "或執行：pip3 install certifi\n") from e
        except Exception as e:  # 網路暫時錯誤就重試
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{url}：{last}")


# ── 解析 ─────────────────────────────────────────────────────────────
class _Base(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1


class TheaterListParser(_Base):
    """從台北區頁面找出所有戲院連結。"""

    def __init__(self):
        super().__init__()
        self.found = {}
        self._cur = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag == "a":
            m = THEATER_HREF_RE.search(dict(attrs).get("href") or "")
            if m:
                self._cur, self._buf = m.group(1), []

    def handle_data(self, data):
        if self._cur and not self._skip:
            self._buf.append(data)

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag == "a" and self._cur:
            name = re.sub(r"\s+", "", "".join(self._buf))
            if name and self._cur not in self.found:
                self.found[self._cur] = name
            self._cur = None


class ShowtimeParser(_Base):
    """
    解析單一戲院單日的時刻表頁。
    依文件順序走過去：遇到片名連結就切換「目前電影」，之後出現的 HH:MM 都算這部片的場次。
    這樣即使網站小改版式（標籤、class 換掉），只要「片名連結在場次前面」就還能用。
    """

    def __init__(self):
        super().__init__()
        self.films = {}       # fid -> {title, runtime, rating}
        self.shows = []       # (fid, "HH:MM", version)
        self.dates = set()    # 這間戲院有公布場次的其他日期 YYYYMMDD
        self.page_date = None # 頁面標示的日期
        self._film = None
        self._ver = ""
        self._in_ver = False
        self._link = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        a = dict(attrs)
        if tag == "a":
            href = a.get("href") or ""
            m = MOVIE_HREF_RE.search(href)
            if m:
                self._link, self._buf = m.group(1), []
            m = DATE_HREF_RE.search(href)
            if m:
                self.dates.add(m.group(1))
        elif tag == "ul":
            self._ver = ""          # 每個版本（數位 / IMAX…）是獨立的一組
        elif tag == "li" and "filmVersion" in (a.get("class") or ""):
            self._in_ver, self._ver = True, ""
        elif tag == "img" and self._film:
            m = re.search(r"cer_([A-Za-z0-9]+)\.", a.get("src") or "")
            if m:
                self.films[self._film]["rating"] = RATING.get(m.group(1).upper(), m.group(1))

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag == "a" and self._link:
            title = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if title and not any(s in title for s in SKIP_TEXT):
                self._film, self._ver = self._link, ""
                self.films.setdefault(self._film, {"title": title, "runtime": None, "rating": ""})
            self._link = None
        elif tag == "li":
            self._in_ver = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._link:
            self._buf.append(data)
            return
        t = data.strip()
        if not t:
            return
        if not self.page_date:
            m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})\s*\(", t)
            if m:
                self.page_date = dt.date(*map(int, m.groups()))
        if not self._film:
            return
        if self._in_ver:
            self._ver = (self._ver + " " + t).strip()
            return
        m = TIME_RE.match(t)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if h < 30 and mi < 60:
                self.shows.append((self._film, f"{h:02d}:{mi:02d}", self._ver))
            return
        m = RUNTIME_RE.search(t)
        if m:
            self.films[self._film]["runtime"] = int(m.group(1))


def parse_showtimes(html):
    p = ShowtimeParser()
    p.feed(html)
    used = {f for f, _, _ in p.shows}
    films = {k: v for k, v in p.films.items() if k in used}
    shows = list(dict.fromkeys(p.shows))  # 去重、保持順序
    return films, shows, p.dates, p.page_date


def discover_theaters():
    try:
        p = TheaterListParser()
        p.feed(fetch(AREA_URL))
        found = p.found
    except Exception as e:
        print(f"  ⚠ 戲院清單抓取失敗（{e}），改用內建名單")
        found = {}
    if len(found) < 10:
        found = dict(FALLBACK_THEATERS) | found
    out = []
    for tid, name in found.items():
        st = MRT.get(tid, ("", "X"))
        out.append({"id": tid, "name": name, "station": st[0], "lines": list(st[1:]),
                    "fav": tid in FAVORITES})
    return out


# ── 主流程 ───────────────────────────────────────────────────────────
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="台北電影時刻查詢")
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "movies.html"))
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    print("① 取得台北地區戲院清單…")
    theaters = discover_theaters()
    print(f"   共 {len(theaters)} 間")

    films, shows, failed, empty = {}, [], [], []
    today = dt.date.today()
    last_day = today + dt.timedelta(days=args.days - 1) if args.days else dt.date.max

    def job(t, ymd):
        # 注意：今天的場次只出現在「不帶日期」的網址；帶今天日期的網址是空的
        url = f"{BASE}/showtime/{t['id']}/a02/" + (f"{ymd}/" if ymd else "")
        html = fetch(url)
        if args.debug:
            os.makedirs("debug_html", exist_ok=True)
            with open(f"debug_html/{t['id']}_{ymd or 'today'}.html", "w", encoding="utf-8") as f:
                f.write(html)
        return parse_showtimes(html)

    def collect(t, d, f, s):
        for fid, info in f.items():
            old = films.setdefault(fid, info)
            old["runtime"] = old["runtime"] or info["runtime"]
            old["rating"] = old["rating"] or info["rating"]
        if not s:
            empty.append(f"{t['name']} {d:%m/%d}")
        shows.extend([d.isoformat(), t["id"], fid, hhmm, ver] for fid, hhmm, ver in s)

    def run(jobs, label):
        done, results = 0, []
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(job, t, ymd): (t, ymd) for t, ymd in jobs}
            for fut in as_completed(futs):
                t, ymd = futs[fut]
                done += 1
                print(f"\r   {label} {done}/{len(jobs)}", end="", flush=True)
                try:
                    results.append((t, ymd, fut.result()))
                except Exception as e:
                    failed.append(f"{t['name']} {ymd or '今天'}：{e}")
        print()
        return results

    seen = set()   # (tid, ymd) 已排入

    def next_jobs(t, d, more):
        out = []
        for ymd in sorted(more):
            day = dt.datetime.strptime(ymd, "%Y%m%d").date()
            if d < day <= last_day and (t["id"], ymd) not in seen:
                seen.add((t["id"], ymd))
                out.append((t, ymd))
        return out

    print("② 抓取今天的場次…")
    later = []
    for t, _, (f, s, more, page_date) in run([(t, None) for t in theaters], "今天"):
        d = page_date or today
        seen.add((t["id"], f"{d:%Y%m%d}"))
        collect(t, d, f, s)
        later += next_jobs(t, d, more)

    # 之後的日期：照各影城頁面上列出的日期抓，抓到的頁面若又列出更後面的日期就繼續抓
    rnd = 0
    while later:
        rnd += 1
        if rnd == 1:
            print("③ 抓取之後已公布的場次…")
        batch, later = later, []
        for t, ymd, (f, s, more, page_date) in run(batch, "之後"):
            d = page_date or dt.datetime.strptime(ymd, "%Y%m%d").date()
            collect(t, d, f, s)
            later += next_jobs(t, d, more)

    dates = sorted({dt.date.fromisoformat(s[0]) for s in shows})

    if not shows:
        raise SystemExit("沒有抓到任何場次，可能是網站改版了。請用 --debug 執行並把 debug_html 資料夾裡的檔案給 Claude 看。")

    data = {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "dates": [d.isoformat() for d in dates],
        "theaters": theaters,
        "lines": [{"id": i, "name": n, "color": c} for i, n, c in LINES],
        "films": films,
        "shows": shows,
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("__DATA__", payload))

    print(f"④ 完成：{len(films)} 部電影、{len(shows)} 場 → {args.out}")
    if empty:
        print(f"   （{len(empty)} 頁沒有場次，多半是戲院當天未排片或資料尚未公布）")
    if failed:
        print(f"   ⚠ {len(failed)} 頁抓取失敗：")
        for x in failed[:8]:
            print("     -", x)
    if not args.no_open:
        webbrowser.open(Path(args.out).resolve().as_uri())   # Mac / Windows 都適用


# ── 頁面範本 ─────────────────────────────────────────────────────────
PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>台北電影時刻</title>
<link rel="apple-touch-icon" href="icon.png">
<link rel="icon" href="icon.png">
<meta name="apple-mobile-web-app-title" content="ʕʘ‿ʘʔ🍿">
<meta name="application-name" content="ʕʘ‿ʘʔ🍿">
<meta name="theme-color" content="#f7f5f1">
<style>
:root{
  --bg:#f7f5f1; --panel:#fff; --ink:#1d1b19; --mute:#7a746c; --faint:#b9b2a8;
  --line:#e6e1d9; --accent:#a3261f; --accent-ink:#fff; --accent-soft:#f6e6e3;
  --star:#d08b00; --chip:#efebe4; --shade:rgba(20,16,12,.38);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#151413; --panel:#1e1c1b; --ink:#ece8e2; --mute:#9d968d; --faint:#5e5953;
  --line:#2f2c29; --accent:#e0564b; --accent-ink:#1a0d0b; --accent-soft:#3a1f1c;
  --star:#f0b43c; --chip:#2a2725; --shade:rgba(0,0,0,.6);
}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 "PingFang TC","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif}
body.lock{overflow:hidden}
button,input,select{font:inherit;color:inherit}
.wrap{max-width:860px;margin:0 auto;padding:0 16px 80px}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 0 10px}
header .wrap{padding-bottom:0}
.top{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
h1{font-size:18px;margin:0;letter-spacing:.04em}
.stamp{font-size:12px;color:var(--mute)}
.row{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:10px}
.seg{display:inline-flex;background:var(--chip);border-radius:9px;padding:3px;max-width:100%;overflow-x:auto;scrollbar-width:none}
.seg::-webkit-scrollbar{display:none}
.seg button{border:0;background:none;padding:5px 12px;border-radius:7px;cursor:pointer;white-space:nowrap}
.seg button.on{background:var(--panel);box-shadow:0 1px 2px rgba(0,0,0,.12);font-weight:600}
.chip{border:1px solid var(--line);background:var(--panel);padding:3px 11px;border-radius:99px;
  cursor:pointer;font-size:13.5px;white-space:nowrap}
.chip.on{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.ctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:16px 0 6px}
input[type=search],input[type=time],select{background:var(--panel);border:1px solid var(--line);
  border-radius:9px;padding:7px 11px;outline:none}
input[type=search]{flex:1;min-width:0}
input:focus,select:focus{border-color:var(--accent)}
.hint{font-size:13px;color:var(--mute);margin:4px 0 10px}

.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--c);flex:none}
.sta{display:inline-flex;align-items:center;gap:3px;white-space:nowrap}

/* 找電影 */
.film{background:var(--panel);border:1px solid var(--line);border-radius:12px;margin:8px 0;overflow:hidden}
.film>button{display:flex;width:100%;text-align:left;border:0;background:none;padding:12px 14px;
  cursor:pointer;gap:10px;align-items:center}
.ft{flex:1;min-width:0}
.ft b{font-size:16px;font-weight:600}
.meta{font-size:12.5px;color:var(--mute)}
.cnt{font-size:12.5px;color:var(--mute);white-space:nowrap}
.chev{color:var(--faint);transition:transform .15s}
.film.open .chev{transform:rotate(90deg)}
.body{border-top:1px solid var(--line);padding:4px 14px 10px}
.th{display:flex;gap:12px;padding:10px 0;border-bottom:1px dashed var(--line)}
.th:last-child{border-bottom:0}
.thn{width:180px;flex:none;font-size:14px}
.thn small{display:flex;color:var(--mute);font-size:12px;margin-top:1px}
.times{display:flex;flex-wrap:wrap;gap:6px;flex:1}
.tm{border:1px solid var(--line);border-radius:7px;padding:2px 8px;font-variant-numeric:tabular-nums;
  line-height:1.3;min-width:58px;text-align:center}
.tm small{display:block;font-size:10.5px;color:var(--mute)}
.tm.past{opacity:.35}
.tm.fav{border-color:var(--accent);background:var(--accent-soft)}
.star{border:0;background:none;cursor:pointer;padding:0 3px 0 0;color:var(--faint);font-size:14px}
.star.on{color:var(--star)}

/* 看時段 */
.hr{font-size:12px;color:var(--mute);margin:18px 0 4px;letter-spacing:.06em}
.slot{display:grid;grid-template-columns:56px 1fr auto;gap:4px 12px;align-items:baseline;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 12px;margin:5px 0}
.slot .t{font-size:17px;font-weight:600;font-variant-numeric:tabular-nums}
.slot .name{border:0;background:none;padding:0;text-align:left;cursor:pointer;font-weight:600;font-size:15px}
.slot .name:hover{color:var(--accent)}
.slot .where{font-size:13px;color:var(--mute);display:flex;align-items:center;flex-wrap:wrap;gap:0 6px}
.slot .end{font-size:12px;color:var(--mute);white-space:nowrap;font-variant-numeric:tabular-nums}
.slot.isfav{border-color:var(--accent)}
.ver{font-size:11px;border:1px solid var(--line);border-radius:4px;padding:0 4px;margin-left:4px;
  color:var(--mute);font-weight:400;white-space:nowrap}
.empty{text-align:center;color:var(--mute);padding:48px 0}

/* 選擇影院 */
.sheet{position:fixed;inset:0;z-index:20;display:flex;align-items:flex-start;justify-content:center;padding:6vh 16px}
.sheet[hidden]{display:none}
.sheet-bg{position:absolute;inset:0;background:var(--shade)}
.card{position:relative;background:var(--bg);border-radius:14px;width:100%;max-width:560px;max-height:88vh;
  display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,.25);overflow:hidden}
.card-hd{display:flex;align-items:center;gap:10px;padding:14px 16px 10px;border-bottom:1px solid var(--line)}
.card-hd b{font-size:16px}
.card-hd .n{color:var(--mute);font-size:13px;flex:1}
.done{border:0;background:var(--accent);color:var(--accent-ink);border-radius:8px;padding:6px 14px;cursor:pointer;font-weight:600}
.presets{display:flex;gap:6px;padding:10px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.card-body{overflow-y:auto;padding:4px 16px 16px;-webkit-overflow-scrolling:touch}
.grp-hd{display:flex;align-items:center;gap:8px;margin:16px 0 6px;font-weight:600;font-size:14px}
.grp-hd .dot{width:11px;height:11px}
.grp-hd button{margin-left:auto;border:0;background:none;color:var(--accent);cursor:pointer;font-size:13px;padding:2px 0}
.trow{display:flex;align-items:center;gap:10px;background:var(--panel);border:1px solid var(--line);
  border-radius:9px;padding:8px 10px;margin:4px 0;cursor:pointer;user-select:none}
.trow.on{border-color:var(--accent)}
.box{width:18px;height:18px;border-radius:5px;border:1.5px solid var(--faint);flex:none;display:grid;place-items:center;
  font-size:12px;line-height:1;color:transparent}
.trow.on .box{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.trow .tn{flex:1;min-width:0;font-size:14px}
.trow .sta{font-size:12px;color:var(--mute)}
.mini{border:1px solid var(--line);background:var(--panel);border-radius:6px;padding:1px 7px;font-size:12px;
  color:var(--mute);cursor:pointer;white-space:nowrap}
.mini:hover{color:var(--ink)}
.mini.warn{color:#fff;background:var(--accent);border-color:var(--accent)}
.lrow{display:flex;align-items:center;gap:6px;background:var(--panel);border:1px solid var(--line);
  border-radius:9px;padding:7px 10px;margin:4px 0}
.lrow.on{border-color:var(--accent)}
.lrow .lname{flex:1;min-width:0;border:0;background:none;text-align:left;cursor:pointer;padding:0;font-size:14px}
.lrow .lname small{color:var(--mute);margin-left:6px}
.newlist{display:flex;gap:6px;margin:6px 0 2px}
.newlist input{flex:1;min-width:0;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 10px;outline:none}
.newlist input:focus{border-color:var(--accent)}
.newlist button{border:0;background:var(--ink);color:var(--bg);border-radius:8px;padding:6px 12px;cursor:pointer;white-space:nowrap}
.newlist button:disabled{opacity:.35;cursor:default}
.note{font-size:12px;color:var(--mute);margin:2px 0}
.fold{display:flex;align-items:center;gap:8px;width:100%;border:0;background:none;padding:0;margin:20px 0 6px;
  font-weight:600;font-size:14px;color:var(--mute);cursor:pointer;text-align:left}
.trow.hid{opacity:.8}

@media (max-width:560px){
  .th{flex-direction:column;gap:6px}.thn{width:auto}
  .slot{grid-template-columns:48px 1fr}.slot .end{grid-column:2}
  .sheet{padding:0;align-items:flex-end}
  .card{max-width:none;max-height:90vh;border-radius:14px 14px 0 0}
}
</style>
</head>
<body>
<header><div class="wrap">
  <div class="top"><h1>台北電影時刻</h1><span class="stamp" id="stamp"></span></div>
  <div class="row"><div class="seg" id="dates"></div></div>
  <div class="row">
    <div class="seg" id="modes">
      <button data-m="film">找電影</button><button data-m="time">看時段</button>
    </div>
  </div>
  <div class="row" id="scope"></div>
</div></header>
<main class="wrap" id="main"></main>

<div class="sheet" id="sheet" hidden>
  <div class="sheet-bg" data-close="1"></div>
  <div class="card" role="dialog" aria-label="選擇影院">
    <div class="card-hd"><b>選擇影院</b><span class="n" id="selN"></span><button class="done" data-close="1">完成</button></div>
    <div class="presets">
      <button class="chip" data-preset="fav">★ 只選常去</button>
      <button class="chip" data-preset="all">全選</button>
      <button class="chip" data-preset="none">全部取消</button>
    </div>
    <div class="card-body" id="sheetBody"></div>
  </div>
</div>

<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const store = {
  get(k, d){ try{ const v = localStorage.getItem("tpm_"+k); return v==null ? d : JSON.parse(v); }catch(e){ return d; } },
  set(k, v){ try{ localStorage.setItem("tpm_"+k, JSON.stringify(v)); }catch(e){} }
};
const T = Object.fromEntries(D.theaters.map(t => [t.id, t]));
const ALL = D.theaters.map(t => t.id);
const LINE = Object.fromEntries(D.lines.map(l => [l.id, l]));
const LORD = Object.fromEntries(D.lines.map((l, i) => [l.id, i]));
const toMin = s => { let [h,m] = s.split(":").map(Number); if (h < 5) h += 24; return h*60+m; };
const fmt = m => { m = ((m % 1440) + 1440) % 1440; return String(Math.floor(m/60)).padStart(2,"0")+":"+String(m%60).padStart(2,"0"); };
const SHOWS = D.shows.map(([date,tid,fid,time,ver]) => ({date,tid,fid,time,ver,min:toMin(time)})).filter(s => T[s.tid] && D.films[s.fid]);

const now = new Date();
const todayStr = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
const nowMin = now.getHours()*60 + now.getMinutes() + (now.getHours() < 5 ? 1440 : 0);
const DATES = D.dates.filter(d => d >= todayStr).length ? D.dates.filter(d => d >= todayStr) : D.dates;

let favs = new Set(store.get("favs", D.theaters.filter(t => t.fav).map(t => t.id)).filter(id => T[id]));
let sel = new Set(store.get("sel", [...favs]).filter(id => T[id]));
let hidden = new Set(store.get("hidden", []).filter(id => T[id]));     // 暫時收起的影院
let lists = store.get("lists", []).map(l => ({...l, ids: l.ids.filter(id => T[id])}));  // 自訂清單
const S = { date: DATES[0], mode: store.get("mode", "film"), q: "", open: null, from: null, span: store.get("span", 3),
            draft: "", showHidden: false, confirmDel: null };
const VISIBLE = () => ALL.filter(id => !hidden.has(id));
const saveLists = () => store.set("lists", lists);

const isFav = tid => favs.has(tid);
const inSel = tid => sel.has(tid);
const isPast = s => s.date === todayStr && s.min < nowMin;
const endOf = s => { const r = D.films[s.fid].runtime; return r ? fmt(s.min + r) : ""; };
const mainLine = tid => T[tid].lines[0] || "X";
const thRank = (a, b) => (isFav(b) - isFav(a)) || (LORD[mainLine(a)] - LORD[mainLine(b)])
  || T[a].station.localeCompare(T[b].station, "zh-Hant") || T[a].name.localeCompare(T[b].name, "zh-Hant");
const dots = tid => T[tid].lines.map(l => `<i class="dot" style="--c:${LINE[l]?.color || "#888"}" title="${esc(LINE[l]?.name)}"></i>`).join("");
const staTag = tid => `<span class="sta">${dots(tid)}${esc(T[tid].station || "—")}</span>`;
const verTag = v => v ? `<span class="ver">${esc(v)}</span>` : "";
const starBtn = tid => `<button class="star ${isFav(tid)?"on":""}" data-star="${tid}" title="${isFav(tid)?"取消常去":"設為常去"}">${isFav(tid)?"★":"☆"}</button>`;
const sameSet = (a, b) => a.size === b.size && [...a].every(x => b.has(x));
const saveSel = () => store.set("sel", [...sel]);

function dateLabel(d){
  const dt = new Date(d+"T00:00:00");
  const wk = "日一二三四五六"[dt.getDay()];
  const diff = Math.round((dt - new Date(todayStr+"T00:00:00"))/864e5);
  const rel = diff === 0 ? " 今天" : diff === 1 ? " 明天" : "";
  return `${dt.getMonth()+1}/${dt.getDate()}（${wk}）${rel}`;
}

function renderHeader(){
  $("#stamp").textContent = "資料更新：" + D.generated.slice(5).replace("-", "/");
  $("#dates").innerHTML = DATES.map(d => `<button data-d="${d}" class="${d===S.date?"on":""}">${dateLabel(d)}</button>`).join("");
  document.querySelectorAll("#modes button").forEach(b => b.classList.toggle("on", b.dataset.m === S.mode));
  const isF = favs.size && sameSet(sel, favs), isA = sameSet(sel, new Set(VISIBLE()));
  const onList = lists.findIndex(l => sameSet(sel, new Set(l.ids)));
  const custom = !isF && !isA && onList < 0;
  $("#scope").innerHTML =
    `<button class="chip ${isF?"on":""}" data-preset="fav">★ 常去</button>` +
    `<button class="chip ${isA&&!isF?"on":""}" data-preset="all">全部影院</button>` +
    lists.map((l, i) => `<button class="chip ${i===onList&&!isF&&!isA?"on":""}" data-apply="${i}">${esc(l.name)}</button>`).join("") +
    `<button class="chip ${custom?"on":""}" data-sheet="1">選擇影院${custom?`（${sel.size}）`:""} ▾</button>`;
}

function emptyMsg(){
  if (!sel.size) return `還沒有選任何影院，點上方「選擇影院」挑幾間`;
  return sel.size < ALL.length ? `這個範圍沒有符合的場次，試試「全部影院」` : `沒有符合的場次`;
}

/* ── 找電影 ── */
function renderFilmShell(){
  $("#main").innerHTML = `<div class="ctl"><input type="search" id="q" placeholder="搜尋片名…" autocomplete="off" enterkeyhint="search"></div><div id="list"></div>`;
  const q = $("#q");
  q.value = S.q;
  let composing = false;   // 注音／倉頡等輸入法選字中，不要重畫
  q.addEventListener("compositionstart", () => { composing = true; });
  q.addEventListener("compositionend", () => { composing = false; S.q = q.value; renderFilmList(); });
  q.addEventListener("input", e => { if (composing || e.isComposing) return; S.q = q.value; renderFilmList(); });
}

function renderFilmList(){
  const day = SHOWS.filter(s => s.date === S.date && inSel(s.tid));
  const by = new Map();
  for (const s of day){ if (!by.has(s.fid)) by.set(s.fid, []); by.get(s.fid).push(s); }
  const q = S.q.trim().toLowerCase();
  const films = [...by.entries()]
    .filter(([fid]) => !q || D.films[fid].title.toLowerCase().includes(q))
    .map(([fid, ss]) => ({fid, ss, th: new Set(ss.map(s => s.tid)), up: ss.filter(s => !isPast(s)).length}))
    .sort((a,b) => (b.up>0)-(a.up>0) || b.th.size - a.th.size || b.ss.length - a.ss.length);

  let h = `<div class="hint">${films.length} 部電影 · ${sel.size} 間影院 · 點片名展開場次</div>`;
  if (!films.length) h += `<div class="empty">${emptyMsg()}</div>`;
  for (const f of films){
    const info = D.films[f.fid], open = S.open === f.fid;
    const meta = [info.runtime ? `${info.runtime} 分` : "", info.rating].filter(Boolean).join(" · ");
    h += `<div class="film ${open?"open":""}" id="f-${f.fid}">
      <button data-open="${f.fid}"><div class="ft"><b>${esc(info.title)}</b><div class="meta">${esc(meta)}</div></div>
      <span class="cnt">${f.th.size} 間 · ${f.ss.length} 場</span><span class="chev">›</span></button>`;
    if (open){
      const byT = new Map();
      for (const s of f.ss){ if (!byT.has(s.tid)) byT.set(s.tid, []); byT.get(s.tid).push(s); }
      const rows = [...byT.entries()].sort((a,b) => thRank(a[0], b[0]));
      h += `<div class="body">` + rows.map(([tid, ss]) => {
        ss.sort((a,b) => a.min - b.min);
        return `<div class="th"><div class="thn">${starBtn(tid)}${esc(T[tid].name)}<small>${staTag(tid)}</small></div>
          <div class="times">${ss.map(s => `<span class="tm ${isPast(s)?"past":""} ${isFav(tid)?"fav":""}">${s.time}${s.ver?`<small>${esc(s.ver)}</small>`:""}${endOf(s)?`<small>→${endOf(s)}</small>`:""}</span>`).join("")}</div></div>`;
      }).join("") + `</div>`;
    }
    h += `</div>`;
  }
  $("#list").innerHTML = h;
}

/* ── 看時段 ── */
function renderTime(){
  if (S.from == null) S.from = S.date === todayStr ? Math.floor(nowMin/5)*5 : 18*60;
  const to = S.span === 0 ? 99999 : S.from + S.span*60;
  const list = SHOWS.filter(s => s.date === S.date && inSel(s.tid) && s.min >= S.from && s.min <= to)
    .sort((a,b) => a.min - b.min || thRank(a.tid, b.tid));
  const spans = [[1,"1 小時內"],[2,"2 小時內"],[3,"3 小時內"],[5,"5 小時內"],[0,"之後全部"]];
  let h = `<div class="ctl">從 <input type="time" id="from" value="${fmt(S.from)}" step="300">
    <select id="span">${spans.map(([v,l]) => `<option value="${v}" ${v===S.span?"selected":""}>${l}</option>`).join("")}</select>
    ${S.date===todayStr?`<button class="chip" id="now">現在</button>`:""}</div>
    <div class="hint">${list.length} 場 · ${sel.size} 間影院 · 點片名看這部片的所有場次</div>`;
  if (!list.length) h += `<div class="empty">${sel.size ? "這段時間沒有場次，試試拉長時間或「全部影院」" : emptyMsg()}</div>`;
  let lastH = null;
  for (const s of list){
    const hr = Math.floor(s.min/60);
    if (hr !== lastH){ lastH = hr; h += `<div class="hr">${hr>=24?"深夜 ":""}${String(hr%24).padStart(2,"0")} 點</div>`; }
    h += `<div class="slot ${isFav(s.tid)?"isfav":""}">
      <span class="t">${s.time}</span>
      <div><button class="name" data-goto="${s.fid}">${esc(D.films[s.fid].title)}</button>${verTag(s.ver)}
        <div class="where"><span>${starBtn(s.tid)}${esc(T[s.tid].name)}</span>${staTag(s.tid)}</div></div>
      <span class="end">${endOf(s) ? "→ "+endOf(s) : ""}</span></div>`;
  }
  $("#main").innerHTML = h;
  $("#from").onchange = e => { if (e.target.value){ const [hh,mm] = e.target.value.split(":").map(Number); S.from = (hh<5?hh+24:hh)*60+mm; render(); } };
  $("#span").onchange = e => { S.span = +e.target.value; store.set("span", S.span); render(); };
  const nb = $("#now"); if (nb) nb.onclick = () => { S.from = Math.floor(nowMin/5)*5; render(); };
}

/* ── 選擇影院面板 ── */
const tRow = id => `<div class="trow ${inSel(id)?"on":""} ${hidden.has(id)?"hid":""}" data-tog="${id}">
  <span class="box">✓</span><span class="tn">${esc(T[id].name)}</span>${staTag(id)}${starBtn(id)}
  <button class="mini" data-hide="${id}">${hidden.has(id) ? "恢復" : "收起"}</button></div>`;

function renderSheet(){
  const body = $("#sheetBody"), top = body.scrollTop;
  $("#selN").textContent = `已選 ${sel.size} 間`;
  let h = `<div class="grp-hd">我的清單</div>`;
  if (!lists.length) h += `<div class="note">先在下面勾好影院，再取個名字存起來，之後一鍵切換。</div>`;
  h += lists.map((l, i) => {
    const on = sameSet(sel, new Set(l.ids)), del = S.confirmDel === i;
    return `<div class="lrow ${on?"on":""}">
      <button class="lname" data-apply="${i}">${esc(l.name)}<small>${l.ids.length} 間</small></button>
      ${on ? "" : `<button class="mini" data-update="${i}" title="改成目前勾選的影院">用目前勾選更新</button>`}
      <button class="mini ${del?"warn":""}" data-del="${i}">${del ? "確定刪除？" : "刪除"}</button></div>`;
  }).join("");
  h += `<div class="newlist"><input id="draft" placeholder="新清單名稱，例如：下班順路" maxlength="16" value="${esc(S.draft)}">
    <button id="saveList" ${sel.size ? "" : "disabled"}>存成清單（${sel.size} 間）</button></div>`;
  for (const L of D.lines){
    const ids = VISIBLE().filter(id => mainLine(id) === L.id)
      .sort((a,b) => T[a].station.localeCompare(T[b].station, "zh-Hant") || T[a].name.localeCompare(T[b].name, "zh-Hant"));
    if (!ids.length) continue;
    const allOn = ids.every(inSel);
    h += `<section><div class="grp-hd"><i class="dot" style="--c:${L.color}"></i>${esc(L.name)}
      <button data-line="${L.id}">${allOn ? "取消這條線" : "整條線全選"}</button></div>`;
    h += ids.map(tRow).join("") + `</section>`;
  }
  if (hidden.size){
    const ids = [...hidden].sort(thRank);
    h += `<button class="fold" data-fold="1">${S.showHidden ? "▾" : "▸"} 已收起的影院（${ids.length}）</button>`;
    if (S.showHidden) h += `<div class="note">收起的影院不會出現在「全部影院」，但仍可以勾選或加入清單。</div>` + ids.map(tRow).join("");
  }
  body.innerHTML = h;
  body.scrollTop = top;
  const dr = $("#draft");
  dr.oninput = () => { S.draft = dr.value; };           // 只記錄，不重畫，注音選字不會被打斷
  dr.onkeydown = e => { if (e.key === "Enter" && !e.isComposing) saveList(); };
  $("#saveList").onclick = saveList;
}
function saveList(){
  const name = S.draft.trim();
  if (!sel.size) return;
  if (!name){ $("#draft").focus(); return; }
  const i = lists.findIndex(l => l.name === name);
  if (i >= 0) lists[i].ids = [...sel]; else lists.push({name, ids: [...sel]});
  S.draft = ""; saveLists(); render();
}
function openSheet(){ S.confirmDel = null; $("#sheet").hidden = false; document.body.classList.add("lock"); renderSheet(); $("#sheetBody").scrollTop = 0; }
function closeSheet(){ $("#sheet").hidden = true; document.body.classList.remove("lock"); render(); }

function render(){
  renderHeader();
  if (S.mode === "time") renderTime();
  else { if (!$("#q")) renderFilmShell(); renderFilmList(); }
  if (!$("#sheet").hidden) renderSheet();
}

document.addEventListener("click", e => {
  const b = e.target.closest("button,[data-tog],[data-close]"); if (!b) return;
  const d = b.dataset;
  if (d.del === undefined) S.confirmDel = null;
  if (d.star){
    e.stopPropagation();
    favs.has(d.star) ? favs.delete(d.star) : favs.add(d.star);
    store.set("favs", [...favs]); render(); return;
  }
  if (d.hide){
    if (hidden.has(d.hide)) hidden.delete(d.hide);
    else { hidden.add(d.hide); sel.delete(d.hide); saveSel(); }
    store.set("hidden", [...hidden]); render(); return;
  }
  if (d.fold){ S.showHidden = !S.showHidden; renderSheet(); return; }
  if (d.apply !== undefined){ sel = new Set(lists[+d.apply].ids); saveSel(); render(); return; }
  if (d.update !== undefined){ lists[+d.update].ids = [...sel]; saveLists(); render(); return; }
  if (d.del !== undefined){
    const i = +d.del;
    if (S.confirmDel === i){ lists.splice(i, 1); S.confirmDel = null; saveLists(); }
    else S.confirmDel = i;
    render(); return;
  }
  if (d.close){ closeSheet(); return; }
  if (d.sheet){ openSheet(); return; }
  if (d.tog){ sel.has(d.tog) ? sel.delete(d.tog) : sel.add(d.tog); saveSel(); render(); return; }
  if (d.line){
    const ids = ALL.filter(id => mainLine(id) === d.line), on = ids.every(inSel);
    ids.forEach(id => on ? sel.delete(id) : sel.add(id)); saveSel(); render(); return;
  }
  if (d.preset){
    sel = new Set(d.preset === "fav" ? favs : d.preset === "all" ? VISIBLE() : []);
    saveSel(); render(); return;
  }
  if (d.d){ S.date = d.d; S.from = null; render(); return; }
  if (d.m){ S.mode = d.m; store.set("mode", S.mode); $("#main").innerHTML = ""; render(); return; }
  if (d.open){ S.open = S.open === d.open ? null : d.open; renderFilmList(); return; }
  if (d.goto){
    S.mode = "film"; S.open = d.goto; S.q = ""; store.set("mode","film");
    $("#main").innerHTML = ""; render();
    const el = document.getElementById("f-"+S.open); if (el) el.scrollIntoView({block:"center"});
  }
});
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#sheet").hidden) closeSheet(); });
render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
