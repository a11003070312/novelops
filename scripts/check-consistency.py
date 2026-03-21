#!/usr/bin/env python3
"""
一致性检查脚本 -- 校验小说工程框架中跨文件数据的一致性。

用法:
    python scripts/check-consistency.py

退出码:
    0 -- 全部通过（可能有 WARN）
    1 -- 存在 FAIL 级别问题
"""

from __future__ import annotations

import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Windows UTF-8 stdout
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# PyYAML 导入 -- 优雅降级
# ---------------------------------------------------------------------------
try:
    import yaml
except ImportError:
    print(
        "错误: 缺少 PyYAML 依赖。\n"
        "请运行以下命令安装:\n"
        "  pip install pyyaml\n"
        "或使用国内镜像:\n"
        "  pip install pyyaml -i https://pypi.tuna.tsinghua.edu.cn/simple"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SEPARATOR_THICK = "=" * 60
SEPARATOR_THIN = "-" * 60
STALE_THREAD_THRESHOLD = 30
VALID_EXPECTED_ACTIONS = {"write_chapter", "new_arc", "audit", "plan_segment"}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    """单项检查结果。"""

    name: str
    level: str = "PASS"  # "PASS" | "WARN" | "FAIL" | "SKIP"
    messages: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def find_project_root() -> Path:
    """从脚本所在目录向上查找 ENTRY.md，确定项目根目录。"""
    current = Path(__file__).resolve().parent
    for _ in range(20):
        if (current / "ENTRY.md").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    print("错误: 无法定位项目根目录（未找到 ENTRY.md）。")
    print("请确保从项目内运行此脚本。")
    sys.exit(2)


def read_yaml_file(path: Path) -> Optional[object]:
    """安全读取 YAML 文件，返回解析后的对象；失败返回 None。"""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except Exception as exc:
        print(f"  (读取 {path.name} 时出错: {exc})")
        return None


def is_template(path: Path) -> bool:
    """判断文件是否为模板文件（文件名以 _ 开头）。"""
    return path.name.startswith("_")


def parse_md_frontmatter(path: Path) -> Optional[dict]:
    """解析 Markdown 文件的 YAML frontmatter（--- 分隔符之间的内容）。"""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except Exception:
        return None

    match = re.match(r"^---\s*\n(.*?\n)---", content, re.DOTALL)
    if not match:
        return None
    try:
        return yaml.safe_load(match.group(1))
    except Exception:
        return None


def extract_chapter_number(filename: str) -> Optional[int]:
    """从文件名中提取章节号，例如 chapter-0012.yaml -> 12。"""
    m = re.search(r"chapter-(\d+)", filename)
    if m:
        return int(m.group(1))
    return None


def collect_character_files(root: Path) -> Dict[str, Path]:
    """收集 characters/ 下所有非模板 .md 文件，返回 {char_id: path} 映射。"""
    char_dir = root / "characters"
    result: Dict[str, Path] = {}
    if not char_dir.is_dir():
        return result
    for fp in char_dir.glob("*.md"):
        if is_template(fp):
            continue
        fm = parse_md_frontmatter(fp)
        if fm and isinstance(fm, dict) and "id" in fm:
            result[fm["id"]] = fp
    return result


def collect_chapter_outlines(root: Path) -> List[Tuple[int, dict, Path]]:
    """收集所有章节大纲，返回 [(章节号, 数据字典, 文件路径)]。"""
    chapter_dir = root / "outline" / "chapters"
    results: List[Tuple[int, dict, Path]] = []
    if not chapter_dir.is_dir():
        return results
    for fp in sorted(chapter_dir.glob("chapter-*.yaml")):
        if is_template(fp):
            continue
        data = read_yaml_file(fp)
        if not isinstance(data, dict):
            continue
        chap_num = extract_chapter_number(fp.name)
        if chap_num is not None:
            results.append((chap_num, data, fp))
    return results


def collect_arc_files(root: Path) -> Dict[str, Path]:
    """收集所有卷大纲文件，返回 {arc_id: path}。"""
    arc_dir = root / "outline" / "arcs"
    result: Dict[str, Path] = {}
    if not arc_dir.is_dir():
        return result
    for fp in arc_dir.glob("arc-*.yaml"):
        if is_template(fp):
            continue
        data = read_yaml_file(fp)
        if isinstance(data, dict) and "id" in data:
            result[data["id"]] = fp
    return result


def collect_fact_files(root: Path) -> List[Tuple[int, dict, Path]]:
    """收集 state/facts/ 下所有事实文件，返回 [(章节号, 数据, 路径)]。"""
    facts_dir = root / "state" / "facts"
    results: List[Tuple[int, dict, Path]] = []
    if not facts_dir.is_dir():
        return results
    for fp in sorted(facts_dir.glob("chapter-*.yaml")):
        if is_template(fp):
            continue
        data = read_yaml_file(fp)
        if not isinstance(data, dict):
            continue
        chap_num = extract_chapter_number(fp.name)
        if chap_num is not None:
            results.append((chap_num, data, fp))
    return results


def safe_list(val: object) -> list:
    """确保返回列表；None 或非列表值返回空列表。"""
    if isinstance(val, list):
        return val
    return []


# ---------------------------------------------------------------------------
# 各项检查
# ---------------------------------------------------------------------------

def check_character_reference(
    root: Path,
    char_map: Dict[str, Path],
    chapters: List[Tuple[int, dict, Path]],
) -> CheckResult:
    """检查 1: 章节大纲中引用的人物是否都存在对应档案文件。"""
    result = CheckResult(name="人物引用")
    if not chapters:
        result.level = "SKIP"
        result.messages.append("未找到章节大纲文件")
        return result

    issues: List[str] = []
    for chap_num, data, fp in chapters:
        for char_id in safe_list(data.get("characters_present")):
            if char_id not in char_map:
                issues.append(
                    f"chapter-{chap_num:04d} 引用了不存在的人物 {char_id}"
                )

    if issues:
        result.level = "FAIL"
        result.messages = issues
    else:
        result.level = "PASS"
        result.messages.append(
            f"共检查 {len(chapters)} 个章节大纲，所有人物引用有效"
        )
    return result


def check_dead_character_appearance(
    root: Path,
    char_map: Dict[str, Path],
    chapters: List[Tuple[int, dict, Path]],
) -> CheckResult:
    """检查 2: 已死亡人物不应出现在未发布章节大纲中。"""
    result = CheckResult(name="死亡人物")

    dead_chars: Dict[str, str] = {}
    for char_id, char_path in char_map.items():
        fm = parse_md_frontmatter(char_path)
        if fm and isinstance(fm, dict) and fm.get("status") == "dead":
            dead_chars[char_id] = fm.get("name", char_id)

    if not dead_chars:
        result.level = "PASS"
        result.messages.append("无已死亡人物")
        return result

    if not chapters:
        result.level = "PASS"
        result.messages.append("无章节大纲可检查")
        return result

    issues: List[str] = []
    for chap_num, data, fp in chapters:
        status = data.get("status", "planned")
        if status == "published":
            continue
        for char_id in safe_list(data.get("characters_present")):
            if char_id in dead_chars:
                name = dead_chars[char_id]
                issues.append(
                    f"{char_id} ({name}) 状态为 dead，"
                    f"但出现在 chapter-{chap_num:04d} 大纲中 "
                    f"(除非为闪回场景)"
                )

    if issues:
        result.level = "WARN"
        result.messages = issues
    else:
        result.level = "PASS"
        result.messages.append(
            f"已检查 {len(dead_chars)} 个死亡人物，无异常出现"
        )
    return result


def check_fact_id_uniqueness(root: Path) -> CheckResult:
    """检查 3: 所有事实 ID 必须唯一。"""
    result = CheckResult(name="事实唯一性")
    fact_files = collect_fact_files(root)
    if not fact_files:
        result.level = "SKIP"
        result.messages.append("未找到事实文件")
        return result

    seen: Dict[str, str] = {}
    duplicates: List[str] = []
    total = 0

    for chap_num, data, fp in fact_files:
        for fact in safe_list(data.get("facts")):
            if not isinstance(fact, dict):
                continue
            fact_id = fact.get("id")
            if not fact_id:
                continue
            total += 1
            source = f"chapter-{chap_num:04d}"
            if fact_id in seen:
                duplicates.append(
                    f"重复 ID: {fact_id} (出现在 {seen[fact_id]} 和 {source})"
                )
            else:
                seen[fact_id] = source

    if duplicates:
        result.level = "FAIL"
        result.messages = duplicates
    else:
        result.level = "PASS"
        result.messages.append(f"共 {total} 条事实，无重复ID")
    return result


def check_fact_character_reference(
    root: Path,
    char_map: Dict[str, Path],
) -> CheckResult:
    """检查 4: 事实中引用的人物 ID 是否都有对应档案。"""
    result = CheckResult(name="事实人物引用")
    fact_files = collect_fact_files(root)
    if not fact_files:
        result.level = "SKIP"
        result.messages.append("未找到事实文件")
        return result

    issues: List[str] = []
    total_checked = 0

    for chap_num, data, fp in fact_files:
        for fact in safe_list(data.get("facts")):
            if not isinstance(fact, dict):
                continue
            fact_id = fact.get("id", "unknown")
            for char_id in safe_list(fact.get("characters")):
                total_checked += 1
                if char_id not in char_map:
                    issues.append(
                        f"事实 {fact_id} (chapter-{chap_num:04d}) "
                        f"引用了不存在的人物 {char_id}"
                    )

    if issues:
        result.level = "WARN"
        result.messages = issues
    else:
        result.level = "PASS"
        result.messages.append(
            f"共检查 {total_checked} 条人物引用，全部有效"
        )
    return result


def check_plot_thread_timeline(
    root: Path,
    chapters: List[Tuple[int, dict, Path]],
) -> CheckResult:
    """检查 5: 伏笔线索时间线有效性。"""
    result = CheckResult(name="伏笔健康")
    pt_path = root / "state" / "plot-threads.yaml"
    data = read_yaml_file(pt_path)
    if data is None:
        result.level = "SKIP"
        result.messages.append("state/plot-threads.yaml 不存在或无法读取")
        return result
    if not isinstance(data, dict):
        result.level = "SKIP"
        result.messages.append("state/plot-threads.yaml 格式异常")
        return result

    chapter_numbers: Set[int] = {c[0] for c in chapters}
    max_chapter = max(chapter_numbers) if chapter_numbers else 0

    active = safe_list(data.get("active_threads"))
    resolved = safe_list(data.get("resolved_threads"))
    abandoned = safe_list(data.get("abandoned_threads"))

    if not active and not resolved and not abandoned:
        result.level = "PASS"
        result.messages.append("暂无伏笔记录")
        return result

    issues: List[str] = []

    # -- 跨列表重复 ID 检测 --
    id_seen: Dict[str, str] = {}
    duplicate_ids: set = set()
    for list_name, threads in (("active", active), ("resolved", resolved), ("abandoned", abandoned)):
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            tid = thread.get("id")
            tname = thread.get("name", tid)
            if not tid:
                continue
            if tid in id_seen:
                issues.append(
                    f"线索 ID \"{tid}\" ({tname}) 同时出现在 {id_seen[tid]} 和 {list_name} 列表中"
                )
                duplicate_ids.add(tid)
            else:
                id_seen[tid] = list_name

    # -- 活跃线索检查（跳过已报告重复 ID 的线索，避免产生嘈杂的二次警告）--
    for thread in active:
        if not isinstance(thread, dict):
            continue
        tid = thread.get("id", "unknown")
        if tid in duplicate_ids:
            continue
        tname = thread.get("name", tid)
        planted = thread.get("planted_chapter")

        # 线索揭露顺序
        clues = safe_list(thread.get("clues_revealed"))
        clue_chapters: List[int] = []
        for clue in clues:
            if isinstance(clue, dict) and "chapter" in clue:
                ch = clue["chapter"]
                if isinstance(ch, int):
                    clue_chapters.append(ch)

        for i in range(1, len(clue_chapters)):
            if clue_chapters[i] < clue_chapters[i - 1]:
                issues.append(
                    f"{tid} \"{tname}\" 线索揭露顺序异常: "
                    f"第{clue_chapters[i - 1]}章 之后出现 第{clue_chapters[i]}章"
                )
                break

        # 停滞检测
        if max_chapter > 0:
            last_activity = planted if isinstance(planted, int) else 0
            if clue_chapters:
                last_activity = max(last_activity, max(clue_chapters))
            if last_activity > 0:
                gap = max_chapter - last_activity
                if gap > STALE_THREAD_THRESHOLD:
                    issues.append(
                        f"{tid} \"{tname}\" 已 {gap} 章未推进 "
                        f"(上次活动: 第{last_activity}章)"
                    )

    # -- 已回收线索检查 --
    for thread in resolved:
        if not isinstance(thread, dict):
            continue
        tid = thread.get("id", "unknown")
        if tid in duplicate_ids:
            continue
        tname = thread.get("name", tid)
        if "resolved_chapter" not in thread or thread["resolved_chapter"] is None:
            issues.append(
                f"已回收线索 {tid} \"{tname}\" 缺少 resolved_chapter"
            )

    # -- 已废弃线索检查 --
    for thread in abandoned:
        if not isinstance(thread, dict):
            continue
        tid = thread.get("id", "unknown")
        if tid in duplicate_ids:
            continue
        tname = thread.get("name", tid)
        if "abandoned_chapter" not in thread or thread["abandoned_chapter"] is None:
            issues.append(
                f"已废弃线索 {tid} \"{tname}\" 缺少 abandoned_chapter"
            )

    if issues:
        # 跨列表重复 ID 是数据完整性错误，升级为 FAIL
        has_dup = any("同时出现在" in msg for msg in issues)
        result.level = "FAIL" if has_dup else "WARN"
        result.messages = issues
    else:
        total = len(active) + len(resolved) + len(abandoned)
        result.level = "PASS"
        result.messages.append(f"共 {total} 条伏笔，状态正常")
    return result


def check_relationship_target_existence(
    root: Path,
    char_map: Dict[str, Path],
) -> CheckResult:
    """检查 6: relationships.yaml 中引用的人物是否存在。"""
    result = CheckResult(name="关系目标存在性")
    rel_path = root / "state" / "relationships.yaml"
    data = read_yaml_file(rel_path)
    if data is None:
        result.level = "SKIP"
        result.messages.append("state/relationships.yaml 不存在或无法读取")
        return result
    if not isinstance(data, dict):
        result.level = "SKIP"
        result.messages.append("state/relationships.yaml 格式异常")
        return result

    rels = safe_list(data.get("relationships"))
    if not rels:
        result.level = "PASS"
        result.messages.append("暂无关系记录")
        return result

    issues: List[str] = []
    for rel in rels:
        if not isinstance(rel, dict):
            continue
        # 支持两种字段名：from/to 或 source/target
        from_id = rel.get("from") or rel.get("source")
        to_id = rel.get("to") or rel.get("target")
        from_name = rel.get("from_name") or rel.get("source_name", from_id)
        to_name = rel.get("to_name") or rel.get("target_name", to_id)

        if from_id and from_id not in char_map:
            issues.append(f"关系记录中 from={from_id} ({from_name}) 不存在对应人物档案")
        if to_id and to_id not in char_map:
            issues.append(f"关系记录中 to={to_id} ({to_name}) 不存在对应人物档案")

    if issues:
        result.level = "FAIL"
        result.messages = issues
    else:
        result.level = "PASS"
        result.messages.append(f"共 {len(rels)} 条关系记录，所有引用有效")
    return result


def check_relationship_symmetry(
    root: Path,
    char_map: Dict[str, Path],
) -> CheckResult:
    """检查 7: 人物关系对称性（A 引用 B，则 B 也应引用 A）。"""
    result = CheckResult(name="关系完整性")

    if not char_map:
        result.level = "SKIP"
        result.messages.append("未找到人物档案")
        return result

    # 收集每个角色的关系目标集合
    char_relationships: Dict[str, Set[str]] = {}
    char_names: Dict[str, str] = {}

    for char_id, char_path in char_map.items():
        fm = parse_md_frontmatter(char_path)
        if not fm or not isinstance(fm, dict):
            continue
        char_names[char_id] = fm.get("name", char_id)
        targets: Set[str] = set()
        for rel in safe_list(fm.get("relationships")):
            if isinstance(rel, dict) and "target" in rel:
                targets.add(rel["target"])
        char_relationships[char_id] = targets

    if not char_relationships:
        result.level = "PASS"
        result.messages.append("无人物关系可检查")
        return result

    issues: List[str] = []
    checked_pairs: Set[Tuple[str, str]] = set()

    for char_a, targets_a in char_relationships.items():
        for char_b in targets_a:
            pair = tuple(sorted([char_a, char_b]))
            if pair in checked_pairs:
                continue
            checked_pairs.add(pair)

            targets_b = char_relationships.get(char_b, set())
            if char_a not in targets_b:
                name_a = char_names.get(char_a, char_a)
                name_b = char_names.get(char_b, char_b)
                issues.append(
                    f"{char_a} ({name_a}) 引用 {char_b} ({name_b}) "
                    f"但 {char_b} 未引用 {char_a}"
                )

    if issues:
        result.level = "WARN"
        result.messages = issues
    else:
        total_pairs = len(checked_pairs)
        result.level = "PASS"
        if total_pairs > 0:
            result.messages.append(f"共 {total_pairs} 对关系，全部对称")
        else:
            result.messages.append("无人物关系可检查")
    return result


def check_timeline_continuity(root: Path) -> CheckResult:
    """检查 8: 时间线连续性。"""
    result = CheckResult(name="时间线连续性")
    tl_path = root / "state" / "timeline.yaml"
    data = read_yaml_file(tl_path)
    if data is None:
        result.level = "SKIP"
        result.messages.append("state/timeline.yaml 不存在或无法读取")
        return result
    if not isinstance(data, dict):
        result.level = "SKIP"
        result.messages.append("state/timeline.yaml 格式异常")
        return result

    entries = safe_list(data.get("entries"))
    if not entries:
        result.level = "PASS"
        result.messages.append("暂无时间线记录")
        return result

    chapters: List[int] = []
    for entry in entries:
        if isinstance(entry, dict) and "chapter" in entry:
            ch = entry["chapter"]
            if isinstance(ch, int):
                chapters.append(ch)

    if not chapters:
        result.level = "PASS"
        result.messages.append("时间线中无有效章节记录")
        return result

    issues: List[str] = []

    # 检查升序
    for i in range(1, len(chapters)):
        if chapters[i] < chapters[i - 1]:
            issues.append(
                f"章节顺序异常: 第{chapters[i - 1]}章 之后出现 第{chapters[i]}章"
            )

    # 检查间隔
    for i in range(1, len(chapters)):
        gap = chapters[i] - chapters[i - 1]
        if gap > 1:
            issues.append(
                f"章节间隔过大: 第{chapters[i - 1]}章 到 第{chapters[i]}章 "
                f"(跳过 {gap - 1} 章)"
            )

    if issues:
        result.level = "WARN"
        result.messages = issues
    else:
        result.level = "PASS"
        result.messages.append(
            f"共 {len(chapters)} 条时间线记录，"
            f"第{chapters[0]}章 至 第{chapters[-1]}章，连续性正常"
        )
    return result


def check_milestone_duplicates(root: Path) -> CheckResult:
    """检查 10: 同一角色不能有重复的修为/能力里程碑。"""
    result = CheckResult(name="里程碑防重")
    ms_path = root / "state" / "milestones.yaml"
    data = read_yaml_file(ms_path)
    if data is None:
        result.level = "SKIP"
        result.messages.append("state/milestones.yaml 不存在")
        return result
    if not isinstance(data, dict):
        result.level = "SKIP"
        result.messages.append("state/milestones.yaml 格式异常")
        return result

    milestones = safe_list(data.get("milestones"))
    if not milestones:
        result.level = "PASS"
        result.messages.append("暂无里程碑记录")
        return result

    # 按 (character, type, event) 分组检查重复
    seen: Dict[Tuple[str, str, str], int] = {}
    duplicates: List[str] = []

    for ms in milestones:
        if not isinstance(ms, dict):
            continue
        char_id = ms.get("character", "unknown")
        ms_type = ms.get("type", "unknown")
        event = ms.get("event", "")
        chapter = ms.get("chapter", "?")

        key = (char_id, ms_type, event)
        if key in seen:
            duplicates.append(
                f"重复里程碑: {char_id} [{ms_type}] \"{event}\" "
                f"(第{seen[key]}章 和 第{chapter}章)"
            )
        else:
            seen[key] = chapter

    # 注：曾经有一个"同章节同事件"二次检查，但已确认是死代码：
    # 主检查的 seen 字典以 (character, type, event) 为 key，任何两条完全相同的
    # 记录（无论章节是否相同）都会被主检查捕获。二次检查是主检查的严格子集，
    # 且会对同一对重复记录产生双重报告。已移除。

    if duplicates:
        result.level = "FAIL"
        result.messages = duplicates
    else:
        result.level = "PASS"
        result.messages.append(f"共 {len(milestones)} 条里程碑，无重复")
    return result


def check_outline_state_consistency(
    root: Path,
    char_map: Dict[str, Path],
    chapters: List[Tuple[int, dict, Path]],
) -> CheckResult:
    """检查 11: 未发布章节大纲中的角色状态描述与当前档案是否一致。"""
    result = CheckResult(name="大纲状态交叉验证")

    if not chapters:
        result.level = "SKIP"
        result.messages.append("未找到章节大纲文件")
        return result

    if not char_map:
        result.level = "SKIP"
        result.messages.append("未找到人物档案")
        return result

    # 收集角色当前修为等级
    char_cultivation: Dict[str, str] = {}
    for char_id, char_path in char_map.items():
        fm = parse_md_frontmatter(char_path)
        if fm and isinstance(fm, dict):
            cl = fm.get("cultivation_level")
            if cl:
                char_cultivation[char_id] = str(cl)

    # 读取里程碑用于交叉验证
    ms_path = root / "state" / "milestones.yaml"
    ms_data = read_yaml_file(ms_path)
    milestone_events: Set[str] = set()
    if isinstance(ms_data, dict):
        for ms in safe_list(ms_data.get("milestones")):
            if isinstance(ms, dict) and ms.get("type") == "cultivation":
                char_id = ms.get("character", "")
                event = ms.get("event", "")
                milestone_events.add(f"{char_id}:{event}")

    issues: List[str] = []
    planned_count = 0

    for chap_num, data, fp in chapters:
        status = data.get("status", "planned")
        if status == "published":
            continue
        planned_count += 1

        # 检查 consistency_notes 中是否有修为引用
        notes = data.get("consistency_notes", [])
        if isinstance(notes, str):
            notes = [notes]
        elif not isinstance(notes, list):
            notes = []

        for note in notes:
            if not isinstance(note, str):
                continue
            # 检查是否提到修为突破
            for keyword in ["突破", "进阶", "晋级", "升级"]:
                if keyword in note:
                    # 检查此突破事件是否已在里程碑中记录
                    for char_id in safe_list(data.get("characters_present")):
                        key = f"{char_id}:{note.strip()}"
                        # 模糊匹配：如果里程碑中已有相同角色的相同描述
                        found_dup = False
                        for ms_key in milestone_events:
                            ms_char, ms_event = ms_key.split(":", 1)
                            # 要求里程碑事件描述至少4字，避免"突破"等短词误匹配
                            if ms_char == char_id and len(ms_event) >= 4 and ms_event in note:
                                found_dup = True
                                break
                        if found_dup:
                            issues.append(
                                f"chapter-{chap_num:04d} 大纲规划的修为变化 "
                                f"\"{note.strip()[:40]}\" 与已有里程碑记录重复"
                            )
                    break

    if not planned_count:
        result.level = "PASS"
        result.messages.append("无未发布章节大纲")
        return result

    if issues:
        result.level = "WARN"
        result.messages = issues
    else:
        result.level = "PASS"
        result.messages.append(
            f"共检查 {planned_count} 个未发布大纲，状态引用正常"
        )
    return result


def collect_location_files(root: Path) -> Dict[str, dict]:
    """收集 locations/ 下所有非模板 YAML 文件，返回 {loc_id: data} 映射。
    同时建立 name -> loc_id 的索引用于模糊匹配。"""
    loc_dir = root / "locations"
    result: Dict[str, dict] = {}
    if not loc_dir.is_dir():
        return result
    for fp in loc_dir.glob("*.yaml"):
        if is_template(fp):
            continue
        data = read_yaml_file(fp)
        if isinstance(data, dict) and "id" in data:
            result[data["id"]] = data
    return result


def check_location_consistency(
    root: Path,
    chapters: List[Tuple[int, dict, Path]],
) -> CheckResult:
    """检查 12: 场景档案一致性。"""
    result = CheckResult(name="场景一致性")
    loc_map = collect_location_files(root)

    if not loc_map:
        result.level = "SKIP"
        result.messages.append("locations/ 目录下无场景档案")
        return result

    issues: List[str] = []

    # 建立 name -> loc_id 索引
    name_to_id: Dict[str, str] = {}
    for loc_id, data in loc_map.items():
        name = data.get("name", "")
        if name:
            name_to_id[name] = loc_id

    # 12a: parent_location 引用必须存在（FAIL）
    for loc_id, data in loc_map.items():
        parent = data.get("parent_location")
        if parent and parent != "null" and isinstance(parent, str):
            if parent not in loc_map:
                issues.append(
                    f"场景 {loc_id} ({data.get('name', '')}) "
                    f"的 parent_location {parent} 不存在对应档案"
                )

    # 12b: 章节大纲 scene 字段中的地点名，建议在 locations/ 有档案（WARN）
    scene_warnings: List[str] = []
    for chap_num, data, fp in chapters:
        scene = data.get("scene", "")
        if not scene or not isinstance(scene, str):
            continue
        # 检查场景名是否有对应档案（按名称模糊匹配）
        found = False
        for loc_name in name_to_id:
            if loc_name in scene:
                found = True
                break
        if not found:
            scene_warnings.append(
                f"chapter-{chap_num:04d} 场景 \"{scene[:30]}\" "
                f"未在 locations/ 中找到对应档案"
            )

    if issues:
        result.level = "FAIL"
        result.messages = issues
        # 追加 WARN 级别的场景匹配提示
        if scene_warnings:
            result.messages.extend([f"(WARN) {w}" for w in scene_warnings[:5]])
    elif scene_warnings:
        result.level = "WARN"
        result.messages = scene_warnings[:10]
        if len(scene_warnings) > 10:
            result.messages.append(f"... 及其他 {len(scene_warnings) - 10} 处")
    else:
        result.level = "PASS"
        result.messages.append(
            f"共 {len(loc_map)} 个场景档案，引用关系正常"
        )
    return result


def check_session_state(
    root: Path,
    arc_map: Dict[str, Path],
) -> CheckResult:
    """检查 9: session-state.yaml 有效性。"""
    result = CheckResult(name="会话状态")
    ss_path = root / "state" / "session-state.yaml"
    data = read_yaml_file(ss_path)
    if data is None:
        result.level = "SKIP"
        result.messages.append(
            "state/session-state.yaml 不存在（新项目尚未初始化）"
        )
        return result
    if not isinstance(data, dict):
        result.level = "SKIP"
        result.messages.append("state/session-state.yaml 格式异常")
        return result

    issues: List[str] = []

    last_session = data.get("last_session")
    next_session = data.get("next_session")

    # 从 last_session 获取已完成章节
    chapter_completed: Optional[int] = None
    if isinstance(last_session, dict):
        chapter_completed = last_session.get("chapter_completed")

    if isinstance(next_session, dict):
        chapter_to_write = next_session.get("chapter_to_write")
        expected_action = next_session.get("expected_action")
        arc = next_session.get("arc")

        # chapter_to_write > chapter_completed
        if (
            isinstance(chapter_to_write, int)
            and isinstance(chapter_completed, int)
            and chapter_to_write <= chapter_completed
        ):
            issues.append(
                f"chapter_to_write ({chapter_to_write}) "
                f"应大于 chapter_completed ({chapter_completed})"
            )

        # expected_action 校验
        if expected_action is not None and expected_action not in VALID_EXPECTED_ACTIONS:
            issues.append(
                f"expected_action \"{expected_action}\" 不是有效值，"
                f"应为: {', '.join(sorted(VALID_EXPECTED_ACTIONS))}"
            )

        # arc 引用校验
        if arc is not None and arc_map and arc not in arc_map:
            issues.append(
                f"arc \"{arc}\" 不存在对应的卷大纲文件"
            )

    if issues:
        result.level = "FAIL"
        result.messages = issues
    else:
        result.level = "PASS"
        result.messages.append("会话状态文件有效")
    return result


def check_segment_exit_state(root: Path, char_map: Dict[str, Path]) -> CheckResult:
    """检查 13: 已完成段落的 exit_state 与当前状态文件是否一致。

    仅检查 status=completed 的段落。
    对每条 exit_state 声明，尝试从状态文件中找到支撑证据。
    无法自动验证的声明标记为 WARN（需人工确认）。
    """
    result = CheckResult(name="段落出口状态验证")
    segments_dir = root / "outline" / "segments"

    if not segments_dir.is_dir():
        result.level = "SKIP"
        result.messages.append("outline/segments/ 目录不存在，跳过检查")
        return result

    completed_segments = []
    for seg_file in sorted(segments_dir.glob("seg-*.yaml")):
        if seg_file.name.startswith("_"):
            continue
        data = read_yaml_file(seg_file)
        if isinstance(data, dict) and data.get("status") == "completed":
            completed_segments.append((seg_file, data))

    if not completed_segments:
        result.level = "PASS"
        result.messages.append("无已完成段落，跳过 exit_state 验证")
        return result

    issues: List[str] = []
    warnings: List[str] = []

    # 预加载角色档案状态（cultivation_level 和 location）
    char_states: Dict[str, dict] = {}
    for char_id, char_path in char_map.items():
        try:
            with open(char_path, encoding="utf-8") as fh:
                content = fh.read()
            match = re.match(r"^\s*---\s*\n(.*?)\n---", content, re.DOTALL)
            if match:
                char_data = yaml.safe_load(match.group(1))
                if isinstance(char_data, dict):
                    char_states[char_id] = char_data
        except Exception:
            pass

    # 预加载 plot-threads 活跃线索
    threads_data = read_yaml_file(root / "state" / "plot-threads.yaml")
    active_thread_ids: Set[str] = set()
    resolved_thread_ids: Set[str] = set()
    if isinstance(threads_data, dict):
        for t in threads_data.get("active_threads", []):
            if isinstance(t, dict) and "id" in t:
                active_thread_ids.add(str(t["id"]))
        for t in threads_data.get("resolved_threads", []):
            if isinstance(t, dict) and "id" in t:
                resolved_thread_ids.add(str(t["id"]))

    for seg_file, seg_data in completed_segments:
        seg_id = seg_data.get("id", seg_file.stem)
        exit_states = seg_data.get("exit_state", [])
        if not isinstance(exit_states, list):
            continue

        for idx, stmt in enumerate(exit_states):
            stmt_str = str(stmt).strip()
            if not stmt_str:
                continue

            # 自动验证：thread-XXX 推进/回收
            thread_match = re.search(r"(thread-\d+)", stmt_str)
            if thread_match:
                tid = thread_match.group(1)
                if "回收" in stmt_str or "resolve" in stmt_str.lower():
                    if tid not in resolved_thread_ids:
                        issues.append(
                            f"{seg_id} exit_state[{idx}]: "
                            f"声明 {tid} 已回收，但 plot-threads.yaml 中未找到对应回收记录"
                        )
                else:
                    if tid not in active_thread_ids and tid not in resolved_thread_ids:
                        warnings.append(
                            f"{seg_id} exit_state[{idx}]: "
                            f"声明 {tid} 已推进，但 plot-threads.yaml 中未找到该线索"
                        )
                continue

            # 其余声明：标记为需人工确认（不设为 FAIL，避免误报）
            warnings.append(
                f"{seg_id} exit_state[{idx}]: \"{stmt_str[:40]}\" — 需人工确认达成状态"
            )

    total_items = sum(
        len(seg_data.get("exit_state", []))
        for _, seg_data in completed_segments
        if isinstance(seg_data.get("exit_state"), list)
    )
    if issues:
        result.level = "FAIL"
        result.messages = issues + warnings
    elif warnings:
        result.level = "WARN"
        result.messages = warnings
    else:
        result.level = "PASS"
        result.messages.append(
            f"已验证 {len(completed_segments)} 个已完成段落，"
            f"共 {total_items} 条 exit_state 声明"
        )
    return result


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_report(results: List[CheckResult]) -> int:
    """打印格式化报告，返回退出码。"""
    fail_count = 0
    warn_count = 0
    pass_count = 0
    skip_count = 0

    for r in results:
        if r.level == "FAIL":
            fail_count += 1
        elif r.level == "WARN":
            warn_count += 1
        elif r.level == "PASS":
            pass_count += 1
        else:
            skip_count += 1

    print()
    print(SEPARATOR_THICK)
    print("  一致性检查报告")
    print(SEPARATOR_THICK)
    print()

    for r in results:
        print(f"  [{r.name}]")
        for msg in r.messages:
            print(f"  {r.level} | {msg}")
        print()

    print(SEPARATOR_THIN)
    parts: List[str] = []
    if fail_count:
        parts.append(f"{fail_count} FAIL")
    if warn_count:
        parts.append(f"{warn_count} WARN")
    if pass_count:
        parts.append(f"{pass_count} PASS")
    if skip_count:
        parts.append(f"{skip_count} SKIP")
    print(f"  统计: {' / '.join(parts)}")

    if fail_count > 0:
        print("  结论: FAIL -- 存在必须修复的一致性问题")
    elif warn_count > 0:
        print("  结论: WARN -- 存在需要关注的潜在问题")
    else:
        print("  结论: PASS -- 所有检查通过")
    print(SEPARATOR_THICK)
    print()

    return 1 if fail_count > 0 else 0


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    """运行全部一致性检查，返回退出码。"""
    root = find_project_root()

    # 预加载公共数据
    char_map = collect_character_files(root)
    chapters = collect_chapter_outlines(root)
    arc_map = collect_arc_files(root)

    results: List[CheckResult] = []

    # 1. 人物引用完整性
    results.append(check_character_reference(root, char_map, chapters))

    # 2. 死亡人物出场检查
    results.append(check_dead_character_appearance(root, char_map, chapters))

    # 3. 事实 ID 唯一性
    results.append(check_fact_id_uniqueness(root))

    # 4. 事实人物引用
    results.append(check_fact_character_reference(root, char_map))

    # 5. 伏笔时间线有效性
    results.append(check_plot_thread_timeline(root, chapters))

    # 6. 关系目标存在性
    results.append(check_relationship_target_existence(root, char_map))

    # 7. 关系对称性
    results.append(check_relationship_symmetry(root, char_map))

    # 8. 时间线连续性
    results.append(check_timeline_continuity(root))

    # 9. 会话状态有效性
    results.append(check_session_state(root, arc_map))

    # 10. 里程碑防重检查
    results.append(check_milestone_duplicates(root))

    # 11. 大纲状态交叉验证
    results.append(check_outline_state_consistency(root, char_map, chapters))

    # 12. 场景一致性
    results.append(check_location_consistency(root, chapters))

    # 13. 已完成段落 exit_state 验证
    results.append(check_segment_exit_state(root, char_map))

    return print_report(results)


if __name__ == "__main__":
    sys.exit(main())
