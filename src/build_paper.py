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
# 字体配置（从 TTC 中提取简体中文 SC 字体为独立 TTF）
# ============================================================

FONT_DIRS = [
    "/usr/share/fonts/noto-cjk",
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/truetype/noto-cjk",
]

# 从 TTC 集合中提取简体中文 (SC) 字体的索引
_SC_INDEX = 2  # NotoSansCJK-Regular.ttc: 0=JP, 1=KR, 2=SC


def _extract_sc_font(ttc_path: str, out_dir: str) -> str:
    """从 TTC 文件提取简体中文 (SC) 字体为独立 TTF"""
    from fontTools.ttLib import TTCollection
    stem = os.path.splitext(os.path.basename(ttc_path))[0]
    out_path = os.path.join(out_dir, f"{stem}-SC.ttf")
    if os.path.exists(out_path):
        return out_path
    ttc = TTCollection(ttc_path)
    font = ttc.fonts[_SC_INDEX]
    font.save(out_path)
    return out_path


def _ensure_sc_fonts() -> str:
    """确保提取后的 SC 字体目录存在，返回该目录路径"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sc_dir = os.path.join(project_root, "resources", "fonts")
    os.makedirs(sc_dir, exist_ok=True)

    # 检查是否已经提取过
    needed = ["NotoSansCJK-Regular-SC.ttf", "NotoSansCJK-Bold-SC.ttf"]
    if all(os.path.exists(os.path.join(sc_dir, f)) for f in needed):
        return sc_dir

    # 从系统 TTC 中提取
    for ttc_name in ["NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc"]:
        for d in FONT_DIRS:
            ttc_path = os.path.join(d, ttc_name)
            if os.path.exists(ttc_path):
                _extract_sc_font(ttc_path, sc_dir)
                break
        else:
            # 找不到 TTC，尝试直接查找单体 TTF
            pass

    # Light 字重没有独立 Bold，从 Regular 提取即可
    light_regular = os.path.join(sc_dir, "NotoSansCJK-Regular-SC.ttf")
    light_out = os.path.join(sc_dir, "NotoSansCJK-Light-SC.ttf")
    if not os.path.exists(light_out) and os.path.exists(light_regular):
        shutil.copy2(light_regular, light_out)

    return sc_dir


def find_font(filename: str, fallback: str = "") -> str:
    """查找字体文件：优先查找已提取的 SC TTF，再查系统 TTC"""
    sc_dir = _ensure_sc_fonts()

    # 先在提取目录中查找
    for name in [filename, fallback] if fallback else [filename]:
        # 替换为 SC 版本
        sc_name = name.replace(".ttc", "-SC.ttf")
        path = os.path.join(sc_dir, sc_name)
        if os.path.exists(path):
            return path
        # 直接查找原文件名（可能是已提取的 TTF）
        path = os.path.join(sc_dir, name)
        if os.path.exists(path):
            return path

    # 回退到系统字体目录
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

    def _write_mixed(self, text: str, font_size: int = 11, line_height: int = 7,
                     align: str = "L", x0: float | None = None):
        """
        写入混合了文本和数学公式的段落。
        用 matplotlib 渲染 LaTeX 公式为内联图片，其余用 cell/multi_cell 写文本。
        """
        segments = _split_text_and_math(text)
        has_math = any(is_math for _, is_math in segments)

        # 没有数学公式，直接用 multi_cell
        if not has_math:
            if x0 is not None:
                self.set_x(x0)
            self.multi_cell(0, line_height, text, align=align)
            return

        # 有数学公式：逐段处理
        self.set_font("SC", "", font_size)
        for content, is_math in segments:
            if not content:
                continue
            if is_math:
                # 尝试渲染为图片
                img_data = render_math_image(content, fontsize=font_size + 2)
                if img_data:
                    from io import BytesIO
                    buf = BytesIO(img_data)
                    img_h = line_height + 2  # 图片高度 mm
                    # 根据图片实际宽高比计算宽度
                    from PIL import Image
                    import io
                    pil_img = Image.open(io.BytesIO(img_data))
                    aspect = pil_img.width / pil_img.height
                    img_w = img_h * aspect  # 按比例缩放

                    # 检查行尾空间
                    cur_x = self.get_x()
                    if cur_x + img_w > self.w - self.r_margin:
                        self.ln(line_height)
                        if x0 is not None:
                            self.set_x(x0)

                    y_before = self.get_y()
                    self.image(buf, x=self.get_x(), y=y_before, w=img_w, h=img_h)
                    self.set_xy(self.get_x() + img_w, y_before)
                else:
                    # 渲染失败，fallback 到 Unicode 文本
                    converted = strip_latex_math(f"${content}$")
                    self.cell(self.get_string_width(converted) + 1, line_height, converted)
            else:
                # 纯文本段落：按行处理，保留换行
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    line = line.strip()
                    if not line:
                        continue
                    # 检查行尾空间
                    w = self.get_string_width(line)
                    avail = self.w - self.r_margin - self.get_x()
                    if w > avail and self.get_x() > (x0 or self.l_margin):
                        self.ln(line_height)
                        if x0 is not None:
                            self.set_x(x0)
                    if x0 is not None and self.get_x() < x0:
                        self.set_x(x0)
                    # 用实际文本宽度，不用 0（0 会占满整行）
                    self.cell(w + 1, line_height, line)
                    if i < len(lines) - 1:
                        self.ln(line_height)
                        if x0 is not None:
                            self.set_x(x0)

        # 结尾换行
        self.ln(line_height)

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
        self._write_mixed(stem, font_size=11, line_height=7,
                          x0=num_end_x)

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
            prefix = f"{key}. "
            if show_answer and key == answer:
                # 正确答案标红加粗
                self.set_font("SC", "B", 11)
                self.set_text_color(200, 0, 0)
                self._write_mixed(prefix + opt + "  ✓", font_size=11,
                                  line_height=7, x0=20)
                self.set_font("SC", "", 11)
                self.set_text_color(0, 0, 0)
            else:
                self._write_mixed(prefix + opt, font_size=11,
                                  line_height=7, x0=20)

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
                self._write_mixed(f"【解析】{explanation}", font_size=9,
                                  line_height=6, x0=x0)

            self.ln(5)

        self.ln(4)


# ============================================================
# 数学公式渲染（matplotlib mathtext）
# ============================================================

# matplotlib 能渲染的 LaTeX 子集：\frac, \sqrt, \sum, \int, \alpha, 上下标等
# 不能渲染的：\begin{matrix} 环境、\text{}、\over 等复杂命令
# 对于不支持的公式，fallback 到 Unicode 文本

def _init_matplotlib():
    """初始化 matplotlib（延迟导入，CI 可能没装）"""
    try:
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib import rcParams
        rcParams['mathtext.fontset'] = 'cm'  # Computer Modern，最接近 LaTeX
        rcParams['axes.unicode_minus'] = False
        return True
    except ImportError:
        return False

_HAS_MATPLOTLIB = None  # 延迟检测


def _can_render_math(latex: str) -> bool:
    """检测 LaTeX 表达式是否能被 matplotlib mathtext 渲染"""
    # 不支持的环境命令
    if re.search(r'\\begin\{', latex) or re.search(r'\\end\{', latex):
        return False
    # 不支持 \text, \over, \displaystyle 等
    if re.search(r'\\(?:text|over|displaystyle|style|frac.*frac.*frac)', latex):
        return False
    # 太长的表达式（超过80字符）渲染效果差
    if len(latex) > 80:
        return False
    return True


def render_math_image(latex: str, fontsize: int = 14, dpi: int = 150) -> bytes | None:
    """
    用 matplotlib 把 LaTeX 数学公式渲染为 PNG 图片，返回 bytes。
    渲染失败返回 None。
    """
    global _HAS_MATPLOTLIB
    if _HAS_MATPLOTLIB is None:
        _HAS_MATPLOTLIB = _init_matplotlib()
    if not _HAS_MATPLOTLIB:
        return None
    if not _can_render_math(latex):
        return None

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from io import BytesIO

        # 预处理：去掉 $ 定界符
        clean = latex.strip().strip('$')
        if not clean:
            return None

        # 计算合适的图片尺寸
        fig_w = max(1.0, len(clean) * 0.12)
        fig, ax = plt.subplots(figsize=(fig_w, 0.5))
        ax.text(0.5, 0.5, f'${clean}$', fontsize=fontsize,
                ha='center', va='center', transform=ax.transAxes)
        ax.axis('off')

        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight',
                    transparent=True, pad_inches=0.03)
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


def _split_text_and_math(text: str) -> list[tuple[str, bool]]:
    """
    把文本拆分为 [(内容, is_math), ...] 段落。
    is_math=True 表示这是 LaTeX 数学公式。
    """
    segments = []
    last_end = 0
    # 匹配 $$...$$ 和 $...$
    for m in re.finditer(r'\$\$(.*?)\$\$|\$(.*?)\$', text, re.DOTALL):
        # 公式前的普通文本
        if m.start() > last_end:
            segments.append((text[last_end:m.start()], False))
        formula = m.group(1) if m.group(1) is not None else m.group(2)
        segments.append((formula, True))
        last_end = m.end()
    # 剩余文本
    if last_end < len(text):
        segments.append((text[last_end:], False))
    return segments if segments else [(text, False)]


# Noto Sans CJK 缺少上/下标数字字形，转写为 ^n / n 形式
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-")

# ── LaTeX → 可读 Unicode 文本 ──

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "lambda": "λ",
    "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}

# 顺序敏感：长命令在前，避免 \le 吃掉 \leq
_MATH_SYMBOLS = [
    (r'\leqslant', '≤'), (r'\geqslant', '≥'), (r'\leq', '≤'), (r'\geq', '≥'),
    (r'\le', '≤'), (r'\ge', '≥'), (r'\neq', '≠'), (r'\ne', '≠'),
    (r'\approx', '≈'), (r'\equiv', '≡'), (r'\sim', '~'),
    (r'\times', '×'), (r'\cdot', '·'), (r'\div', '÷'), (r'\pm', '±'), (r'\mp', '∓'),
    (r'\infty', '∞'), (r'\propto', '∝'),
    (r'\notin', '∉'), (r'\in', '∈'), (r'\subseteq', '⊆'), (r'\subset', '⊂'),
    (r'\supseteq', '⊇'), (r'\supset', '⊃'), (r'\cup', '∪'), (r'\cap', '∩'),
    (r'\emptyset', '∅'), (r'\varnothing', '∅'), (r'\forall', '∀'), (r'\exists', '∃'),
    (r'\Rightarrow', '⇒'), (r'\Leftarrow', '⇐'), (r'\Leftrightarrow', '⇔'),
    (r'\leftrightarrow', '↔'), (r'\rightarrow', '→'), (r'\leftarrow', '←'),
    (r'\longrightarrow', '→'), (r'\mapsto', '→'), (r'\to', '→'), (r'\gets', '←'),
    (r'\langle', '〈'), (r'\rangle', '〉'),
    (r'\lfloor', '⌊'), (r'\rfloor', '⌋'), (r'\lceil', '⌈'), (r'\rceil', '⌉'),
    (r'\sum', 'Σ'), (r'\prod', 'Π'),
    (r'\ldots', '…'), (r'\cdots', '…'), (r'\dots', '…'), (r'\vdots', '⋮'),
    (r'\wedge', '∧'), (r'\vee', '∨'), (r'\neg', '¬'), (r'\oplus', '⊕'),
    (r'\bmod', ' mod '), (r'\pmod', ' mod '), (r'\mid', '|'), (r'\|', '‖'),
    (r'\%', '%'), (r'\&', '&'), (r'\#', '#'), (r'\_', '_'),
    (r'\quad', ' '), (r'\qquad', '  '), (r'\,', ' '), (r'\;', ' '), (r'\!', ''),
    ('\\ ', ' '),
]


def _greek_word(word: str) -> str:
    """希腊字母命令还原；处理 \\mu\\text{s} 剥壳后粘连成 mus 的情况"""
    if word in _GREEK:
        return _GREEK[word]
    for name in sorted(_GREEK, key=len, reverse=True):
        if word.startswith(name):
            return _GREEK[name] + word[len(name):]
    return word

_SIMPLE_TOKEN = re.compile(r'[0-9A-Za-zα-ωΑ-Ω+\-|=.…]+')


def _wrap(s: str) -> str:
    """复合表达式加括号，简单表达式原样"""
    s = s.strip()
    return s if _SIMPLE_TOKEN.fullmatch(s) else f'({s})'


def _convert_math(s: str) -> str:
    """把一段 LaTeX 数学内容转成可读的 Unicode 文本"""
    # 字面大括号先保护
    s = s.replace(r'\{', '\x01').replace(r'\}', '\x02')

    # 矩阵环境 → [行1; 行2; ...]
    def matrix_repl(m):
        rows = [' '.join(c.strip() for c in row.split('&'))
                for row in re.split(r'\\\\', m.group(1)) if row.strip()]
        return '[' + '; '.join(rows) + ']'
    s = re.sub(r'\\begin\{[bpvB]?matrix\}(.*?)\\end\{[bpvB]?matrix\}',
               matrix_repl, s, flags=re.DOTALL)

    # 文本类宏剥壳（处理嵌套，最多3层）
    for _ in range(3):
        s = re.sub(r'\\(?:text|textbf|textit|mathrm|mathbf|mathit|mathsf|mathcal|mathbb|operatorname)\{([^{}]*)\}',
                   r'\1', s)

    s = re.sub(r'\\xrightarrow\{([^{}]*)\}', r' --\1--> ', s)
    s = re.sub(r'\\xleftarrow\{([^{}]*)\}', r' <--\1-- ', s)
    s = re.sub(r'\\binom\{([^{}]*)\}\{([^{}]*)\}', r'C(\1,\2)', s)

    # 分数 → a/b（由内到外反复处理嵌套）
    def frac_repl(m):
        return f'{_wrap(m.group(1))}/{_wrap(m.group(2))}'
    for _ in range(4):
        s2 = re.sub(r'\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}', frac_repl, s)
        if s2 == s:
            break
        s = s2

    s = re.sub(r'\\sqrt\{([^{}]*)\}', lambda m: '√' + _wrap(m.group(1)), s)

    # 符号与希腊字母
    for cmd, ch in _MATH_SYMBOLS:
        s = s.replace(cmd, ch)
    s = re.sub(r'\\([A-Za-z]+)', lambda m: _greek_word(m.group(1)), s)

    # 带上下限的求和/连乘：Σ_{a}^{b} X → Σ(a..b)
    s = re.sub(r'([ΣΠ])\s*_\{([^{}]*)\}\s*\^\{([^{}]*)\}', r'\1(\2..\3)', s)
    s = re.sub(r'([ΣΠ])\s*_\{([^{}]*)\}', r'\1(\2)', s)

    # 一般上下标：^{...} → ^x / ^(x)，_{...} → _x / _(x)
    s = re.sub(r'\^\{([^{}]+)\}', lambda m: '^' + _wrap(m.group(1)), s)
    s = re.sub(r'_\{([^{}]+)\}', lambda m: '_' + _wrap(m.group(1)), s)

    # 收尾：残余定界与分组括号
    s = re.sub(r'\\\\', ' ', s)
    s = s.replace('{', '').replace('}', '')
    s = s.replace('\x01', '{').replace('\x02', '}')
    return re.sub(r'  +', ' ', s).strip()

# 字体缺字形的符号 → 等价可渲染字符
_GLYPH_FIXES = {
    "​": "",   # 零宽空格
    "✅": "√",
    "❌": "×",
    "✗": "×",
}


def strip_latex_math(text: str) -> str:
    """把文本中的 LaTeX 数学片段转成可读的 Unicode 文本"""
    if not text:
        return ""

    def conv(m):
        return _convert_math(m.group(1))

    text = re.sub(r'\$\$(.*?)\$\$', conv, text, flags=re.DOTALL)
    text = re.sub(r'\$(.*?)\$', conv, text)
    text = re.sub(r'\\\((.*?)\\\)', conv, text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.*?)\\\]', conv, text, flags=re.DOTALL)

    # 定界符外偶尔也有裸的 LaTeX 命令，只做符号级替换（不动括号结构）
    if '\\' in text:
        text = text.replace(r'\{', '{').replace(r'\}', '}')
        for cmd, ch in _MATH_SYMBOLS:
            text = text.replace(cmd, ch)
        text = re.sub(r'\\(?:text|mathrm|mathbf|mathit)\{([^{}]*)\}', r'\1', text)
        text = re.sub(r'\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}',
                      lambda m: f'{_wrap(m.group(1))}/{_wrap(m.group(2))}', text)
        text = re.sub(r'\\([A-Za-z]+)', lambda m: _greek_word(m.group(1)), text)

    # 上标串 → ^串（如 10⁷ → 10^7，2⁻⁶ → 2^-6；Noto CJK 缺这些字形）
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

# 硬换行哨兵：表格行等必须保留换行的行
_HARD_LINE = "\x00"


def _disp_width(s: str) -> int:
    return sum(2 if ord(c) > 127 else 1 for c in s)


def convert_md_tables(text: str) -> str:
    """把 markdown 表格转成按列对齐的纯文本行（行首打硬换行哨兵）"""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        if t.startswith("|") and t.count("|") >= 2:
            # 收集整张表
            rows = []
            while i < len(lines):
                t = lines[i].strip()
                if not (t.startswith("|") and t.count("|") >= 2):
                    break
                if not re.fullmatch(r'\|[\s:|-]+\|?', t):  # 跳过 | :--- | 分隔行
                    rows.append([c.strip() for c in t.strip("|").split("|")])
                i += 1
            if rows:
                ncols = max(len(r) for r in rows)
                widths = [
                    max((_disp_width(r[c]) for r in rows if c < len(r)), default=0)
                    for c in range(ncols)
                ]
                for r in rows:
                    cells = [
                        (r[c] if c < len(r) else "")
                        + " " * (widths[c] - _disp_width(r[c] if c < len(r) else ""))
                        for c in range(ncols)
                    ]
                    out.append(_HARD_LINE + "  ".join(cells).rstrip())
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def merge_soft_newlines(text: str) -> str:
    """
    合并题干中的软换行：真题HTML里行内公式常被拆成独立行，
    除列表项外把断行重新拼回一句（英文单词间补空格）
    """
    lines = [ln.rstrip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return ""
    out = lines[0].lstrip(_HARD_LINE)
    prev_hard = lines[0].startswith(_HARD_LINE)
    for ln in lines[1:]:
        if ln.startswith(_HARD_LINE):
            out += "\n" + ln[len(_HARD_LINE):]
            prev_hard = True
        elif _LIST_MARKER.match(ln.strip()):
            out += "\n" + ln.strip()
            prev_hard = False
        elif prev_hard:
            # 表格等块级内容之后的正文另起一行
            out += "\n" + ln.strip()
            prev_hard = False
        else:
            ln = ln.strip()
            sep = " " if (out[-1].isascii() and out[-1].isalnum()
                          and ln[0].isascii() and ln[0].isalnum()) else ""
            out += sep + ln
    return out


def normalize_question(q: dict) -> dict:
    """返回清理过表格与换行的题目副本（保留 $...$ 给 _write_mixed 渲染数学图片）"""
    q = dict(q)
    # question_text 和 options 保留 $...$，由 _write_mixed 分段渲染
    q["question_text"] = merge_soft_newlines(
        convert_md_tables(q.get("question_text", "")))
    q["options"] = {k: merge_soft_newlines(v)
                    for k, v in q.get("options", {}).items()}
    # explanation 仍然 strip（太长，不适合逐公式渲染图片）
    q["explanation"] = convert_md_tables(strip_latex_math(q.get("explanation", "")))
    # explanation 里的硬换行哨兵直接还原为普通行
    q["explanation"] = q["explanation"].replace(_HARD_LINE, "")
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
