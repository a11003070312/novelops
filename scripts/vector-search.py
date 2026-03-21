#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vector-search.py -- 小说工程语义向量检索工具

基于 LanceDB + sentence-transformers (BAAI/bge-base-zh-v1.5) 对项目中的
YAML/Markdown 文件进行语义索引和检索，作为 search-facts.py 关键词检索的
语义补充通道。

用法:
  python scripts/vector-search.py "灵脉裂缝"
  python scripts/vector-search.py "陆沉的修炼方法" --top 10
  python scripts/vector-search.py --rebuild              # 强制重建索引
  python scripts/vector-search.py --status               # 查看索引状态

依赖:
  pip install lancedb sentence-transformers pyarrow pyyaml

首次运行会自动下载 bge-base-zh-v1.5 模型（约400MB），后续使用本地缓存。
索引文件存储在 .vector-db/ 目录下。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Windows UTF-8 stdout
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# HuggingFace 国内镜像（自动设置，无需手动 export）
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ---------------------------------------------------------------------------
# 依赖检查
# ---------------------------------------------------------------------------

MISSING_DEPS: List[str] = []

try:
    import yaml
except ImportError:
    MISSING_DEPS.append("pyyaml")

try:
    import lancedb
except ImportError:
    MISSING_DEPS.append("lancedb")

try:
    import pyarrow as pa
except ImportError:
    MISSING_DEPS.append("pyarrow")

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    MISSING_DEPS.append("sentence-transformers")

if MISSING_DEPS:
    print(
        "[错误] 缺少以下依赖:\n"
        f"  {', '.join(MISSING_DEPS)}\n\n"
        "请执行以下命令安装:\n"
        f"  pip install {' '.join(MISSING_DEPS)}\n"
        "或使用国内镜像:\n"
        f"  pip install {' '.join(MISSING_DEPS)} -i https://pypi.tuna.tsinghua.edu.cn/simple",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SEPARATOR_THICK = "=" * 60
SEPARATOR_THIN = "-" * 60
EXIT_CLEAN = 0
EXIT_ERROR = 2

MODEL_NAME = "BAAI/bge-base-zh-v1.5"
DB_DIR_NAME = ".vector-db"
TABLE_NAME = "novel_chunks"
CHUNK_SIZE = 300       # 每个文本块的目标字数
CHUNK_OVERLAP = 50     # 块之间的重叠字数
TOP_K_DEFAULT = 5

# 索引范围：相对于项目根目录的路径
INDEX_DIRS = [
    ("state/facts", "*.yaml"),
    ("state/summaries/chapters", "*.yaml"),
    ("state/summaries/arcs", "*.md"),
    ("characters", "*.md"),
    ("outline/chapters", "*.yaml"),
    ("config", "*.yaml"),
    ("chapters", "*.md"),
]


# ---------------------------------------------------------------------------
# 项目根目录定位
# ---------------------------------------------------------------------------

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
        "[错误] 无法定位项目根目录 (未找到 ENTRY.md)",
        file=sys.stderr,
    )
    sys.exit(EXIT_ERROR)


# ---------------------------------------------------------------------------
# 文件收集与解析
# ---------------------------------------------------------------------------

def collect_files(root: Path) -> List[Path]:
    """收集所有需要索引的文件。"""
    files: List[Path] = []
    for dir_rel, pattern in INDEX_DIRS:
        dir_path = root / dir_rel
        if not dir_path.is_dir():
            continue
        for fp in sorted(dir_path.rglob(pattern)):
            if fp.name.startswith("_"):
                continue
            files.append(fp)
    return files


def read_file_text(filepath: Path) -> str:
    """读取文件全文。"""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            return fh.read()
    except (UnicodeDecodeError, OSError):
        return ""


def extract_yaml_text(text: str) -> str:
    """从 YAML 文件中提取有意义的文本内容。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return text

    if data is None:
        return ""
    return _flatten_yaml_values(data)


def _flatten_yaml_values(obj: object, depth: int = 0) -> str:
    """递归提取 YAML 值中的所有文本。"""
    if depth > 10:
        return ""
    parts: List[str] = []
    if isinstance(obj, str):
        parts.append(obj)
    elif isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(key, str):
                parts.append(key)
            parts.append(_flatten_yaml_values(val, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            parts.append(_flatten_yaml_values(item, depth + 1))
    return " ".join(p for p in parts if p)


def extract_md_text(text: str) -> str:
    """从 Markdown 文件提取正文（跳过 frontmatter）。"""
    # 移除 YAML frontmatter
    if text.startswith("---"):
        match = re.match(r"^---\s*\n.*?\n---\s*\n?", text, re.DOTALL)
        if match:
            text = text[match.end():]
    # 移除 Markdown 标题标记但保留文字
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return text


def chunk_text(
    text: str,
    source: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[Dict[str, str]]:
    """将文本分块。每块包含 source 来源信息。"""
    if not text.strip():
        return []

    # 按段落分割
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: List[Dict[str, str]] = []
    current_chunk: List[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)

        if current_len + para_len > chunk_size and current_chunk:
            chunk_text_val = "\n\n".join(current_chunk)
            chunks.append({
                "text": chunk_text_val,
                "source": source,
            })
            # 重叠：保留最后一段
            if overlap > 0 and current_chunk:
                last = current_chunk[-1]
                current_chunk = [last]
                current_len = len(last)
            else:
                current_chunk = []
                current_len = 0

        current_chunk.append(para)
        current_len += para_len

    # 最后一块
    if current_chunk:
        chunk_text_val = "\n\n".join(current_chunk)
        chunks.append({
            "text": chunk_text_val,
            "source": source,
        })

    return chunks


def process_file(filepath: Path, root: Path) -> List[Dict[str, str]]:
    """处理单个文件，返回文本块列表。"""
    text = read_file_text(filepath)
    if not text:
        return []

    rel_path = str(filepath.relative_to(root)).replace("\\", "/")

    if filepath.suffix == ".yaml":
        content = extract_yaml_text(text)
    elif filepath.suffix == ".md":
        content = extract_md_text(text)
    else:
        content = text

    return chunk_text(content, rel_path)


# ---------------------------------------------------------------------------
# 文件指纹：增量索引用
# ---------------------------------------------------------------------------

def compute_fingerprint(files: List[Path]) -> str:
    """计算所有文件的联合指纹（用于判断是否需要重建索引）。"""
    hasher = hashlib.sha256()
    for fp in sorted(files):
        try:
            stat = fp.stat()
            hasher.update(f"{fp}:{stat.st_mtime}:{stat.st_size}".encode())
        except OSError:
            pass
    return hasher.hexdigest()[:16]


def read_saved_fingerprint(db_dir: Path) -> str:
    """读取上次索引时保存的指纹。"""
    fp_file = db_dir / "fingerprint.txt"
    if fp_file.exists():
        return fp_file.read_text(encoding="utf-8").strip()
    return ""


def save_fingerprint(db_dir: Path, fingerprint: str) -> None:
    """保存当前指纹。"""
    fp_file = db_dir / "fingerprint.txt"
    fp_file.write_text(fingerprint, encoding="utf-8")


# ---------------------------------------------------------------------------
# 向量数据库操作
# ---------------------------------------------------------------------------

def get_model() -> SentenceTransformer:
    """加载 embedding 模型。"""
    print(f"  加载模型: {MODEL_NAME} ...", file=sys.stderr)
    model = SentenceTransformer(MODEL_NAME)
    return model


def build_index(
    root: Path,
    db_dir: Path,
    model: SentenceTransformer,
    files: List[Path],
) -> int:
    """构建或重建向量索引。返回索引的文本块数量。"""
    print(f"  收集文件: {len(files)} 个", file=sys.stderr)

    all_chunks: List[Dict[str, str]] = []
    for fp in files:
        chunks = process_file(fp, root)
        all_chunks.extend(chunks)

    if not all_chunks:
        print("  [警告] 无有效文本块可索引", file=sys.stderr)
        return 0

    print(f"  生成向量: {len(all_chunks)} 个文本块 ...", file=sys.stderr)
    texts = [c["text"] for c in all_chunks]
    vectors = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

    # 构建 PyArrow table
    data = []
    for i, chunk in enumerate(all_chunks):
        data.append({
            "id": i,
            "text": chunk["text"],
            "source": chunk["source"],
            "vector": vectors[i].tolist(),
        })

    db = lancedb.connect(str(db_dir / "lance.db"))

    # 删除旧表（如果存在）
    try:
        db.drop_table(TABLE_NAME)
    except Exception:
        pass

    table = db.create_table(TABLE_NAME, data)

    # 保存指纹
    fingerprint = compute_fingerprint(files)
    save_fingerprint(db_dir, fingerprint)

    print(f"  索引完成: {len(all_chunks)} 个文本块", file=sys.stderr)
    return len(all_chunks)


def search_index(
    db_dir: Path,
    model: SentenceTransformer,
    query: str,
    top_k: int = TOP_K_DEFAULT,
) -> List[Dict]:
    """在索引中搜索，返回匹配结果。"""
    db_path = db_dir / "lance.db"
    if not db_path.exists():
        print("[错误] 索引不存在，请先运行 --rebuild", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    db = lancedb.connect(str(db_path))
    try:
        table = db.open_table(TABLE_NAME)
    except Exception:
        print("[错误] 索引表不存在，请先运行 --rebuild", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    # BGE 模型的查询需要加前缀以获得最佳效果
    query_text = f"为这个句子生成表示以用于检索相关段落：{query}"
    query_vec = model.encode([query_text], normalize_embeddings=True)[0]

    results = (
        table.search(query_vec.tolist())
        .metric("cosine")
        .limit(top_k)
        .to_list()
    )

    return results


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def format_results(query: str, results: List[Dict]) -> str:
    """格式化搜索结果。"""
    parts: List[str] = []
    parts.append(SEPARATOR_THICK)
    parts.append(f"  语义检索: \"{query}\"")
    parts.append(SEPARATOR_THICK)

    if not results:
        parts.append("  (无匹配结果)")
        parts.append(SEPARATOR_THICK)
        return "\n".join(parts)

    for i, r in enumerate(results, 1):
        score = 1.0 - r.get("_distance", 0.0)  # cosine distance -> similarity
        source = r.get("source", "unknown")
        text = r.get("text", "")

        # 截断长文本
        display_text = text[:200] + "..." if len(text) > 200 else text
        display_text = display_text.replace("\n", " ")

        parts.append(f"  [{i}] 相关度: {score:.3f} | 来源: {source}")
        parts.append(f"      {display_text}")
        parts.append("")

    parts.append(SEPARATOR_THIN)
    parts.append(f"  共 {len(results)} 条结果")
    parts.append(SEPARATOR_THICK)
    return "\n".join(parts)


def format_status(db_dir: Path, root: Path) -> str:
    """输出索引状态信息。"""
    parts: List[str] = []
    parts.append(SEPARATOR_THICK)
    parts.append("  向量索引状态")
    parts.append(SEPARATOR_THICK)

    db_path = db_dir / "lance.db"
    if not db_path.exists():
        parts.append("  状态: 未构建")
        parts.append("  运行 --rebuild 创建索引")
        parts.append(SEPARATOR_THICK)
        return "\n".join(parts)

    # 检查文件指纹
    files = collect_files(root)
    current_fp = compute_fingerprint(files)
    saved_fp = read_saved_fingerprint(db_dir)

    parts.append(f"  索引位置: {db_dir}")
    parts.append(f"  索引文件数: {len(files)}")

    if current_fp == saved_fp:
        parts.append("  状态: 最新（文件无变化）")
    else:
        parts.append("  状态: 过期（文件已变化，建议 --rebuild）")

    # 尝试获取记录数
    try:
        db = lancedb.connect(str(db_path))
        table = db.open_table(TABLE_NAME)
        count = table.count_rows()
        parts.append(f"  索引块数: {count}")
    except Exception:
        parts.append("  索引块数: (无法读取)")

    parts.append(SEPARATOR_THICK)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="小说工程语义向量检索工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            '  python scripts/vector-search.py "灵脉裂缝"\n'
            '  python scripts/vector-search.py "陆沉修炼" --top 10\n'
            "  python scripts/vector-search.py --rebuild\n"
            "  python scripts/vector-search.py --status"
        ),
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="搜索查询文本",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=TOP_K_DEFAULT,
        help=f"返回结果数量 (默认 {TOP_K_DEFAULT})",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="强制重建索引",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="查看索引状态",
    )
    args = parser.parse_args()

    root = find_project_root()
    db_dir = root / DB_DIR_NAME
    db_dir.mkdir(exist_ok=True)

    # 状态查询
    if args.status:
        print(format_status(db_dir, root))
        return EXIT_CLEAN

    # 重建索引
    if args.rebuild:
        files = collect_files(root)
        if not files:
            print("[警告] 未找到可索引的文件", file=sys.stderr)
            return EXIT_ERROR
        model = get_model()
        count = build_index(root, db_dir, model, files)
        print(f"\n索引重建完成: {count} 个文本块")
        return EXIT_CLEAN

    # 搜索
    if not args.query:
        parser.print_help()
        return EXIT_ERROR

    # 自动构建索引（如果不存在或过期）
    files = collect_files(root)
    current_fp = compute_fingerprint(files)
    saved_fp = read_saved_fingerprint(db_dir)
    db_path = db_dir / "lance.db"

    model = get_model()

    if not db_path.exists() or current_fp != saved_fp:
        print("  索引不存在或已过期，自动重建 ...", file=sys.stderr)
        build_index(root, db_dir, model, files)

    results = search_index(db_dir, model, args.query, args.top)
    print(format_results(args.query, results))
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
