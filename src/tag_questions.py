#!/usr/bin/env python3
"""
真题考点补标
找出题库中无 tags 的真题（如2026年整年），调用 AI 按 data/大纲.json
的知识点体系分类，把考点写回 data/<科目>.json 与 _sequence.json，
并重建知识点索引。可重复运行（只处理仍无 tag 的题）。
"""

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate import DATA_DIR, SUBJECTS, _bigrams, call_ai, load_config, load_outline, log

MAX_RETRIES = 2


def build_topic_index(data_dir: str):
    """从已保存的题库生成知识点分类索引 data/_知识点索引.json"""
    index: dict[str, dict[str, list[str]]] = {}
    for subject in SUBJECTS:
        path = os.path.join(data_dir, f"{subject}.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            questions = json.load(f)["questions"]
        by_tag: dict[str, list[str]] = {}
        for q in questions:
            for tag in q["tags"] or ["未标注"]:
                by_tag.setdefault(tag, []).append(q["id"])
        index[subject] = dict(sorted(by_tag.items(), key=lambda kv: -len(kv[1])))

    out_path = os.path.join(data_dir, "_知识点索引.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "subjects": {
                subj: {
                    "tag_count": len(tags),
                    "by_tag": {t: {"count": len(ids), "ids": ids} for t, ids in tags.items()},
                }
                for subj, tags in index.items()
            },
        }, f, ensure_ascii=False, indent=2)
    print(f"已保存知识点索引: {out_path} "
          f"({', '.join(f'{s} {len(t)}类' for s, t in index.items())})")


def outline_topics_for(outline: dict, subject: str) -> list[str]:
    """科目的全部 '章-知识点' 条目"""
    for s in outline["subjects"]:
        if s["name"] == subject:
            return [
                f"{ch['title']}-{t['name']}"
                for ch in s["chapters"]
                for t in ch["topics"]
            ]
    return []


def build_prompt(subject: str, topics: list[str], q: dict) -> str:
    options_str = "\n".join(f"{k}. {v}" for k, v in sorted(q.get("options", {}).items()))
    topics_str = "\n".join(f"  - {t}" for t in topics)
    return f"""你是408考研辅导专家。请为下面这道「{subject}」真题标注考点。

可选考点列表（必须严格从下列条目中选择一条，原样输出完整条目）:
{topics_str}

题目:
{q['question_text']}
{options_str}
答案: {q.get('answer', '')}
解析: {q.get('explanation', '')[:500]}

输出格式（严格执行，只输出一行，不要任何其他内容）:
【考点】<从列表中选出的最匹配条目>"""


def resolve_topic(raw: str, topics: list[str]) -> str:
    """把AI输出对齐到考点列表：精确 → 包含 → 二元字组最相似"""
    raw = raw.strip()
    if raw in topics:
        return raw
    for t in topics:
        if raw in t or t in raw:
            return t
    rg = _bigrams(raw)
    best, best_score = "", 0
    for t in topics:
        score = len(rg & _bigrams(t))
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 3 else ""


def tag_one(config: dict, subject: str, topics: list[str], q: dict) -> str:
    """给一道题打标，返回考点（失败返回空串）"""
    for attempt in range(1, MAX_RETRIES + 1):
        response = call_ai(config, build_prompt(subject, topics, q), temperature=0.2)
        if not response:
            continue
        m = re.search(r'【考点】\s*(.+?)\s*(?:\n|$)', response)
        if not m:
            log(f"  [格式不符 {attempt}/{MAX_RETRIES}] {q['id']}")
            continue
        topic = resolve_topic(m.group(1), topics)
        if topic:
            log(f"  [标注] {q['year']}年第{q['number']}题({subject}) → {topic}")
            return topic
        log(f"  [无法对齐 {attempt}/{MAX_RETRIES}] {q['id']} AI给出: {m.group(1)!r}")
    return ""


def main():
    config = load_config()
    outline = load_outline()

    # 收集所有无tag题
    banks: dict[str, dict] = {}
    todo: list[tuple[str, dict]] = []
    for subject in SUBJECTS:
        with open(os.path.join(DATA_DIR, f"{subject}.json"), encoding="utf-8") as f:
            banks[subject] = json.load(f)
        for q in banks[subject]["questions"]:
            if not q["tags"]:
                todo.append((subject, q))

    if not todo:
        print("所有真题均已有考点标注")
        return

    log(f"待补标: {len(todo)} 题 | 模型:{config['model']} | 并发:{config.get('max_concurrent', 3)}")

    topics_cache = {s: outline_topics_for(outline, s) for s in SUBJECTS}
    tagged = 0
    with ThreadPoolExecutor(max_workers=config.get("max_concurrent", 3)) as executor:
        future_map = {
            executor.submit(tag_one, config, subject, topics_cache[subject], q): (subject, q)
            for subject, q in todo
        }
        for future in as_completed(future_map):
            subject, q = future_map[future]
            try:
                topic = future.result()
            except Exception as e:
                log(f"  [异常] {q['id']}: {e}")
                continue
            if topic:
                q["tags"] = [topic]
                q["_tag_source"] = "ai"
                tagged += 1

    # 写回科目题库
    for subject in SUBJECTS:
        path = os.path.join(DATA_DIR, f"{subject}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(banks[subject], f, ensure_ascii=False, indent=2)

    # 同步 _sequence.json 的 tags
    seq_path = os.path.join(DATA_DIR, "_sequence.json")
    tags_by_id = {
        q["id"]: q["tags"]
        for bank in banks.values() for q in bank["questions"]
    }
    with open(seq_path, encoding="utf-8") as f:
        seq = json.load(f)
    for s in seq["sequence"]:
        if s["source_id"] in tags_by_id:
            s["tags"] = tags_by_id[s["source_id"]]
    with open(seq_path, "w", encoding="utf-8") as f:
        json.dump(seq, f, ensure_ascii=False, indent=2)

    # 重建知识点索引
    build_topic_index(DATA_DIR)

    log(f"完成: {tagged}/{len(todo)} 题已补标" +
        (f"（{len(todo) - tagged} 题失败，可重跑本脚本重试）" if tagged < len(todo) else ""))


if __name__ == "__main__":
    main()
