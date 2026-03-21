#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
write-chapter.py -- Qwen API 章节写作脚本

通过 DashScope OpenAI 兼容接口调用 Qwen 模型，执行三稿制章节写作。
Claude 负责 Phase 1/1.5 的上下文组装，本脚本负责 Phase 2 的文本生成。

用法:
  # 骨架稿
  python scripts/write-chapter.py --chapter 2 --draft skeleton --context-file state/writing-context.json

  # 感官稿（需要前一稿）
  python scripts/write-chapter.py --chapter 2 --draft sensory --context-file state/writing-context.json --prev-draft chapters/arc-001/chapter-0002-skeleton.md

  # 删减稿（需要前一稿）
  python scripts/write-chapter.py --chapter 2 --draft trimmed --context-file state/writing-context.json --prev-draft chapters/arc-001/chapter-0002-sensory.md

  # 调试：只打印 prompt 不调用 API
  python scripts/write-chapter.py --chapter 2 --draft skeleton --context-file state/writing-context.json --dry-run
"""

import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path

# Windows UTF-8 stdout
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import yaml
except ImportError:
    print(
        "[error] missing: PyYAML\n  pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(2)

try:
    from openai import OpenAI
except ImportError:
    print(
        "[error] missing: openai\n  pip install openai",
        file=sys.stderr,
    )
    sys.exit(2)

EXIT_OK = 0
EXIT_ERROR = 2

DRAFT_NAMES = {
    "skeleton": "骨架稿",
    "sensory": "感官稿",
    "trimmed": "删减稿",
    "review": "机审稿",
    "elevate": "文笔升级稿",
}


# ------------------------------------------------------------------
# Project root
# ------------------------------------------------------------------
def find_project_root() -> Path:
    """Walk up from script dir looking for ENTRY.md."""
    current = Path(__file__).resolve().parent
    for _ in range(20):
        if (current / "ENTRY.md").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    print("[error] cannot locate project root (ENTRY.md not found)", file=sys.stderr)
    sys.exit(EXIT_ERROR)


# ------------------------------------------------------------------
# .env loader (no python-dotenv dependency)
# ------------------------------------------------------------------
def load_dotenv(project_root: Path) -> None:
    """Read .env file and inject into os.environ (skip existing keys)."""
    env_file = project_root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ------------------------------------------------------------------
# Config loaders
# ------------------------------------------------------------------
def load_yaml(path: Path) -> dict:
    """Load a YAML file, return dict. Exit on error."""
    if not path.exists():
        print(f"[error] file not found: {path}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def load_md_with_frontmatter(path: Path) -> tuple:
    """Load a .md file with YAML frontmatter. Returns (frontmatter_dict, body_str)."""
    if not path.exists():
        print(f"[error] file not found: {path}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    text = path.read_text(encoding="utf-8")
    fm, body = {}, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
    return fm, body


def load_api_key() -> str:
    """Get DASHSCOPE_API_KEY from environment."""
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key or key == "sk-your-key-here":
        print(
            "[error] DASHSCOPE_API_KEY not set.\n"
            "Set it in .env or as environment variable.",
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)
    return key


def load_llm_config(project_root: Path) -> dict:
    """Load config/llm-config.yaml."""
    return load_yaml(project_root / "config" / "llm-config.yaml")


# ------------------------------------------------------------------
# Writing config loaders
# ------------------------------------------------------------------
def load_writing_configs(project_root: Path) -> dict:
    """Load all 5 writing config files into a dict."""
    cfg_dir = project_root / "config"
    return {
        "writing_style": load_yaml(cfg_dir / "writing-style.yaml"),
        "novel_identity": load_yaml(cfg_dir / "novel-identity.yaml"),
        "anti_ai": load_yaml(cfg_dir / "anti-ai-patterns.yaml"),
        "prose_ref": load_yaml(cfg_dir / "prose-reference.yaml"),
        "style_samples": load_yaml(cfg_dir / "style-samples.yaml"),
    }


def load_chapter_outline(project_root: Path, chapter_num: int) -> dict:
    """Load outline/chapters/chapter-NNNN.yaml."""
    fname = f"chapter-{chapter_num:04d}.yaml"
    path = project_root / "outline" / "chapters" / fname
    return load_yaml(path)


def load_context_json(path: Path) -> dict:
    """Load the writing context JSON assembled by Claude."""
    if not path.exists():
        print(f"[error] context file not found: {path}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prev_draft(path_str: str, project_root: Path) -> str:
    """Load previous draft text."""
    p = Path(path_str)
    if not p.is_absolute():
        p = project_root / p
    if not p.exists():
        print(f"[error] previous draft not found: {p}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    return p.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# Prompt rendering: banned words & patterns -> Chinese text
# ------------------------------------------------------------------
def render_banned_words(banned_words: list) -> str:
    """Group banned words by severity, render as Chinese lists."""
    groups = {"critical": [], "high": [], "medium": []}
    for w in banned_words:
        sev = w.get("severity", "medium")
        word = w.get("word", "")
        if sev in groups and word:
            groups[sev].append(word)

    lines = []
    if groups["critical"]:
        lines.append(f"[绝对禁用] {', '.join(groups['critical'])}")
    if groups["high"]:
        lines.append(f"[强烈禁用] {', '.join(groups['high'])}")
    if groups["medium"]:
        lines.append(f"[限制使用，单章最多1次] {', '.join(groups['medium'])}")
    return "\n".join(lines)


def render_banned_patterns(patterns: list) -> str:
    """Render banned patterns as natural language descriptions."""
    lines = []
    for p in patterns:
        desc = p.get("description", "")
        note = p.get("note", "")
        if desc:
            line = f"- 禁止: {desc}"
            if note:
                line += f" ({note})"
            lines.append(line)
    return "\n".join(lines)


def render_structural_rules(rules: dict) -> str:
    """Render structural rules as Chinese instructions."""
    lines = []
    pv = rules.get("paragraph_variation", {})
    if pv:
        lines.append(f"- 段落节奏: {pv.get('rule', '')}")

    iv = rules.get("inner_voice_density", {})
    if iv:
        lines.append(f"- 内心声音: {iv.get('rule', '')}")

    ob = rules.get("chapter_opening_ban", {})
    if ob and ob.get("patterns"):
        descs = [p.get("regex", "").lstrip("^") for p in ob["patterns"]]
        lines.append(f"- 开头禁止: 以下开头方式全部禁用: {'、'.join(descs[:3])}等")

    tb = rules.get("transition_ban", {})
    if tb and tb.get("words"):
        words = [w.get("word", "") for w in tb["words"] if w.get("word")]
        lines.append(f"- 转场禁用词: {', '.join(words)}")
        lines.append(f"  {tb.get('note', '')}")

    eb = rules.get("ending_ban", {})
    if eb and eb.get("patterns"):
        lines.append("- 结尾禁止: '而这一切才刚刚开始'等套话")

    return "\n".join(lines)


def render_translation_tone(tt: dict) -> str:
    """Render translation tone rules with before/after examples."""
    lines = ["[翻译腔检测 -- 以下痕迹必须消灭]"]

    pv = tt.get("passive_voice", [])
    if pv:
        lines.append("A. 被动语态: 中文天然用主动句，'被'字暗示不幸，改为主动句")
        for item in pv:
            ex = item.get("examples", {})
            if ex:
                lines.append(f"   错: {ex.get('bad', '')}")
                lines.append(f"   对: {ex.get('good', '')}")

    dc = tt.get("de_chain", [])
    if dc:
        lines.append("B. 的字链: 连续3个'的'修饰结构是英式定语从句习惯，拆成多句")
        for item in dc:
            ex = item.get("examples", {})
            if ex:
                lines.append(f"   错: {ex.get('bad', '')}")
                lines.append(f"   对: {ex.get('good', '')}")

    fc = tt.get("formal_connectors", {})
    if fc and fc.get("words"):
        words = [w.get("word", "") for w in fc["words"] if w.get("word")]
        lines.append(f"C. 形合连接词禁用/限用: {', '.join(words)}")

    td = tt.get("temporal_dang", [])
    if td:
        lines.append("D. '当...时'句式: 英式时间从句，改为直接叙述")
        for item in td:
            ex = item.get("examples", {})
            if ex:
                lines.append(f"   错: {ex.get('bad', '')}")
                lines.append(f"   对: {ex.get('good', '')}")

    mc = tt.get("metaphor_calques", {})
    if mc and mc.get("examples"):
        lines.append("E. 英语隐喻直译: 字面中文但比喻逻辑来自英语")
        for ex in mc["examples"][:3]:
            lines.append(f"   错: {ex.get('bad', '')}")
            lines.append(f"   对: {ex.get('good', '')}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Prompt rendering: style samples
# ------------------------------------------------------------------
def render_style_samples(samples_cfg: dict, scene_types: list) -> str:
    """Always include ss-009/ss-010 (translation tone anchors).
    Add scene-type-specific samples based on chapter content."""
    samples = samples_cfg.get("samples", [])
    if not samples:
        return ""

    must_include = {"ss-009", "ss-010"}
    scene_tag_map = {
        "combat": ["战斗", "心理", "环境", "意境"],
        "slice_of_life": ["日常", "对话", "环境"],
        "solo": ["内心", "克制", "留白", "环境", "意境", "苍茫", "孤独", "节奏", "收束", "余韵"],
        "emotional": ["克制", "情绪", "留白", "苍茫", "孤独", "环境"],
        "mystery": ["信息密度", "环境"],
        "power_up": ["环境", "意境", "节奏"],
        "setback": ["苍茫", "孤独", "环境", "克制"],
    }
    wanted_tags = set()
    for st in scene_types:
        wanted_tags.update(scene_tag_map.get(st, []))
    wanted_tags.update(["翻译腔", "语感", "核心"])

    selected = []
    for s in samples:
        sid = s.get("id", "")
        tags = set(s.get("tags", []))
        if sid in must_include or tags & wanted_tags:
            selected.append(s)

    lines = ["[文笔参照样本 -- 感受这些段落的呼吸节奏，用自己的故事写出同样的呼吸感]"]
    for s in selected:
        sid = s.get("id", "")
        source = s.get("source", "")
        technique = s.get("technique", "")
        text = s.get("text", "").strip()
        why = s.get("why_it_works", "").strip()
        lines.append(f"\n--- {sid} ({source}) -- {technique} ---")
        if len(text) > 800:
            text = text[:800] + "..."
        lines.append(text)
        if why:
            if len(why) > 400:
                why = why[:400] + "..."
            lines.append(f"要点: {why}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Prompt rendering: scene style guide
# ------------------------------------------------------------------
def render_scene_styles(identity_cfg: dict, scene_types: list) -> str:
    """Render only relevant scene type style guides."""
    styles = identity_cfg.get("scene_styles", {})
    if not styles or not scene_types:
        return ""

    lines = ["[场景风格指南]"]
    for st in scene_types:
        style = styles.get(st)
        if not style:
            continue
        lines.append(f"\n-- {st} --")
        for key in ("philosophy", "pacing", "focus", "approach", "forbidden"):
            val = style.get(key, "")
            if val:
                val_str = val.strip() if isinstance(val, str) else str(val)
                if len(val_str) > 400:
                    val_str = val_str[:400] + "..."
                lines.append(f"{key}: {val_str}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Prompt rendering: prose quality
# ------------------------------------------------------------------
def render_prose_quality(prose_ref: dict) -> str:
    """Render prose elevation checklist, Zhu Xian techniques, style DNA."""
    lines = ["[文笔质量标准]"]

    elev = prose_ref.get("prose_elevation", {})

    philosophy = elev.get("philosophy", "")
    if philosophy:
        lines.append(f"理念: {philosophy.strip()}")

    checklist = elev.get("elevation_checklist", [])
    if checklist:
        lines.append("\n文笔提升检查清单:")
        for item in checklist:
            name = item.get("name", "")
            rule = item.get("rule", "")
            if name:
                lines.append(f"- {name}: {rule}")

    zx = elev.get("zhu_xian_style_guide", [])
    if zx:
        lines.append("\n诛仙七法:")
        for item in zx:
            name = item.get("name", "")
            desc = item.get("description", "")
            if name:
                lines.append(f"- {name}: {desc}")

    dna = elev.get("style_dna", {})
    if dna:
        lines.append("\n风格DNA指标:")
        for k, v in dna.items():
            lines.append(f"- {k}: {v}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Draft-specific instructions
# ------------------------------------------------------------------
def render_draft_instructions(prose_ref: dict, draft_type: str) -> str:
    """Render draft-specific instructions from three_draft_system."""
    if draft_type == "review":
        return ""  # review uses its own system prompt

    tds = prose_ref.get("three_draft_system", {})

    draft_key_map = {
        "skeleton": "draft_1",
        "sensory": "draft_2",
        "trimmed": "draft_3",
    }
    key = draft_key_map.get(draft_type, "")
    d = tds.get(key, {})
    if not d:
        fallback = {
            "skeleton": "骨架稿: 专注逻辑链和节奏骨架，不追求文笔细节",
            "sensory": "感官稿: 在骨架基础上填充五感细节、环境描写、情绪层次",
            "trimmed": "删减稿: 删除15-20%内容，去掉冗余修饰，让文字更干净有力",
        }
        return fallback.get(draft_type, "")

    lines = [f"[本稿任务: {d.get('name', '')}]"]
    focus = d.get("focus", "")
    if focus:
        lines.append(f"核心关注: {focus}")
    word_count = d.get("word_count", "")
    if word_count:
        lines.append(f"目标字数: {word_count}")
    rules = d.get("rules", [])
    if rules:
        lines.append("规则:")
        for r in rules:
            lines.append(f"  - {r}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Build system prompt (~6000-7000 tokens)
# ------------------------------------------------------------------
def build_system_prompt(
    configs: dict,
    draft_type: str,
    scene_types: list,
) -> str:
    """Assemble the full Chinese system prompt from config files."""
    ws = configs["writing_style"]
    ni = configs["novel_identity"]
    anti = configs["anti_ai"]
    pr = configs["prose_ref"]
    ss = configs["style_samples"]

    sections = []

    # 1. Writer identity + novel soul + LITERARY QUALITY MANDATE
    identity = ni.get("identity", {})
    core_emo = identity.get("core_emotions", {})
    identity_lines = [
        "你是一位文笔以萧鼎《诛仙》为标杆的中文玄幻小说作者。",
        "你写的不是网文初稿，而是有文学质感的完成品。",
        "",
        "[文笔最高优先级指令 -- 贯穿全文每一句]",
        "1. 以景写情：角色内心不用情绪词，让天地替他说。他难过就写雨，他孤独就写空山。",
        "2. 长短交替：长句铺展画面（30字以上），短句冲击转折（10字以下）。绝不能全篇短句。",
        "   示范节奏：'一道裂痕横贯苍穹，似瓷碗摔碎的纹，透着惨白的光。百丈高空，两道身影悬着。'（长句铺画面）",
        "   '天破了。'（短句冲击）",
        "   长短交替如呼吸，一口气写完三个短句必须接一个长句舒展。",
        "3. 意境句：每个场景至少一句纯画面定调句，不推剧情只造意境。",
        "   示范：'雨从半夜下到天亮，把整座山洗成了青黑色。'",
        "   示范：'雾从谷底涌出来，没过了溪涧，没过了杂木林，最后连那几棵老松也只剩下影子。'",
        "4. 万物有灵：环境不是背景板。雾会吞人，光会漏过裂缝，山会压下来。",
        "5. 水墨留白：描写写三分留七分，不把画面填满。虚实相生才有意境。",
        "6. 天地苍茫：每章至少一处让读者感受到人在天地间的渺小。",
        "7. 克制的力量感：越重要的时刻字越少。高潮不堆华丽辞藻，用最朴素的词最短的句。",
        "",
        f"小说灵魂: {identity.get('soul', '')}",
        f"核心情绪: {core_emo.get('primary', '')}",
        f"暗流: {core_emo.get('undercurrent', '')}",
        f"世界质感: {identity.get('world_texture', {}).get('tone_balance', '')}",
    ]
    sections.append("\n".join(identity_lines))

    # 2. Writing technique
    tech_lines = ["[写作技法]"]
    pov = ws.get("pov", "")
    if pov:
        tech_lines.append(f"视角: {pov}")
    tone = ws.get("tone", "")
    if tone:
        tech_lines.append(f"基调: {tone}")

    im = ws.get("inner_monologue", "")
    if im:
        # Multiline string -- truncate to key rules
        im_text = im.strip()
        if len(im_text) > 1500:
            im_text = im_text[:1500] + "..."
        tech_lines.append(f"内心独白规则:\n{im_text}")

    ncr = ws.get("native_chinese_rules", {})
    native_rules = ncr.get("rules", []) if isinstance(ncr, dict) else []
    if native_rules:
        tech_lines.append("中文母语规则:")
        for r in native_rules:
            if isinstance(r, dict):
                name = r.get("name", "")
                principle = r.get("principle", "")
                tech_lines.append(f"  - {name}: {principle}")
            else:
                tech_lines.append(f"  - {r}")
    sections.append("\n".join(tech_lines))

    # 3. Scene style guide (only relevant types)
    scene_sec = render_scene_styles(ni, scene_types)
    if scene_sec:
        sections.append(scene_sec)

    # 4. Anti-AI banned list
    anti_lines = ["[反AI检测规则 -- 违反即FAIL]"]
    bw = anti.get("banned_words", [])
    if bw:
        anti_lines.append(render_banned_words(bw))
    bp = anti.get("banned_patterns", [])
    if bp:
        anti_lines.append(render_banned_patterns(bp))
    sr = anti.get("structural_rules", {})
    if sr:
        anti_lines.append(render_structural_rules(sr))
    tt = anti.get("translation_tone_patterns", {})
    if tt:
        anti_lines.append(render_translation_tone(tt))
    sections.append("\n".join(anti_lines))

    # 5. Prose quality
    sections.append(render_prose_quality(pr))

    # 6. Style samples (dynamic by scene type)
    samples_sec = render_style_samples(ss, scene_types)
    if samples_sec:
        sections.append(samples_sec)

    # 7. Draft-specific instructions
    sections.append(render_draft_instructions(pr, draft_type))

    return "\n\n".join(s for s in sections if s)


# ------------------------------------------------------------------
# Build elevate system prompt (pure literary rewrite)
# ------------------------------------------------------------------
def build_elevate_system_prompt(configs: dict) -> str:
    """Build a clean, focused system prompt for literary elevation.

    This prompt contains ONLY style directives -- no rules, no constraints,
    no banned words. All constraint enforcement happens in the first step.
    """
    ss = configs["style_samples"]
    pr = configs["prose_ref"]

    sections = []

    # 1. Role: pure literary author
    sections.append(
        "你是萧鼎。你写过《诛仙》。现在你要用你写诛仙的笔力，重写一段玄幻小说章节。\n\n"
        "初稿的情节骨架是对的，但文笔像记流水账。你要做的是：\n"
        "用你写诛仙时的全部功力——意境、节奏、画面、苍茫感——彻底重铸这段文字的语言层。\n\n"
        "约束（只有两条）：\n"
        "1. 情节事件和信息量不变（谁做了什么、发现了什么、想了什么，这些不能改）\n"
        "2. 内心独白保留（短句独白是这本书的特色，保持频率，措辞可以升级）\n\n"
        "除此之外，语言层面你拥有完全自由：可以重组句序、重写描述、替换比喻、\n"
        "增加意境句、调整节奏——像你写诛仙一样写。不是润色，是重写。\n"
    )

    # 2. Zhu Xian style guide (the core creative directive)
    elev = pr.get("prose_elevation", {})
    zx = elev.get("zhu_xian_style_guide", [])
    if zx:
        zx_lines = ["[诛仙七法 -- 重写时逐条对照执行]"]
        for item in zx:
            name = item.get("name", "")
            desc = item.get("description", "")
            examples = item.get("examples", [])
            zx_lines.append(f"\n{name}:")
            zx_lines.append(f"  {desc}")
            if examples:
                for ex in examples:
                    zx_lines.append(f"  范例：{ex}")
        sections.append("\n".join(zx_lines))

    # 3. Key style samples (full text, no truncation)
    samples = ss.get("samples", [])
    key_ids = {"ss-001", "ss-002", "ss-004", "ss-007", "ss-008", "ss-009", "ss-010"}
    if samples:
        ss_lines = ["[文笔参照 -- 感受这些段落的呼吸节奏，写出同样的呼吸感]"]
        for s in samples:
            sid = s.get("id", "")
            if sid not in key_ids:
                continue
            source = s.get("source", "")
            technique = s.get("technique", "")
            text = s.get("text", "").strip()
            why = s.get("why_it_works", "").strip()
            ss_lines.append(f"\n--- {sid} ({source}) -- {technique} ---")
            ss_lines.append(text)
            if why:
                ss_lines.append(f"要点: {why}")
        sections.append("\n".join(ss_lines))

    # 4. Style DNA (quantitative targets)
    dna = elev.get("style_dna", {})
    if dna:
        dna_lines = ["[风格DNA指标]"]
        for k, v in dna.items():
            if k == "description":
                continue
            if isinstance(v, dict):
                for sk, sv in v.items():
                    dna_lines.append(f"- {k}.{sk}: {sv}")
            else:
                dna_lines.append(f"- {k}: {v}")
        sections.append("\n".join(dna_lines))

    return "\n\n".join(s for s in sections if s)


def build_elevate_user_prompt(prev_draft_text: str, context: dict) -> str:
    """Build user prompt for literary elevation."""
    ch_num = context.get("chapter_number", "?")
    ch_title = context.get("chapter_title", "")
    word_target = context.get("word_target", "2000-2500")

    # Include mood board for emotional guidance
    mood = context.get("mood_board", {})
    mood_str = ""
    if mood:
        mood_parts = []
        for key in ("core_image", "reader_emotion", "sensory_anchor", "key_line"):
            val = mood.get(key, "")
            if val:
                mood_parts.append(f"  {key}: {val}")
        if mood_parts:
            mood_str = "\n[本章情绪板]\n" + "\n".join(mood_parts) + "\n"

    return (
        f"以下是第{ch_num}章「{ch_title}」的初稿。情节和信息量都是对的，但文笔平淡。\n"
        f"请用诛仙级别的文笔重写全文。目标字数{word_target}字。\n"
        f"{mood_str}\n"
        f"[初稿全文]\n{prev_draft_text}\n\n"
        "直接输出重写后的正文，不要标题，不要解释。"
    )


# ------------------------------------------------------------------
# Build review system prompt (editor/proofreader role)
# ------------------------------------------------------------------
def build_review_system_prompt(configs: dict) -> str:
    """Build system prompt for the review (machine-edit) draft."""
    anti = configs["anti_ai"]
    pr = configs["prose_ref"]

    sections = []

    # 1. Role definition
    sections.append(
        "你是一位严苛的中文小说编辑，专门审校网文终稿。\n"
        "你的任务是逐句审读全文，找出并修正以下问题:\n"
        "1. 病句: 读出声来不通顺的句子，无论多有'意境'\n"
        "2. 搭配不当: 动词与名词搭配不成立、拟人动词生硬\n"
        "3. 翻译腔: 被动语态、的字链、当字句、形合连接词\n"
        "4. 禁用词/禁用句式: 违反反AI检测规则的用词和句式\n"
        "5. 节奏问题: 连续500字以上单一节奏、段落长度无变化\n"
        "6. 情绪直述: 直接用形容词说破情绪而非通过行为/细节展现\n"
        "7. 对话标签: '愤怒地说'等副词标签\n\n"
        "审校原则:\n"
        "- 只改有问题的句子，没问题的不动\n"
        "- 修改后的句子必须保持原意和语境\n"
        "- 不改变剧情、人物行为、对话内容\n"
        "- 不添加新内容，不删除情节\n"
        "- 修改幅度尽量小，能改一个词不改整句"
    )

    # 2. Anti-AI rules (same as writing prompt)
    anti_lines = ["[反AI检测规则 -- 违反即必须修正]"]
    bw = anti.get("banned_words", [])
    if bw:
        anti_lines.append(render_banned_words(bw))
    bp = anti.get("banned_patterns", [])
    if bp:
        anti_lines.append(render_banned_patterns(bp))
    sr = anti.get("structural_rules", {})
    if sr:
        anti_lines.append(render_structural_rules(sr))
    tt = anti.get("translation_tone_patterns", {})
    if tt:
        anti_lines.append(render_translation_tone(tt))
    sections.append("\n".join(anti_lines))

    # 3. Prose quality checklist
    sections.append(render_prose_quality(pr))

    return "\n\n".join(s for s in sections if s)


# ------------------------------------------------------------------
# Build review user prompt
# ------------------------------------------------------------------
def build_review_user_prompt(prev_draft_text: str, context: dict) -> str:
    """Build user prompt for the review draft."""
    ch_num = context.get("chapter_number", "?")
    ch_title = context.get("chapter_title", "")

    sections = []
    sections.append(
        f"以下是第{ch_num}章「{ch_title}」的终稿全文。\n"
        "请逐句审读，执行以下操作:\n\n"
        "1. 输出修正后的完整正文（直接从第一句开始，不要标题）\n"
        "2. 在正文之后，用以下格式输出修改清单:\n\n"
        "---修改清单---\n"
        "| 序号 | 原文 | 修改为 | 修改原因 |\n"
        "|------|------|--------|----------|\n"
        "| 1 | 原句 | 改后句 | 病句/翻译腔/禁用词/... |\n\n"
        "如果全文无需修改，正文后只写:\n"
        "---修改清单---\n"
        "无修改。"
    )

    sections.append(f"[终稿全文]\n{prev_draft_text}")

    return "\n\n".join(sections)


# ------------------------------------------------------------------
# Build user prompt (~2000-5000 tokens)
# ------------------------------------------------------------------
def build_user_prompt(
    context: dict,
    draft_type: str,
    prev_draft_text: str,
) -> str:
    """Assemble chapter-specific user prompt from context JSON."""
    sections = []

    # 0a. Story direction (master outline excerpt)
    story_dir = context.get("story_direction", "")
    if story_dir:
        sections.append(f"[全书走向]\n{story_dir}")

    # 0b. Arc outline (current volume goals)
    arc_outline = context.get("arc_outline", "")
    if arc_outline:
        if isinstance(arc_outline, dict):
            ao_lines = ["[本卷大纲]"]
            for k, v in arc_outline.items():
                if isinstance(v, list):
                    ao_lines.append(f"{k}:")
                    for item in v:
                        ao_lines.append(f"  - {item}")
                else:
                    ao_lines.append(f"{k}: {v}")
            sections.append("\n".join(ao_lines))
        else:
            sections.append(f"[本卷大纲]\n{arc_outline}")

    # 0c. World brief
    world_brief = context.get("world_brief", "")
    if world_brief:
        sections.append(f"[世界观摘要]\n{world_brief}")

    # 0d. Retrieved knowledge (vector search + fact search results)
    retrieved = context.get("retrieved_knowledge", [])
    if retrieved:
        rk_lines = ["[检索到的关键信息 -- Claude 从向量数据库和事实库中提取]"]
        for item in retrieved:
            if isinstance(item, dict):
                source = item.get("source", "")
                content = item.get("content", "")
                rk_lines.append(f"  - [{source}] {content}")
            else:
                rk_lines.append(f"  - {item}")
        sections.append("\n".join(rk_lines))

    # 1. Chapter outline
    outline = context.get("chapter_outline", {})
    if outline:
        ol_lines = ["[章节大纲]"]
        for key in ("scene", "objectives", "chapter_hook", "pacing",
                     "consistency_notes", "emotional_arc"):
            val = outline.get(key)
            if val:
                if isinstance(val, list):
                    ol_lines.append(f"{key}:")
                    for item in val:
                        ol_lines.append(f"  - {item}")
                else:
                    ol_lines.append(f"{key}: {val}")
        sections.append("\n".join(ol_lines))

    # 2. Character briefs (with detailed current_state)
    chars = context.get("characters", {})
    if chars:
        ch_lines = ["[出场角色]"]
        for cid, info in chars.items():
            name = info.get("name", cid)
            ch_lines.append(f"\n{name} ({cid}):")
            for key in ("personality_brief", "speech_style", "behavior_patterns"):
                val = info.get(key)
                if val:
                    ch_lines.append(f"  {key}: {val}")
            cs = info.get("current_state", {})
            if cs:
                ch_lines.append("  当前状态:")
                for sk in ("cultivation_level", "location", "status",
                           "hp_condition", "mental_state"):
                    sv = cs.get(sk, "")
                    if sv:
                        ch_lines.append(f"    {sk}: {sv}")
                inv = cs.get("inventory", [])
                if inv:
                    ch_lines.append(f"    持有道具: {', '.join(str(i) for i in inv)}")
                secrets = cs.get("known_secrets", [])
                if secrets:
                    ch_lines.append(f"    已知秘密: {', '.join(str(s) for s in secrets)}")
        sections.append("\n".join(ch_lines))

    # 3. Recent summaries
    summaries = context.get("recent_summaries", [])
    if summaries:
        sm_lines = ["[近期章节摘要]"]
        for s in summaries:
            ch = s.get("chapter", "?")
            liner = s.get("one_liner", "")
            sm_lines.append(f"  第{ch}章: {liner}")
        sections.append("\n".join(sm_lines))

    # 4. Constraints
    constraints = context.get("constraints", [])
    if constraints:
        ct_lines = ["[事实约束 -- 不可违反]"]
        for c in constraints:
            ct_lines.append(f"  - {c}")
        sections.append("\n".join(ct_lines))

    # 4a. Character relationships
    rels = context.get("character_relationships", [])
    if rels:
        rl_lines = ["[角色关系网 -- 当前状态]"]
        for r in rels:
            if isinstance(r, dict):
                src = r.get("from", "")
                tgt = r.get("to", "")
                rel_type = r.get("type", "")
                desc = r.get("description", "")
                rl_lines.append(f"  - {src} -> {tgt}: {rel_type} ({desc})")
            else:
                rl_lines.append(f"  - {r}")
        sections.append("\n".join(rl_lines))

    # 4b. Active plot threads
    threads = context.get("active_plot_threads", [])
    if threads:
        pt_lines = ["[活跃伏笔线 -- 不可提前揭露]"]
        for t in threads:
            if isinstance(t, dict):
                tid = t.get("id", "")
                desc = t.get("description", "")
                status = t.get("status", "")
                clues = t.get("clues_revealed", [])
                clue_str = f"已揭露线索: {len(clues)}条" if clues else "尚无线索"
                pt_lines.append(f"  - {tid}: {desc} [{status}] ({clue_str})")
            else:
                pt_lines.append(f"  - {t}")
        sections.append("\n".join(pt_lines))

    # 4c. Timeline context
    timeline = context.get("timeline_context", {})
    if timeline:
        tl_lines = ["[时间线]"]
        for k in ("current_story_time", "time_since_last_chapter",
                   "season", "time_of_day"):
            v = timeline.get(k, "")
            if v:
                tl_lines.append(f"  {k}: {v}")
        sections.append("\n".join(tl_lines))

    # 4d. World state (dynamic faction/event status)
    ws = context.get("world_state", {})
    if ws:
        ws_lines = ["[世界动态状态]"]
        factions = ws.get("factions", [])
        for f in factions:
            if isinstance(f, dict):
                ws_lines.append(f"  - {f.get('name','')}: {f.get('status','')}")
            else:
                ws_lines.append(f"  - {f}")
        events = ws.get("ongoing_events", [])
        for e in events:
            ws_lines.append(f"  - [事件] {e}")
        if len(ws_lines) > 1:
            sections.append("\n".join(ws_lines))

    # 4e. Character milestones (prevent duplicate breakthroughs)
    milestones = context.get("character_milestones", [])
    if milestones:
        ms_lines = ["[角色里程碑 -- 已发生，不可重复]"]
        for m in milestones:
            if isinstance(m, dict):
                char = m.get("character", "")
                event = m.get("event", "")
                ch = m.get("chapter", "")
                ms_lines.append(f"  - {char} 第{ch}章: {event}")
            else:
                ms_lines.append(f"  - {m}")
        sections.append("\n".join(ms_lines))

    # 4f. Recent changelog
    changes = context.get("recent_changes", [])
    if changes:
        rc_lines = ["[近期状态变更]"]
        for c in changes:
            rc_lines.append(f"  - {c}")
        sections.append("\n".join(rc_lines))

    # 5. Emotion threads (enhanced: include accumulation history and payoff target)
    emo = context.get("active_emotion_threads", [])
    if emo:
        em_lines = ["[情感线积累目标 -- 本章应在对应线上积累细节]"]
        for e in emo:
            if isinstance(e, dict):
                eid = e.get("id", "")
                name = e.get("name", "")
                desc = e.get("description", "")
                payoff = e.get("payoff_target", "")
                em_lines.append(f"\n  {eid} ({name}): {desc}")
                if payoff:
                    em_lines.append(f"    payoff目标: 第{payoff}章")
                acc = e.get("recent_accumulation", [])
                if acc:
                    em_lines.append("    近期积累:")
                    for a in acc[-3:]:
                        if isinstance(a, dict):
                            ch = a.get("chapter", "?")
                            detail = a.get("detail", "")
                            method = a.get("method", "")
                            em_lines.append(f"      第{ch}章: {detail} ({method})")
                        else:
                            em_lines.append(f"      {a}")
                this_ch = e.get("this_chapter_goal", "")
                if this_ch:
                    em_lines.append(f"    本章积累目标: {this_ch}")
            else:
                em_lines.append(f"  - {e}")
        sections.append("\n".join(em_lines))

    # 6. Mood board
    mood = context.get("mood_board", {})
    if mood:
        mb_lines = ["[情绪板]"]
        for key in ("core_image", "reader_emotion", "sensory_anchor",
                     "rhythm", "color_palette"):
            val = mood.get(key, "")
            if val:
                mb_lines.append(f"  {key}: {val}")
        sections.append("\n".join(mb_lines))

    # 7. Battle workshop
    battle = context.get("battle_workshop")
    if battle:
        sections.append(f"[战斗设计]\n{json.dumps(battle, ensure_ascii=False, indent=2)}")

    # 8. Style reference (previous chapter text as baseline)
    style_ref = context.get("style_reference", "")
    if style_ref:
        sections.append(
            f"[文笔基线参照 -- 仅作文笔水准参照，严禁直接引用或复制原文中的句子、意象、比喻。"
            f"本章文笔水准不得低于此基线]\n{style_ref}"
        )

    # 9. Previous draft (for sensory/trimmed)
    if prev_draft_text and draft_type in ("sensory", "trimmed"):
        label = "骨架稿" if draft_type == "sensory" else "感官稿"
        sections.append(f"[上一稿 ({label})]\n{prev_draft_text}")

    # 10. Output instruction
    ch_title = context.get("chapter_title", "")
    word_target = context.get("word_target", "3000-4000")
    sections.append(
        f"请直接输出第{context.get('chapter_number', '?')}章"
        f"「{ch_title}」的{DRAFT_NAMES.get(draft_type, draft_type)}正文。\n"
        f"目标字数: {word_target}字。\n"
        "不要输出任何解释、标题标记或元信息，直接从正文第一句开始。"
    )

    return "\n\n".join(s for s in sections if s)


# ------------------------------------------------------------------
# Qwen API call with retry
# ------------------------------------------------------------------
def call_qwen_api(
    system_prompt: str,
    user_prompt: str,
    llm_config: dict,
    draft_type: str,
    model_override: str = "",
) -> str:
    """Call Qwen via DashScope OpenAI-compatible API. Returns generated text."""
    api_key = load_api_key()
    api_base = llm_config.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    models = llm_config.get("models", {})
    model = model_override or models.get("primary", "qwen3.5-plus")
    fallback_model = models.get("fallback", "qwen-max")

    temps = llm_config.get("temperatures", {})
    temperature = temps.get(draft_type, 0.8)

    gen = llm_config.get("generation", {})
    max_tokens = gen.get("max_tokens", 8192)
    top_p = gen.get("top_p", 0.9)

    net = llm_config.get("network", {})
    timeout = net.get("timeout", 120)
    max_retries = net.get("max_retries", 3)
    base_delay = net.get("retry_base_delay", 2)

    client = OpenAI(api_key=api_key, base_url=api_base, timeout=timeout)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error = None
    for attempt in range(max_retries):
        current_model = model if attempt < max_retries - 1 else fallback_model
        try:
            print(
                f"[info] calling {current_model} (attempt {attempt + 1}/{max_retries}, "
                f"temp={temperature})",
                file=sys.stderr,
            )
            response = client.chat.completions.create(
                model=current_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                extra_body={"enable_thinking": True},
                stream=True,
            )
            thinking_parts = []
            content_parts = []
            for chunk in response:
                delta = chunk.choices[0].delta
                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    thinking_parts.append(delta.reasoning_content)
                if hasattr(delta, "content") and delta.content:
                    content_parts.append(delta.content)
            thinking_text = "".join(thinking_parts)
            if thinking_text:
                print(f"[info] thinking: {len(thinking_text)} chars", file=sys.stderr)
            text = "".join(content_parts).strip()
            if not text:
                print("[warn] empty response, retrying...", file=sys.stderr)
                last_error = RuntimeError("empty response")
                time.sleep(base_delay * (2 ** attempt))
                continue
            return text

        except Exception as e:
            last_error = e
            print(f"[warn] API error: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"[info] retrying in {delay}s...", file=sys.stderr)
                time.sleep(delay)

    print(f"[error] all {max_retries} attempts failed: {last_error}", file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------------
# Detect scene types from chapter outline
# ------------------------------------------------------------------
def detect_scene_types(outline: dict, context: dict) -> list:
    """Infer scene types from chapter outline and context."""
    types = set()
    scene = str(outline.get("scene", "")).lower()
    tags = [str(t).lower() for t in outline.get("tags", [])]
    objectives = " ".join(str(o).lower() for o in outline.get("objectives", []))
    all_text = scene + " " + " ".join(tags) + " " + objectives

    mapping = {
        "combat": ["战斗", "打斗", "交手", "对决", "battle", "combat", "fight"],
        "slice_of_life": ["日常", "生活", "闲聊", "slice"],
        "solo": ["独处", "修炼", "独白", "solo", "alone"],
        "emotional": ["情感", "离别", "重逢", "emotional", "感情"],
        "mystery": ["悬疑", "谜", "mystery", "秘密"],
        "power_up": ["突破", "进阶", "觉醒", "power"],
        "setback": ["挫折", "失败", "setback", "低谷"],
    }
    for stype, keywords in mapping.items():
        for kw in keywords:
            if kw in all_text:
                types.add(stype)
                break

    # Also check mood board
    mood = context.get("mood_board", {})
    mood_text = str(mood.get("core_image", "")).lower()
    for stype, keywords in mapping.items():
        for kw in keywords:
            if kw in mood_text:
                types.add(stype)
                break

    return list(types) if types else ["solo"]


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qwen API chapter writer for the novel framework"
    )
    parser.add_argument(
        "--chapter", type=int, required=True,
        help="Chapter number (e.g. 2)",
    )
    parser.add_argument(
        "--draft", choices=["skeleton", "sensory", "trimmed", "review", "elevate"], required=True,
        help="Draft type",
    )
    parser.add_argument(
        "--context-file", required=True,
        help="Path to writing-context.json (assembled by Claude)",
    )
    parser.add_argument(
        "--prev-draft", default="",
        help="Path to previous draft file (required for sensory/trimmed)",
    )
    parser.add_argument(
        "--model", default="",
        help="Override model name (e.g. qwen-max)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print prompts without calling API",
    )
    args = parser.parse_args()

    # Validate prev-draft requirement
    if args.draft in ("sensory", "trimmed", "review", "elevate") and not args.prev_draft:
        parser.error(f"--prev-draft is required for {args.draft} draft")

    # Setup
    project_root = find_project_root()
    load_dotenv(project_root)
    llm_config = load_llm_config(project_root)
    configs = load_writing_configs(project_root)

    # Load context
    ctx_path = Path(args.context_file)
    if not ctx_path.is_absolute():
        ctx_path = project_root / ctx_path
    context = load_context_json(ctx_path)

    # Load chapter outline
    outline = load_chapter_outline(project_root, args.chapter)

    # Load previous draft if needed
    prev_text = ""
    if args.prev_draft:
        prev_text = load_prev_draft(args.prev_draft, project_root)

    # Detect scene types
    scene_types = detect_scene_types(outline, context)
    print(
        f"[info] chapter={args.chapter} draft={args.draft} "
        f"scenes={scene_types}",
        file=sys.stderr,
    )

    # Build prompts
    if args.draft == "elevate":
        system_prompt = build_elevate_system_prompt(configs)
        user_prompt = build_elevate_user_prompt(prev_text, context)
    elif args.draft == "review":
        system_prompt = build_review_system_prompt(configs)
        user_prompt = build_review_user_prompt(prev_text, context)
    else:
        system_prompt = build_system_prompt(configs, args.draft, scene_types)
        user_prompt = build_user_prompt(context, args.draft, prev_text)

    if args.dry_run:
        print("=" * 60, file=sys.stderr)
        print("SYSTEM PROMPT", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(system_prompt, file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("USER PROMPT", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(user_prompt, file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys_tokens = len(system_prompt) // 2
        usr_tokens = len(user_prompt) // 2
        print(
            f"[info] estimated tokens: system~{sys_tokens} user~{usr_tokens} "
            f"total~{sys_tokens + usr_tokens}",
            file=sys.stderr,
        )
        return

    # Call API
    result = call_qwen_api(
        system_prompt, user_prompt, llm_config, args.draft, args.model
    )

    # Output to stdout (Claude captures this)
    print(result)

    char_count = len(result)
    print(f"[info] output: {char_count} chars", file=sys.stderr)


if __name__ == "__main__":
    main()
