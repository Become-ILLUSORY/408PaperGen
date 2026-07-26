#!/usr/bin/env python3
"""
历年真题结构分析
统计 2009-2026 真题中每个科目的考点频次（参考信息），以及科目内每个
题位（第几道）历年落在哪些「章节」（卷面结构），输出 data/_考点分布.json。

generate.py 只用「题位→章节」的结构分布来还原真题卷面编排（比如数据
结构第1题通常出现在绪论/线性表附近），章节内的具体知识点则均匀随机
抽取——冷门与热门考点机会均等，避免每张卷都押同样的高频考点。
"""

import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate import SUBJECTS, _bigrams, call_ai, load_config, load_outline, related

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MAPPING_PATH = os.path.join(DATA_DIR, "_tag章节映射.json")


def outline_chapters(outline: dict, subject: str) -> list[tuple[str, list[str]]]:
    """科目的 [(章节名, [知识点名...]), ...]"""
    for s in outline["subjects"]:
        if s["name"] == subject:
            return [
                (ch["title"],
                 [t["name"] for t in ch["topics"]]
                 + [st for t in ch["topics"] for st in t.get("subtopics", [])])
                for ch in s["chapters"]
            ]
    return []


def map_tag_to_chapter(tag: str, chapters: list[tuple[str, list[str]]]) -> str:
    """启发式兜底：把真题tag模糊映射到大纲章节（包含关系记3分，否则二元字组重叠数，≥2才算）"""
    tg = _bigrams(tag)
    best, best_score = "", 0
    for title, names in chapters:
        for name in [title] + names:
            if related(tag, name):
                score = max(3, len(tg & _bigrams(name)))
            else:
                score = len(tg & _bigrams(name))
            if score > best_score:
                best, best_score = title, score
    return best if best_score >= 2 else ""


def ai_map_tags(config: dict, subject: str, titles: list[str], tags: list[str]) -> dict[str, str]:
    """一次AI调用为一批考点tag标注所属章节；无法归类的返回空串"""
    prompt = f"""你是408考研辅导专家。请把下列「{subject}」真题考点标签归类到章节。

章节列表（只能从中选择）:
{chr(10).join('  - ' + t for t in titles)}

考点标签:
{chr(10).join('  - ' + t for t in tags)}

输出格式（严格执行，每个标签一行，无法归类的章节名填 无，不要输出其他内容）:
标签|章节名"""
    response = call_ai(config, prompt, temperature=0.1)
    result: dict[str, str] = {}
    if not response:
        return result
    for line in response.splitlines():
        if "|" not in line:
            continue
        tag, ch = (x.strip().lstrip("-").strip() for x in line.split("|", 1))
        if tag not in tags:
            continue
        if ch in titles:
            result[tag] = ch
        elif ch in ("无", ""):
            result[tag] = ""
        else:
            aligned = next((t for t in titles if related(ch, t)), None)
            if aligned:
                result[tag] = aligned
    return result


def ensure_tag_mapping(outline: dict) -> dict[str, dict[str, str]]:
    """
    加载/补全 tag→章节映射（data/_tag章节映射.json，可人工修订）。
    缺失的 tag 优先用 AI 归类，AI 不可用时退回启发式匹配。
    """
    mapping: dict[str, dict[str, str]] = {}
    if os.path.exists(MAPPING_PATH):
        with open(MAPPING_PATH, encoding="utf-8") as f:
            mapping = json.load(f)

    try:
        config = load_config()
    except Exception:
        config = None

    changed = False
    for subject in SUBJECTS:
        with open(os.path.join(DATA_DIR, f"{subject}.json"), encoding="utf-8") as f:
            questions = json.load(f)["questions"]
        all_tags = sorted({t for q in questions for t in q["tags"]})
        sub_map = mapping.setdefault(subject, {})
        missing = [t for t in all_tags if t not in sub_map]
        if not missing:
            continue

        chapters = outline_chapters(outline, subject)
        titles = [t for t, _ in chapters]
        ai_result = ai_map_tags(config, subject, titles, missing) if config else {}
        for tag in missing:
            if tag in ai_result:
                sub_map[tag] = ai_result[tag]
            else:
                sub_map[tag] = map_tag_to_chapter(tag, chapters)
        changed = True
        n_ai = sum(1 for t in missing if t in ai_result)
        print(f"{subject}: 新映射 {len(missing)} 个tag (AI归类{n_ai}, 启发式兜底{len(missing) - n_ai})")

    if changed:
        with open(MAPPING_PATH, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        print(f"已保存 tag→章节映射: {MAPPING_PATH}")
    return mapping


def main():
    outline = load_outline()
    tag_mapping = ensure_tag_mapping(outline)
    out = {"subjects": {}}
    for subject in SUBJECTS:
        with open(os.path.join(DATA_DIR, f"{subject}.json"), encoding="utf-8") as f:
            questions = json.load(f)["questions"]
        choice = [q for q in questions if q["section"] == "选择题" and q["tags"]]
        chapters = outline_chapters(outline, subject)

        # 全科目考点频次（仅供参考，不用于加权）
        tag_freq = Counter(t for q in choice for t in q["tags"])

        # 题位→章节结构：科目内第 i 道选择题历年落在哪些章节
        sub_map = tag_mapping.get(subject, {})
        by_year = defaultdict(list)
        for q in choice:
            by_year[q["year"]].append(q)
        pos_chapters: dict[int, Counter] = defaultdict(Counter)
        unmapped = 0
        for year, yqs in by_year.items():
            yqs.sort(key=lambda q: int(q["number"]) if q["number"].isdigit() else 999)
            for i, q in enumerate(yqs):
                ch = sub_map.get(q["tags"][0]) or map_tag_to_chapter(q["tags"][0], chapters)
                if ch:
                    pos_chapters[i][ch] += 1
                else:
                    unmapped += 1

        out["subjects"][subject] = {
            "questions": len(choice),
            "years": len(by_year),
            "tag_freq": dict(tag_freq.most_common()),
            "by_position_chapters": {
                str(i): dict(c.most_common())
                for i, c in sorted(pos_chapters.items())
            },
        }

        print(f"{subject}: {len(choice)} 题 / {len(by_year)} 年 / "
              f"{len(tag_freq)} 个考点 / 章节映射失败 {unmapped} 题")
        p0 = dict(pos_chapters.get(0, Counter()).most_common(3))
        print(f"  题位0历年章节Top3: {p0}")

    out_path = os.path.join(DATA_DIR, "_考点分布.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n已保存考点分布: {out_path}")


if __name__ == "__main__":
    main()
