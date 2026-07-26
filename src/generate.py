#!/usr/bin/env python3
"""
408真题生成器
根据大纲随机抽取题库题目，调用AI生成新题，含质检环节，输出为JSON
"""

import json
import os
import random
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# ── 日志：带时间戳，控制台 + 试卷目录下的 generate.log ──
_print_lock = threading.Lock()
_log_file: Optional[str] = None


def init_log(paper_dir: str):
    global _log_file
    _log_file = os.path.join(paper_dir, "generate.log")


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with _print_lock:
        print(line, flush=True)
        if _log_file:
            with open(_log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

# ============================================================
# 路径配置
# ============================================================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# 科目规范名（与 config/settings.json、data/大纲.json、data/<科目>.json 文件名一致）
SUBJECTS = ["数据结构", "计算机组成原理", "操作系统", "计算机网络"]
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)

# ============================================================
# 配置加载
# ============================================================

def load_config() -> dict:
    """
    配置解析优先级：环境变量 > config/settings.local.json（不入库） > config/settings.json。
    API 地址/密钥/模型属于隐私信息，不要写进 settings.json 提交到仓库。
    """
    with open(os.path.join(CONFIG_DIR, "settings.json"), encoding="utf-8") as f:
        config = json.load(f)

    local_path = os.path.join(CONFIG_DIR, "settings.local.json")
    if os.path.exists(local_path):
        with open(local_path, encoding="utf-8") as f:
            config.update(json.load(f))

    for key, env in (("api_base", "API_BASE"), ("api_key", "API_KEY"), ("model", "MODEL")):
        if os.environ.get(env):
            config[key] = os.environ[env]

    missing = [k for k in ("api_base", "api_key", "model") if not config.get(k)]
    if missing:
        raise SystemExit(
            f"缺少 AI 配置: {', '.join(missing)}\n"
            f"请设置环境变量 API_BASE / API_KEY / MODEL，"
            f"或创建 config/settings.local.json 填入这三项（该文件不入库）")
    return config


def load_outline() -> dict:
    with open(os.path.join(DATA_DIR, "大纲.json"), encoding="utf-8") as f:
        return json.load(f)


def load_question_bank(subject: str) -> list[dict]:
    path = os.path.join(DATA_DIR, f"{subject}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["questions"]


# ============================================================
# AI 调用（OpenAI 兼容格式）
# ============================================================

def call_ai(config: dict, prompt: str, temperature: float = 0.8) -> Optional[str]:
    """
    调用 OpenAI 兼容 API（流式）。
    必须用流式：推理模型的思维链可能长达数分钟，非流式要等全部生成完才返回
    第一个字节，任何总时长超时都会误杀；流式下 request_timeout 的语义是
    「连续无数据的空闲超时」，思维链增量会持续到达，正常生成不会被杀。
    """
    url = f"{config['api_base'].rstrip('/')}/chat/completions"
    max_tokens = config.get("max_tokens", 16384)
    idle_timeout = config.get("request_timeout", 120)

    for attempt in range(3):
        payload = json.dumps({
            "model": config["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }).encode("utf-8")
        t0 = time.time()
        try:
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config['api_key']}",
                "Accept": "text/event-stream",
                # 部分服务商 WAF 会拦截 Python-urllib 默认 UA
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
            })

            content_parts: list[str] = []
            reasoning_chars = 0
            finish = "?"
            first_data_at = None

            with urllib.request.urlopen(req, timeout=idle_timeout) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except ValueError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        if first_data_at is None:
                            first_data_at = time.time()
                        reasoning_chars += len(delta["reasoning_content"])
                    if delta.get("content"):
                        if first_data_at is None:
                            first_data_at = time.time()
                        content_parts.append(delta["content"])
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]

            content = "".join(content_parts)
            first = f"{first_data_at - t0:.0f}s" if first_data_at else "无"
            log(f"    [AI响应 {time.time()-t0:.0f}s] 首数据{first} "
                f"正文{len(content)}字 思维链{reasoning_chars}字 结束原因:{finish}")

            if finish == "length" and not content.strip():
                # 思维链耗尽全部输出额度：翻倍重试
                max_tokens = min(max_tokens * 2, 65536)
                log(f"    [截断重试 {attempt+1}/3] 思维链耗尽输出额度且无正文，"
                    f"max_tokens 提高至 {max_tokens} 重试...")
                continue
            if finish == "length":
                log(f"    [警告] 输出被 max_tokens({max_tokens}) 截断，正文可能不完整")
            if not content.strip():
                log(f"    [空响应 {attempt+1}/3] 模型未返回正文，重试...")
                continue
            return content
        except Exception as e:
            log(f"    [AI调用失败 {attempt+1}/3 耗时{time.time()-t0:.0f}s] {e}")
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    return None


# ============================================================
# Prompt 构建
# ============================================================

def get_subject_range_str(subjects_config: list[dict]) -> str:
    """生成科目与题号范围的描述"""
    lines = []
    for s in subjects_config:
        lines.append(f"    {s['name']}：第 {s['range'][0]}～{s['range'][1]} 题")
    return "\n".join(lines)


def build_generate_prompt(
    question_num: int,
    subject_name: str,
    subject_range_str: str,
    template: dict,
    outline_topics: list[str],
    difficulty: str,
    feedback: str = "",
    fusion: bool = False,
) -> str:
    """构建单题生成 prompt；feedback 为上一次质检不合格的原因；fusion 为科目内知识点融合题"""
    template_text = template.get("question_text", "")
    template_options = template.get("options", {})
    template_answer = template.get("answer", "")
    template_tags = ", ".join(template.get("tags", []))

    topics_str = "、".join(outline_topics) if outline_topics else subject_name

    if fusion:
        primary = outline_topics[0] if outline_topics else subject_name
        candidates = "、".join(outline_topics[1:]) if len(outline_topics) > 1 else "（无）"
        scope_block = f"""    考查范围（知识点融合题）
    本题为{subject_name}科目内的知识点融合题，主考点为：{primary}
    请从以下候选知识点中，选择一个与主考点融合最自然、最贴近真题命题逻辑的进行融合命题：
    {candidates}
    若候选均不适合，也可自行从{subject_name}大纲中另选一个更合适的搭配知识点，但严禁引入其他科目内容。
    融合方式参考：用其中一个知识点的结构/方法支撑或实现另一个（如某排序算法借助栈或队列实现）、比较两者在同一场景下的行为或效率、分析两者结合时的性质。
    融合必须自然合理，宁可平实不可生硬拼凑；【考点】处写明实际融合的两个知识点。"""
    else:
        scope_block = f"""    考查范围
    本题应考查以下知识点之一：{topics_str}"""

    feedback_block = ""
    if feedback:
        feedback_block = f"""

    上一次生成的题目未通过质检，本次必须规避以下问题：
    {feedback}"""

    return f"""请只生成 1 道408 计算机学科专业基础综合单项选择题，严格遵守以下全部约束，不得多题、不得超纲、不得偏离真题难度与风格。

一、硬性约束

    科目与题号
    本题为第 {question_num} 题，科目为：
{subject_range_str}

    难度要求
    难度严格对标 408 统考真题，只能为：基础 / 中档 / 拔高 其中一种，不得过易或过难。
    本题目标难度：{difficulty}

{scope_block}

    题目规范
        仅 4 选 1 单选题，唯一正确答案，其余三项必须确定错误、无争议
        题干严谨、无歧义、无模糊表述、无逻辑漏洞，条件充分且不冗余
        术语、符号、用词与408统考真题及主流教材保持一致，禁止口语化表达
        选项必须竖排，禁止横向排列
        四个选项形式和长度均衡，干扰项来自典型错误思路，不得凑数、不得从形式上暴露答案
        计算量与真题一致，不出现冗余复杂计算；涉及计算必须自行验算确保答案正确
        完全原创，不照搬参考真题，不得仅改数字复用其结构
        如需使用数学公式，请使用 LaTeX 格式（行内公式用 $...$，独立公式用 $$...$$）

    解析规范
        【解析】必须给出得到正确答案的完整推导/验算过程
        必须逐一说明其余三个选项错误的原因
        【考点】填写本题实际考查的知识点名称

    参考真题风格（仅供参考难度，不要照搬内容）
    以下是一道同类科目的真题示例：
    题目：{template_text}
    选项：{json.dumps(template_options, ensure_ascii=False)}
    答案：{template_answer}
    涉及知识点：{template_tags}{feedback_block}

二、输出格式（严格执行）

本题试卷内容
{question_num}、题干
A. 选项
B. 选项
C. 选项
D. 选项

本题参考答案与解析
【答案】
【考点】
【难度】
【解析】"""


def build_validate_prompt(
    question_data: dict,
    subject_range_str: str,
    template: Optional[dict] = None,
    fusion: bool = False,
) -> str:
    """构建题目质检 prompt；template 用于原创性对照，fusion 标记融合题"""
    num = question_data["number"]
    stem = question_data.get("question_text", "")
    options = question_data.get("options", {})
    options_str = "\n".join(f"{k}. {v}" for k, v in sorted(options.items()))
    answer = question_data.get("answer", "")
    tags = "、".join(question_data.get("tags", []))
    explanation = question_data.get("explanation", "")

    template_block = ""
    if template and template.get("question_text"):
        template_block = f"""

命题时参考的真题（用于第9条原创性检查）：
{template['question_text']}
选项：{json.dumps(template.get('options', {}), ensure_ascii=False)}"""

    fusion_item = ""
    if fusion:
        fusion_item = "\n    10. 本题为科目内知识点融合题：两个知识点的融合是否自然合理、不生硬拼凑，且未跨科目引入其他科目内容"

    return f"""请对下方这道 408 计算机考研选择题，严格对照近年 408 统考真题命题标准进行专业严谨校验。你是最后一道质量关卡，必须逐条独立验证，不得遗漏，宁可错杀不可放过。

待校验题目：
第{num}题
{stem}
{options_str}
【答案】{answer}
【考点】{tags}
【解析】{explanation}

科目与题号对应关系：
{subject_range_str}{template_block}

一、校验维度（逐条编号检查）

    1. 科目与题号是否匹配（对照上方"科目与题号对应关系"）
    2. 考点是否在 408 考纲内，无超纲、无偏题
    3. 题干是否严谨无歧义、无模糊描述、无逻辑漏洞、条件是否充分
    4. 答案是否唯一正确：请独立解答本题（涉及计算必须亲自验算），确认你的答案与标注答案一致，且其余三项确定错误
    5. 解析是否正确：推导过程能否真正得出标注答案，对干扰项的否定是否成立
    6. 选项设置是否合理：干扰项是否来自典型错误思路、有迷惑性，四项形式长度是否均衡、不从形式上暴露答案
    7. 难度是否在 408 真题区间内，不过于简单或过难
    8. 命题风格、术语、措辞是否贴近统考真题与主流教材，无口语化、无冗余内容
    9. 原创性：是否与参考真题实质雷同（照搬结构仅改数字也算雷同）{fusion_item}

二、输出格式（严格执行）

【题目校验结论】合格 / 不合格
【不合格项编号】直接列出不通过的数字序号，全部合格则填 "无"
【整改建议】具体说明每个不合格项的问题与修改方向，供重新命题时规避"""


# ============================================================
# AI 响应解析
# ============================================================

def parse_ai_response(response: str, question_num: int, subject_name: str, year: str) -> Optional[dict]:
    """解析AI返回的题目文本，提取结构化数据"""

    # 清理部分模型输出的 markdown 痕迹（加粗、代码反引号）
    response = response.replace("**", "").replace("`", "")

    parts = re.split(r'本题参考答案与解析', response, maxsplit=1)
    if len(parts) < 2:
        return None

    quiz_part = parts[0]
    answer_part = parts[1]

    lines = quiz_part.strip().split("\n")
    question_lines = []
    options = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.match(r'^(本题试卷内容|题干)$', line):
            continue
        if re.match(r'^[-#*=—]{2,}$', line):  # markdown 分隔线/标题残留
            continue
        if re.match(rf'^{question_num}[、．.]', line):
            stem = re.sub(rf'^{question_num}[、．.]\s*', '', line)
            if stem and stem != "题干":
                question_lines.append(stem)
            continue
        m = re.match(r'^([A-D])[.．、]\s*(.*)', line)
        if m:
            options[m.group(1)] = m.group(2).strip()
            continue
        if line and "【" not in line and "答案" not in line:
            question_lines.append(line)

    question_text = "\n".join(question_lines).strip()

    # 出现多个互相矛盾的【答案】说明模型输出了纠结过程，判解析失败重出
    all_answers = re.findall(r'【答案】\s*([A-D])', answer_part)
    if len(set(all_answers)) > 1:
        return None
    answer = all_answers[0] if all_answers else ""

    tag_match = re.search(r'【考点】\s*(.+?)(?:\n|$|【)', answer_part)
    tag = tag_match.group(1).strip() if tag_match else ""

    diff_match = re.search(r'【难度】\s*(.+?)(?:\n|$|【)', answer_part)
    difficulty = diff_match.group(1).strip() if diff_match else ""

    exp_match = re.search(r'【解析】\s*(.*)', answer_part, re.DOTALL)
    explanation = exp_match.group(1).strip() if exp_match else ""

    if not question_text or not options or not answer or len(options) != 4:
        return None

    # 解析异常长通常是模型把思考过程写进了解析，判失败重出
    if len(explanation) > 3000:
        return None

    return {
        "id": f"gen-{year}-{question_num:02d}",
        "year": year,
        "number": str(question_num),
        "subject": subject_name,
        "section": "选择题",
        "tags": [tag] if tag else [],
        "answer": answer,
        "multiple": False,
        "question_text": question_text,
        "options": options,
        "explanation": explanation,
        "_meta": {
            "difficulty": difficulty,
            "template_id": "",
            "validated": False,
            "attempts": 0,
        },
    }


def parse_validation_response(response: str) -> dict:
    """解析质检结果"""
    verdict_match = re.search(r'【题目校验结论】\s*(合格|不合格)', response)
    verdict = verdict_match.group(1) if verdict_match else "不合格"

    issues_match = re.search(r'【不合格项编号】\s*(.+?)(?:\n|$|【)', response)
    issues = issues_match.group(1).strip() if issues_match else "无"

    suggestion_match = re.search(r'【整改建议】\s*(.+?)(?:\n|$|【])', response)
    suggestion = suggestion_match.group(1).strip() if suggestion_match else ""

    return {
        "verdict": verdict,
        "issues": issues,
        "suggestion": suggestion,
    }


# ============================================================
# 单题生成（含质检循环）
# ============================================================

MAX_RETRIES = 3


# ── 考点抽取：按历年真题分布加权 ──

def _bigrams(s: str) -> set[str]:
    s = re.sub(r'[^0-9A-Za-z一-鿿]', '', s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def related(a: str, b: str) -> bool:
    """两个考点短语是否相关：一方(≥2字)包含于另一方，或二元字组重叠≥2"""
    aa = re.sub(r'[^0-9A-Za-z一-鿿]', '', a)
    bb = re.sub(r'[^0-9A-Za-z一-鿿]', '', b)
    if len(aa) >= 2 and len(bb) >= 2 and (aa in bb or bb in aa):
        return True
    return len(_bigrams(a) & _bigrams(b)) >= 2


_distribution_cache: Optional[dict] = None


def load_distribution() -> Optional[dict]:
    """加载真题考点分布（由 src/analyze.py 生成）；不存在则返回 None"""
    global _distribution_cache
    if _distribution_cache is None:
        path = os.path.join(DATA_DIR, "_考点分布.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                _distribution_cache = json.load(f)
        else:
            _distribution_cache = {}
    return _distribution_cache or None


def build_chapters(outline: dict, subject: str) -> list[tuple[str, list[str]]]:
    """科目的 [(章节名, [知识点(-子知识点)...]), ...]"""
    for s in outline["subjects"]:
        if s["name"] == subject:
            chapters = []
            for ch in s["chapters"]:
                topics = []
                for t in ch["topics"]:
                    topics.append(t["name"])
                    for st in t.get("subtopics", []):
                        topics.append(f"{t['name']}-{st}")
                chapters.append((ch["title"], topics))
            return chapters
    return []


def choose_focus(
    subject: str,
    offset: int,
    question_bank: list[dict],
    chapters: list[tuple[str, list[str]]],
    used_topics: Optional[set] = None,
    fusion: bool = False,
) -> tuple[list[str], dict, str, str]:
    """
    抽取本题考点——只学真题的卷面结构，不押高频考点：
      1. 章节：按历年该题位的章节分布加权（每章都有基础权重1做平滑，
         保证任何章节都可能出现），还原真题的编排顺序；
      2. 知识点：章内均匀随机——冷门与热门机会均等，且排除本卷已用考点，
         每张卷都是对大纲的一次全新随机覆盖；
      3. 融合题（fusion=True）：再从同科目的另一章节均匀抽一个知识点，
         要求一道题融合考查两个知识点（科目内融合，不跨科目）；
      4. 模板：优先同知识点真题，其次同章节，最后全库随机（仅作风格参考）。
    返回 (prompt知识点列表, 模板题, 日志说明, 考点标识)。
    """
    used = used_topics if used_topics is not None else set()

    # 1) 章节：题位结构加权 + 平滑
    titles = [t for t, _ in chapters]
    weights = {t: 1.0 for t in titles}
    dist = load_distribution()
    sd = dist["subjects"].get(subject) if dist else None
    if sd:
        for ch_title, cnt in sd.get("by_position_chapters", {}).get(str(offset), {}).items():
            if ch_title in weights:
                weights[ch_title] += cnt
    chapter = random.choices(titles, weights=[weights[t] for t in titles], k=1)[0]
    by_title = dict(chapters)
    topics_in_ch = by_title[chapter]

    def pick_in_chapter(ch: str) -> str:
        full_paths = [f"{ch}-{t}" for t in by_title[ch]]
        available = [t for t in full_paths if t not in used] or full_paths
        focus = random.choice(available)
        used.add(focus)
        return focus

    # 2) 知识点：章内均匀抽取，排除本卷已用（全用过则放开）
    foci = [pick_in_chapter(chapter)]

    # 3) 融合题：从其他章节均匀抽一组候选搭配知识点，由 AI 自选融合最自然的
    #    （只硬定主考点；硬点两个可能无法自然融合的知识点会逼出生硬拼凑的题）
    if fusion and len(titles) > 1:
        others = [
            f"{t}-{topic}"
            for t in titles if t != chapter
            for topic in by_title[t]
            if f"{t}-{topic}" not in used
        ]
        foci += random.sample(others, min(5, len(others)))

    # 4) 模板：同知识点 → 同章节 → 全库（融合题只按主考点匹配）
    #    图形题/选项不全的题不适合做文字模板，先排除
    usable_bank = [q for q in question_bank
                   if len(q.get("options", {})) == 4 and not q.get("figure")]
    focus_keys = foci[0].split("-")[1:]

    def matches(q: dict, keys: list[str]) -> bool:
        return any(related(tag, k) for tag in q.get("tags", []) for k in keys)

    candidates = [q for q in usable_bank if matches(q, focus_keys)]
    hist = len(candidates)
    kind = "同考点模板"
    if not candidates:
        ch_keys = [t.split("-")[-1] for t in topics_in_ch] + [chapter]
        candidates = [q for q in usable_bank if matches(q, ch_keys)]
        kind = "同章节模板"
    if not candidates:
        candidates = usable_bank or question_bank
        kind = "全库随机模板"
    template = random.choice(candidates) if candidates else {}

    if fusion:
        info = (f"融合题: 主考点[{foci[0]}] + 候选搭配{len(foci) - 1}个（AI自选最自然的融合）"
                f"| {kind}(候选{len(candidates)}题)")
    else:
        info = (f"章节[{chapter}]（题位结构加权）→ 考点[{'-'.join(foci[0].split('-')[1:])}]"
                f"（章内均匀抽取, 历年考过{hist}次）| {kind}(候选{len(candidates)}题)")
    return foci, template, info, foci[0]


def generate_one_question(
    config: dict,
    outline: dict,
    question_num: int,
    subject_name: str,
    subject_range_str: str,
    question_bank: list[dict],
    year: str,
    used_topics: Optional[set] = None,
    fusion: bool = False,
) -> Optional[dict]:
    """生成一道题，含质检循环，不合格则重试。used_topics 为本卷已用考点集合（跨线程共享）"""

    chapters = build_chapters(outline, subject_name)

    # 本题在科目内的题位（用于查历年该题位的章节结构）
    subj_cfg = get_subject_for_num(question_num, config["paper"]["subjects"])
    offset = question_num - subj_cfg["range"][0] if subj_cfg else 0

    feedback = ""
    for attempt in range(1, MAX_RETRIES + 1):
        # 随机难度
        difficulty = random.choice(["基础", "中档", "拔高"])

        # 章节按题位结构加权、知识点章内均匀抽取（本卷不重复），选相关真题作模板
        sample_topics, template, match_info, focus = choose_focus(
            subject_name, offset, question_bank, chapters, used_topics, fusion=fusion)

        log(f"  [生成] 第{question_num}题 第{attempt}次尝试 ({subject_name}) 难度:{difficulty}"
            + (" [融合题]" if fusion else ""))
        log(f"         {match_info}")
        log(f"         模板真题: {template.get('year', '?')}年第{template.get('number', '?')}题")

        # ── 步骤1: 生成题目 ──
        prompt = build_generate_prompt(
            question_num=question_num,
            subject_name=subject_name,
            subject_range_str=subject_range_str,
            template=template,
            outline_topics=sample_topics,
            difficulty=difficulty,
            feedback=feedback,
            fusion=fusion,
        )

        response = call_ai(config, prompt)
        if not response:
            log(f"  [失败] 第{question_num}题 AI调用失败")
            continue

        result = parse_ai_response(response, question_num, subject_name, year)
        if not result:
            log(f"  [解析失败] 第{question_num}题 格式不符，重试...")
            feedback = "输出格式不符合要求，请严格按照给定的输出格式作答"
            continue

        result["_meta"]["template_id"] = template.get("id", "")
        result["_meta"]["attempts"] = attempt
        result["_meta"]["focus"] = focus
        result["_meta"]["fusion"] = fusion

        # ── 步骤2: 质检 ──
        log(f"  [质检] 第{question_num}题 校验中...")
        validate_prompt = build_validate_prompt(result, subject_range_str,
                                                template=template, fusion=fusion)
        validate_response = call_ai(config, validate_prompt, temperature=0.3)

        if not validate_response:
            log(f"  [质检异常] 第{question_num}题 质检AI调用失败，保留题目但标记为未验证（建议人工复核）")
            result["_meta"]["validated"] = False
            return result

        validation = parse_validation_response(validate_response)

        if validation["verdict"] == "合格":
            result["_meta"]["validated"] = True
            log(f"  [合格] 第{question_num}题 答案:{result['answer']} 考点:{result['tags']} (第{attempt}次)")
            return result
        else:
            log(f"  [不合格] 第{question_num}题 问题:{validation['issues']} 建议:{validation['suggestion']}")
            feedback = f"不合格项：{validation['issues']}；整改建议：{validation['suggestion']}"
            if attempt < MAX_RETRIES:
                log(f"           → 重新生成...")
            continue

    log(f"  [放弃] 第{question_num}题 已达最大重试次数({MAX_RETRIES})")
    return None


# ============================================================
# 主流程
# ============================================================

def get_subject_for_num(num: int, subjects_config: list[dict]) -> Optional[dict]:
    """根据题号确定所属科目"""
    for s in subjects_config:
        if s["range"][0] <= num <= s["range"][1]:
            return s
    return None


def save_incremental(json_path: str, results: dict, total: int):
    """按题号顺序保存到JSON（并发完成顺序无关，始终按题号排序落盘）"""
    ordered = [results[i] for i in sorted(results.keys())]
    data = {
        "total": total,
        "generated": len(ordered),
        "questions": ordered,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_latest_unfinished(total: int) -> Optional[str]:
    """找最近修改的未完成试卷目录名"""
    candidates = []
    if not os.path.exists(GENERATED_DIR):
        return None
    for name in os.listdir(GENERATED_DIR):
        q_path = os.path.join(GENERATED_DIR, name, "questions.json")
        if os.path.exists(q_path):
            with open(q_path, encoding="utf-8") as f:
                d = json.load(f)
            if d["generated"] < d["total"]:
                candidates.append((os.path.getmtime(q_path), name))
    return max(candidates)[1] if candidates else None


def generate_batch(config: dict, paper_name: Optional[str], count: Optional[int]) -> str:
    """
    批量生成：为指定（或最近未完成/新建）的试卷生成接下来的 count 道缺失题。
    count=None 表示生成全部剩余。返回试卷目录名。
    """
    outline = load_outline()
    paper_config = config["paper"]
    total = paper_config["total_questions"]
    max_concurrent = config.get("max_concurrent", 3)
    year = str(time.localtime().tm_year)

    if paper_name is None:
        paper_name = find_latest_unfinished(total)
    if paper_name is None:
        paper_name = f"408模拟_{year}_{int(time.time())}"

    paper_dir = os.path.join(GENERATED_DIR, paper_name)
    os.makedirs(paper_dir, exist_ok=True)
    init_log(paper_dir)
    json_path = os.path.join(paper_dir, "questions.json")

    # 断点续传：加载已有结果（key=题号，保证顺序）
    results: dict[int, dict] = {}
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            for q in json.load(f).get("questions", []):
                results[int(q["number"])] = q

    missing = [n for n in range(1, total + 1) if n not in results]
    if not missing:
        log(f"试卷 {paper_name} 已全部生成完毕 ({total}/{total})")
        log(f"下一步: python3 src/build_paper.py {paper_name}")
        return paper_name

    batch = missing if count is None else missing[:count]
    banks = {s["name"]: load_question_bank(s["name"]) for s in paper_config["subjects"]}
    subject_range_str = get_subject_range_str(paper_config["subjects"])

    # 本卷已用考点集合（跨线程共享），保证整卷知识点不重复
    used_topics: set = set()
    for q in results.values():
        focus = q.get("_meta", {}).get("focus")
        if focus:
            used_topics.add(focus)

    # 融合题槽位：每科目 1~2 道科目内知识点融合题。
    # 以试卷名为随机种子 → 同一张卷多次续跑，融合槽位保持一致
    rng = random.Random(paper_name)
    fusion_slots: set[int] = set()
    for s in paper_config["subjects"]:
        lo, hi = s["range"]
        k = rng.randint(1, 2)
        fusion_slots.update(rng.sample(range(lo, hi + 1), min(k, hi - lo + 1)))

    log(f"试卷: {paper_name} | 已有 {len(results)}/{total} 题")
    log(f"本卷融合题槽位: {sorted(fusion_slots)}")
    log(f"本批生成: 第 {batch} 题 | 并发:{min(max_concurrent, len(batch))} "
        f"| 模型:{config['model']} | 超时:{config.get('request_timeout', 300)}s")

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        future_map = {}
        for num in batch:
            subj = get_subject_for_num(num, paper_config["subjects"])
            if not subj:
                continue
            future = executor.submit(
                generate_one_question,
                config=config,
                outline=outline,
                question_num=num,
                subject_name=subj["name"],
                subject_range_str=subject_range_str,
                question_bank=banks[subj["name"]],
                year=year,
                used_topics=used_topics,
                fusion=num in fusion_slots,
            )
            future_map[future] = num

        for future in as_completed(future_map):
            num = future_map[future]
            try:
                result = future.result()
                if result:
                    results[num] = result
                    # 每完成一题就保存（断点续传）
                    save_incremental(json_path, results, total)
            except Exception as e:
                log(f"  [异常] 第{num}题: {e}")

    save_incremental(json_path, results, total)
    remaining = total - len(results)
    log(f"{'=' * 50}")
    log(f"本批完成! 进度 {len(results)}/{total}, JSON: {json_path}")
    if remaining:
        log(f"剩余 {remaining} 题, 继续: python3 src/generate.py")
    else:
        log(f"整卷完成! 下一步: python3 src/build_paper.py {paper_name}")
    return paper_name


# ============================================================
# 入口
# ============================================================

def print_status():
    if not os.path.exists(GENERATED_DIR):
        print("暂无生成记录")
        return
    found = False
    for name in sorted(os.listdir(GENERATED_DIR)):
        q_path = os.path.join(GENERATED_DIR, name, "questions.json")
        if os.path.exists(q_path):
            with open(q_path, encoding="utf-8") as f:
                d = json.load(f)
            print(f"{name}: {d['generated']}/{d['total']} 题")
            found = True
    if not found:
        print("暂无生成记录")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="408模拟题生成器：默认每次生成一小批（batch_size）题，反复运行直到整卷完成")
    parser.add_argument("--status", action="store_true", help="列出各试卷生成进度")
    parser.add_argument("--new", action="store_true", help="新建试卷（默认续写最近未完成的试卷）")
    parser.add_argument("--paper", help="指定要续写的试卷目录名")
    parser.add_argument("--count", type=int, help="本次生成的题数（默认取 config 的 batch_size=3）")
    parser.add_argument("--all", action="store_true", help="一次性生成全部剩余题目")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    config = load_config()
    if args.all:
        count = None
    elif args.count:
        count = args.count
    else:
        count = config.get("batch_size", 3)

    paper_name = None if args.new else args.paper
    if args.new and args.paper:
        parser.error("--new 与 --paper 不能同时使用")

    generate_batch(config, paper_name, count)


if __name__ == "__main__":
    main()
