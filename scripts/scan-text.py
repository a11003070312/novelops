#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan-text.py -- 小说章节文本去AI感扫描工具

读取 config/anti-ai-patterns.yaml 中的规则，对章节 .md 文件执行自动化检测，
输出结构化扫描报告，帮助作者在提交前排查 AI 痕迹。

用法:
  python scripts/scan-text.py <文件或目录>
  python scripts/scan-text.py --chapter chapters/arc-001/chapter-0001.md
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from pathlib import Path

# Windows UTF-8 stdout
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import yaml
except ImportError:
    print(
        "[错误] 缺少依赖: PyYAML\n"
        "请执行以下命令安装:\n"
        "  pip install pyyaml\n"
        "或使用国内镜像:\n"
        "  pip install pyyaml -i https://pypi.tuna.tsinghua.edu.cn/simple",
        file=sys.stderr,
    )
    sys.exit(2)


# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------
SEPARATOR_THICK = "=" * 60
SEPARATOR_THIN = "-" * 60
EXIT_CLEAN = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2

# 对话引号的正则: 匹配 "" 和 「」 内的内容
DIALOGUE_PATTERN = re.compile(
    r"\u201c([^\u201d]*)\u201d"  # ""
    r"|\u300c([^\u300d]*)\u300d"  # 「」
)


# ------------------------------------------------------------------
# 项目根目录定位
# ------------------------------------------------------------------
def find_project_root() -> Path:
    """从脚本所在目录向上查找包含 ENTRY.md 的目录作为项目根。"""
    current = Path(__file__).resolve().parent
    for _ in range(20):
        if (current / "ENTRY.md").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    print(
        "[错误] 无法定位项目根目录 (未找到 ENTRY.md)\n"
        "请确保脚本位于项目的 scripts/ 目录下。",
        file=sys.stderr,
    )
    sys.exit(EXIT_ERROR)


# ------------------------------------------------------------------
# 配置加载
# ------------------------------------------------------------------
def load_config(project_root: Path) -> dict:
    """加载并返回 anti-ai-patterns.yaml 配置。"""
    config_path = project_root / "config" / "anti-ai-patterns.yaml"
    if not config_path.exists():
        print(
            f"[错误] 配置文件不存在: {config_path}",
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        print(
            f"[错误] 配置文件 YAML 解析失败: {config_path}\n{exc}",
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)
    if not isinstance(data, dict):
        print(
            f"[错误] 配置文件格式异常: {config_path}",
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)
    return data


def load_glossary(project_root: Path) -> list[dict]:
    """加载 config/glossary.yaml 术语表，返回 terms 列表。
    文件不存在时返回空列表（不报错，术语表是可选增强）。"""
    glossary_path = project_root / "config" / "glossary.yaml"
    if not glossary_path.exists():
        return []
    try:
        with open(glossary_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    return data.get("terms", []) or []


# ------------------------------------------------------------------
# 文本工具函数
# ------------------------------------------------------------------
def read_text(filepath: Path) -> str:
    """以 UTF-8 读取文本文件的全部内容。"""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        print(f"[错误] 无法读取文件: {filepath}\n{exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)


def split_lines(text: str) -> list[str]:
    """将文本按行分割，保留内容（不含换行符）。"""
    return text.splitlines()


def extract_paragraphs(text: str) -> list[str]:
    """将文本按空行分割为段落，过滤空段落。"""
    raw = re.split(r"\n\s*\n", text)
    return [p.strip() for p in raw if p.strip()]


def get_first_paragraph(text: str) -> str:
    """获取第一段（第一个非空行/第一个空行前的内容块）。"""
    paragraphs = extract_paragraphs(text)
    if paragraphs:
        return paragraphs[0]
    return ""


def get_last_paragraph(text: str) -> str:
    """获取最后一段。"""
    paragraphs = extract_paragraphs(text)
    if paragraphs:
        return paragraphs[-1]
    return ""


def strip_markdown_header(text: str) -> tuple[str, int]:
    """移除开头的 Markdown 标题行 (# 开头) 以避免干扰正文检测。
    返回 (stripped_text, header_line_count) 用于行号偏移修正。"""
    lines = text.splitlines()
    content_lines: list[str] = []
    header_done = False
    header_line_count = 0
    for line in lines:
        stripped = line.strip()
        if not header_done:
            if stripped.startswith("#") or stripped == "":
                header_line_count += 1
                continue
            header_done = True
        content_lines.append(line)
    return "\n".join(content_lines), header_line_count


def remove_dialogue(text: str) -> str:
    """移除文本中所有对话引号内的内容，用于 dialogue 豁免计算。"""
    return DIALOGUE_PATTERN.sub("", text)


def extract_dialogues(text: str) -> list[str]:
    """提取所有对话段落的文本内容。"""
    results: list[str] = []
    for match in DIALOGUE_PATTERN.finditer(text):
        content = match.group(1) if match.group(1) is not None else match.group(2)
        if content:
            results.append(content)
    return results


def find_line_numbers(
    lines: list[str], word: str, offset: int = 0
) -> list[tuple[int, str]]:
    """
    在行列表中查找包含 word 的所有行。
    返回 [(行号(1-based, 含偏移), 行内容), ...]
    offset: 因 Markdown 标题剥离导致的行号偏移量
    """
    results: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        if word in line:
            results.append((idx + offset, line.strip()))
    return results


def find_regex_line_numbers(
    lines: list[str], pattern: re.Pattern, offset: int = 0
) -> list[tuple[int, str]]:
    """
    在行列表中查找匹配正则的所有行。
    返回 [(行号(1-based, 含偏移), 匹配片段), ...]
    offset: 因 Markdown 标题剥离导致的行号偏移量
    """
    results: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        m = pattern.search(line)
        if m:
            results.append((idx + offset, m.group(0)))
    return results


def count_in_text(text: str, word: str) -> int:
    """计算 word 在 text 中的非重叠出现次数。"""
    return text.count(word)


def truncate_context(line: str, max_len: int = 40) -> str:
    """截断行内容用于报告展示。"""
    if len(line) <= max_len:
        return line
    return line[:max_len] + "..."


# ------------------------------------------------------------------
# 检测结果数据结构
# ------------------------------------------------------------------
class Finding:
    """单条检测结果。"""

    def __init__(
        self,
        level: str,
        category: str,
        message: str,
        details: list[str] | None = None,
    ):
        # level: "FAIL", "WARN", "PASS"
        self.level = level
        # category: "词汇", "句式", "结构", "开篇", "结尾", "情感", "对话", "转场", "翻译腔"
        self.category = category
        self.message = message
        self.details = details or []


# ------------------------------------------------------------------
# 检测函数
# ------------------------------------------------------------------
def check_banned_words(
    text: str, lines: list[str], config_words: list[dict],
    offset: int = 0,
) -> list[Finding]:
    """检测 banned_words 规则。"""
    findings: list[Finding] = []
    text_no_dialogue = remove_dialogue(text)

    for entry in config_words:
        word = entry["word"]
        severity = entry.get("severity", "medium")
        max_occ = entry.get("max_occurrences", 0)
        exceptions = entry.get("context_exceptions") or []

        total_count = count_in_text(text, word)
        if total_count == 0:
            continue

        if "dialogue" in exceptions:
            effective_count = count_in_text(text_no_dialogue, word)
        else:
            effective_count = total_count

        if effective_count <= max_occ:
            continue

        is_fail = severity in ("critical", "high")
        level = "FAIL" if is_fail else "WARN"

        replacements = entry.get("replacements", [])
        replace_hint = ""
        if replacements:
            replace_hint = f" (建议替换: {'/'.join(replacements)})"

        msg = (
            f'"{word}" 出现 {effective_count} 次 '
            f"(上限 {max_occ}) [{severity}]{replace_hint}"
        )

        detail_lines: list[str] = []
        hits = find_line_numbers(lines, word, offset)
        for lineno, content in hits[:5]:
            detail_lines.append(f"行 {lineno}: {truncate_context(content)}")
        if len(hits) > 5:
            detail_lines.append(f"... 及其他 {len(hits) - 5} 处")

        findings.append(Finding(level, "词汇", msg, detail_lines))

    return findings


def check_banned_patterns(
    text: str, lines: list[str], config_patterns: list[dict],
    offset: int = 0,
) -> list[Finding]:
    """检测 banned_patterns 规则。"""
    findings: list[Finding] = []

    for entry in config_patterns:
        pat_id = entry["id"]
        description = entry.get("description", "")
        regex_str = entry["regex"]
        severity = entry.get("severity", "medium")

        try:
            pattern = re.compile(regex_str)
        except re.error:
            findings.append(
                Finding("WARN", "句式", f"{pat_id} 正则编译失败: {regex_str}")
            )
            continue

        hits = find_regex_line_numbers(lines, pattern, offset)
        if not hits:
            continue

        max_occ = entry.get("max_occurrences", 0)
        # critical：任何匹配即 FAIL，忽略 max_occurrences
        # high：超过阈值才 FAIL（阈值为 0 时任何匹配即 FAIL，与文档一致）
        if severity == "critical":
            level = "FAIL"
        elif severity == "high":
            level = "FAIL" if (max_occ == 0 or len(hits) > max_occ) else "WARN"
        elif severity == "medium" and max_occ > 0 and len(hits) > max_occ:
            level = "WARN"
        elif severity == "medium" and max_occ == 0:
            level = "WARN"
        elif severity == "medium":
            continue  # within threshold, skip
        elif severity == "low":
            continue  # low severity patterns are informational only
        else:
            # Unknown severity value — warn about misconfiguration, not the match
            findings.append(
                Finding("WARN", "配置", f"{pat_id} 未知 severity 值: \"{severity}\"")
            )
            continue

        count_info = f" 出现 {len(hits)} 次 (上限 {max_occ})" if max_occ > 0 else ""
        msg = f'{pat_id} "{description}"{count_info} [{severity}]'

        detail_lines: list[str] = []
        for lineno, matched in hits[:5]:
            detail_lines.append(f"行 {lineno}: {truncate_context(matched)}")
        if len(hits) > 5:
            detail_lines.append(f"... 及其他 {len(hits) - 5} 处")

        findings.append(Finding(level, "句式", msg, detail_lines))

    return findings


def check_chapter_opening(
    text: str, config_opening: dict
) -> list[Finding]:
    """检测 chapter_opening_ban 规则（仅检查首段）。"""
    findings: list[Finding] = []
    first_para = get_first_paragraph(text)
    if not first_para:
        return findings

    first_lines = split_lines(first_para)
    patterns = config_opening.get("patterns", [])
    has_violation = False

    for entry in patterns:
        pat_id = entry["id"]
        regex_str = entry["regex"]
        severity = entry.get("severity", "medium")

        try:
            pattern = re.compile(regex_str)
        except re.error:
            continue

        hits = find_regex_line_numbers(first_lines, pattern)
        if hits:
            has_violation = True
            is_fail = severity in ("critical", "high")
            level = "FAIL" if is_fail else "WARN"
            msg = f"{pat_id} 开篇违规句式 [{severity}]"
            detail_lines = [
                f"首段: {truncate_context(matched)}"
                for _, matched in hits[:3]
            ]
            findings.append(Finding(level, "开篇", msg, detail_lines))

    if not has_violation:
        findings.append(Finding("PASS", "开篇", "无违规"))

    return findings


def check_ending(text: str, config_ending: dict) -> list[Finding]:
    """检测 ending_ban 规则（仅检查末段）。"""
    findings: list[Finding] = []
    last_para = get_last_paragraph(text)
    if not last_para:
        return findings

    last_lines = split_lines(last_para)
    patterns = config_ending.get("patterns", [])
    has_violation = False

    for entry in patterns:
        pat_id = entry["id"]
        regex_str = entry["regex"]
        severity = entry.get("severity", "medium")

        try:
            pattern = re.compile(regex_str)
        except re.error:
            continue

        hits = find_regex_line_numbers(last_lines, pattern)
        if hits:
            has_violation = True
            is_fail = severity in ("critical", "high")
            level = "FAIL" if is_fail else "WARN"
            msg = f"{pat_id} 结尾违规句式 [{severity}]"
            detail_lines = [
                f"末段: {truncate_context(matched)}"
                for _, matched in hits[:3]
            ]
            findings.append(Finding(level, "结尾", msg, detail_lines))

    if not has_violation:
        findings.append(Finding("PASS", "结尾", "无违规"))

    return findings


def check_paragraph_variation(
    text: str, config_variation: dict
) -> list[Finding]:
    """检测 paragraph_variation 规则（连续三段长度过于均匀）。"""
    findings: list[Finding] = []
    threshold = config_variation.get("threshold_ratio", 0.3)

    paragraphs = extract_paragraphs(text)
    if len(paragraphs) < 3:
        return findings

    lengths = [len(p.replace("\n", "").replace(" ", "")) for p in paragraphs]
    has_violation = False

    for i in range(len(lengths) - 2):
        l1, l2, l3 = lengths[i], lengths[i + 1], lengths[i + 2]
        if l1 == 0 or l2 == 0 or l3 == 0:
            continue
        avg = (l1 + l2 + l3) / 3.0
        if avg == 0:
            continue
        d1 = abs(l1 - avg) / avg
        d2 = abs(l2 - avg) / avg
        d3 = abs(l3 - avg) / avg
        if d1 < threshold and d2 < threshold and d3 < threshold:
            has_violation = True
            p_start = i + 1  # 1-based paragraph number
            p_end = i + 3
            msg = (
                f"第{p_start}-{p_end}段落长度过于均匀 "
                f"({l1}字/{l2}字/{l3}字)"
            )
            findings.append(Finding("WARN", "结构", msg))

    if not has_violation:
        findings.append(Finding("PASS", "结构", "段落长度变化正常"))

    return findings


def check_paragraph_density(
    text: str, config_density: dict
) -> list[Finding]:
    """检测段落碎片化：平均段落长度、单句段落占比、连续短段落。"""
    findings: list[Finding] = []
    if not config_density:
        return findings

    paragraphs = extract_paragraphs(text)
    if len(paragraphs) < 5:
        return findings

    avg_min = config_density.get("avg_length_min", 40)
    single_max_ratio = config_density.get("single_sentence_max_ratio", 0.55)
    consec_max = config_density.get("consecutive_short_max", 5)
    consec_threshold = config_density.get("consecutive_short_threshold", 15)

    lengths = [len(p.replace("\n", "").replace(" ", "")) for p in paragraphs]
    total_chars = sum(lengths)
    para_count = len(paragraphs)
    avg_len = total_chars / para_count

    # 1) 平均段落长度过低
    if avg_len < avg_min:
        findings.append(Finding(
            "WARN", "结构",
            f"段落过度碎片化: 平均段落长度 {avg_len:.0f} 字 "
            f"(阈值 {avg_min} 字, 共 {para_count} 段/{total_chars} 字)"
        ))

    # 2) 单句段落占比过高
    single_count = 0
    for p in paragraphs:
        endings = len(re.findall(r"[。！？]", p))
        if endings <= 1:
            single_count += 1
    single_ratio = single_count / para_count
    if single_ratio > single_max_ratio:
        findings.append(Finding(
            "WARN", "结构",
            f"单句段落过多: {single_count}/{para_count} "
            f"({single_ratio:.0%}, 阈值 {single_max_ratio:.0%})"
        ))

    # 3) 连续短段落过多（节奏段豁免：如果连续短段内容是递进计数则跳过）
    run = 0
    run_start = 0
    for i, length in enumerate(lengths):
        if length <= consec_threshold:
            if run == 0:
                run_start = i
            run += 1
        else:
            if run > consec_max:
                # 检查是否为节奏段（如"一息。两息。三息。"）
                run_texts = [paragraphs[j].strip() for j in range(run_start, run_start + run)]
                is_rhythm = all(len(t) <= 8 for t in run_texts)
                if not is_rhythm:
                    findings.append(Finding(
                        "WARN", "结构",
                        f"第{run_start+1}-{run_start+run}段: "
                        f"连续 {run} 个短段落 (每段<={consec_threshold}字)"
                    ))
            run = 0
    # 处理末尾
    if run > consec_max:
        run_texts = [paragraphs[j].strip() for j in range(run_start, run_start + run)]
        is_rhythm = all(len(t) <= 8 for t in run_texts)
        if not is_rhythm:
            findings.append(Finding(
                "WARN", "结构",
                f"第{run_start+1}-{run_start+run}段: "
                f"连续 {run} 个短段落 (每段<={consec_threshold}字)"
            ))

    if not findings:
        findings.append(Finding("PASS", "结构", "段落密度正常"))

    return findings


def check_sentence_burstiness(text: str) -> list[Finding]:
    """检测句子多样性（Burstiness）：句子长度标准差过低表示句式过于均匀，AI痕迹明显。

    判断逻辑：
    - 提取所有句子（以句号/感叹号/问号结尾）
    - 计算每句字符长度
    - 若标准差 < 低阈值 → WARN（句式单调）
    - 若标准差 >= 正常阈值 → PASS
    - 同时检测句子长度极差（max-min），极差过小也报 WARN
    """
    import math

    findings: list[Finding] = []

    # 提取句子：按中文句末标点切割
    # 最低保留 2 字，确保对话短句（"好。""走！"）也参与统计
    # 保留对话短句是关键：它们是人类散文中长短变化的主要来源
    sentences_raw = re.split(r"[。！？!?]+", text)
    sentences = [s.strip() for s in sentences_raw if len(s.strip()) >= 2]

    if len(sentences) < 10:
        # 章节太短，不做检测
        return findings

    lengths = [len(s) for s in sentences]
    n = len(lengths)
    mean = sum(lengths) / n
    # 使用样本方差（n-1），与校准时的计算方式一致
    variance = sum((x - mean) ** 2 for x in lengths) / (n - 1) if n > 1 else 0
    std = math.sqrt(variance)
    rng = max(lengths) - min(lengths)

    # 使用变异系数（CV = STD/mean），与句子平均长度无关
    # 校准数据：诛仙原文 CV≈0.57，本框架优质章节 CV≈0.57-0.62
    # CV < 0.35 → 句式极度均匀（AI典型特征）→ FAIL
    # CV < 0.45 → 句式偏单调 → WARN
    cv = std / mean if mean > 0 else 0
    cv_fail_threshold = 0.35    # 极度均匀
    cv_warn_threshold = 0.45    # 偏单调

    if cv < cv_fail_threshold:
        findings.append(Finding(
            "FAIL", "句式多样性",
            f"句子长度变异系数极低: CV={cv:.2f} (阈值>{cv_warn_threshold}), "
            f"STD={std:.1f}, 均={mean:.0f}字, 共{n}句 — 句式高度单调，AI痕迹强，必须修改"
        ))
    elif cv < cv_warn_threshold:
        findings.append(Finding(
            "WARN", "句式多样性",
            f"句子长度变异系数偏低: CV={cv:.2f} (阈值>{cv_warn_threshold}), "
            f"STD={std:.1f}, 均={mean:.0f}字 — 长短句变化不足"
        ))
    else:
        findings.append(Finding(
            "PASS", "句式多样性",
            f"句子多样性正常: CV={cv:.2f}, STD={std:.1f}, 均={mean:.0f}字, 共{n}句"
        ))

    return findings


def check_transition_ban(
    text: str, lines: list[str], config_transition: dict,
    offset: int = 0,
) -> list[Finding]:
    """检测 transition_ban 规则。"""
    findings: list[Finding] = []
    words = config_transition.get("words", [])
    text_no_dialogue = remove_dialogue(text)

    for entry in words:
        word = entry["word"]
        severity = entry.get("severity", "medium")
        max_occ = entry.get("max_occurrences", 0)
        exceptions = entry.get("context_exceptions") or []

        total_count = count_in_text(text, word)
        if total_count == 0:
            continue

        if "dialogue" in exceptions:
            effective_count = count_in_text(text_no_dialogue, word)
        else:
            effective_count = total_count

        if effective_count <= max_occ:
            continue

        is_fail = severity in ("critical", "high")
        level = "FAIL" if is_fail else "WARN"
        msg = (
            f'转场词 "{word}" 出现 {effective_count} 次 '
            f"(上限 {max_occ}) [{severity}]"
        )

        detail_lines: list[str] = []
        hits = find_line_numbers(lines, word, offset)
        for lineno, content in hits[:5]:
            detail_lines.append(f"行 {lineno}: {truncate_context(content)}")

        findings.append(Finding(level, "转场", msg, detail_lines))

    return findings


def check_show_dont_tell(
    text: str, lines: list[str], config_emotion: dict,
    offset: int = 0,
) -> list[Finding]:
    """检测 show_dont_tell 规则。"""
    findings: list[Finding] = []
    show_rules = config_emotion.get("show_dont_tell", {})
    tell_patterns = show_rules.get("tell_patterns", [])

    for entry in tell_patterns:
        pat_id = entry["id"]
        regex_str = entry["regex"]
        severity = entry.get("severity", "medium")

        try:
            pattern = re.compile(regex_str)
        except re.error:
            continue

        hits = find_regex_line_numbers(lines, pattern, offset)
        if not hits:
            continue

        is_fail = severity in ("critical", "high")
        level = "FAIL" if is_fail else "WARN"
        note = entry.get("note", "")
        msg = f"{pat_id} 直接告知情绪 [{severity}]"
        if note:
            msg += f" ({note})"

        detail_lines: list[str] = []
        for lineno, matched in hits[:5]:
            detail_lines.append(f"行 {lineno}: {truncate_context(matched)}")

        findings.append(Finding(level, "情感", msg, detail_lines))

    return findings


def check_glossary(
    text: str, lines: list[str], glossary_terms: list[dict],
    offset: int = 0,
) -> list[Finding]:
    """检测 glossary.yaml 术语表中的错别字/形近字。"""
    findings: list[Finding] = []
    has_typo = False

    for entry in glossary_terms:
        correct = entry.get("correct", "")
        wrong_list = entry.get("wrong") or []
        note = entry.get("note", "")

        for wrong_word in wrong_list:
            count = count_in_text(text, wrong_word)
            if count == 0:
                continue
            has_typo = True
            msg = (
                f'错别字 "{wrong_word}" 出现 {count} 次，'
                f'应为 "{correct}"'
            )
            if note:
                msg += f" ({note})"

            detail_lines: list[str] = []
            hits = find_line_numbers(lines, wrong_word, offset)
            for lineno, content in hits[:5]:
                detail_lines.append(f"行 {lineno}: {truncate_context(content)}")
            if len(hits) > 5:
                detail_lines.append(f"... 及其他 {len(hits) - 5} 处")

            findings.append(Finding("FAIL", "错别字", msg, detail_lines))

    if not has_typo and glossary_terms:
        findings.append(Finding("PASS", "错别字", "术语表检查通过"))

    return findings


def check_exposition_dump(
    text: str, lines: list[str], config_dialog: dict,
    offset: int = 0,
) -> list[Finding]:
    """检测 exposition_dump 规则（单条对话过长）。"""
    findings: list[Finding] = []
    dump_rules = config_dialog.get("exposition_dump", {})
    max_length = dump_rules.get("max_dialogue_length", 100)

    dialogues = extract_dialogues(text)
    if not dialogues:
        return findings

    for d in dialogues:
        char_count = len(d)
        if char_count <= max_length:
            continue
        preview = truncate_context(d, 50)
        # 定位对话所在行号
        line_hits = find_line_numbers(lines, d[:20], offset) if len(d) >= 20 else []
        lineno_hint = f"行 {line_hits[0][0]}: " if line_hits else ""
        msg = f"对话过长: {char_count}字 (上限 {max_length}字)"
        findings.append(
            Finding("WARN", "对话", msg, [f'{lineno_hint}内容: \u201c{preview}\u201d'])
        )

    return findings


def check_encoding_corruption(
    text: str, lines: list[str], offset: int = 0,
) -> list[Finding]:
    """检测 U+FFFD (Unicode replacement character) 编码损坏。

    文件写入时偶尔会出现 UTF-8 截断，导致某个汉字变成 U+FFFD。
    此检查在所有其他检查之前运行，发现即 FAIL。
    """
    findings: list[Finding] = []
    marker = "\ufffd"

    if marker not in text:
        findings.append(Finding("PASS", "编码", "无 U+FFFD 损坏"))
        return findings

    detail_lines: list[str] = []
    hits = find_line_numbers(lines, marker, offset)
    for lineno, content in hits[:10]:
        detail_lines.append(f"行 {lineno}: {truncate_context(content)}")
    if len(hits) > 10:
        detail_lines.append(f"... 及其他 {len(hits) - 10} 处")

    findings.append(Finding(
        "FAIL", "编码",
        f"检测到 {len(hits)} 处 U+FFFD 编码损坏（文件写入时 UTF-8 截断）",
        detail_lines,
    ))
    return findings


def check_inner_voice_density(
    text: str, config: dict,
) -> list[Finding]:
    """检测内心声音密度：每500字至少要有短句独白或自言自语。

    短句独白定义：独立段落且长度 <= 10 个中文字符（不含标点空格）
    自言自语定义：正文中出现的短引号对话（引号内 <= 15 字）

    配置字段（在 structural_rules.inner_voice_density 下）:
      window_size: 500  （滑窗字数）
      severity: "high"  （违规级别）
    """
    findings: list[Finding] = []
    structural = config.get("structural_rules", {})
    voice_config = structural.get("inner_voice_density", {})
    if not voice_config:
        return findings

    window_size = voice_config.get("window_size", 500)
    severity = voice_config.get("severity", "high")

    paragraphs = extract_paragraphs(text)
    if not paragraphs:
        return findings

    # 构建段落信息：(段落文本, 段落起始字符位置, 段落纯文字长度, 是否算作内心声音)
    para_info: list[tuple[str, int, int, bool]] = []
    char_pos = 0
    for p in paragraphs:
        # 纯文字长度（去标点空格换行）
        clean = re.sub(r"[\s\u3000，。！？、；：\u201c\u201d\u2018\u2019（）《》【】…—\.\!\?\,\;\:\"\'\(\)\[\]\-]", "", p)
        pure_len = len(clean)

        is_voice = False
        # 条件1：短句独立段落（纯文字 <= 10 字，且不含对话引号）
        if pure_len <= 10 and "\u201c" not in p and "\u201d" not in p:
            is_voice = True
        # 条件2：段落中包含短对话（引号内 <= 15 字）
        short_dialogues = [
            m for m in DIALOGUE_PATTERN.finditer(p)
            if len((m.group(1) or m.group(2) or "")) <= 15
        ]
        if short_dialogues:
            is_voice = True

        para_info.append((p, char_pos, pure_len, is_voice))
        char_pos += pure_len

    # 滑窗检查：累积字数达到 window_size 时，检查窗口内是否有内心声音
    total_chars = sum(info[2] for info in para_info)
    if total_chars < window_size:
        # 全文不到一个窗口，检查整体
        has_voice = any(info[3] for info in para_info)
        if not has_voice:
            level = "FAIL" if severity in ("critical", "high") else "WARN"
            findings.append(Finding(
                level, "内心声音",
                f"全文 {total_chars} 字无内心声音介入（短句独白或自言自语）"
            ))
        else:
            findings.append(Finding("PASS", "内心声音", "密度正常"))
        return findings

    # 滑窗
    window_char_count = 0
    window_has_voice = False
    window_start_para = 0
    violations: list[str] = []

    for i, (p_text, p_pos, p_len, p_voice) in enumerate(para_info):
        window_char_count += p_len
        if p_voice:
            window_has_voice = True

        # 窗口满了
        if window_char_count >= window_size:
            if not window_has_voice:
                # 计算段落范围用于报告
                violations.append(
                    f"第{window_start_para + 1}-{i + 1}段 (~{window_char_count}字) 无内心声音"
                )
            # 滑动窗口：从前面缩减到约一半
            shrink_target = window_char_count // 2
            while window_start_para < i and window_char_count > shrink_target:
                old_len = para_info[window_start_para][2]
                old_voice = para_info[window_start_para][3]
                window_char_count -= old_len
                window_start_para += 1
            # 重新计算窗口内是否有 voice
            window_has_voice = any(
                para_info[j][3] for j in range(window_start_para, i + 1)
            )

    if violations:
        level = "FAIL" if severity in ("critical", "high") else "WARN"
        findings.append(Finding(
            level, "内心声音",
            f"发现 {len(violations)} 处超过 {window_size} 字无内心声音介入",
            violations[:5],
        ))
    else:
        findings.append(Finding("PASS", "内心声音", "密度正常"))

    return findings


def check_translation_tone(
    text: str, lines: list[str], config: dict,
    offset: int = 0,
) -> list[Finding]:
    """检测翻译腔：被动语态、的字链、英式并列、形合连接词、当字句。

    读取 config 中的 translation_tone_patterns 段落，分五个子类别检测。
    """
    findings: list[Finding] = []
    tt_config = config.get("translation_tone_patterns", {})
    if not tt_config:
        return findings

    text_no_dialogue = remove_dialogue(text)
    lines_no_dialogue = split_lines(text_no_dialogue)

    has_violation = False

    # --- A. 被动语态 (passive_voice) ---
    for entry in tt_config.get("passive_voice", []):
        pat_id = entry.get("id", "tt-passive")
        regex_str = entry.get("regex", "")
        severity = entry.get("severity", "high")
        max_occ = entry.get("max_occurrences", 1)
        exceptions = entry.get("context_exceptions") or []

        try:
            pattern = re.compile(regex_str)
        except re.error:
            findings.append(Finding("WARN", "翻译腔", f"{pat_id} 正则编译失败: {regex_str}"))
            continue

        if "dialogue" in exceptions:
            hits = find_regex_line_numbers(lines_no_dialogue, pattern, offset)
        else:
            hits = find_regex_line_numbers(lines, pattern, offset)

        if len(hits) <= max_occ:
            continue

        has_violation = True
        is_fail = severity in ("critical", "high")
        level = "FAIL" if is_fail else "WARN"
        note = entry.get("note", "")
        msg = f'{pat_id} 被动语态 {len(hits)} 处 (上限 {max_occ}) [{severity}]'
        if note:
            msg += f" -- {note}"

        detail_lines: list[str] = []
        for lineno, matched in hits[:5]:
            detail_lines.append(f"行 {lineno}: {truncate_context(matched)}")
        if len(hits) > 5:
            detail_lines.append(f"... 及其他 {len(hits) - 5} 处")

        findings.append(Finding(level, "翻译腔", msg, detail_lines))

    # --- B. 的字链 (de_chain) ---
    for entry in tt_config.get("de_chain", []):
        pat_id = entry.get("id", "tt-de")
        regex_str = entry.get("regex", "")
        severity = entry.get("severity", "medium")
        max_occ = entry.get("max_occurrences", 2)

        try:
            pattern = re.compile(regex_str)
        except re.error:
            findings.append(Finding("WARN", "翻译腔", f"{pat_id} 正则编译失败: {regex_str}"))
            continue

        hits = find_regex_line_numbers(lines, pattern, offset)
        if len(hits) <= max_occ:
            continue

        has_violation = True
        is_fail = severity in ("critical", "high")
        level = "FAIL" if is_fail else "WARN"
        note = entry.get("note", "")
        msg = f'{pat_id} 的字链 {len(hits)} 处 (上限 {max_occ}) [{severity}]'
        if note:
            msg += f" -- {note}"

        detail_lines = []
        for lineno, matched in hits[:5]:
            detail_lines.append(f"行 {lineno}: {truncate_context(matched)}")
        if len(hits) > 5:
            detail_lines.append(f"... 及其他 {len(hits) - 5} 处")

        findings.append(Finding(level, "翻译腔", msg, detail_lines))

    # (C. 英语隐喻直译 -- 语义层面，由 LLM 写作时自检，scan-text 不做自动检测)

    # --- D. 形合连接词 (formal_connectors) ---
    fc_config = tt_config.get("formal_connectors", {})
    fc_words = fc_config.get("words", [])
    for entry in fc_words:
        word = entry.get("word", "")
        severity = entry.get("severity", "medium")
        max_occ = entry.get("max_occurrences", 1)
        exceptions = entry.get("context_exceptions") or []

        total_count = count_in_text(text, word)
        if total_count == 0:
            continue

        if "dialogue" in exceptions:
            effective_count = count_in_text(text_no_dialogue, word)
        else:
            effective_count = total_count

        if effective_count <= max_occ:
            continue

        has_violation = True
        is_fail = severity in ("critical", "high")
        level = "FAIL" if is_fail else "WARN"
        note = entry.get("note", "")
        msg = (
            f'形合连接词 "{word}" {effective_count} 处 '
            f'(上限 {max_occ}) [{severity}]'
        )
        if note:
            msg += f" -- {note}"

        detail_lines = []
        hits = find_line_numbers(lines, word, offset)
        for lineno, content in hits[:5]:
            detail_lines.append(f"行 {lineno}: {truncate_context(content)}")
        if len(hits) > 5:
            detail_lines.append(f"... 及其他 {len(hits) - 5} 处")

        findings.append(Finding(level, "翻译腔", msg, detail_lines))

    # --- E. "当...时"句式 (temporal_dang) ---
    for entry in tt_config.get("temporal_dang", []):
        pat_id = entry.get("id", "tt-dang")
        regex_str = entry.get("regex", "")
        severity = entry.get("severity", "medium")
        max_occ = entry.get("max_occurrences", 1)

        try:
            pattern = re.compile(regex_str)
        except re.error:
            findings.append(Finding("WARN", "翻译腔", f"{pat_id} 正则编译失败: {regex_str}"))
            continue

        hits = find_regex_line_numbers(lines, pattern, offset)
        if len(hits) <= max_occ:
            continue

        has_violation = True
        is_fail = severity in ("critical", "high")
        level = "FAIL" if is_fail else "WARN"
        note = entry.get("note", "")
        msg = f'{pat_id} "当...时"句式 {len(hits)} 处 (上限 {max_occ}) [{severity}]'
        if note:
            msg += f" -- {note}"

        detail_lines = []
        for lineno, matched in hits[:5]:
            detail_lines.append(f"行 {lineno}: {truncate_context(matched)}")
        if len(hits) > 5:
            detail_lines.append(f"... 及其他 {len(hits) - 5} 处")

        findings.append(Finding(level, "翻译腔", msg, detail_lines))

    if not has_violation:
        findings.append(Finding("PASS", "翻译腔", "无翻译腔问题"))

    return findings


def check_sensory_density(
    text: str, config: dict,
) -> list[Finding]:
    """检测环境描写密度：感官词根在滑动窗口中的出现频率。

    读取 config 中的 sensory_rules 段落，使用感官关键词列表检测。
    两层检查：
    1. 滑动窗口：每 window_size 字至少命中 min_per_window 个感官词根
    2. 全文密度：每千字至少 min_density_per_1000 个感官词
    """
    findings: list[Finding] = []
    sr_config = config.get("sensory_rules", {})
    if not sr_config:
        return findings

    # 收集所有感官关键词（去重，防止跨类别重复计数）
    keywords_map = sr_config.get("sensory_keywords", {})
    kw_set: set[str] = set()
    for category_words in keywords_map.values():
        if isinstance(category_words, list):
            kw_set.update(category_words)
    all_keywords: list[str] = sorted(kw_set)

    if not all_keywords:
        return findings

    window_size = sr_config.get("window_size", 800)
    min_per_window = sr_config.get("min_per_window", 1)
    min_density = sr_config.get("min_density_per_1000", 3)
    severity = sr_config.get("severity", "medium")

    # 去除对话内容，环境描写主要在叙述部分
    text_clean = remove_dialogue(text)
    # 去除标点空格，只留中文字符用于计算
    chars_only = re.sub(r"[^\u4e00-\u9fff]", "", text_clean)
    total_chars = len(chars_only)

    if total_chars < 200:
        return findings

    # 统计全文感官词命中
    total_hits = 0
    for kw in all_keywords:
        total_hits += chars_only.count(kw)

    # 全文密度检查
    density = total_hits / total_chars * 1000 if total_chars > 0 else 0
    if density < min_density:
        level = "FAIL" if severity in ("critical", "high") else "WARN"
        findings.append(Finding(
            level, "环境描写",
            f"感官词密度偏低: {density:.1f}/千字 "
            f"(阈值 {min_density}/千字, 共 {total_hits} 个感官词/{total_chars} 字)"
        ))

    # 滑动窗口检查
    if total_chars >= window_size:
        cold_zones: list[str] = []
        step = window_size // 2  # 半窗口步进
        for start in range(0, total_chars - window_size + 1, step):
            window = chars_only[start:start + window_size]
            window_hits = 0
            for kw in all_keywords:
                window_hits += window.count(kw)
            if window_hits < min_per_window:
                # 估算段落位置（粗略）
                approx_percent = int(start / total_chars * 100)
                cold_zones.append(f"约{approx_percent}%-{min(approx_percent + int(window_size/total_chars*100), 100)}%处")

        if cold_zones:
            level = "FAIL" if severity in ("critical", "high") else "WARN"
            # 只报告前5个
            findings.append(Finding(
                level, "环境描写",
                f"发现 {len(cold_zones)} 个感官空白区 (每{window_size}字窗口无感官词)",
                cold_zones[:5],
            ))

    if not findings:
        findings.append(Finding("PASS", "环境描写", f"感官密度正常 ({density:.1f}/千字)"))

    return findings


# ------------------------------------------------------------------
# 单文件扫描
# ------------------------------------------------------------------
def scan_file(
    filepath: Path, config: dict, glossary_terms: list[dict] | None = None,
) -> list[Finding]:
    """对单个文件执行全部检测，返回所有 Finding。"""
    raw_text = read_text(filepath)
    text, header_offset = strip_markdown_header(raw_text)
    lines = split_lines(text)

    all_findings: list[Finding] = []

    # 0-pre) encoding corruption check (must be first)
    all_findings.extend(check_encoding_corruption(text, lines, header_offset))

    # 0) glossary typo check
    if glossary_terms:
        all_findings.extend(check_glossary(text, lines, glossary_terms, header_offset))

    # A) banned_words
    banned_words = config.get("banned_words", [])
    all_findings.extend(check_banned_words(text, lines, banned_words, header_offset))

    # B) banned_patterns
    banned_patterns = config.get("banned_patterns", [])
    all_findings.extend(check_banned_patterns(text, lines, banned_patterns, header_offset))

    # structural_rules
    structural = config.get("structural_rules", {})

    # C) chapter_opening_ban
    opening = structural.get("chapter_opening_ban", {})
    all_findings.extend(check_chapter_opening(text, opening))

    # D) ending_ban
    ending = structural.get("ending_ban", {})
    all_findings.extend(check_ending(text, ending))

    # E) paragraph_variation
    variation = structural.get("paragraph_variation", {})
    all_findings.extend(check_paragraph_variation(text, variation))

    # E2) paragraph_density (碎片化检测)
    density = structural.get("paragraph_density", {})
    all_findings.extend(check_paragraph_density(text, density))

    # F) transition_ban
    transition = structural.get("transition_ban", {})
    all_findings.extend(check_transition_ban(text, lines, transition, header_offset))

    # G) show_dont_tell
    emotion_rules = config.get("emotion_rules", {})
    all_findings.extend(check_show_dont_tell(text, lines, emotion_rules, header_offset))

    # H) exposition_dump
    dialog_rules = config.get("dialog_rules", {})
    all_findings.extend(check_exposition_dump(text, lines, dialog_rules, header_offset))

    # I) inner_voice_density
    all_findings.extend(check_inner_voice_density(text, config))

    # J) translation_tone (翻译腔检测)
    all_findings.extend(check_translation_tone(text, lines, config, header_offset))

    # K) sensory_density (环境描写密度)
    all_findings.extend(check_sensory_density(text, config))

    # L) sentence_burstiness (句子多样性)
    all_findings.extend(check_sentence_burstiness(text))

    return all_findings


# ------------------------------------------------------------------
# 报告输出
# ------------------------------------------------------------------
def format_report(filename: str, findings: list[Finding]) -> str:
    """将检测结果格式化为可读的文本报告。"""
    parts: list[str] = []
    parts.append(SEPARATOR_THICK)
    parts.append(f"  扫描报告: {filename}")
    parts.append(SEPARATOR_THICK)
    parts.append("")

    fail_count = 0
    warn_count = 0
    pass_count = 0

    for f in findings:
        if f.level == "FAIL":
            fail_count += 1
        elif f.level == "WARN":
            warn_count += 1
        else:
            pass_count += 1

        prefix = f"  {f.level:4s} | [{f.category}] {f.message}"
        parts.append(prefix)
        for detail in f.details:
            parts.append(f"        {detail}")

    parts.append("")
    parts.append(SEPARATOR_THIN)
    parts.append(f"  统计: {fail_count} FAIL / {warn_count} WARN / {pass_count} PASS")

    if fail_count > 0:
        parts.append("  结论: FAIL -- 必须修复后重新提交")
    elif warn_count > 0:
        parts.append("  结论: WARN -- 建议修复后提交")
    else:
        parts.append("  结论: PASS -- 全部通过")

    parts.append(SEPARATOR_THICK)
    return "\n".join(parts)


def format_directory_summary(
    file_results: list[tuple[str, int, int, int]],
) -> str:
    """输出目录扫描的汇总报告。"""
    parts: list[str] = []
    parts.append("")
    parts.append(SEPARATOR_THICK)
    parts.append("  汇总报告")
    parts.append(SEPARATOR_THICK)
    parts.append("")

    total_fail = 0
    total_warn = 0
    total_pass = 0

    for filename, fc, wc, pc in file_results:
        status = "FAIL" if fc > 0 else ("WARN" if wc > 0 else "PASS")
        parts.append(
            f"  {status:4s} | {filename} "
            f"({fc} FAIL / {wc} WARN / {pc} PASS)"
        )
        total_fail += fc
        total_warn += wc
        total_pass += pc

    parts.append("")
    parts.append(SEPARATOR_THIN)
    parts.append(
        f"  文件数: {len(file_results)} | "
        f"总计: {total_fail} FAIL / {total_warn} WARN / {total_pass} PASS"
    )

    if total_fail > 0:
        parts.append("  最终结论: FAIL -- 存在必须修复的问题")
    elif total_warn > 0:
        parts.append("  最终结论: WARN -- 存在建议修复的问题")
    else:
        parts.append("  最终结论: PASS -- 全部文件通过检查")

    parts.append(SEPARATOR_THICK)
    return "\n".join(parts)


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------
def collect_md_files(target: Path) -> list[Path]:
    """收集目标路径下的所有 .md 文件。"""
    if target.is_file():
        return [target]
    if target.is_dir():
        files = sorted(target.rglob("*.md"))
        if not files:
            print(f"[警告] 目录中未找到 .md 文件: {target}", file=sys.stderr)
        return files
    print(f"[错误] 路径不存在: {target}", file=sys.stderr)
    sys.exit(EXIT_ERROR)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="小说章节去AI感文本扫描工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python scripts/scan-text.py chapters/\n"
            "  python scripts/scan-text.py --chapter chapters/arc-001/chapter-0001.md"
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="要扫描的文件或目录路径",
    )
    parser.add_argument(
        "--chapter",
        help="指定扫描单个章节文件",
    )
    args = parser.parse_args()

    target_str = args.chapter if args.chapter else args.target
    if not target_str:
        parser.print_help()
        return EXIT_ERROR

    project_root = find_project_root()
    config = load_config(project_root)
    glossary_terms = load_glossary(project_root)

    target_path = Path(target_str)
    if not target_path.is_absolute():
        target_path = Path.cwd() / target_path

    md_files = collect_md_files(target_path)
    if not md_files:
        print("[信息] 没有要扫描的文件。")
        return EXIT_CLEAN

    has_failure = False
    file_results: list[tuple[str, int, int, int]] = []

    for filepath in md_files:
        findings = scan_file(filepath, config, glossary_terms)
        filename = filepath.name
        report = format_report(filename, findings)
        print(report)

        fc = sum(1 for f in findings if f.level == "FAIL")
        wc = sum(1 for f in findings if f.level == "WARN")
        pc = sum(1 for f in findings if f.level == "PASS")
        file_results.append((filename, fc, wc, pc))

        if fc > 0:
            has_failure = True

    if len(md_files) > 1:
        summary = format_directory_summary(file_results)
        print(summary)

    return EXIT_VIOLATIONS if has_failure else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
