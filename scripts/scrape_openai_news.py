#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_openai_news.py

分阶段工作流：
  1. 从 Sitemap 收集文章 URL：  python scrape_openai_news.py fetch
  2. 按批次 LLM 处理：          python scrape_openai_news.py phase 1   # 第1批
                                python scrape_openai_news.py phase 2   # 第2批
                                python scrape_openai_news.py phase 3   # 第3批
                                python scrape_openai_news.py phase 4   # 第4批
  3. 补充真实发布日期：         python scrape_openai_news.py enrich
  4. 合并生成最终 JSON：         python scrape_openai_news.py merge

说明：
  OpenAI 官网对 requests 返回 403，无法直接抓取文章页面。
  本脚本改用以下策略：
  - fetch：从公开的 sitemap.xml 抓取文章 URL 列表（按分类）
  - phase：将 URL slug 批量发给 DeepSeek LLM，由 LLM 根据训练数据
           识别文章内容、判断是否重要、提供发布年月、生成中文标题和摘要
  - merge：合并所有 phase 文件，生成 TimelineJS3 兼容 JSON

环境变量：
  SILICONFLOW_API_KEY    （phase 子命令需要）
"""

import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT  = os.path.dirname(SCRIPT_DIR)
RAW_PATH      = os.path.join(SCRIPT_DIR, "raw_articles.json")
OUTPUT_PATH   = os.path.join(PROJECT_ROOT, "contrib", "examples", "new_openai_data.json")
ORIGINAL_PATH = os.path.join(PROJECT_ROOT, "contrib", "examples", "openai_data.json")

def phase_path(n):  # type: (int) -> str
    return os.path.join(SCRIPT_DIR, "phase{}.json".format(n))

# ─── Config ───────────────────────────────────────────────────────────────────

SITEMAP_BASE  = "https://openai.com/sitemap.xml/{}"
REQUEST_DELAY = 1.0

# Sitemaps to collect URLs from (ordered by relevance)
SITEMAP_CATS = [
    "milestone",       # curated milestones
    "release",         # model/API releases
    "product",         # product launches
    "research",        # research papers
    "company",         # company news (funding, org changes)
    "safety",          # safety & policy
    "global-affairs",  # government & policy
    "publication",     # academic publications
]

# How many articles per phase (LLM batch size per API call)
BATCH_SIZE   = 20   # articles per LLM call
PHASE_COUNT  = 4    # number of phases

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

SILICONFLOW_URL   = "https://api.siliconflow.cn/v1/chat/completions"
SILICONFLOW_MODEL = "deepseek-ai/DeepSeek-V3"

LINK_HTML = (
    '<a href="{url}" target="_blank" rel="noopener" '
    'style="font-size:0.85em;color:#4285f4;">'
    '\U0001f4ce \u67e5\u770b\u539f\u6587</a>'
)  # 📎 查看原文

# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_json(path):  # type: (str) -> object
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):  # type: (str, object) -> None
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def slug_from_url(url):  # type: (str) -> str
    return url.rstrip("/").split("/")[-1]

def _call_llm(api_key, messages, temperature=0):  # type: (str, List[Dict], float) -> str
    resp = requests.post(
        SILICONFLOW_URL,
        headers={
            "Authorization": "Bearer {}".format(api_key),
            "Content-Type": "application/json",
        },
        json={
            "model": SILICONFLOW_MODEL,
            "temperature": temperature,
            "messages": messages,
        },
        timeout=60,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    # Strip markdown code fences if present
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    return raw

# ─── Step 1: fetch from sitemaps ─────────────────────────────────────────────

def cmd_fetch():
    """
    Collect article URLs from OpenAI's public sitemaps.
    Saves to raw_articles.json: list of {url, slug, sitemap_cat}.
    No article-page fetching — avoids 403 on article pages.
    """
    print("=" * 60)
    print("fetch: 从 sitemap 收集文章 URL")
    print("=" * 60)

    if os.path.exists(RAW_PATH):
        print("  文件已存在，将覆盖: {}".format(RAW_PATH))

    all_articles = []
    seen = set()

    for cat in SITEMAP_CATS:
        url = SITEMAP_BASE.format(cat + "/")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            print("  {} ERROR: {}".format(cat, e))
            continue

        urls = re.findall(r"<loc>(.*?)</loc>", r.text)
        count = 0
        for u in urls:
            # Skip non-article URLs (sitemap index pages, etc.)
            if not re.search(r"/index/|/research/index/", u):
                continue
            if u in seen:
                continue
            seen.add(u)
            all_articles.append({
                "url": u,
                "slug": slug_from_url(u),
                "sitemap_cat": cat,
            })
            count += 1

        print("  {:20s}: {} 条".format(cat, count))
        time.sleep(REQUEST_DELAY)

    save_json(RAW_PATH, all_articles)
    print("\n已收集 {} 条文章 URL，保存至 raw_articles.json".format(len(all_articles)))

# ─── Step 2: LLM batch processing per phase ──────────────────────────────────

PHASE_SYSTEM = """你是 OpenAI 历史专家。我会给你一批 OpenAI 文章的 URL slug 列表。

对每个 slug，请根据你的训练知识判断：
1. 这是否是值得记录在 OpenAI 发展历程中的重要事件？
   重要事件包括：产品发布、模型发布、融资、商业合作、重要研究成果、重大政策声明。
   不重要的包括：普通博客、工程文章、招聘、小更新、安全测试报告等。

2. 如果重要，提供：
   - year: 发布年份（4位数字字符串，如 "2022"）
   - month: 发布月份（2位数字字符串，如 "11"）
   - headline_zh: 简洁中文标题（≤20字）
   - summary_zh: 一句话中文摘要（≤80字，说明事件意义）
   - category: 分类，只能是以下之一：产品 / 融资 / 合作 / 研究 / 政策

返回严格 JSON 数组，每个元素对应输入列表中的一个 slug（保持顺序）：
[
  {"slug": "...", "significant": true, "year": "2022", "month": "11", "headline_zh": "...", "summary_zh": "...", "category": "..."},
  {"slug": "...", "significant": false},
  ...
]

如果你不确定某个 slug 是什么文章，significant 设为 false。只输出 JSON，不要有其他文字。"""


def process_batch(api_key, batch):  # type: (str, List[Dict]) -> List[Dict]
    """Send a batch of articles to LLM. Returns enriched articles for significant ones."""
    slugs = [a["slug"] for a in batch]
    slug_list = "\n".join(["{}. {}".format(i+1, s) for i, s in enumerate(slugs)])

    raw = _call_llm(api_key, [
        {"role": "system", "content": PHASE_SYSTEM},
        {"role": "user",   "content": "请处理以下 slug 列表：\n\n" + slug_list},
    ])

    try:
        results = json.loads(raw)
    except ValueError as e:
        print("  WARNING: JSON parse error: {}".format(e))
        print("  Raw response: {}".format(raw[:200]))
        return []

    enriched = []
    for i, result in enumerate(results):
        if not result.get("significant"):
            continue
        if i >= len(batch):
            break
        article = dict(batch[i])
        article["year"]        = result.get("year", "")
        article["month"]       = result.get("month", "01")
        article["headline_zh"] = result.get("headline_zh", article["slug"])
        article["summary_zh"]  = result.get("summary_zh", "")
        article["category"]    = result.get("category", "产品")
        enriched.append(article)

    return enriched


def cmd_phase(phase_num):  # type: (int) -> None
    """
    Process one phase (batch of articles) using LLM.
    Each phase processes 1/4 of raw_articles.json.
    LLM identifies significance, date, Chinese title+summary for each article.
    """
    if phase_num < 1 or phase_num > PHASE_COUNT:
        print("ERROR: phase must be 1-{}".format(PHASE_COUNT), file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("phase {}: LLM 批量处理第 {} 批文章".format(phase_num, phase_num))
    print("=" * 60)

    api_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        print("ERROR: SILICONFLOW_API_KEY 未设置", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(RAW_PATH):
        print("ERROR: raw_articles.json 不存在，请先运行 fetch", file=sys.stderr)
        sys.exit(1)

    all_articles = load_json(RAW_PATH)
    total = len(all_articles)

    # Divide into PHASE_COUNT equal slices
    chunk = (total + PHASE_COUNT - 1) // PHASE_COUNT
    start = (phase_num - 1) * chunk
    end   = min(start + chunk, total)
    phase_articles = all_articles[start:end]

    print("  处理第 {}-{} 条（共 {} 条）".format(start+1, end, total))

    selected = []
    skipped  = 0

    # Process in batches of BATCH_SIZE
    for i in range(0, len(phase_articles), BATCH_SIZE):
        batch = phase_articles[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(phase_articles) + BATCH_SIZE - 1) // BATCH_SIZE
        print("  批次 {}/{} ({} 条)...".format(batch_num, total_batches, len(batch)))

        results = process_batch(api_key, batch)
        selected.extend(results)
        skipped += len(batch) - len(results)

        for r in results:
            print("    [{}] {}/{} {}".format(
                r["category"], r["year"], r["month"], r["headline_zh"]
            ))

        time.sleep(1.0)

    out = phase_path(phase_num)
    save_json(out, selected)
    print("\nPhase {} 完成：入选 {} 条，跳过 {} 条".format(phase_num, len(selected), skipped))

# ─── Step 3: enrich with real dates via puppeteer ────────────────────────────

FETCH_DATES_JS = os.path.join(SCRIPT_DIR, "fetch_dates.js")


def cmd_enrich():
    """
    Enrich phase*.json with real publication dates fetched via puppeteer.
    For each significant article, visits the OpenAI page and extracts
    datePublished from JSON-LD, Open Graph, or <time> elements.
    Overwrites year/month in phase*.json with the real values.
    """
    print("=" * 60)
    print("enrich: 用 puppeteer 补充真实发布日期")
    print("=" * 60)

    if not os.path.exists(FETCH_DATES_JS):
        print("ERROR: fetch_dates.js 不存在: {}".format(FETCH_DATES_JS), file=sys.stderr)
        sys.exit(1)

    # 1. Collect all articles from all phase files
    all_articles = []  # list of (phase_num, index_in_phase, article_dict)
    phase_data = {}    # phase_num -> list of articles

    for n in range(1, PHASE_COUNT + 1):
        p = phase_path(n)
        if not os.path.exists(p):
            print("  phase{}.json 不存在，跳过".format(n))
            continue
        articles = load_json(p)
        phase_data[n] = articles
        for i, art in enumerate(articles):
            if art.get("url"):
                all_articles.append((n, i, art))

    if not all_articles:
        print("没有找到任何文章，请先运行 phase 命令")
        sys.exit(1)

    print("  共 {} 条文章需要获取日期".format(len(all_articles)))

    # 2. Build input for fetch_dates.js
    url_items = [
        {"url": art["url"], "slug": art["slug"]}
        for (_, _, art) in all_articles
    ]

    # 3. Call node fetch_dates.js via subprocess
    print("  启动 puppeteer（并发 3）...")
    node_input = json.dumps(url_items, ensure_ascii=False)

    try:
        proc = subprocess.Popen(
            ["node", FETCH_DATES_JS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=SCRIPT_DIR,
        )
        stdout, stderr = proc.communicate(input=node_input.encode("utf-8"), timeout=600)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("ERROR: puppeteer 超时（10分钟）", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("ERROR: node 未安装或不在 PATH 中", file=sys.stderr)
        sys.exit(1)

    # Print stderr from node (progress info)
    if stderr:
        print(stderr.decode("utf-8", errors="replace"), end="")

    if proc.returncode != 0:
        print("ERROR: fetch_dates.js 返回错误码 {}".format(proc.returncode), file=sys.stderr)
        sys.exit(1)

    try:
        date_results = json.loads(stdout.decode("utf-8"))
    except ValueError as e:
        print("ERROR: 解析 fetch_dates.js 输出失败: {}".format(e), file=sys.stderr)
        sys.exit(1)

    # 4. Build url -> date mapping
    url_to_date = {}
    for r in date_results:
        if r.get("date") and r.get("url"):
            url_to_date[r["url"]] = r["date"]

    # 5. Update phase data with real dates
    updated = 0
    not_found = 0

    for n, i, art in all_articles:
        url = art["url"]
        if url in url_to_date:
            raw_date = url_to_date[url]
            # Parse year and month from ISO date string (e.g. "2021-08-10T...")
            m = re.match(r"(\d{4})-(\d{2})", raw_date)
            if m:
                phase_data[n][i]["year"]  = m.group(1)
                phase_data[n][i]["month"] = m.group(2)
                updated += 1
            else:
                not_found += 1
        else:
            not_found += 1

    # 6. Write back to phase files
    for n, articles in phase_data.items():
        save_json(phase_path(n), articles)

    print("\nenrich 完成：更新了 {} 条日期，{} 条未找到（保持 LLM 推断值）".format(
        updated, not_found
    ))


# ─── Step 4: merge ────────────────────────────────────────────────────────────

def _sort_key(event):  # type: (Dict) -> Tuple[int, int]
    sd = event.get("start_date", {})
    return (int(sd.get("year", 0)), int(sd.get("month", 0)))


def cmd_merge():
    """Merge phase1-4.json into new_openai_data.json (TimelineJS3 format)."""
    print("=" * 60)
    print("merge: 合并所有阶段数据")
    print("=" * 60)

    assert os.path.abspath(OUTPUT_PATH) != os.path.abspath(ORIGINAL_PATH)

    events = []
    ev_index = 0
    for n in range(1, PHASE_COUNT + 1):
        p = phase_path(n)
        if not os.path.exists(p):
            print("  WARNING: {} 不存在，跳过".format(p))
            continue
        articles = load_json(p)
        print("  phase{}.json: {} 条".format(n, len(articles)))

        for art in articles:
            year  = art.get("year", "")
            month = str(int(art.get("month", "1")))  # 去掉前导零
            day   = str(int(art.get("day", "0") or "0")) if art.get("day") else ""
            if not year:
                print("  SKIP (no year): {}".format(art.get("slug", "")))
                continue

            url      = art.get("url", "")
            headline = art.get("headline_zh") or art.get("slug", "")
            summary  = art.get("summary_zh", "")
            category = art.get("category", "产品")
            link     = LINK_HTML.format(url=url) if url else ""
            body     = "{}<br><br>{}".format(summary, link) if (summary and link) else (summary or link)

            start_date = {"year": year, "month": month}
            if day and day != "0":
                start_date["day"] = day

            ev = {
                "unique_id":  "ev_{}".format(ev_index),
                "start_date": start_date,
                "text":       {"headline": headline, "text": body},
                "group":      category,
            }
            if url:
                ev["_source"] = url
            events.append(ev)
            ev_index += 1

    events.sort(key=_sort_key)

    output = {
        "title": {
            "text": {
                "headline": "OpenAI \u53d1\u5c55\u5386\u7a0b",
                "text":     "\u4ea7\u54c1\u53d1\u5e03 \u00b7 \u878d\u8d44\u8f6e\u6b21 \u00b7 \u5546\u4e1a\u5408\u4f5c",
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
        print("  python scrape_openai_news.py fetch          # 从 sitemap 收集文章 URL")
        print("  python scrape_openai_news.py phase <1-4>    # LLM 批量处理（按批次）")
        print("  python scrape_openai_news.py enrich         # puppeteer 补充真实发布日期")
        print("  python scrape_openai_news.py merge          # 合并生成最终 JSON")
        sys.exit(0)

    cmd = args[0]

    if cmd == "fetch":
        cmd_fetch()
    elif cmd == "phase":
        if len(args) < 2 or not args[1].isdigit():
            print("ERROR: 请指定批次编号，例如：phase 1", file=sys.stderr)
            sys.exit(1)
        cmd_phase(int(args[1]))
    elif cmd == "enrich":
        cmd_enrich()
    elif cmd == "merge":
        cmd_merge()
    else:
        print("ERROR: 未知子命令 '{}'".format(cmd), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
