#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_openai_news.py

分阶段工作流：
  1. 抓取全量文章：  python scrape_openai_news.py fetch
  2. 按年份分批处理：python scrape_openai_news.py phase 1   # 2015-2019
                     python scrape_openai_news.py phase 2   # 2020-2021
                     python scrape_openai_news.py phase 3   # 2022-2023
                     python scrape_openai_news.py phase 4   # 2024-至今
  3. 合并生成 JSON：  python scrape_openai_news.py merge

环境变量：
  SILICONFLOW_API_KEY    （phase 子命令需要）
"""

import json
import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
RAW_PATH     = os.path.join(SCRIPT_DIR, "raw_articles.json")
OUTPUT_PATH  = os.path.join(PROJECT_ROOT, "contrib", "examples", "new_openai_data.json")
ORIGINAL_PATH = os.path.join(PROJECT_ROOT, "contrib", "examples", "openai_data.json")

def phase_path(n):  # type: (int) -> str
    return os.path.join(SCRIPT_DIR, "phase{}.json".format(n))

# Year ranges for each phase
PHASE_RANGES = {
    1: (2015, 2019),
    2: (2020, 2021),
    3: (2022, 2023),
    4: (2024, 9999),
}

# ─── Config ───────────────────────────────────────────────────────────────────

BASE_URL     = "https://openai.com"
NEWS_URL     = "https://openai.com/news/?sortBy=old"
REQUEST_DELAY = 1.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─── Classification rules (checked in order; first match wins) ────────────────

CATEGORY_RULES = [
    ("融资", [
        "funding", "investment", "invest", "raises", "valuation",
        "billion", "million", "capital", "financing", "secured",
        "series ", "round ",
    ]),
    ("合作", [
        "partnership", "partner with", "agreement with", "collaboration with",
        "teams with", "works with", "integrat",
        "microsoft", "apple", "softbank", "oracle", "salesforce",
        "nvidia", "amazon", "google", "lg ", "arm ",
    ]),
    ("研究", [
        "research", "technical report", "arxiv", "we trained",
        "breakthrough", "scaling", "alignment", "superalignment",
        "interpretability", "emergent", "multimodal", "reinforcement learning",
        "preparedness",
    ]),
    ("政策", [
        "safety", "governance", "policy", "regulation", "congressional",
        "government", "legislation", "senate", "congress",
        "democratic inputs", "executive order", "ai act", "board of",
    ]),
    ("产品", [
        "introducing", "launch", "new model", "available", "releasing",
        "release", "chatgpt", "gpt-", "dall-e", "dall\u00b7e",
        "whisper", "sora", "codex", "operator", "o1 ", "o3 ", "o4 ",
        "jukebox", "point-e", "universe", "gym", "plugin", "store",
        "assistants api", "voice mode", "canvas", "deep research",
        "tasks", "projects", "memory", "custom instructions",
        "api ", "access to", "we\u2019re releasing", "we are releasing",
    ]),
]

LINK_HTML = (
    '<a href="{url}" target="_blank" '
    'style="display:block;margin-top:6px;color:#10a37f">'
    '\u2192 OpenAI \u5b98\u65b9\u62a5\u9053</a>'
)  # → OpenAI 官方报道

# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_json(path):  # type: (str) -> object
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):  # type: (str, object) -> None
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def year_of(date_str):  # type: (Optional[str]) -> Optional[int]
    """Extract year from a date string like '2023-01-15' or 'January 15, 2023'."""
    if not date_str:
        return None
    m = re.search(r"\b(20\d{2}|201[0-9]|200[0-9])\b", date_str)
    return int(m.group(1)) if m else None

# ─── Step 1: fetch ────────────────────────────────────────────────────────────

def _next_data_articles(html):  # type: (str) -> Optional[List[Dict]]
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.DOTALL
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None

    page_props = data.get("props", {}).get("pageProps", {})

    for key in ("posts", "articles", "news", "items", "results", "entries", "data"):
        candidate = page_props.get(key)
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], dict):
            return candidate

    for val in page_props.values():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            if any(k in val[0] for k in ("title", "headline", "slug", "publishedAt")):
                return val

    return None


def _normalize(raw):  # type: (Dict) -> Dict
    title = raw.get("title") or raw.get("headline") or raw.get("name") or ""
    slug  = raw.get("slug") or ""
    url   = raw.get("url") or raw.get("href") or raw.get("link") or ""
    if not url and slug:
        url = "{}/index/{}/".format(BASE_URL, slug)
    elif url and url.startswith("/"):
        url = BASE_URL + url
    date_str = (
        raw.get("publishedAt") or raw.get("published_at") or
        raw.get("date") or raw.get("createdAt") or ""
    )
    return {"title": title, "url": url, "date_str": date_str}


def _html_articles(html):  # type: (str) -> List[Dict]
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    candidates = soup.find_all("article") or soup.find_all(
        lambda tag: tag.name in ("div", "section", "li") and
        any("article" in str(tag.get(a, "")).lower() or
            "post" in str(tag.get(a, "")).lower() or
            "card" in str(tag.get(a, "")).lower()
            for a in ("class", "data-component", "data-testid"))
    )
    for el in candidates:
        title_el = el.find(["h1", "h2", "h3", "h4"])
        title = title_el.get_text(strip=True) if title_el else ""
        link_el = el.find("a", href=True)
        href = ""
        if link_el:
            href = link_el["href"]
            if href.startswith("/"):
                href = BASE_URL + href
        date_el = el.find("time")
        date_str = ""
        if date_el:
            date_str = date_el.get("datetime") or date_el.get_text(strip=True)
        if title:
            articles.append({"title": title, "url": href, "date_str": date_str})
    return articles


def _next_page(html, current_url):  # type: (str, str) -> Optional[str]
    soup = BeautifulSoup(html, "html.parser")
    el = (
        soup.find("a", string=re.compile(r"next|more|›|→", re.I)) or
        soup.find("a", attrs={"aria-label": re.compile(r"next|more", re.I)})
    )
    if el and el.get("href"):
        href = el["href"]
        return BASE_URL + href if href.startswith("/") else href
    if "page=" in current_url:
        m = re.search(r"page=(\d+)", current_url)
        if m:
            return re.sub(r"page=\d+", "page={}".format(int(m.group(1)) + 1), current_url)
    return None


def cmd_fetch():
    """US-001: Fetch all articles and save to raw_articles.json."""
    print("=" * 60)
    print("fetch: 抓取 openai.com/news 全量文章")
    print("=" * 60)

    if os.path.exists(RAW_PATH):
        print("  文件已存在，将覆盖: {}".format(RAW_PATH))

    all_articles = []
    seen = set()
    url = NEWS_URL
    page = 1

    while url:
        print("  Page {}: {}".format(page, url))
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print("  ERROR: {}".format(e), file=sys.stderr)
            break

        html = resp.text
        raw_list = _next_data_articles(html)
        if raw_list:
            page_articles = [_normalize(r) for r in raw_list]
            print("  Found {} via __NEXT_DATA__".format(len(page_articles)))
        else:
            page_articles = _html_articles(html)
            if page_articles:
                print("  Found {} via HTML parsing".format(len(page_articles)))
            else:
                print("  WARNING: No articles found on page {}.".format(page))
                print("  The page may require JavaScript rendering (playwright).")
                break

        new = 0
        for art in page_articles:
            key = art.get("url") or art.get("title", "")
            if key and key not in seen:
                seen.add(key)
                all_articles.append(art)
                new += 1

        print("  +{} new (total {})".format(new, len(all_articles)))
        if new == 0:
            break

        next_url = _next_page(html, url)
        if not next_url or next_url == url:
            break
        url = next_url
        page += 1
        time.sleep(REQUEST_DELAY)

    save_json(RAW_PATH, all_articles)
    print("\n已抓取 {} 篇文章，保存至 raw_articles.json".format(len(all_articles)))

# ─── Step 2: classify & translate (per phase) ────────────────────────────────

def classify_article(title, url):  # type: (str, str) -> Optional[str]
    text = (title + " " + url).lower()
    for category, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in text:
                return category
    return None


TRANSLATE_SYSTEM = (
    "你是一个专业的AI领域翻译助手。\n"
    "根据给定的OpenAI新闻英文标题、URL和分类，输出：\n"
    "1. 简洁准确的中文标题\n"
    "2. 一句话中文摘要（不超过80字，概述该事件的意义）\n\n"
    "严格输出JSON格式，不含任何其他文字：\n"
    "{\"headline\": \"中文标题\", \"summary\": \"一句话中文摘要\"}"
)


SILICONFLOW_URL   = "https://api.siliconflow.cn/v1/chat/completions"
SILICONFLOW_MODEL = "deepseek-ai/DeepSeek-V3"


def _call_openai(api_key, messages):  # type: (str, List[Dict]) -> str
    resp = requests.post(
        SILICONFLOW_URL,
        headers={
            "Authorization": "Bearer {}".format(api_key),
            "Content-Type": "application/json",
        },
        json={"model": SILICONFLOW_MODEL, "temperature": 0, "messages": messages},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def cmd_phase(phase_num):  # type: (int) -> None
    """US-002~005: Classify + translate articles for a given year range."""
    if phase_num not in PHASE_RANGES:
        print("ERROR: phase must be 1-4", file=sys.stderr)
        sys.exit(1)

    year_from, year_to = PHASE_RANGES[phase_num]
    label = "{}-{}".format(year_from, year_to if year_to < 9999 else "至今")

    print("=" * 60)
    print("phase {}: {} 年的文章".format(phase_num, label))
    print("=" * 60)

    api_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        print("ERROR: SILICONFLOW_API_KEY 未设置", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(RAW_PATH):
        print("ERROR: raw_articles.json 不存在，请先运行 fetch", file=sys.stderr)
        sys.exit(1)

    all_articles = load_json(RAW_PATH)

    # Filter by year range
    in_range = []
    for art in all_articles:
        y = year_of(art.get("date_str", ""))
        if y and year_from <= y <= year_to:
            in_range.append(art)

    print("  年份范围内共 {} 篇文章".format(len(in_range)))

    # Classify
    classified = []
    skipped = 0
    for art in in_range:
        cat = classify_article(art.get("title", ""), art.get("url", ""))
        if cat:
            art["category"] = cat
            classified.append(art)
            print("  [{}] {}".format(cat, art.get("title", "")))
        else:
            skipped += 1

    print("\n  入选 {}，跳过 {}".format(len(classified), skipped))

    # Translate
    print("\n  翻译中（共 {} 条）...".format(len(classified)))
    total = len(classified)
    for i, art in enumerate(classified, 1):
        prompt = "英文标题: {}\nURL: {}\n分类: {}\n\n请翻译并生成摘要。".format(
            art.get("title", ""), art.get("url", ""), art.get("category", "")
        )
        try:
            raw = _call_openai(api_key, [
                {"role": "system", "content": TRANSLATE_SYSTEM},
                {"role": "user", "content": prompt},
            ])
            raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
            result = json.loads(raw)
            art["headline_zh"] = result.get("headline", art.get("title", ""))
            art["summary_zh"]  = result.get("summary", "")
        except Exception as e:
            print("  WARNING: 翻译失败 '{}': {}".format(art.get("title", ""), e))
            art["headline_zh"] = art.get("title", "")
            art["summary_zh"]  = ""
        print("  [{}/{}] {}".format(i, total, art["headline_zh"]))
        time.sleep(0.3)

    out = phase_path(phase_num)
    save_json(out, classified)
    print("\nPhase {} 完成：入选 {} 条，跳过 {} 条".format(phase_num, len(classified), skipped))

# ─── Step 3: merge ────────────────────────────────────────────────────────────

def _parse_date(date_str):  # type: (Optional[str]) -> Optional[Dict]
    if not date_str:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
    if m:
        result = {"year": m.group(1), "month": m.group(2)}
        if m.group(3) != "01":
            result["day"] = m.group(3)
        return result
    month_map = {
        "january":"01","february":"02","march":"03","april":"04",
        "may":"05","june":"06","july":"07","august":"08",
        "september":"09","october":"10","november":"11","december":"12",
        "jan":"01","feb":"02","mar":"03","apr":"04",
        "jun":"06","jul":"07","aug":"08",
        "sep":"09","oct":"10","nov":"11","dec":"12",
    }
    m2 = re.match(r"([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})", date_str)
    if m2:
        mon = month_map.get(m2.group(1).lower())
        if mon:
            return {"year": m2.group(3), "month": mon, "day": m2.group(2).zfill(2)}
    return None


def _sort_key(event):  # type: (Dict) -> Tuple[int, int, int]
    sd = event.get("start_date", {})
    return (int(sd.get("year", 0)), int(sd.get("month", 0)), int(sd.get("day", 0)))


def cmd_merge():
    """US-006: Merge phase1-4.json into new_openai_data.json."""
    print("=" * 60)
    print("merge: 合并所有阶段数据")
    print("=" * 60)

    assert os.path.abspath(OUTPUT_PATH) != os.path.abspath(ORIGINAL_PATH)

    events = []
    for n in (1, 2, 3, 4):
        p = phase_path(n)
        if not os.path.exists(p):
            print("  WARNING: {} 不存在，跳过".format(p))
            continue
        articles = load_json(p)
        print("  读取 phase{}.json：{} 条".format(n, len(articles)))
        for art in articles:
            start_date = _parse_date(art.get("date_str", ""))
            if not start_date:
                print("  SKIP (no date): {}".format(art.get("title", "")))
                continue
            url      = art.get("url", "")
            headline = art.get("headline_zh") or art.get("title", "")
            summary  = art.get("summary_zh", "")
            category = art.get("category", "产品")
            link     = LINK_HTML.format(url=url) if url else ""
            body     = "{}{}".format(summary, link) if summary else link
            events.append({
                "start_date": start_date,
                "text": {"headline": headline, "text": body},
                "group": category,
            })

    events.sort(key=_sort_key)

    output = {
        "title": {
            "text": {
                "headline": "OpenAI \u53d1\u5c55\u5386\u7a0b",
                "text": "\u4ea7\u54c1\u53d1\u5e03 \u00b7 \u878d\u8d44\u8f6e\u6b21 \u00b7 \u5546\u4e1a\u5408\u4f5c",
            }
        },
        "events": events,
    }

    if os.path.exists(OUTPUT_PATH):
        print("  文件已存在，将覆盖: {}".format(OUTPUT_PATH))

    save_json(OUTPUT_PATH, output)
    print("\n已写入 {} 条事件到 new_openai_data.json".format(len(events)))

# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print("用法:")
        print("  python scrape_openai_news.py fetch          # 抓取全量文章缓存")
        print("  python scrape_openai_news.py phase <1-4>    # 分阶段分类+翻译")
        print("  python scrape_openai_news.py merge          # 合并生成最终 JSON")
        sys.exit(0)

    cmd = args[0]

    if cmd == "fetch":
        cmd_fetch()
    elif cmd == "phase":
        if len(args) < 2 or not args[1].isdigit():
            print("ERROR: 请指定阶段编号，例如：phase 1", file=sys.stderr)
            sys.exit(1)
        cmd_phase(int(args[1]))
    elif cmd == "merge":
        cmd_merge()
    else:
        print("ERROR: 未知子命令 '{}'".format(cmd), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
