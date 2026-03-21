#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check-vector.py -- 向量检索环境检测

检测向量检索依赖和索引状态，供 ENTRY.md Section 1.0 调用。

退出码:
  0  -- 依赖已安装且索引已建立（VECTOR_OK）
  1  -- 依赖已安装但索引不存在（INDEX_MISSING）
  2  -- 依赖未安装（DEPS_MISSING）

注意: 本脚本的退出码含义与项目中其他校验脚本（check-consistency.py、
check-schema.py 等）不同。其他校验脚本遵循 "1 = FAIL" 约定；本脚本
exit(1) 表示"依赖就绪但索引待建立"，是软性提示而非错误。
本脚本仅由 ENTRY.md Section 1.0 调用，不应挂载到自动化 hook 中。
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 依赖检查
# ---------------------------------------------------------------------------

missing = []

try:
    import lancedb  # noqa: F401
except ImportError:
    missing.append("lancedb")

try:
    import pyarrow  # noqa: F401
except ImportError:
    missing.append("pyarrow")

try:
    from sentence_transformers import SentenceTransformer  # noqa: F401
except ImportError:
    missing.append("sentence-transformers")

if missing:
    print(f"DEPS_MISSING: {', '.join(missing)}")
    sys.exit(2)

# ---------------------------------------------------------------------------
# 索引检查
# ---------------------------------------------------------------------------

root = Path(__file__).parent.parent
db_dir = root / ".vector-db"

# 索引存在且非空才算就绪（lancedb 在 .vector-db/ 下创建子目录）
try:
    index_ready = db_dir.exists() and any(db_dir.iterdir())
except OSError:
    # 目录被其他进程锁定（Windows 常见），保守判断为未就绪
    index_ready = False

if not index_ready:
    print("INDEX_MISSING")
    sys.exit(1)

print("VECTOR_OK")
sys.exit(0)
