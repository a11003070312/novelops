#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search-facts.py -- 事实语义检索工具

对 state/facts/ 中的历史事实执行多策略模糊检索，弥补 tags 精确匹配的遗漏。
零外部依赖（仅 Python stdlib + PyYAML）。

用法:
  python scripts/search-facts.py "山路 伏击"
  python scripts/search-facts.py "char-001" "玉佩" --limit 20
  python scripts/search-facts.py --query "主角左手的伤疤" --limit 5
  python scripts/search-facts.py --query "青云宗的地理布局" --category environment
  python scripts/search-facts.py --character char-001 --limit 10

退出码:
  0 -- 正常（有或无结果）
  2 -- 环境/用法错误（缺少依赖、参数、项目根目录）
"""

from __future__ import annotations

import argparse
import io
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Windows UTF-8 stdout
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

try:
    import yaml
except ImportError:
    print(
        "错误: 缺少 PyYAML 依赖。\n"
        "请运行: pip install -r scripts/requirements.txt",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SEPARATOR = "=" * 60
SEPARATOR_THIN = "-" * 60

# 匹配策略权重
W_TAG_EXACT = 1.0       # tag 精确命中
W_TAG_FUZZY = 0.6       # tag 模糊命中 (SequenceMatcher > 0.7)
W_CONTENT_SUBSTR = 0.8  # content 子串命中
W_CONTENT_NGRAM = 0.5   # content 字符 bigram Jaccard
W_CATEGORY = 0.3        # category 命中
W_CHARACTER = 0.7       # character ID 命中

FUZZY_THRESHOLD = 0.65  # tag 模糊匹配最低相似度


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Fact:
    """一条事实记录。"""

    id: str
    chapter: int
    category: str
    content: str
    characters: List[str]
    tags: List[str]
    permanence: str
    valid_until: Optional[int]
    source_file: str


@dataclass
class SearchHit:
    """一条检索命中。"""

    fact: Fact
    score: float
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def find_project_root() -> Path:
    """从脚本位置向上查找 ENTRY.md 所在目录。"""
    current = Path(__file__).resolve().parent
    for _ in range(20):
        if (current / "ENTRY.md").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    print("错误: 未找到项目根目录 (ENTRY.md)", file=sys.stderr)
    sys.exit(2)


def load_all_facts(root: Path) -> List[Fact]:
    """加载 state/facts/ 下所有非模板事实文件。"""
    facts_dir = root / "state" / "facts"
    if not facts_dir.exists():
        return []

    all_facts: List[Fact] = []
    for fp in sorted(facts_dir.glob("chapter-*.yaml")):
        if fp.name.startswith("_"):
            continue
        try:
            with open(fp, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            continue

        if not data or not isinstance(data, dict):
            continue

        chapter_num = data.get("chapter", 0)
        facts_list = data.get("facts", [])
        if not isinstance(facts_list, list):
            continue

        for entry in facts_list:
            if not isinstance(entry, dict):
                continue
            fact = Fact(
                id=str(entry.get("id", "")),
                chapter=chapter_num,
                category=str(entry.get("category", "")),
                content=str(entry.get("content", "")),
                characters=[
                    str(c) for c in entry.get("characters", []) if c
                ],
                tags=[str(t) for t in entry.get("tags", []) if t],
                permanence=str(entry.get("permanence", "")),
                valid_until=entry.get("valid_until"),
                source_file=fp.name,
            )
            if fact.content:
                all_facts.append(fact)

    return all_facts


# ---------------------------------------------------------------------------
# 中文分词 (轻量级, 无外部依赖)
# ---------------------------------------------------------------------------
# 中文标点
_CN_PUNCT = set("，。！？、；：""''（）【】《》—…·\u3000")
_SPLIT_RE = re.compile(r"[\s,.\-_/;:!?，。！？、；：""''（）【】《》—…·\u3000]+")


def tokenize_chinese(text: str) -> List[str]:
    """
    轻量中文分词: 按标点和空白切分为短语，
    然后对每个短语生成字符 unigram 和 bigram。
    同时保留英文单词作为独立 token。
    """
    tokens: List[str] = []
    segments = _SPLIT_RE.split(text.strip())
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # 英文单词直接作为 token
        if seg.isascii():
            tokens.append(seg.lower())
            continue
        # 中文: unigram
        for ch in seg:
            if ch and ch not in _CN_PUNCT:
                tokens.append(ch)
        # 中文: bigram
        for i in range(len(seg) - 1):
            if seg[i] not in _CN_PUNCT and seg[i + 1] not in _CN_PUNCT:
                tokens.append(seg[i : i + 2])

    return tokens


def char_bigrams(text: str) -> Set[str]:
    """提取文本的字符 bigram 集合 (用于 Jaccard 相似度)。"""
    cleaned = re.sub(r"\s+", "", text)
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)} if len(cleaned) >= 2 else set()


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Jaccard 相似度。"""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# TF-IDF 轻量实现
# ---------------------------------------------------------------------------
class TinyTFIDF:
    """极简 TF-IDF, 用于事实内容的相关性排序。"""

    def __init__(self) -> None:
        self.doc_count = 0
        self.df: Counter = Counter()        # 文档频率
        self.doc_tfs: List[Counter] = []    # 每篇文档的词频
        self.doc_ids: List[int] = []        # 文档索引

    def add_document(self, doc_idx: int, tokens: List[str]) -> None:
        tf = Counter(tokens)
        self.doc_tfs.append(tf)
        self.doc_ids.append(doc_idx)
        self.doc_count += 1
        for term in set(tokens):
            self.df[term] += 1

    def query(self, query_tokens: List[str], top_k: int = 20) -> List[Tuple[int, float]]:
        """返回 [(doc_idx, score), ...] 按 score 降序。"""
        if self.doc_count == 0:
            return []

        scores: List[Tuple[int, float]] = []
        for i, (tf, doc_idx) in enumerate(
            zip(self.doc_tfs, self.doc_ids)
        ):
            score = 0.0
            for term in query_tokens:
                if term in tf:
                    term_tf = tf[term]
                    idf = math.log(
                        (self.doc_count + 1) / (self.df.get(term, 0) + 1)
                    )
                    score += term_tf * idf
            if score > 0:
                scores.append((doc_idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


# ---------------------------------------------------------------------------
# 多策略检索引擎
# ---------------------------------------------------------------------------
def search_facts(
    facts: List[Fact],
    query_terms: List[str],
    *,
    filter_category: Optional[str] = None,
    filter_character: Optional[str] = None,
    limit: int = 10,
) -> List[SearchHit]:
    """
    多策略检索:
    1. Tag 精确匹配
    2. Tag 模糊匹配 (SequenceMatcher)
    3. Content 子串匹配
    4. Content bigram Jaccard
    5. TF-IDF 排序
    6. Category 匹配
    7. Character ID 匹配
    """
    if not facts or not query_terms:
        return []

    # 预过滤
    candidates = facts
    if filter_category:
        candidates = [f for f in candidates if f.category == filter_category]
    if filter_character:
        candidates = [
            f for f in candidates if filter_character in f.characters
        ]

    if not candidates:
        return []

    # 准备查询
    query_lower = [t.lower() for t in query_terms]
    query_text = " ".join(query_terms)
    query_bigrams = char_bigrams(query_text)
    query_tokens = tokenize_chinese(query_text)

    # 构建 TF-IDF 索引
    tfidf = TinyTFIDF()
    for idx, fact in enumerate(candidates):
        doc_text = f"{fact.content} {' '.join(fact.tags)} {fact.category}"
        doc_tokens = tokenize_chinese(doc_text)
        tfidf.add_document(idx, doc_tokens)

    # TF-IDF 分数 (归一化到 0-1)
    tfidf_results = tfidf.query(query_tokens, top_k=len(candidates))
    tfidf_scores: Dict[int, float] = {}
    if tfidf_results:
        max_score = tfidf_results[0][1] if tfidf_results[0][1] > 0 else 1.0
        for idx, score in tfidf_results:
            tfidf_scores[idx] = score / max_score

    # 逐条评分
    hits: List[SearchHit] = []

    for idx, fact in enumerate(candidates):
        score = 0.0
        reasons: List[str] = []

        # --- 策略 1: Tag 精确匹配 ---
        fact_tags_lower = [t.lower() for t in fact.tags]
        tag_exact_count = 0
        for qt in query_lower:
            if qt in fact_tags_lower:
                tag_exact_count += 1
        if tag_exact_count > 0:
            s = W_TAG_EXACT * tag_exact_count
            score += s
            reasons.append(f"tag精确命中x{tag_exact_count}")

        # --- 策略 2: Tag 模糊匹配 ---
        tag_fuzzy_count = 0
        best_fuzzy_pairs: List[str] = []
        for qt in query_lower:
            if qt in fact_tags_lower:
                continue  # 已在策略1计分
            for ft in fact_tags_lower:
                sim = SequenceMatcher(None, qt, ft).ratio()
                if sim >= FUZZY_THRESHOLD:
                    tag_fuzzy_count += 1
                    best_fuzzy_pairs.append(f"{qt}~{ft}")
                    break
        if tag_fuzzy_count > 0:
            s = W_TAG_FUZZY * tag_fuzzy_count
            score += s
            reasons.append(
                f"tag模糊命中x{tag_fuzzy_count} ({', '.join(best_fuzzy_pairs[:3])})"
            )

        # --- 策略 3: Content 子串匹配 ---
        content_substr_count = 0
        for qt in query_terms:
            if len(qt) >= 2 and qt in fact.content:
                content_substr_count += 1
        if content_substr_count > 0:
            s = W_CONTENT_SUBSTR * content_substr_count
            score += s
            reasons.append(f"内容子串命中x{content_substr_count}")

        # --- 策略 4: Content bigram Jaccard ---
        fact_bigrams = char_bigrams(fact.content)
        jac = jaccard_similarity(query_bigrams, fact_bigrams)
        if jac > 0.05:
            s = W_CONTENT_NGRAM * jac
            score += s
            reasons.append(f"内容相似度={jac:.2f}")

        # --- 策略 5: TF-IDF ---
        tfidf_s = tfidf_scores.get(idx, 0.0)
        if tfidf_s > 0:
            s = 0.4 * tfidf_s  # TF-IDF 作为补充信号
            score += s
            if tfidf_s > 0.3:
                reasons.append(f"TF-IDF={tfidf_s:.2f}")

        # --- 策略 6: Category 匹配 ---
        for qt in query_lower:
            if qt == fact.category.lower():
                score += W_CATEGORY
                reasons.append(f"类别命中={fact.category}")
                break

        # --- 策略 7: Character ID 匹配 ---
        fact_chars_lower = [c.lower() for c in fact.characters]
        for qt in query_lower:
            if qt in fact_chars_lower:
                score += W_CHARACTER
                reasons.append(f"人物命中={qt}")
                break

        if score > 0:
            hits.append(SearchHit(fact=fact, score=score, reasons=reasons))

    # 排序
    hits.sort(key=lambda h: h.score, reverse=True)

    # 限制数量
    return hits[:limit]


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------
def format_report(
    hits: List[SearchHit],
    query_terms: List[str],
    total_facts: int,
) -> str:
    """格式化检索报告。"""
    lines: List[str] = []
    lines.append(SEPARATOR)
    lines.append(f"  事实检索报告")
    lines.append(SEPARATOR)
    lines.append(f"  查询词: {' / '.join(query_terms)}")
    lines.append(f"  事实库总量: {total_facts} 条")
    lines.append(f"  命中数: {len(hits)} 条")
    lines.append("")

    if not hits:
        lines.append("  (无匹配结果)")
        lines.append("")
        lines.append(SEPARATOR)
        return "\n".join(lines)

    # 表头
    lines.append(
        f"  {'排名':<4} {'ID':<16} {'章':<4} {'类别':<12} "
        f"{'得分':<6} {'内容'}"
    )
    lines.append(f"  {SEPARATOR_THIN}")

    for rank, hit in enumerate(hits, 1):
        f = hit.fact
        # 截断内容
        content_display = f.content
        if len(content_display) > 40:
            content_display = content_display[:38] + "..."

        lines.append(
            f"  {rank:<4} {f.id:<16} {f.chapter:<4} {f.category:<12} "
            f"{hit.score:<6.2f} {content_display}"
        )
        # 匹配原因
        reasons_str = "; ".join(hit.reasons)
        lines.append(f"       匹配: {reasons_str}")
        # permanence 标记
        if f.permanence == "temporary":
            valid_str = (
                f"(临时, 有效至第{f.valid_until}章)"
                if f.valid_until
                else "(临时)"
            )
            lines.append(f"       {valid_str}")
        lines.append("")

    lines.append(SEPARATOR)

    # 约束提示
    if hits:
        lines.append("")
        lines.append("以上事实应作为写作硬约束 -- 正文不得与之矛盾。")
        temp_facts = [h for h in hits if h.fact.permanence == "temporary"]
        if temp_facts:
            lines.append(
                f"其中 {len(temp_facts)} 条为临时事实，请注意有效期。"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="事实语义检索工具 -- 多策略模糊匹配 state/facts/ 中的历史事实"
    )
    parser.add_argument(
        "terms",
        nargs="*",
        help="检索关键词 (空格分隔的多个词)",
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        default="",
        help="检索语句 (自动拆分为关键词)",
    )
    parser.add_argument(
        "--category", "-c",
        type=str,
        default=None,
        help="按事实类别过滤 (appearance/measurement/world_rule/...)",
    )
    parser.add_argument(
        "--character",
        type=str,
        default=None,
        help="按人物 ID 过滤 (如 char-001)",
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        default=10,
        help="返回结果数上限 (默认 10)",
    )

    args = parser.parse_args()

    # 收集查询词
    query_terms: List[str] = list(args.terms) if args.terms else []
    if args.query:
        # 按标点和空白拆分
        extra = _SPLIT_RE.split(args.query.strip())
        query_terms.extend([t for t in extra if t.strip()])

    if not query_terms:
        print("错误: 请提供至少一个检索关键词。", file=sys.stderr)
        print("用法: python scripts/search-facts.py \"关键词1\" \"关键词2\"", file=sys.stderr)
        print("  或: python scripts/search-facts.py --query \"检索语句\"", file=sys.stderr)
        return 2

    # 加载事实
    root = find_project_root()
    facts = load_all_facts(root)

    if not facts:
        print(SEPARATOR)
        print("  事实检索报告")
        print(SEPARATOR)
        print(f"  查询词: {' / '.join(query_terms)}")
        print("  事实库为空 (尚无已提取的事实)")
        print(SEPARATOR)
        return 0

    # 执行检索
    hits = search_facts(
        facts,
        query_terms,
        filter_category=args.category,
        filter_character=args.character,
        limit=args.limit,
    )

    # 输出报告
    report = format_report(hits, query_terms, len(facts))
    print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
