# 考研408模拟试卷生成器

基于历年（2009–2026）408 统考真题题库分析真实命题规律，用 LLM 生成**原创**模拟选择题（含 AI 质检闭环），最终排版输出试卷 PDF 与答案解析 PDF。

## 特性

- **真题驱动的命题策略**：只学真题的"卷面结构"、不押高频考点——章节按历年题位分布加权（还原真题编排顺序），章节内知识点均匀随机（冷门热门机会均等），同卷考点不重复，每张卷都是对大纲的全新随机覆盖
- **知识点融合题**：每科目 1–2 道科目内跨章节融合题（如"用栈实现某排序"），只锁定主考点，搭配知识点由 AI 从候选中自选最自然的，严禁跨科目
- **双 AI 质检闭环**：生成时答案+解析同题产出；第二个 AI 调用按 10 个维度独立校验（独立解答验算、解析推导、选项均衡、对照模板真题查原创性、融合自然度等），不合格的整改意见回灌重出，最多 3 轮
- **批量生成 + 断点续传**：默认每次只生成 3 题，反复运行自动续写同一张卷直到 40 题攒齐；并发生成但落盘始终按题号有序
- **流式 AI 调用**：兼容思维链动辄上万字的推理模型——超时语义为"空闲无数据"而非总时长，思维链耗尽输出额度时自动翻倍 max_tokens 重试；日志记录每次调用的耗时/字数/结束原因
- **纯标准库**：除 `fpdf2`（PDF 渲染）外零依赖

## 目录结构

```
src/
  tag_questions.py  # AI 为无考点标注的真题补标（按 data/大纲.json 分类）
  analyze.py        # 分析历年考点分布 → data/_考点分布.json、data/_tag章节映射.json
  generate.py       # 批量生成模拟题（质检闭环、断点续传）
  build_paper.py    # 排版渲染 试卷.pdf + 答案解析.pdf
  test_paper.py     # 组卷冒烟测试（少量AI题+真题填满整卷并渲染）
config/
  settings.json         # 公共配置（试卷结构、并发、批量大小等）
  settings.local.json   # 本地私有配置（API地址/密钥/模型），不入库
data/                   # 数据集：历年真题库、考纲、考点分布等（仓库自带）
generated/              # 生成中/已完成的试卷 JSON 与日志（不入库）
output/                 # 最终 PDF（不入库）
```

> 出题**完全离线基于仓库自带的 `data/` 数据集**，不访问任何外部数据源；
> 唯一的网络调用是 OpenAI 兼容的 AI 接口。

## 快速开始

**依赖**：Python 3.10+、`fpdf2`、Noto Sans CJK 字体

```bash
pip install fpdf2
# Arch: pacman -S noto-fonts-cjk    Debian/Ubuntu: apt install fonts-noto-cjk
```

**配置 AI 接口**（OpenAI 兼容格式），二选一：

```bash
# 方式一：环境变量（优先级最高）
export API_BASE="https://your-provider.example/v1"
export API_KEY="sk-..."
export MODEL="your-model"

# 方式二：创建 config/settings.local.json（已被 .gitignore 忽略）
{
  "api_base": "https://your-provider.example/v1",
  "api_key": "sk-...",
  "model": "your-model"
}
```

**完整流程**（仓库自带完整 `data/` 数据集，直接从生成开始）：

```bash
python3 src/generate.py --new   # 1. 新建试卷，生成第一批3题
python3 src/generate.py         #    反复运行，每次+3题，自动续写直到40题
python3 src/generate.py --all   #    或一次性生成全部剩余
python3 src/generate.py --status

python3 src/build_paper.py <试卷目录名>   # 2. 渲染 PDF（目录名见 --status）
```

题库数据更新后（如补充新一年真题），依次重跑 `python3 src/tag_questions.py`（AI 补标无考点的题，幂等）和 `python3 src/analyze.py`（重建考点分布）。

## 配置说明（config/settings.json）

| 字段 | 说明 |
|---|---|
| `api_base` / `api_key` / `model` | AI 接口三件套；留空，用环境变量或 settings.local.json 提供 |
| `max_tokens` | 单次调用输出上限（默认 16384；推理模型思维链计入此额度，耗尽时自动翻倍重试） |
| `request_timeout` | 流式空闲超时秒数（连续无数据才算超时，默认 120） |
| `max_concurrent` | 并发生成数（默认 3） |
| `batch_size` | 每次运行生成的题数（默认 3） |
| `paper.subjects` | 科目、题号范围、分值（同时驱动生成 prompt 与 PDF 排版） |

## GitHub Actions

仓库内置 `生成408模拟试卷` 工作流（`.github/workflows/generate-paper.yml`）：

- **每天北京时间 06:00 自动运行**（也可在 Actions 页手动触发，`count` 留空生成整卷 40 题）
- 基于仓库自带的 `data/` 数据集生成整卷 → 渲染 PDF → 产物存为 Artifact（保留30天）
- 配置了邮箱后，自动把两份 PDF **发送到指定邮箱**（支持多个，英文逗号分隔）

使用前在仓库 **Settings → Secrets and variables → Actions** 配置：

| Secret | 必需 | 内容 |
|---|---|---|
| `API_BASE` | ✓ | OpenAI 兼容接口地址，如 `https://your-provider.example/v1` |
| `API_KEY` | ✓ | 接口密钥 |
| `MODEL` | ✓ | 模型 ID |
| `MAIL_SERVER` | 邮件功能 | SMTP 服务器，如 `smtp.qq.com`（不配则跳过发信） |
| `MAIL_PORT` | 可选 | SMTP 端口，默认 465 |
| `MAIL_USERNAME` | 邮件功能 | SMTP 账号（同时作为发件人） |
| `MAIL_PASSWORD` | 邮件功能 | SMTP 密码/授权码 |
| `MAIL_TO` | 邮件功能 | 收件邮箱，多个用英文逗号分隔，如 `a@x.com,b@y.com` |

## 注意事项

- 模型选择：优先选**不外露思维链**的模型，速度快且不烧 token；重代推理模型（思维链上万字）单题可能耗时数分钟，靠流式+自动翻倍机制兜底
- 部分服务商 WAF 拦截 Python 默认 User-Agent，`call_ai` 已内置浏览器 UA
- 质检调用失败的题会保留但标记 `"validated": false`，建议人工复核
- 真题中带图片的题以 `![](url)` 标记存储，PDF 不渲染图片；AI 生成题均为纯文字
