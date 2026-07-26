#!/usr/bin/env python3
"""
试卷组装与PDF生成
读取生成的题目JSON，用 fpdf2 生成试卷PDF和答案解析PDF
"""

import json
import os
import re
import shutil
import sys

try:
    from fpdf import FPDF
except ImportError:
    sys.exit("缺少依赖 fpdf2，请先安装: pip install --user fpdf2")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED_DIR = os.path.join(PROJECT_ROOT, "generated")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")

# ============================================================
# 字体配置（在常见路径中查找 Noto Sans CJK）
# ============================================================

FONT_DIRS = [
    "/usr/share/fonts/noto-cjk",
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/truetype/noto-cjk",
]


def find_font(filename: str, fallback: str = "") -> str:
    for name in [filename, fallback] if fallback else [filename]:
        for d in FONT_DIRS:
            path = os.path.join(d, name)
            if os.path.exists(path):
                return path
    sys.exit(f"找不到字体 {filename}，请安装 Noto Sans CJK（查找路径: {', '.join(FONT_DIRS)}）")

# ============================================================
# PDF 生成类
# ============================================================

class ExamPDF(FPDF):
    """408试卷PDF生成器"""

    def __init__(self, title: str = "", subtitle: str = ""):
        super().__init__()
        self.title_text = title
        self.subtitle_text = subtitle
        self.set_auto_page_break(auto=True, margin=25)

        # 注册中文字体
        self.add_font("SC", "", find_font("NotoSansCJK-Regular.ttc"))
        self.add_font("SC", "B", find_font("NotoSansCJK-Bold.ttc"))
        # Light 字重部分发行版不带（如 Ubuntu 的 fonts-noto-cjk），降级用 Regular
        self.add_font("SC_LIGHT", "", find_font("NotoSansCJK-Light.ttc",
                                                fallback="NotoSansCJK-Regular.ttc"))

    def header(self):
        if self.page_no() == 1:
            return  # 首页用自定义标题
        self.set_font("SC_LIGHT", "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, f"{self.title_text}  |  {self.subtitle_text}", align="R")
        self.ln(4)
        # 细线
        self.set_draw_color(200, 200, 200)
        self.line(20, self.get_y(), self.w - 20, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-20)
        self.set_font("SC_LIGHT", "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"第 {self.page_no()} 页", align="C")

    def add_title_page(self, paper_config: dict):
        """添加封面页（内容由 config/settings.json 的 paper 配置驱动）"""
        self.add_page()
        self.ln(40)

        # 主标题
        self.set_font("SC", "B", 28)
        self.set_text_color(0, 0, 0)
        self.cell(0, 15, "考研408模拟试卷", align="C")
        self.ln(18)

        # 副标题
        self.set_font("SC", "", 16)
        self.set_text_color(80, 80, 80)
        self.cell(0, 10, "计算机学科专业基础综合", align="C")
        self.ln(25)

        # 信息区
        self.set_font("SC", "", 12)
        self.set_text_color(60, 60, 60)
        info_lines = [
            f"满分：{paper_config['total_score']} 分      "
            f"考试时间：{paper_config['duration_minutes']} 分钟",
            "答题方式：闭卷笔试",
            f"题型：单项选择题（共 {paper_config['total_questions']} 题）",
        ]
        for line in info_lines:
            self.cell(0, 8, line, align="C")
            self.ln(8)

        self.ln(15)

        # 注意事项
        self.set_font("SC", "B", 12)
        self.set_text_color(0, 0, 0)
        self.cell(0, 8, "注意事项：")
        self.ln(10)

        self.set_font("SC", "", 10)
        self.set_text_color(50, 50, 50)
        notes = ["1. 各科目题号与分值分布："]
        for s in paper_config["subjects"]:
            notes.append(
                f"   {s['name']}：第 {s['range'][0]}～{s['range'][1]} 题（{s['score']} 分）"
            )
        notes.append("2. 请将答案填写在答题纸上，写在试卷上无效。")
        for note in notes:
            self.cell(0, 7, note)
            self.ln(7)

    def add_subject_header(self, name: str, score: int):
        """添加科目分隔标题"""
        self.ln(5)
        self.set_fill_color(240, 240, 240)
        self.set_font("SC", "B", 13)
        self.set_text_color(30, 30, 30)
        self.cell(0, 10, f"  {name}（{score}分）", fill=True)
        self.ln(10)

    def add_question(self, q: dict, show_answer: bool = False):
        """添加一道题"""
        num = q["number"]
        stem = q.get("question_text", "")
        options = q.get("options", {})
        answer = q.get("answer", "")
        tags = q.get("tags", [])
        explanation = q.get("explanation", "")

        # 检查页面空间（至少需要 60mm）
        if self.get_y() > self.h - 60:
            self.add_page()

        # 题号 + 题干
        self.set_font("SC", "B", 11)
        self.set_text_color(0, 0, 0)
        num_text = f"{num}、"

        # 先写题号
        x_start = self.get_x()
        self.cell(self.get_string_width(num_text) + 1, 7, num_text)
        num_end_x = self.get_x()

        # 写题干（可能多行；左对齐，避免两端对齐拉宽空格）
        self.set_font("SC", "", 11)
        self.multi_cell(self.w - self.r_margin - num_end_x + 5, 7, stem, align="L")

        # 知识点标签（灰色小字）
        if tags and not show_answer:
            self.set_font("SC_LIGHT", "", 8)
            self.set_text_color(160, 160, 160)
            self.cell(0, 5, f"  [{' / '.join(tags)}]")
            self.ln(6)

        # 选项（multi_cell 支持长选项自动换行）
        self.set_font("SC", "", 11)
        self.set_text_color(0, 0, 0)
        for key in ["A", "B", "C", "D"]:
            opt = options.get(key, "")
            self.set_x(20)
            if show_answer and key == answer:
                # 正确答案标红加粗
                self.set_font("SC", "B", 11)
                self.set_text_color(200, 0, 0)
                self.multi_cell(0, 7, f"{key}. {opt}  ✓", align="L")
                self.set_font("SC", "", 11)
                self.set_text_color(0, 0, 0)
            else:
                self.multi_cell(0, 7, f"{key}. {opt}", align="L")

        # 答案与解析（仅在答案卷显示）
        if show_answer:
            self.ln(2)
            self.set_fill_color(248, 248, 248)
            self.set_font("SC", "B", 10)
            self.set_text_color(30, 30, 30)

            # 解析框
            x0 = self.get_x() + 5
            y0 = self.get_y()
            self.set_x(x0)

            self.set_font("SC", "B", 10)
            self.cell(0, 6, f"【答案】{answer}")
            self.ln(6)

            if tags:
                self.set_x(x0)
                self.set_font("SC", "", 9)
                self.set_text_color(80, 80, 80)
                self.cell(0, 6, f"【考点】{' / '.join(tags)}")
                self.ln(6)

            # 母题溯源（AI生成题的风格模板来自哪道真题）
            template_src = q.get("_template_src", "")
            if template_src:
                self.set_x(x0)
                self.set_font("SC_LIGHT", "", 9)
                self.set_text_color(130, 130, 130)
                self.cell(0, 6, f"【母题】{template_src}")
                self.ln(6)

            if explanation:
                self.set_x(x0)
                self.set_font("SC", "", 9)
                self.set_text_color(60, 60, 60)
                self.multi_cell(self.w - self.r_margin - x0, 6, f"【解析】{explanation}", align="L")

            self.ln(5)

        self.ln(4)


# Noto Sans CJK 缺少上/下标数字字形，转写为 ^n / n 形式
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-")

# 常见 LaTeX 命令 → 普通字符（去掉 $ 定界符后可能残留）
_LATEX_REPLACEMENTS = [
    (r'\{', '{'), (r'\}', '}'), (r'\%', '%'), (r'\_', '_'), (r'\&', '&'),
    (r'\times', '×'), (r'\cdot', '·'), (r'\div', '÷'), (r'\pm', '±'),
    (r'\le', '≤'), (r'\leq', '≤'), (r'\ge', '≥'), (r'\geq', '≥'),
    (r'\neq', '≠'), (r'\approx', '≈'), (r'\infty', '∞'),
    (r'\rightarrow', '→'), (r'\to', '→'), (r'\leftarrow', '←'),
]

# 字体缺字形的符号 → 等价可渲染字符
_GLYPH_FIXES = {
    "​": "",   # 零宽空格
    "✅": "√",
    "❌": "×",
    "✗": "×",
}


def strip_latex_math(text: str) -> str:
    """去除LaTeX数学标记，保留内容文本"""
    if not text:
        return ""
    # $$...$$ → 内容
    text = re.sub(r'\$\$(.*?)\$\$', r'\1', text, flags=re.DOTALL)
    # $...$ → 内容
    text = re.sub(r'\$(.*?)\$', r'\1', text)
    # \(...\) 与 \[...\] → 内容
    text = re.sub(r'\\\((.*?)\\\)', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.*?)\\\]', r'\1', text, flags=re.DOTALL)
    # LaTeX 命令替换（长命令优先，避免 \le 吃掉 \leq 的前缀）
    for cmd, ch in sorted(_LATEX_REPLACEMENTS, key=lambda x: -len(x[0])):
        text = text.replace(cmd, ch)
    # 上标串 → ^串（如 10⁷ → 10^7，2⁻⁶ → 2^-6）
    text = re.sub(
        r'[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+',
        lambda m: '^' + m.group(0).translate(_SUPERSCRIPTS),
        text,
    )
    # 下标串直接转为普通数字
    text = text.translate(_SUBSCRIPTS)
    # 字体缺字形的符号
    for bad, good in _GLYPH_FIXES.items():
        text = text.replace(bad, good)
    return text


# 需要保留换行的行首标记（罗马数字/带圈数字/编号列表）
_LIST_MARKER = re.compile(r'^(?:[ⅠⅡⅢⅣⅤ①②③④⑤⑥⑦⑧⑨]|(?:IX|IV|V?I{1,3})\s*[.、．]|[0-9]+\s*[.、．）)])')


def merge_soft_newlines(text: str) -> str:
    """
    合并题干中的软换行：真题HTML里行内公式常被拆成独立行，
    除列表项外把断行重新拼回一句（英文单词间补空格）
    """
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return ""
    out = lines[0]
    for ln in lines[1:]:
        if _LIST_MARKER.match(ln):
            out += "\n" + ln
        else:
            sep = " " if (out[-1].isascii() and out[-1].isalnum()
                          and ln[0].isascii() and ln[0].isalnum()) else ""
            out += sep + ln
    return out


def normalize_question(q: dict) -> dict:
    """返回清理过 LaTeX 标记、修复过换行的题目副本"""
    q = dict(q)
    q["question_text"] = merge_soft_newlines(strip_latex_math(q.get("question_text", "")))
    q["options"] = {k: merge_soft_newlines(strip_latex_math(v))
                    for k, v in q.get("options", {}).items()}
    q["explanation"] = strip_latex_math(q.get("explanation", ""))
    return q


def build_question_pdf(questions: list[dict], paper_config: dict, output_path: str):
    """生成试卷PDF（不含答案）"""
    subjects = paper_config["subjects"]

    pdf = ExamPDF(title="考研408模拟试卷", subtitle="计算机学科专业基础综合")
    pdf.add_title_page(paper_config)

    # 正文开始
    pdf.add_page()
    current_subject = None

    for q in questions:
        q = normalize_question(q)
        q_subject = q.get("subject", "")

        if q_subject != current_subject:
            current_subject = q_subject
            score = next((s["score"] for s in subjects if s["name"] == q_subject), 0)
            pdf.add_subject_header(q_subject, score)

        pdf.add_question(q, show_answer=False)

    pdf.output(output_path)
    print(f"  试卷PDF: {output_path} ({os.path.getsize(output_path) // 1024}KB)")


def load_template_index(paper_config: dict) -> dict:
    """题库 id → '某年第几题（科目）'，用于答案卷标注母题溯源"""
    index = {}
    for s in paper_config["subjects"]:
        path = os.path.join(PROJECT_ROOT, "data", f"{s['name']}.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for q in json.load(f)["questions"]:
                index[q["id"]] = f"{q['year']} 年第 {q['number']} 题（{s['name']}真题）"
    return index


def build_answer_pdf(questions: list[dict], paper_config: dict, output_path: str):
    """生成答案解析PDF"""
    subjects = paper_config["subjects"]
    template_index = load_template_index(paper_config)

    pdf = ExamPDF(title="考研408模拟试卷", subtitle="答案与解析")
    pdf.add_page()

    # 标题
    pdf.set_font("SC", "B", 20)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 12, "答案与解析", align="C")
    pdf.ln(18)

    # ── 答案速查表 ──
    pdf.set_font("SC", "B", 12)
    pdf.cell(0, 8, "答案速查")
    pdf.ln(10)

    answer_map = {q["number"]: q.get("answer", "") for q in questions}

    # 表头
    pdf.set_font("SC", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    col_w = 13
    row_h = 7

    total = paper_config["total_questions"]
    for start in range(1, total + 1, 10):
        end = min(start + 9, total)

        # 题号行
        pdf.set_x(20)
        pdf.cell(col_w, row_h, "题号", border=1, fill=True, align="C")
        for n in range(start, end + 1):
            pdf.cell(col_w, row_h, str(n), border=1, align="C")
        pdf.ln(row_h)

        # 答案行
        pdf.set_x(20)
        pdf.cell(col_w, row_h, "答案", border=1, fill=True, align="C")
        for n in range(start, end + 1):
            ans = answer_map.get(str(n), "")
            pdf.set_font("SC", "B", 9)
            pdf.cell(col_w, row_h, ans, border=1, align="C")
            pdf.set_font("SC", "", 9)
        pdf.ln(row_h + 3)

    pdf.ln(8)

    # ── 详细解析 ──
    pdf.set_font("SC", "B", 14)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, "详细解析")
    pdf.ln(12)

    current_subject = None
    for q in questions:
        q = normalize_question(q)
        q_subject = q.get("subject", "")

        # 母题溯源
        template_id = q.get("_meta", {}).get("template_id", "")
        if template_id and template_id in template_index:
            q["_template_src"] = template_index[template_id]

        if q_subject != current_subject:
            current_subject = q_subject
            pdf.set_font("SC", "B", 12)
            pdf.set_text_color(30, 30, 30)
            pdf.cell(0, 8, f"▶ {q_subject}")
            pdf.ln(10)

        pdf.add_question(q, show_answer=True)

    pdf.output(output_path)
    print(f"  答案PDF: {output_path} ({os.path.getsize(output_path) // 1024}KB)")


# ============================================================
# 主流程
# ============================================================

def build_paper(paper_dir_name: str):
    """组装试卷并生成PDF"""
    paper_dir = os.path.join(GENERATED_DIR, paper_dir_name)
    questions_path = os.path.join(paper_dir, "questions.json")

    if not os.path.exists(questions_path):
        print(f"错误: 找不到 {questions_path}")
        return

    with open(questions_path, encoding="utf-8") as f:
        data = json.load(f)

    questions = data["questions"]
    print(f"加载 {len(questions)}/{data['total']} 道题目")

    if not questions:
        print("没有题目，退出")
        return

    # 加载试卷配置
    config_path = os.path.join(CONFIG_DIR, "settings.json")
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    paper_config = config["paper"]

    # 输出路径
    q_pdf_path = os.path.join(paper_dir, "试卷.pdf")
    a_pdf_path = os.path.join(paper_dir, "答案解析.pdf")

    print("\n生成试卷PDF...")
    build_question_pdf(questions, paper_config, q_pdf_path)

    print("生成答案解析PDF...")
    build_answer_pdf(questions, paper_config, a_pdf_path)

    # 复制到 output 目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if os.path.exists(q_pdf_path):
        shutil.copy2(q_pdf_path, os.path.join(OUTPUT_DIR, f"{paper_dir_name}_试卷.pdf"))
    if os.path.exists(a_pdf_path):
        shutil.copy2(a_pdf_path, os.path.join(OUTPUT_DIR, f"{paper_dir_name}_答案解析.pdf"))

    print(f"\n{'='*50}")
    print(f"完成!")
    print(f"  试卷: {q_pdf_path}")
    print(f"  答案: {a_pdf_path}")
    if os.path.exists(os.path.join(OUTPUT_DIR, f"{paper_dir_name}_试卷.pdf")):
        print(f"  (已复制到 output/ 目录)")
    print(f"{'='*50}")


def main():
    if len(sys.argv) < 2:
        print("用法: python3 src/build_paper.py <paper_dir_name>")
        print("\n已生成的试卷:")
        if os.path.exists(GENERATED_DIR):
            for name in sorted(os.listdir(GENERATED_DIR)):
                q_path = os.path.join(GENERATED_DIR, name, "questions.json")
                if os.path.exists(q_path):
                    with open(q_path, encoding="utf-8") as f:
                        d = json.load(f)
                    print(f"  {name}: {d['generated']}/{d['total']} 题")
        return

    paper_name = sys.argv[1]
    build_paper(paper_name)


if __name__ == "__main__":
    main()
