#!/usr/bin/env node
/**
 * fetch_dates.js
 *
 * 从 stdin 读取 JSON 数组 [{url, slug}, ...]
 * 用 puppeteer 并发（最多 3 个）访问每个 URL
 * 提取 datePublished，按优先级：
 *   1. JSON-LD <script type="application/ld+json">
 *   2. Open Graph <meta property="article:published_time">
 *   3. <time datetime="...">
 * 结果写入 stdout：[{url, slug, date}, ...]
 * date 为 ISO 格式字符串，找不到时为 null
 */

const puppeteer = require('puppeteer');

const CONCURRENCY = 3;
const PAGE_TIMEOUT = 15000; // 15 seconds

async function extractDate(page) {
  return page.evaluate(() => {
    // 1. JSON-LD
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const script of scripts) {
      try {
        const data = JSON.parse(script.textContent);
        const items = Array.isArray(data) ? data : [data];
        for (const item of items) {
          if (item.datePublished) return item.datePublished;
          // Check @graph
          if (item['@graph']) {
            for (const node of item['@graph']) {
              if (node.datePublished) return node.datePublished;
            }
          }
        }
      } catch (e) {}
    }

    // 2. Open Graph
    const ogMeta = document.querySelector('meta[property="article:published_time"]');
    if (ogMeta && ogMeta.getAttribute('content')) {
      return ogMeta.getAttribute('content');
    }

    // 3. <time datetime>
    const timeEl = document.querySelector('time[datetime]');
    if (timeEl && timeEl.getAttribute('datetime')) {
      return timeEl.getAttribute('datetime');
    }

    return null;
  });
}

async function fetchDate(browser, item) {
  const { url, slug } = item;
  let page;
  try {
    page = await browser.newPage();
    await page.setUserAgent(
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ' +
      'AppleWebKit/537.36 (KHTML, like Gecko) ' +
      'Chrome/120.0.0.0 Safari/537.36'
    );
    // Block images/fonts/media to speed up loading
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      const type = req.resourceType();
      if (['image', 'media', 'font', 'stylesheet'].includes(type)) {
        req.abort();
      } else {
        req.continue();
      }
    });

    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT });
    const date = await extractDate(page);
    return { url, slug, date };
  } catch (e) {
    process.stderr.write(`WARN: ${slug} failed: ${e.message}\n`);
    return { url, slug, date: null };
  } finally {
    if (page) await page.close();
  }
}

async function main() {
  // Read stdin
  let input = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) {
    input += chunk;
  }

  let items;
  try {
    items = JSON.parse(input);
  } catch (e) {
    process.stderr.write(`ERROR: invalid JSON input: ${e.message}\n`);
    process.exit(1);
  }

  if (!Array.isArray(items) || items.length === 0) {
    process.stdout.write('[]\n');
    return;
  }

  process.stderr.write(`fetch_dates: processing ${items.length} URLs (concurrency=${CONCURRENCY})\n`);

  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const results = [];
  let done = 0;

  // Process with limited concurrency using a queue
  const queue = [...items];
  const workers = Array.from({ length: CONCURRENCY }, async () => {
    while (queue.length > 0) {
      const item = queue.shift();
      if (!item) break;
      const result = await fetchDate(browser, item);
      results.push(result);
      done++;
      process.stderr.write(`  [${done}/${items.length}] ${item.slug}: ${result.date || 'null'}\n`);
    }
  });

  await Promise.all(workers);
  await browser.close();

  process.stdout.write(JSON.stringify(results, null, 2) + '\n');
}

main().catch((e) => {
  process.stderr.write(`FATAL: ${e.message}\n`);
  process.exit(1);
});
