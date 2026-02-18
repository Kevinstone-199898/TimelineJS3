# PRD: OpenAI 官网 News 抓取与 Timeline 数据生成

## Introduction

从 OpenAI 官网 https://openai.com/news/?sortBy=old 按时间顺序（最早到最晚）抓取全部 News，筛选出值得记录的事件（产品发布、融资、商业合作、重要研究论文、重大政策声明），按 TimelineJS3 规范格式写入 `contrib/examples/new_openai_data.json`，**不覆盖**已有的 `openai_data.json`。

## Goals

- 自动爬取 openai.com/news（从最早到最新，分页全量获取）
- 筛选出五类事件：产品发布、融资、商业合作、重要研究论文、重大政策声明
- 每条事件包含：标题（中文）、日期、一句话中文摘要、指向 OpenAI 官方 News 的链接
- 输出格式完全兼容 `openai_data.json` 中的 TimelineJS3 JSON 规范
- 结果保存到 `contrib/examples/new_openai_data.json`，不修改原始文件

## User Stories

### US-001: 编写爬虫脚本抓取 News 列表
**Description:** As a developer, I want a script to fetch all OpenAI news articles from the official website, sorted from oldest to newest, so that I have the raw data to process.

**Acceptance Criteria:**
- [ ] 脚本位于 `scripts/scrape_openai_news.py`（Python）
- [ ] 访问 `https://openai.com/news/?sortBy=old`，自动翻页直到抓取全部文章
- [ ] 每条抓取的字段包括：标题（英文原文）、发布日期（year/month/day）、文章 URL
- [ ] 若网站使用 JavaScript 动态渲染，使用 `requests-html` 或 `playwright` 处理
- [ ] 脚本执行完后在终端打印抓取到的条目总数

### US-002: 筛选值得收录的事件
**Description:** As a developer, I want the script to automatically filter articles by category so that only significant events are included in the timeline.

**Acceptance Criteria:**
- [ ] 筛选规则覆盖以下五类（关键词/规则硬编码或通过 OpenAI API 辅助判断）：
  - 产品发布（product launch, introducing, releasing, new model, API）
  - 融资（funding, investment, raises, valuation, partnership with financial entities）
  - 商业合作（partnership, agreement, collaboration with named companies）
  - 重要研究论文（research paper, arxiv, breakthrough in, we trained a model）
  - 重大政策声明（safety, governance, policy, regulation, congressional）
- [ ] 不符合上述五类的文章（博客杂谈、招聘公告等）被跳过
- [ ] 筛选结果可在终端以列表形式打印，便于人工核查

### US-003: 生成 TimelineJS3 兼容 JSON
**Description:** As a developer, I want the script to output a JSON file that matches the existing openai_data.json format so it can be used directly in the TimelineJS3 visualization.

**Acceptance Criteria:**
- [ ] 输出 JSON 结构与 `contrib/examples/openai_data.json` 完全一致（含 `title` 和 `events` 字段）
- [ ] 每条 event 包含：
  - `start_date`：`{ "year": "YYYY", "month": "MM" }`（如无 day 则省略）
  - `text.headline`：**中文**标题（英文标题翻译为中文，或人工标注）
  - `text.text`：一句话中文摘要（≤80 字）+ HTML 链接，格式示例：
    ```
    描述文字。<a href="https://openai.com/..." target="_blank" style="display:block;margin-top:6px;color:#10a37f">→ OpenAI 官方报道</a>
    ```
  - `group`：按类别填写，值域为：`产品` / `融资` / `合作` / `研究` / `政策` / `融资/合作` / `产品/合作`
- [ ] 事件按 `start_date` 从早到晚升序排列
- [ ] 标题 `title.text.headline` 保持为 `"OpenAI 发展历程"`

### US-004: 保存到新文件，不覆盖原文件
**Description:** As a developer, I want the output saved to a new file so that the original curated data is preserved.

**Acceptance Criteria:**
- [ ] 输出路径固定为 `contrib/examples/new_openai_data.json`
- [ ] 脚本运行前若文件已存在，覆盖时在终端给出提示
- [ ] 原文件 `contrib/examples/openai_data.json` 不被修改、不被删除
- [ ] 输出 JSON 使用 2 空格缩进，`ensure_ascii=False`（中文不转义）

## Functional Requirements

- **FR-1:** 脚本以 `python scripts/scrape_openai_news.py` 单命令运行，无需额外参数
- **FR-2:** 爬取目标 URL：`https://openai.com/news/?sortBy=old`，自动翻页
- **FR-3:** 对每篇文章抓取：英文标题、URL、发布日期（精确到月，有 day 则保留）
- **FR-4:** 通过关键词规则或 LLM 分类，将文章归入五类之一，无法归类则跳过
- **FR-5:** 将英文标题翻译为中文，生成一句话中文摘要（可调用 OpenAI API `gpt-4o-mini`）
- **FR-6:** 按 TimelineJS3 JSON 规范生成 event 对象，`group` 字段使用中文分类标签
- **FR-7:** 所有 event 按日期升序排序后写入 `contrib/examples/new_openai_data.json`
- **FR-8:** 脚本结束时打印：「已写入 N 条事件到 new_openai_data.json」

## Non-Goals

- 不实现增量更新（不与 openai_data.json 合并去重）
- 不生成 Timeline 的 HTML 页面，只生成数据 JSON
- 不抓取文章正文全文，只用标题 + URL + 摘要（可选）
- 不提供 Web UI 或定时任务
- 不修改 TimelineJS3 的任何源代码

## Design Considerations

### 现有 JSON 格式（参考 openai_data.json）
```json
{
  "title": {
    "text": {
      "headline": "OpenAI 发展历程",
      "text": "产品发布 · 融资轮次 · 商业合作"
    }
  },
  "events": [
    {
      "start_date": { "year": "2015", "month": "12" },
      "text": {
        "headline": "OpenAI 成立",
        "text": "描述文字。<a href=\"https://openai.com/...\" target=\"_blank\" style=\"display:block;margin-top:6px;color:#10a37f\">→ OpenAI 官方报道</a>"
      },
      "group": "融资"
    }
  ]
}
```

### group 字段取值规范
| 类别 | group 值 |
|------|----------|
| 产品发布 | `"产品"` |
| 融资 | `"融资"` |
| 商业合作 | `"合作"` |
| 研究论文 | `"研究"` |
| 政策声明 | `"政策"` |
| 融资 + 合作 | `"融资/合作"` |
| 产品 + 合作 | `"产品/合作"` |

## Technical Considerations

- **Python 版本：** 3.9+
- **依赖库：** `requests`, `beautifulsoup4`（静态页面优先）；若页面需 JS 渲染则改用 `playwright`
- **翻译/摘要：** 调用 `openai` Python SDK，使用 `gpt-4o-mini` 模型，批量处理控制成本
- **速率限制：** 爬取时每请求间隔 1 秒，避免被封禁
- **编码：** 输出 JSON 使用 `json.dump(..., ensure_ascii=False, indent=2)`
- **环境变量：** OpenAI API Key 通过 `OPENAI_API_KEY` 环境变量读取

## Success Metrics

- 脚本一次执行，无人工干预，成功输出 `new_openai_data.json`
- 收录事件数量 ≥ 50 条（OpenAI 2015-2025 有大量值得记录的事件）
- 每条事件均包含中文标题、中文摘要、官方链接
- 输出 JSON 可直接被 TimelineJS3 加载，无格式错误

## Open Questions

- OpenAI News 页面是否使用 JS 动态渲染？需先手动访问确认，若是则需 playwright
- 是否需要对已有 `openai_data.json` 中的事件去重？（当前 PRD 范围内：不需要）
- 翻译质量不满意时，是否需要人工审核流程？（当前范围内：不包含）
