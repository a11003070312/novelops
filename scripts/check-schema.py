#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YAML Schema Validator for Novel Writing Framework
--------------------------------------------------
Validates all YAML files in the project against their expected schemas.

Usage:
    python scripts/check-schema.py              # validate all files
    python scripts/check-schema.py <file_path>  # validate a specific file
"""

from __future__ import annotations

import datetime
import io
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
    print("ERROR: PyYAML is required. Install it with:")
    print("  pip install pyyaml")
    sys.exit(2)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEPARATOR_THICK = "=" * 60
SEPARATOR_THIN = "-" * 60

VALID_TARGET_AUDIENCE = ["男频", "女频"]
VALID_ARC_STATUS = ["planned", "in_progress", "completed"]
VALID_CHAPTER_STATUS = ["planned", "draft", "review", "published"]
VALID_SESSION_ACTION = ["write_chapter", "new_arc", "audit", "plan_segment"]
VALID_FACT_CATEGORY = [
    "appearance",
    "measurement",
    "character_statement",
    "world_rule",
    "event_detail",
    "environment",
    "time_marker",
    "item_detail",
    "naming",
]
VALID_FACT_PERMANENCE = ["permanent", "temporary", "conditional"]
VALID_CHAR_ROLE = ["protagonist", "heroine", "villain", "antagonist", "supporting", "minor", "neutral"]
VALID_CHAR_STATUS = ["alive", "dead", "missing", "unknown"]
VALID_LOCATION_TYPE = ["居住区", "坊市", "秘境", "野外", "地下", "建筑", "战场", "其他"]
VALID_SEGMENT_STATUS = ["planned", "in_progress", "completed"]
VALID_SEGMENT_HIGHLIGHT = ["智斗", "战斗", "情感", "揭秘", "成长", "日常"]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def find_project_root() -> Path:
    """Walk up from the script location looking for ENTRY.md."""
    current = Path(__file__).resolve().parent
    for _ in range(20):
        if (current / "ENTRY.md").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    print("ERROR: 无法定位项目根目录（未找到 ENTRY.md）")
    sys.exit(2)


def load_yaml_file(filepath: Path):
    """Load a YAML file with UTF-8 encoding. Returns parsed data or None."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        return ("YAML_PARSE_ERROR", str(exc))
    except OSError as exc:
        return ("IO_ERROR", str(exc))


def extract_frontmatter(filepath: Path):
    """Extract YAML frontmatter from a Markdown file (between --- delimiters)."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        return ("IO_ERROR", str(exc))

    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        # Try with possible leading whitespace / BOM
        match = re.match(r"^\s*---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return ("NO_FRONTMATTER", "未找到 YAML 前置元数据 (--- ... ---)")

    try:
        data = yaml.safe_load(match.group(1))
        if data is None:
            return ("EMPTY_FRONTMATTER", "YAML 前置元数据为空")
        return data
    except yaml.YAMLError as exc:
        return ("YAML_PARSE_ERROR", str(exc))


def is_nonempty_string(value) -> bool:
    return isinstance(value, str) and len(value.strip()) > 0


def is_list_of_strings(value) -> bool:
    if not isinstance(value, list):
        return False
    return all(isinstance(item, str) for item in value)


def relative_path(filepath: Path, root: Path) -> str:
    """Return a clean forward-slash relative path for display."""
    try:
        return str(filepath.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(filepath).replace("\\", "/")


# ---------------------------------------------------------------------------
# Result collector
# ---------------------------------------------------------------------------

class FileResult:
    """Collects PASS / FAIL / WARN messages for a single file."""

    def __init__(self, display_path: str):
        self.display_path = display_path
        self.messages: list[tuple[str, str]] = []  # (level, message)

    def fail(self, msg: str):
        self.messages.append(("FAIL", msg))

    def warn(self, msg: str):
        self.messages.append(("WARN", msg))

    def ok(self, msg: str = "全部字段校验通过"):
        self.messages.append(("PASS", msg))

    @property
    def has_fail(self) -> bool:
        return any(level == "FAIL" for level, _ in self.messages)

    @property
    def has_warn(self) -> bool:
        return any(level == "WARN" for level, _ in self.messages)

    @property
    def status(self) -> str:
        if self.has_fail:
            return "FAIL"
        if self.has_warn:
            return "WARN"
        return "PASS"


# ---------------------------------------------------------------------------
# Schema validators
# ---------------------------------------------------------------------------

def validate_project_yaml(data, result: FileResult):
    """Validate config/project.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # Required: title
    if "title" not in data:
        result.fail("缺少必填字段: title")
    elif not is_nonempty_string(data["title"]):
        result.fail("title 不能为空字符串")

    # Required: genre
    if "genre" not in data:
        result.fail("缺少必填字段: genre")
    elif not is_nonempty_string(data["genre"]):
        result.fail("genre 不能为空字符串")

    # Required: target_audience
    if "target_audience" not in data:
        result.fail("缺少必填字段: target_audience")
    else:
        val = data["target_audience"]
        if not is_nonempty_string(val):
            result.fail("target_audience 不能为空字符串")
        elif val not in VALID_TARGET_AUDIENCE:
            result.fail(
                f'target_audience 值 "{val}" 不在允许范围 '
                f"[{'/'.join(VALID_TARGET_AUDIENCE)}]"
            )

    # Required: chapter_word_count
    if "chapter_word_count" not in data:
        result.fail("缺少必填字段: chapter_word_count")
    else:
        cwc = data["chapter_word_count"]
        if not isinstance(cwc, list) or len(cwc) != 2:
            result.fail("chapter_word_count 必须是包含 2 个整数的列表")
        elif not all(isinstance(v, int) for v in cwc):
            result.fail("chapter_word_count 的每个元素必须是整数")

    # Required: core_hooks
    if "core_hooks" not in data:
        result.fail("缺少必填字段: core_hooks")
    else:
        hooks = data["core_hooks"]
        if not isinstance(hooks, list):
            result.fail("core_hooks 必须是列表")
        elif len(hooks) == 0:
            result.fail("core_hooks 不能为空列表")
        else:
            non_empty = [h for h in hooks if is_nonempty_string(h)]
            if len(non_empty) == 0:
                result.fail("core_hooks 必须至少包含 1 个非空字符串")

    # Optional warnings
    if "one_liner" in data and not is_nonempty_string(data.get("one_liner", "")):
        result.warn("one_liner 为空（建议填写）")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_arc_yaml(data, result: FileResult):
    """Validate outline/arcs/arc-*.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # Required: id
    if "id" not in data:
        result.fail("缺少必填字段: id")
    else:
        val = data["id"]
        if not isinstance(val, str) or not re.match(r"^arc-\d+$", val):
            result.fail(f'id 值 "{val}" 不匹配格式 arc-\\d+')

    # Required: title
    if "title" not in data:
        result.fail("缺少必填字段: title")
    elif not is_nonempty_string(data["title"]):
        result.fail("title 不能为空字符串")

    # Required: chapter_range
    if "chapter_range" not in data:
        result.fail("缺少必填字段: chapter_range")
    else:
        cr = data["chapter_range"]
        if not isinstance(cr, list) or len(cr) != 2:
            result.fail("chapter_range 必须是包含 2 个整数的列表")
        elif not all(isinstance(v, int) for v in cr):
            result.fail("chapter_range 的每个元素必须是整数")
        elif cr[1] < cr[0]:
            result.fail(
                f"chapter_range 结束值 ({cr[1]}) 不能小于起始值 ({cr[0]})"
            )

    # Required: status
    if "status" not in data:
        result.fail("缺少必填字段: status")
    else:
        val = data["status"]
        if val not in VALID_ARC_STATUS:
            result.fail(
                f'status 值 "{val}" 不在允许范围 '
                f"[{'/'.join(VALID_ARC_STATUS)}]"
            )

    # Required: arc_goal
    if "arc_goal" not in data:
        result.fail("缺少必填字段: arc_goal")
    elif not is_nonempty_string(data["arc_goal"]):
        result.fail("arc_goal 不能为空字符串")

    # Required: characters (non-empty list)
    if "characters" not in data:
        result.fail("缺少必填字段: characters")
    elif not isinstance(data["characters"], list):
        result.fail("characters 必须是列表")
    elif len(data["characters"]) == 0:
        result.fail("characters 不能为空列表（至少需要 1 个角色）")

    # Required: key_events (non-empty list)
    if "key_events" not in data:
        result.fail("缺少必填字段: key_events")
    elif not isinstance(data["key_events"], list):
        result.fail("key_events 必须是列表")
    elif len(data["key_events"]) == 0:
        result.fail("key_events 不能为空列表（至少需要 1 个事件）")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_chapter_yaml(data, result: FileResult):
    """Validate outline/chapters/chapter-*.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # Required: id
    if "id" not in data:
        result.fail("缺少必填字段: id")
    else:
        val = data["id"]
        if not isinstance(val, str) or not re.match(r"^chapter-\d+$", val):
            result.fail(f'id 值 "{val}" 不匹配格式 chapter-\\d+')

    # Required: arc
    if "arc" not in data:
        result.fail("缺少必填字段: arc")
    else:
        val = data["arc"]
        if not isinstance(val, str) or not re.match(r"^arc-\d+$", val):
            result.fail(f'arc 值 "{val}" 不匹配格式 arc-\\d+')

    # Required: title
    if "title" not in data:
        result.fail("缺少必填字段: title")
    elif not is_nonempty_string(data["title"]):
        result.fail("title 不能为空字符串")

    # Required: status
    if "status" not in data:
        result.fail("缺少必填字段: status")
    else:
        val = data["status"]
        if val not in VALID_CHAPTER_STATUS:
            result.fail(
                f'status 值 "{val}" 不在允许范围 '
                f"[{'/'.join(VALID_CHAPTER_STATUS)}]"
            )

    # Required: scene
    if "scene" not in data:
        result.fail("缺少必填字段: scene")
    elif not is_nonempty_string(data["scene"]):
        result.fail("scene 不能为空字符串")

    # Required: characters_present (list with >= 1 string)
    if "characters_present" not in data:
        result.fail("缺少必填字段: characters_present")
    else:
        cp = data["characters_present"]
        if not isinstance(cp, list):
            result.fail("characters_present 必须是列表")
        elif len(cp) == 0:
            result.fail("characters_present 不能为空列表（至少需要 1 个角色）")
        elif not all(isinstance(s, str) for s in cp):
            result.fail("characters_present 的每个元素必须是字符串")

    # Required: objectives (list with >= 1 non-empty string)
    if "objectives" not in data:
        result.fail("缺少必填字段: objectives")
    else:
        obj = data["objectives"]
        if not isinstance(obj, list):
            result.fail("objectives 必须是列表")
        elif len(obj) == 0:
            result.fail("objectives 不能为空列表（至少需要 1 个目标）")
        else:
            non_empty = [o for o in obj if is_nonempty_string(o)]
            if len(non_empty) == 0:
                result.fail("objectives 必须至少包含 1 个非空字符串")

    # Required: chapter_hook
    if "chapter_hook" not in data:
        result.fail("缺少必填字段: chapter_hook")
    elif not is_nonempty_string(data["chapter_hook"]):
        result.fail("chapter_hook 不能为空字符串")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_session_state_yaml(data, result: FileResult):
    """Validate state/session-state.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # Required: last_session (dict)
    if "last_session" not in data:
        result.fail("缺少必填字段: last_session")
    else:
        ls = data["last_session"]
        if not isinstance(ls, dict):
            result.fail("last_session 必须是字典")
        else:
            if "timestamp" not in ls:
                result.fail("last_session 缺少必填字段: timestamp")
            else:
                ts = ls["timestamp"]
                if isinstance(ts, datetime.datetime):
                    pass  # PyYAML 将未加引号的 ISO 日期解析为 datetime，合法
                elif not is_nonempty_string(ts):
                    result.fail("last_session.timestamp 不能为空字符串")

            if "chapter_completed" not in ls:
                result.fail("last_session 缺少必填字段: chapter_completed")
            elif not isinstance(ls["chapter_completed"], int):
                result.fail("last_session.chapter_completed 必须是整数")

            if "all_phases_done" not in ls:
                result.fail("last_session 缺少必填字段: all_phases_done")
            elif not isinstance(ls["all_phases_done"], bool):
                result.fail("last_session.all_phases_done 必须是布尔值")

            if "incomplete_phase" not in ls:
                result.fail("last_session 缺少必填字段: incomplete_phase")

    # Required: next_session (dict)
    if "next_session" not in data:
        result.fail("缺少必填字段: next_session")
    else:
        ns = data["next_session"]
        if not isinstance(ns, dict):
            result.fail("next_session 必须是字典")
        else:
            if "expected_action" not in ns:
                result.fail("next_session 缺少必填字段: expected_action")
            else:
                val = ns["expected_action"]
                if val not in VALID_SESSION_ACTION:
                    result.fail(
                        f'next_session.expected_action 值 "{val}" 不在允许范围 '
                        f"[{'/'.join(VALID_SESSION_ACTION)}]"
                    )

            if "chapter_to_write" not in ns:
                result.fail("next_session 缺少必填字段: chapter_to_write")
            elif not isinstance(ns["chapter_to_write"], int):
                result.fail("next_session.chapter_to_write 必须是整数")

            if "arc" not in ns:
                result.fail("next_session 缺少必填字段: arc")
            else:
                val = ns["arc"]
                if not isinstance(val, str) or not re.match(r"^arc-\d+$", val):
                    result.fail(f'next_session.arc 值 "{val}" 不匹配格式 arc-\\d+')

    # Optional: autonomous_mode + current_segment（联合校验：两者应同时存在或同时缺失）
    autonomous_mode = data.get("autonomous_mode")
    cs = data.get("current_segment")
    if autonomous_mode is True and (cs is None or cs.get("id") is None):
        result.warn(
            "autonomous_mode=true 但 current_segment.id 未设置，"
            "自主模式下应同时记录当前段落ID"
        )
    if cs is not None and isinstance(cs, dict):
        seg_id = cs.get("id")
        if seg_id is not None:
            if not isinstance(seg_id, str) or not re.match(r"^seg-\d+-\d+$", str(seg_id)):
                result.warn(f'current_segment.id 值 "{seg_id}" 不匹配格式 seg-\\d+-\\d+')
        chap_done = cs.get("chapter_completed_in_segment")
        if chap_done is not None and not isinstance(chap_done, int):
            result.warn("current_segment.chapter_completed_in_segment 应为整数")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_plot_threads_yaml(data, result: FileResult):
    """Validate state/plot-threads.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # active_threads (optional, but if present validate entries)
    if "active_threads" in data and isinstance(data["active_threads"], list):
        for idx, thread in enumerate(data["active_threads"]):
            prefix = f"active_threads[{idx}]"
            if not isinstance(thread, dict):
                result.fail(f"{prefix} 必须是字典")
                continue
            if "id" not in thread:
                result.fail(f"{prefix} 缺少必填字段: id")
            elif not is_nonempty_string(thread["id"]):
                result.fail(f"{prefix}.id 不能为空字符串")
            if "name" not in thread:
                result.fail(f"{prefix} 缺少必填字段: name")
            elif not is_nonempty_string(thread["name"]):
                result.fail(f"{prefix}.name 不能为空字符串")
            if "planted_chapter" not in thread:
                result.fail(f"{prefix} 缺少必填字段: planted_chapter")
            elif not isinstance(thread["planted_chapter"], int):
                result.fail(f"{prefix}.planted_chapter 必须是整数")

    # resolved_threads (optional, but if present validate entries)
    if "resolved_threads" in data and isinstance(data["resolved_threads"], list):
        for idx, thread in enumerate(data["resolved_threads"]):
            prefix = f"resolved_threads[{idx}]"
            if not isinstance(thread, dict):
                result.fail(f"{prefix} 必须是字典")
                continue
            if "id" not in thread:
                result.fail(f"{prefix} 缺少必填字段: id")
            elif not is_nonempty_string(thread["id"]):
                result.fail(f"{prefix}.id 不能为空字符串")
            if "name" not in thread:
                result.fail(f"{prefix} 缺少必填字段: name")
            elif not is_nonempty_string(thread["name"]):
                result.fail(f"{prefix}.name 不能为空字符串")
            if "resolved_chapter" not in thread:
                result.fail(f"{prefix} 缺少必填字段: resolved_chapter")
            elif not isinstance(thread["resolved_chapter"], int):
                result.fail(f"{prefix}.resolved_chapter 必须是整数")

    # abandoned_threads (optional, but if present validate entries)
    if "abandoned_threads" in data and isinstance(data["abandoned_threads"], list):
        for idx, thread in enumerate(data["abandoned_threads"]):
            prefix = f"abandoned_threads[{idx}]"
            if not isinstance(thread, dict):
                result.fail(f"{prefix} 必须是字典")
                continue
            if "id" not in thread:
                result.fail(f"{prefix} 缺少必填字段: id")
            elif not is_nonempty_string(thread["id"]):
                result.fail(f"{prefix}.id 不能为空字符串")
            if "name" not in thread:
                result.fail(f"{prefix} 缺少必填字段: name")
            elif not is_nonempty_string(thread["name"]):
                result.fail(f"{prefix}.name 不能为空字符串")
            if "abandoned_chapter" not in thread:
                result.fail(f"{prefix} 缺少必填字段: abandoned_chapter")
            elif not isinstance(thread["abandoned_chapter"], int):
                result.fail(f"{prefix}.abandoned_chapter 必须是整数")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_plot_pattern_tracker_yaml(data, result: FileResult):
    """Validate state/plot-pattern-tracker.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # conflict_patterns must be a list
    if "conflict_patterns" in data:
        if not isinstance(data["conflict_patterns"], list):
            result.fail("conflict_patterns 必须是列表")
        else:
            for idx, entry in enumerate(data["conflict_patterns"]):
                prefix = f"conflict_patterns[{idx}]"
                if not isinstance(entry, dict):
                    result.fail(f"{prefix} 必须是字典")
                    continue
                if "pattern" not in entry:
                    result.fail(f"{prefix} 缺少必填字段: pattern")
                if "occurrences" in entry and not isinstance(entry["occurrences"], list):
                    result.fail(f"{prefix}.occurrences 必须是列表")

    # emotional_beats must be a list
    if "emotional_beats" in data:
        if not isinstance(data["emotional_beats"], list):
            result.fail("emotional_beats 必须是列表")

    # recent_openings must be a list
    if "recent_openings" in data:
        if not isinstance(data["recent_openings"], list):
            result.fail("recent_openings 必须是列表")
        else:
            for idx, entry in enumerate(data["recent_openings"]):
                prefix = f"recent_openings[{idx}]"
                if not isinstance(entry, dict):
                    result.fail(f"{prefix} 必须是字典")
                    continue
                if "chapter" not in entry:
                    result.fail(f"{prefix} 缺少必填字段: chapter")
                elif not isinstance(entry["chapter"], int):
                    result.fail(f"{prefix}.chapter 必须是整数")
                if "type" not in entry:
                    result.fail(f"{prefix} 缺少必填字段: type")

    # consecutive_same_type must be a dict
    if "consecutive_same_type" in data:
        cst = data["consecutive_same_type"]
        if not isinstance(cst, dict):
            result.fail("consecutive_same_type 必须是字典")
        else:
            if "count" in cst and not isinstance(cst["count"], int):
                result.fail("consecutive_same_type.count 必须是整数")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_relationships_yaml(data, result: FileResult):
    """Validate state/relationships.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    if "relationships" in data and isinstance(data["relationships"], list):
        for idx, rel in enumerate(data["relationships"]):
            prefix = f"relationships[{idx}]"
            if not isinstance(rel, dict):
                result.fail(f"{prefix} 必须是字典")
                continue
            # 支持两种字段名：from/to（模板默认）或 source/target（项目可选）
            has_from = "from" in rel or "source" in rel
            has_to = "to" in rel or "target" in rel
            if not has_from:
                result.fail(f"{prefix} 缺少必填字段: source (或 from)")
            else:
                val = rel.get("from") or rel.get("source")
                if not is_nonempty_string(val):
                    result.fail(f"{prefix}.source 不能为空字符串")
            if not has_to:
                result.fail(f"{prefix} 缺少必填字段: target (或 to)")
            else:
                val = rel.get("to") or rel.get("target")
                if not is_nonempty_string(val):
                    result.fail(f"{prefix}.target 不能为空字符串")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_facts_yaml(data, result: FileResult):
    """Validate state/facts/chapter-*.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # Root-level chapter field
    if "chapter" not in data:
        result.fail("缺少必填字段: chapter")
    elif not isinstance(data["chapter"], int):
        result.fail("chapter 必须是整数")

    facts = data.get("facts")
    if facts is None or (isinstance(facts, list) and len(facts) == 0):
        if not result.has_fail:
            result.ok("facts 列表为空（尚无记录）")
        return

    if not isinstance(facts, list):
        result.fail("facts 必须是列表")
        return

    for idx, fact in enumerate(facts):
        prefix = f"facts[{idx}]"
        if not isinstance(fact, dict):
            result.fail(f"{prefix} 必须是字典")
            continue

        # id
        if "id" not in fact:
            result.fail(f"{prefix} 缺少必填字段: id")
        else:
            val = fact["id"]
            if not isinstance(val, str) or not re.match(r"^fact-\d+-\d+$", val):
                result.fail(f'{prefix}.id 值 "{val}" 不匹配格式 fact-\\d+-\\d+')

        # category
        if "category" not in fact:
            result.fail(f"{prefix} 缺少必填字段: category")
        else:
            val = fact["category"]
            if val not in VALID_FACT_CATEGORY:
                result.fail(
                    f'{prefix}.category 值 "{val}" 不在允许范围 '
                    f"[{'/'.join(VALID_FACT_CATEGORY)}]"
                )

        # content
        if "content" not in fact:
            result.fail(f"{prefix} 缺少必填字段: content")
        elif not is_nonempty_string(fact["content"]):
            result.fail(f"{prefix}.content 不能为空字符串")

        # characters
        if "characters" not in fact:
            result.fail(f"{prefix} 缺少必填字段: characters")
        elif not is_list_of_strings(fact["characters"]):
            result.fail(f"{prefix}.characters 必须是字符串列表")

        # tags
        if "tags" not in fact:
            result.fail(f"{prefix} 缺少必填字段: tags")
        else:
            tags = fact["tags"]
            if not isinstance(tags, list):
                result.fail(f"{prefix}.tags 必须是列表")
            elif len(tags) < 3 or len(tags) > 5:
                result.fail(f"{prefix}.tags 必须包含 3-5 个标签（当前 {len(tags)} 个）")
            elif not all(isinstance(t, str) for t in tags):
                result.fail(f"{prefix}.tags 的每个元素必须是字符串")

        # permanence
        if "permanence" not in fact:
            result.fail(f"{prefix} 缺少必填字段: permanence")
        else:
            val = fact["permanence"]
            if val not in VALID_FACT_PERMANENCE:
                result.fail(
                    f'{prefix}.permanence 值 "{val}" 不在允许范围 '
                    f"[{'/'.join(VALID_FACT_PERMANENCE)}]"
                )

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_character_md(data, result: FileResult):
    """Validate YAML frontmatter of characters/*.md."""
    if not isinstance(data, dict):
        result.fail("前置元数据不是有效的 YAML 字典")
        return

    # Required: id
    if "id" not in data:
        result.fail("缺少必填字段: id")
    else:
        val = str(data["id"])
        if not re.match(r"^char-\d+$", val):
            result.fail(f'id 值 "{val}" 不匹配格式 char-\\d+')

    # Required: name
    if "name" not in data:
        result.fail("缺少必填字段: name")
    elif not is_nonempty_string(data["name"]):
        result.fail("name 不能为空字符串")

    # Required: role
    if "role" not in data:
        result.fail("缺少必填字段: role")
    else:
        val = data["role"]
        if not isinstance(val, str) or val not in VALID_CHAR_ROLE:
            result.fail(
                f'role 值 "{val}" 不在允许范围 '
                f"[{'/'.join(VALID_CHAR_ROLE)}]"
            )

    # Required: status
    if "status" not in data:
        result.fail("缺少必填字段: status")
    else:
        val = data["status"]
        if not isinstance(val, str) or val not in VALID_CHAR_STATUS:
            result.fail(
                f'status 值 "{val}" 不在允许范围 '
                f"[{'/'.join(VALID_CHAR_STATUS)}]"
            )

    # Required: first_appearance (int >= 0)
    if "first_appearance" not in data:
        result.fail("缺少必填字段: first_appearance")
    else:
        val = data["first_appearance"]
        if not isinstance(val, int):
            result.fail("first_appearance 必须是整数")
        elif val < 0:
            result.fail(f"first_appearance 值 ({val}) 不能为负数")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_chapter_summary_yaml(data, result: FileResult):
    """Validate state/summaries/chapters/chapter-*.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # Required: chapter (int)
    if "chapter" not in data:
        result.fail("缺少必填字段: chapter")
    elif not isinstance(data["chapter"], int):
        result.fail("chapter 必须是整数")

    # Required: arc (string, arc-\d+ format)
    if "arc" not in data:
        result.fail("缺少必填字段: arc")
    else:
        val = data["arc"]
        if not isinstance(val, str) or not re.match(r"^arc-\d+$", val):
            result.fail(f'arc 值 "{val}" 不匹配格式 arc-\\d+')

    # Required: story_time
    if "story_time" not in data:
        result.fail("缺少必填字段: story_time")
    elif not is_nonempty_string(data["story_time"]):
        result.fail("story_time 不能为空字符串")

    # Required: one_liner
    if "one_liner" not in data:
        result.fail("缺少必填字段: one_liner")
    elif not is_nonempty_string(data["one_liner"]):
        result.fail("one_liner 不能为空字符串")

    # Required: events (list, >= 1)
    if "events" not in data:
        result.fail("缺少必填字段: events")
    else:
        events = data["events"]
        if not isinstance(events, list):
            result.fail("events 必须是列表")
        elif len(events) == 0:
            result.fail("events 不能为空列表（至少需要 1 个事件）")

    # Required: emotional_note
    if "emotional_note" not in data:
        result.fail("缺少必填字段: emotional_note")
    elif not is_nonempty_string(data["emotional_note"]):
        result.fail("emotional_note 不能为空字符串")

    # Required: characters_appeared (list, >= 1)
    if "characters_appeared" not in data:
        result.fail("缺少必填字段: characters_appeared")
    else:
        ca = data["characters_appeared"]
        if not isinstance(ca, list):
            result.fail("characters_appeared 必须是列表")
        elif len(ca) == 0:
            result.fail("characters_appeared 不能为空列表")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_master_outline_md(data, result: FileResult):
    """Validate outline/master-outline.md frontmatter."""
    if not isinstance(data, dict):
        result.fail("前置元数据不是有效的 YAML 字典")
        return

    if "title" not in data:
        result.fail("缺少必填字段: title")
    elif not is_nonempty_string(data["title"]):
        result.fail("title 不能为空字符串")

    if "core_conflict" not in data:
        result.fail("缺少必填字段: core_conflict")
    elif not is_nonempty_string(data["core_conflict"]):
        result.fail("core_conflict 不能为空字符串")

    if "ending_type" not in data:
        result.fail("缺少必填字段: ending_type")
    elif not is_nonempty_string(data["ending_type"]):
        result.fail("ending_type 不能为空字符串")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_arc_summary_md(data, result: FileResult):
    """Validate state/summaries/arcs/arc-*-summary.md frontmatter."""
    if not isinstance(data, dict):
        result.fail("前置元数据不是有效的 YAML 字典")
        return

    if "arc" not in data:
        result.fail("缺少必填字段: arc")
    else:
        val = data["arc"]
        if not isinstance(val, str) or not re.match(r"^arc-\d+$", val):
            result.fail(f'arc 值 "{val}" 不匹配格式 arc-\\d+')

    if "title" not in data:
        result.fail("缺少必填字段: title")
    elif not is_nonempty_string(data["title"]):
        result.fail("title 不能为空字符串")

    if "chapter_range" not in data:
        result.fail("缺少必填字段: chapter_range")
    else:
        cr = data["chapter_range"]
        if not isinstance(cr, list) or len(cr) != 2:
            result.fail("chapter_range 必须是包含 2 个整数的列表")
        elif not all(isinstance(v, int) for v in cr):
            result.fail("chapter_range 的每个元素必须是整数")

    if "story_time_span" not in data:
        result.fail("缺少必填字段: story_time_span")
    elif not is_nonempty_string(data["story_time_span"]):
        result.fail("story_time_span 不能为空字符串")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_location_yaml(data, result: FileResult):
    """Validate locations/*.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # Required: id
    if "id" not in data:
        result.fail("缺少必填字段: id")
    else:
        val = data["id"]
        if not isinstance(val, str) or not re.match(r"^loc-\d{3}$", val):
            result.fail(f'id 值 "{val}" 不匹配格式 loc-\\d{{3}}')

    # Required: name
    if "name" not in data:
        result.fail("缺少必填字段: name")
    elif not is_nonempty_string(data["name"]):
        result.fail("name 不能为空字符串")

    # Required: region
    if "region" not in data:
        result.fail("缺少必填字段: region")
    elif not is_nonempty_string(data["region"]):
        result.fail("region 不能为空字符串")

    # Required: type
    if "type" not in data:
        result.fail("缺少必填字段: type")
    else:
        val = data["type"]
        if val not in VALID_LOCATION_TYPE:
            result.fail(
                f'type 值 "{val}" 不在允许范围 '
                f"[{'/'.join(VALID_LOCATION_TYPE)}]"
            )

    # Required: first_appearance (positive int)
    if "first_appearance" not in data:
        result.fail("缺少必填字段: first_appearance")
    else:
        val = data["first_appearance"]
        if not isinstance(val, int) or val < 0:
            result.fail("first_appearance 必须是非负整数")

    # Required: sensory_anchors (dict with visual and atmosphere)
    if "sensory_anchors" not in data:
        result.fail("缺少必填字段: sensory_anchors")
    else:
        sa = data["sensory_anchors"]
        if not isinstance(sa, dict):
            result.fail("sensory_anchors 必须是字典")
        else:
            if "visual" not in sa or not is_nonempty_string(sa.get("visual", "")):
                result.fail("sensory_anchors.visual 必填且不能为空")
            if "atmosphere" not in sa or not is_nonempty_string(sa.get("atmosphere", "")):
                result.fail("sensory_anchors.atmosphere 必填且不能为空")

    # Optional: parent_location (if present, must match loc-\d{3})
    parent = data.get("parent_location")
    if parent is not None:
        if not isinstance(parent, str) or not re.match(r"^loc-\d{3}$", str(parent)):
            result.fail(f'parent_location 值 "{parent}" 不匹配格式 loc-\\d{{3}}')

    # Optional: evolution (list of dicts with chapter + change)
    evolution = data.get("evolution")
    if evolution is not None and isinstance(evolution, list):
        for idx, entry in enumerate(evolution):
            if not isinstance(entry, dict):
                result.fail(f"evolution[{idx}] 必须是字典")
                continue
            if "chapter" not in entry or not isinstance(entry["chapter"], int):
                result.fail(f"evolution[{idx}] 缺少有效的 chapter 字段（正整数）")
            if "change" not in entry or not is_nonempty_string(entry.get("change", "")):
                result.fail(f"evolution[{idx}] 缺少有效的 change 字段（非空字符串）")

    # Optional: appeared_in (list of int)
    appeared = data.get("appeared_in")
    if appeared is not None and isinstance(appeared, list):
        for idx, val in enumerate(appeared):
            if not isinstance(val, int):
                result.fail(f"appeared_in[{idx}] 值 {val} 必须是整数")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_segment_yaml(data, result: FileResult):
    """Validate outline/segments/seg-*.yaml."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return

    # Required: id (seg-\d+-\d+)
    if "id" not in data:
        result.fail("缺少必填字段: id")
    else:
        val = str(data["id"])
        if not re.match(r"^seg-\d+-\d+$", val):
            result.fail(f'id 值 "{val}" 不匹配格式 seg-\\d+-\\d+')

    # Required: arc
    if "arc" not in data:
        result.fail("缺少必填字段: arc")
    else:
        val = data["arc"]
        if not isinstance(val, str) or not re.match(r"^arc-\d+$", val):
            result.fail(f'arc 值 "{val}" 不匹配格式 arc-\\d+')

    # Required: title
    if "title" not in data:
        result.fail("缺少必填字段: title")
    elif not is_nonempty_string(data["title"]):
        result.fail("title 不能为空字符串")

    # Required: status
    if "status" not in data:
        result.fail("缺少必填字段: status")
    else:
        val = data["status"]
        if val not in VALID_SEGMENT_STATUS:
            result.fail(
                f'status 值 "{val}" 不在允许范围 '
                f"[{'/'.join(VALID_SEGMENT_STATUS)}]"
            )

    # Required: entry_state (dict with story_time and characters)
    if "entry_state" not in data:
        result.fail("缺少必填字段: entry_state")
    else:
        es = data["entry_state"]
        if not isinstance(es, dict):
            result.fail("entry_state 必须是字典")
        else:
            if "story_time" not in es or not is_nonempty_string(es.get("story_time", "")):
                result.fail("entry_state.story_time 必填且不能为空")
            chars = es.get("characters")
            if not isinstance(chars, list) or len(chars) == 0:
                result.fail("entry_state.characters 必须是非空列表")
            else:
                for idx, c in enumerate(chars):
                    if not isinstance(c, dict):
                        result.fail(f"entry_state.characters[{idx}] 必须是字典")
                        continue
                    if "char" not in c or not is_nonempty_string(str(c.get("char", ""))):
                        result.fail(f"entry_state.characters[{idx}].char 必填")
                    if "location" not in c or not is_nonempty_string(str(c.get("location", ""))):
                        result.fail(f"entry_state.characters[{idx}].location 必填")
                    if "cultivation" not in c or not is_nonempty_string(str(c.get("cultivation", ""))):
                        result.fail(f"entry_state.characters[{idx}].cultivation 必填")

    # Required: exit_state (list of >= 2 substantive strings, min 10 chars each)
    if "exit_state" not in data:
        result.fail("缺少必填字段: exit_state")
    else:
        ex = data["exit_state"]
        if not isinstance(ex, list) or len(ex) < 2:
            result.fail("exit_state 必须是至少包含 2 条的列表")
        else:
            for idx, item in enumerate(ex):
                s = str(item).strip()
                if len(s) < 10:
                    result.fail(
                        f"exit_state[{idx}] 内容过短（{len(s)}字，最少10字），"
                        "请填写实质性的状态描述"
                    )

    # Required: narrative_arc
    if "narrative_arc" not in data:
        result.fail("缺少必填字段: narrative_arc")
    elif not is_nonempty_string(data["narrative_arc"]):
        result.fail("narrative_arc 不能为空字符串")

    # Required: key_events (list >= 2)
    if "key_events" not in data:
        result.fail("缺少必填字段: key_events")
    else:
        ke = data["key_events"]
        if not isinstance(ke, list) or len(ke) < 2:
            result.fail("key_events 必须是至少包含 2 条事件的列表")

    # Required: highlight_type
    if "highlight_type" not in data:
        result.fail("缺少必填字段: highlight_type")
    else:
        val = data["highlight_type"]
        if val not in VALID_SEGMENT_HIGHLIGHT:
            result.fail(
                f'highlight_type 值 "{val}" 不在允许范围 '
                f"[{'/'.join(VALID_SEGMENT_HIGHLIGHT)}]"
            )

    # Required: estimated_chapters (positive int)
    if "estimated_chapters" not in data:
        result.fail("缺少必填字段: estimated_chapters")
    else:
        val = data["estimated_chapters"]
        if not isinstance(val, int) or val < 1:
            result.fail("estimated_chapters 必须是正整数")
        elif val > 10:
            result.warn(f"estimated_chapters={val} 超过10章，段落可能过长")

    if not result.has_fail and not result.has_warn:
        result.ok()


def validate_yaml_parseable(data, result: FileResult):
    """Basic validation: only checks that the file is a valid YAML dict."""
    if not isinstance(data, dict):
        result.fail("文件内容不是有效的 YAML 字典")
        return
    result.ok("YAML 结构有效")


# ---------------------------------------------------------------------------
# File discovery and routing
# ---------------------------------------------------------------------------

def should_skip(filepath: Path) -> bool:
    """Skip template files whose name starts with underscore."""
    return filepath.name.startswith("_")


def detect_and_validate(filepath: Path, root: Path) -> FileResult:
    """Detect the schema type by path and run the appropriate validator."""
    rel = relative_path(filepath, root)
    result = FileResult(rel)

    # Determine which validator to use based on file path
    rel_posix = rel.replace("\\", "/")

    # Load data
    if filepath.suffix == ".md":
        data = extract_frontmatter(filepath)
    else:
        data = load_yaml_file(filepath)

    # Handle load errors
    if isinstance(data, tuple):
        error_type, error_msg = data
        result.fail(f"{error_type}: {error_msg}")
        return result

    if data is None:
        result.fail("文件内容为空或无法解析")
        return result

    # Route to correct validator
    if rel_posix == "config/project.yaml":
        validate_project_yaml(data, result)
    elif rel_posix == "outline/master-outline.md":
        validate_master_outline_md(data, result)
    elif re.match(r"outline/arcs/arc-.*\.yaml$", rel_posix):
        validate_arc_yaml(data, result)
    elif re.match(r"outline/segments/seg-.*\.yaml$", rel_posix):
        validate_segment_yaml(data, result)
    elif re.match(r"outline/chapters/chapter-.*\.yaml$", rel_posix):
        validate_chapter_yaml(data, result)
    elif rel_posix == "state/session-state.yaml":
        validate_session_state_yaml(data, result)
    elif rel_posix == "state/plot-threads.yaml":
        validate_plot_threads_yaml(data, result)
    elif re.match(r"state/facts/chapter-.*\.yaml$", rel_posix):
        validate_facts_yaml(data, result)
    elif re.match(r"state/summaries/chapters/chapter-.*\.yaml$", rel_posix):
        validate_chapter_summary_yaml(data, result)
    elif re.match(r"state/summaries/arcs/arc-.*-summary\.md$", rel_posix):
        validate_arc_summary_md(data, result)
    elif rel_posix == "state/plot-pattern-tracker.yaml":
        validate_plot_pattern_tracker_yaml(data, result)
    elif rel_posix == "state/relationships.yaml":
        validate_relationships_yaml(data, result)
    elif rel_posix in (
        "state/timeline.yaml",
        "state/world-state.yaml",
        "state/milestones.yaml",
        "state/pacing-tracker.yaml",
        "state/emotion-threads.yaml",
        "state/character-appearances.yaml",
    ):
        validate_yaml_parseable(data, result)
    elif rel_posix.startswith("config/") and filepath.suffix == ".yaml":
        validate_yaml_parseable(data, result)
    elif (
        rel_posix.startswith("characters/")
        and filepath.suffix == ".md"
    ):
        validate_character_md(data, result)
    elif (
        rel_posix.startswith("locations/")
        and filepath.suffix == ".yaml"
    ):
        validate_location_yaml(data, result)
    else:
        result.ok("不在校验范围内，已跳过")

    return result


def discover_files(root: Path) -> list[Path]:
    """Discover all validatable files in the project."""
    files: list[Path] = []

    # config/project.yaml
    p = root / "config" / "project.yaml"
    if p.exists():
        files.append(p)

    # outline/master-outline.md
    p = root / "outline" / "master-outline.md"
    if p.exists():
        files.append(p)

    # outline/arcs/arc-*.yaml
    arcs_dir = root / "outline" / "arcs"
    if arcs_dir.is_dir():
        for f in sorted(arcs_dir.glob("arc-*.yaml")):
            if not should_skip(f):
                files.append(f)

    # outline/segments/seg-*.yaml
    segments_dir = root / "outline" / "segments"
    if segments_dir.is_dir():
        for f in sorted(segments_dir.glob("seg-*.yaml")):
            if not should_skip(f):
                files.append(f)

    # outline/chapters/chapter-*.yaml
    chapters_dir = root / "outline" / "chapters"
    if chapters_dir.is_dir():
        for f in sorted(chapters_dir.glob("chapter-*.yaml")):
            if not should_skip(f):
                files.append(f)

    # state/session-state.yaml
    p = root / "state" / "session-state.yaml"
    if p.exists():
        files.append(p)

    # state/plot-threads.yaml
    p = root / "state" / "plot-threads.yaml"
    if p.exists():
        files.append(p)

    # state/facts/chapter-*.yaml
    facts_dir = root / "state" / "facts"
    if facts_dir.is_dir():
        for f in sorted(facts_dir.glob("chapter-*.yaml")):
            if not should_skip(f):
                files.append(f)

    # state/summaries/chapters/chapter-*.yaml
    summaries_dir = root / "state" / "summaries" / "chapters"
    if summaries_dir.is_dir():
        for f in sorted(summaries_dir.glob("chapter-*.yaml")):
            if not should_skip(f):
                files.append(f)

    # state/summaries/arcs/arc-*-summary.md
    arc_summaries_dir = root / "state" / "summaries" / "arcs"
    if arc_summaries_dir.is_dir():
        for f in sorted(arc_summaries_dir.glob("arc-*-summary.md")):
            if not should_skip(f):
                files.append(f)

    # state YAML files
    for name in (
        "relationships.yaml",
        "timeline.yaml",
        "world-state.yaml",
        "plot-pattern-tracker.yaml",
        "milestones.yaml",
        "pacing-tracker.yaml",
        "emotion-threads.yaml",
        "character-appearances.yaml",
    ):
        p = root / "state" / name
        if p.exists():
            files.append(p)

    # config YAML files (parse validation)
    config_dir = root / "config"
    if config_dir.is_dir():
        for f in sorted(config_dir.glob("*.yaml")):
            if not should_skip(f) and f.name != "project.yaml":
                files.append(f)

    # characters/*.md (excluding templates)
    chars_dir = root / "characters"
    if chars_dir.is_dir():
        for f in sorted(chars_dir.glob("*.md")):
            if not should_skip(f):
                files.append(f)

    # locations/*.yaml (excluding templates)
    locs_dir = root / "locations"
    if locs_dir.is_dir():
        for f in sorted(locs_dir.glob("*.yaml")):
            if not should_skip(f):
                files.append(f)

    return files


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

def print_report(results: list[FileResult]):
    """Print the formatted validation report."""
    print()
    print(SEPARATOR_THICK)
    print("  Schema 校验报告")
    print(SEPARATOR_THICK)

    if not results:
        print()
        print("  没有可校验的文件（模板文件已跳过）")
        print()
        print(SEPARATOR_THICK)
        return

    for r in results:
        print()
        print(f"  {r.display_path}")
        for level, msg in r.messages:
            print(f"  {level} | {msg}")

    fail_count = sum(1 for r in results if r.status == "FAIL")
    warn_count = sum(1 for r in results if r.status == "WARN")
    pass_count = sum(1 for r in results if r.status == "PASS")
    total = len(results)

    print()
    print(SEPARATOR_THIN)
    print(
        f"  统计: 已检查 {total} 个文件 -- "
        f"{fail_count} FAIL / {warn_count} WARN / {pass_count} PASS"
    )

    if fail_count > 0:
        print("  结论: FAIL -- 存在不符合规范的文件")
    elif warn_count > 0:
        print("  结论: WARN -- 全部通过但存在建议项")
    else:
        print("  结论: PASS -- 全部文件校验通过")

    print(SEPARATOR_THICK)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    root = find_project_root()

    # Single file mode
    if len(sys.argv) > 1:
        target = Path(sys.argv[1]).resolve()
        if not target.exists():
            print(f"ERROR: 文件不存在: {target}")
            return 2
        if should_skip(target):
            print(f"  跳过模板文件: {target.name}")
            return 0
        result = detect_and_validate(target, root)
        print_report([result])
        return 1 if result.has_fail else 0

    # Full project scan
    files = discover_files(root)

    # Report empty categories
    arcs_dir = root / "outline" / "arcs"
    chapters_dir = root / "outline" / "chapters"
    facts_dir = root / "state" / "facts"
    chars_dir = root / "characters"

    empty_categories: list[str] = []
    if arcs_dir.is_dir() and not list(
        f for f in arcs_dir.glob("arc-*.yaml") if not should_skip(f)
    ):
        empty_categories.append("outline/arcs (卷大纲)")
    if chapters_dir.is_dir() and not list(
        f for f in chapters_dir.glob("chapter-*.yaml") if not should_skip(f)
    ):
        empty_categories.append("outline/chapters (章节大纲)")
    if facts_dir.is_dir() and not list(
        f for f in facts_dir.glob("chapter-*.yaml") if not should_skip(f)
    ):
        empty_categories.append("state/facts (事实注册表)")
    if chars_dir.is_dir() and not list(
        f for f in chars_dir.glob("*.md") if not should_skip(f)
    ):
        empty_categories.append("characters (人物档案)")

    results = [detect_and_validate(f, root) for f in files]

    # Print report (reuse print_report for consistency)
    if empty_categories:
        print()
        print(SEPARATOR_THICK)
        print("  Schema 校验报告 -- 空类别提示")
        print(SEPARATOR_THICK)
        for cat in empty_categories:
            print(f"  [INFO] {cat}: 暂无可校验文件（仅存在模板）")

    print_report(results)

    fail_count = sum(1 for r in results if r.status == "FAIL")
    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
