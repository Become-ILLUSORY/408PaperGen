#!/usr/bin/env python3
"""
组卷冒烟测试
用 AI 实际生成少量题目（默认第1、12题，走完整的 生成→质检→重试 链路），
其余题号从真题题库随机填充，把整卷排满后渲染 PDF，用于检验：
  1. 组卷排满：40 个题号全部有题、科目分区正确、PDF 正常渲染
  2. AI 生成的题目是否达到预期（测试结束会把 AI 题完整打印出来供人工检查）

用法:
  python3 src/test_paper.py           # 默认 AI 生成第 1、12 题
  python3 src/test_paper.py 3,35     # 指定 AI 生成的题号（逗号分隔）
"""

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate import (
    GENERATED_DIR,
    generate_one_question,
    get_subject_for_num,
    get_subject_range_str,
    load_config,
    load_outline,
    load_question_bank,
)
from build_paper import build_paper


def pick_filler(bank: list[dict]) -> dict:
    """从题库挑一道适合排版测试的单选题（4选项、无图片）"""
    candidates = [
        q for q in bank
        if q["section"] == "选择题"
        and not q.get("multiple")
        and not q.get("figure")
        and len(q.get("options", {})) == 4
        and "![](" not in q["question_text"]
        and all("![](" not in v for v in q["options"].values())
    ]
    return random.choice(candidates)


def print_ai_question(q: dict):
    meta = q.get("_meta", {})
    print(f"\n{'─' * 60}")
    print(f"第 {q['number']} 题（{q['subject']}）  "
          f"难度:{meta.get('difficulty', '?')}  尝试次数:{meta.get('attempts', '?')}  "
          f"质检:{'通过' if meta.get('validated') else '未通过'}")
    print(f"{'─' * 60}")
    print(q["question_text"])
    for k in "ABCD":
        print(f"  {k}. {q['options'].get(k, '')}")
    print(f"\n  【答案】{q['answer']}    【考点】{' / '.join(q['tags'])}")
    print(f"  【解析】{q['explanation']}")


def main():
    ai_slots = [1, 12]
    if len(sys.argv) > 1:
        ai_slots = sorted({int(x) for x in sys.argv[1].split(",")})

    config = load_config()
    outline = load_outline()
    paper_config = config["paper"]
    total = paper_config["total_questions"]
    subject_range_str = get_subject_range_str(paper_config["subjects"])
    year = str(time.localtime().tm_year)

    bad = [n for n in ai_slots if not get_subject_for_num(n, paper_config["subjects"])]
    if bad:
        sys.exit(f"题号超出范围: {bad}")

    banks = {s["name"]: load_question_bank(s["name"]) for s in paper_config["subjects"]}

    print(f"组卷测试: AI 生成题号 {ai_slots}（模型 {config['model']}），其余 {total - len(ai_slots)} 题用真题填充\n")

    # ── 逐题组卷 ──
    questions = []
    ai_questions = []
    for num in range(1, total + 1):
        subj = get_subject_for_num(num, paper_config["subjects"])
        if num in ai_slots:
            print(f"=== AI 生成第 {num} 题（{subj['name']}）===")
            q = generate_one_question(
                config=config,
                outline=outline,
                question_num=num,
                subject_name=subj["name"],
                subject_range_str=subject_range_str,
                question_bank=banks[subj["name"]],
                year=year,
            )
            if not q:
                sys.exit(f"第 {num} 题 AI 生成失败（已达最大重试），测试终止")
            ai_questions.append(q)
        else:
            src = pick_filler(banks[subj["name"]])
            q = dict(src)
            q["number"] = str(num)
            q["subject"] = subj["name"]
            q["_meta"] = {"source": "question_bank", "source_id": src["id"]}
        questions.append(q)

    # ── 保存试卷 JSON ──
    paper_name = f"测试组卷_{int(time.time())}"
    paper_dir = os.path.join(GENERATED_DIR, paper_name)
    os.makedirs(paper_dir, exist_ok=True)
    json_path = os.path.join(paper_dir, "questions.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"total": total, "generated": len(questions), "questions": questions},
                  f, ensure_ascii=False, indent=2)

    # ── 校验排满 ──
    numbers = [int(q["number"]) for q in questions]
    assert numbers == list(range(1, total + 1)), f"题号不连续: {numbers}"
    for s in paper_config["subjects"]:
        lo, hi = s["range"]
        for q in questions[lo - 1:hi]:
            assert q["subject"] == s["name"], f"第{q['number']}题科目错误: {q['subject']} != {s['name']}"
    print(f"\n[排满校验] {total}/{total} 题, 题号连续, 科目分区正确 ✓")

    # ── 渲染 PDF ──
    print()
    build_paper(paper_name)

    # ── 打印 AI 题目供人工检查 ──
    print(f"\n{'=' * 60}")
    print(f"以下为本次 AI 生成的 {len(ai_questions)} 道题目，请人工检查是否达到预期:")
    print(f"{'=' * 60}")
    for q in ai_questions:
        print_ai_question(q)


if __name__ == "__main__":
    main()
