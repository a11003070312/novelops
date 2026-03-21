# NovelOps

把 CI/CD 理念搬进中文网文创作。NovelOps 提供完整的章节生产流水线、自动化禁用词扫描、跨章一致性校验和向量语义检索，让 Claude Code 成为真正可靠的连载写作引擎。

---

## 这是什么

NovelOps 是一个用 Claude Code 写中文长篇网文的生产框架。

它解决的核心问题：**用 AI 写长篇小说，最大的敌人不是文笔，而是一致性崩塌**。第50章的角色状态和第10章矛盾了，伏笔埋了30章忘了回收，节奏连续15章铺垫没有爽点——这些问题在人工创作中靠作者记忆兜底，在 AI 辅助创作中会被上下文窗口直接抹掉。

NovelOps 的做法：**工程化**。把每章的创作流程拆成6个强制阶段，用5个自动化脚本做质量门控，用结构化的 YAML 状态文件替代 AI 的"记忆"。

---

## 核心特性

### 八阶段章节流水线

每章必须按顺序跑完所有阶段，不得跳过：

| 阶段 | 名称 | 核心动作 |
|------|------|--------|
| Phase 1 | 上下文组装 + 写前检查 | 加载角色/场景/伏笔状态，双通道事实检索，覆盖矩阵验证 |
| Phase 1.5 | 场景设计 + 战斗工作坊 | 纯中文子代理生成情绪板；有重要战斗时启动五步设计法 |
| Phase 2 | 章节写作（两稿制） | 极简创作指令 → Opus 子代理初稿 → 主会话事实核验 → 定稿 |
| Phase 3 | 写后自检 | scan-text.py 扫描 + 人工朗读通顺性检查 |
| Phase 4 | 人类审核 | 等待确认，可要求修改或重写 |
| Phase 4.5 | 标题定稿 | 提出候选标题，人类选定后写入章节文件和大纲 |
| Phase 5 | 状态更新 | 更新所有状态文件，提取事实，重建向量索引 |
| Phase 6 | 收尾 | 生成下章大纲，更新会话状态 |

### 五个自动化验证脚本

```bash
# 正文禁用词/句式/结构扫描（Phase 3 必跑）
python scripts/scan-text.py chapters/arc-001/chapter-0001.md

# 跨文件一致性校验：角色引用、死亡角色、伏笔健康、关系对称（Phase 1 和 Phase 5 必跑）
python scripts/check-consistency.py

# YAML 结构完整性校验（任何 YAML 文件修改后必跑）
python scripts/check-schema.py

# 关键词语义检索历史事实（Phase 1 写前检查）
python scripts/search-facts.py "关键词1" "关键词2"

# 向量语义检索（LanceDB + bge-base-zh-v1.5，Phase 1 写前检查）
python scripts/vector-search.py "查询语句"
python scripts/vector-search.py --rebuild  # 重建索引（Phase 5 结束时）
```

### 两稿制写作架构（极简提示词哲学）

经过实测验证的核心结论：**创作指令越短，文笔越好**。

创作指令只包含三条固定写法要求：
1. 像诛仙那样写——句子连贯地流，一段话只做一件事
2. "不是X，是Y"句式全章最多用两次
3. 给读者留呼吸空间——允许角色发呆、沉默、什么都不想地走一段路

所有禁忌检查（禁用词、翻译腔、段落密度）全部交给 Phase 3 的 `scan-text.py` 事后执行，不放进创作指令。

### 自主段落模式

除了逐章的监督模式，NovelOps 还支持自主批量生产：

- 先规划一个"段落"（3-7章的叙事单元）
- Claude 自动按顺序完成每章的 Phase 1-6
- 段落末章触发人类审核（Section 4.6）
- 内置冷读测试（cold-reader 子代理）和 OOC 检查（ooc-checker 子代理）
- 自适应中断机制：reader-pull ≤ 2 / 事实冲突 FAIL / 重要战斗时暂停等待人工决策

---

## 快速开始

### 环境要求

- Claude Code（claude.ai/code）
- Python 3.8+

### 安装

```bash
# 克隆模板
git clone https://github.com/a11003070312/novelops.git my-novel
cd my-novel

# 安装脚本依赖
pip install -r scripts/requirements.txt

# 安装向量检索依赖（可选，推荐在超过30章后启用）
pip install -r scripts/requirements-vector.txt
# 首次运行 vector-search.py 时会自动下载 bge-base-zh-v1.5 模型（约400MB），之后自动缓存
# 如不需要向量检索，跳过此步骤，仅使用 search-facts.py 的关键词检索即可
```

### 每次对话的启动方式（重要）

**每次开启新对话时，必须先 @ 入口文件：**

```
@ENTRY.md
```

ENTRY.md 是 Claude 的完整操作手册，包含所有 Phase 流程定义、铁律和报告格式。不 @ 这个文件，Claude 将无法执行正确的写作流程。CLAUDE.md 只是摘要，不能替代 ENTRY.md。

### 启动新书

在 Claude Code 中打开项目目录，发送 `@ENTRY.md`。Claude 会检测到 `state/session-state.yaml` 不存在，自动进入新书引导流程（ENTRY.md 附录B）。

引导流程分四个阶段，每阶段人类确认后进入下一阶段：

1. **Stage 1 - 核心设定**：填写 `config/` 下的配置文件
2. **Stage 2 - 大纲规划**：完成 `outline/master-outline.md` 和第一卷大纲
3. **Stage 3 - 人物创建**：创建主要角色档案
4. **Stage 4 - 框架初始化**：生成第一章大纲，校验，准备开写

### 继续已有项目

发送 `@ENTRY.md`，Claude 会读取 `state/session-state.yaml` 恢复上次进度，报告当前状态，等待你确认后继续。

---

## 目录结构

```
novelops/
├── ENTRY.md                        # 核心操作手册（Claude 每次必读）
├── CLAUDE.md                       # Claude Code 项目指令
│
├── config/                         # 定义"这本书是什么"
│   ├── project.yaml                # 项目元信息（书名、题材、目标）
│   ├── writing-style.yaml          # 写作技法（视角、句式、语言特征）
│   ├── novel-identity.yaml         # 小说灵魂（调性、场景风格、节奏模板）
│   ├── anti-ai-patterns.yaml       # 去AI感规则（禁用词、禁用句式）
│   ├── writing-rules.yaml          # 番茄平台节奏规则
│   ├── world-settings.yaml         # 世界观设定
│   ├── battle-design.yaml          # 战斗设计工作坊（五步设计法）
│   ├── prose-reference.yaml        # 两稿制流程 + 文笔锚点
│   ├── style-samples.yaml          # 文笔参考样本
│   └── glossary.yaml               # 术语表（防错别字）
│
├── outline/                        # 故事骨架
│   ├── master-outline.md           # 主线大纲
│   ├── arcs/                       # 卷大纲（arc-001.yaml...）
│   ├── chapters/                   # 章节大纲（chapter-0001.yaml...）
│   └── segments/                   # 段落大纲（自主模式调度单位）
│
├── characters/                     # 角色档案（*.md）
├── locations/                      # 场景档案（*.yaml）
│
├── state/                          # 实时状态记忆
│   ├── session-state.yaml          # 会话衔接
│   ├── plot-threads.yaml           # 伏笔生命周期
│   ├── emotion-threads.yaml        # 情感线积累
│   ├── relationships.yaml          # 人物关系网
│   ├── timeline.yaml               # 故事时间线
│   ├── world-state.yaml            # 世界势力格局
│   ├── milestones.yaml             # 修为/能力里程碑
│   ├── pacing-tracker.yaml         # 爽点节奏追踪
│   ├── character-appearances.yaml  # 配角出场频率
│   ├── plot-pattern-tracker.yaml   # 情节模式重复检测
│   ├── facts/                      # 每章可校验事实
│   ├── summaries/                  # 章节/卷/全局摘要
│   └── phase-reports/              # 各 Phase 详细报告
│
├── chapters/                       # 已发布章节正文
├── logs/                           # 变更日志
└── scripts/                        # 自动化验证脚本
```

---

## 设计哲学

### 状态外化，不靠记忆

AI 没有跨会话记忆。NovelOps 把所有"需要记住的事"写进结构化文件：角色档案记录当前状态，facts/ 记录每章的可校验事实，timeline 记录时间线……每次写作前强制加载，每次写作后强制更新。

### 检查后置，不压制创作

创作指令不放规则，规则交给 Phase 3 检查。这是经过实测的设计：190行约束的提示词产出"像在完成任务"的文字，3行要求的提示词产出"像在讲故事"的文字。

### 门控强制，不能跳过

scan-text.py 的输出必须原文贴入报告，不能只声明"已检查"。check-consistency.py 的 FAIL 必须修复后重跑，不能忽略。这些是硬性要求，不是建议。

### 两套模式，按需选择

- **监督模式**：每章人工确认，适合精雕细琢
- **自主段落模式**：3-7章批量生产，段落结束后人工审核，适合高速更新

---

## 关键配置文件说明

### `config/anti-ai-patterns.yaml`

机器可解析格式，直接供 scan-text.py 消费。包含8类规则：

- **banned_words**：40+ AI高频词，按 severity 分级（critical/high/medium）
- **banned_patterns**：19条正则，检测AI典型句式结构
- **translation_tone_patterns**：翻译腔检测（被动语态、的字链、当字句等）
- **sensory_rules**：环境描写密度检测
- **structural_rules**：段落变化、开头/结尾/过渡禁忌
- ...

所有规则开箱即用，可根据项目需要追加条目。

### `config/prose-reference.yaml`

定义写作哲学和两稿制流程。核心三原则：
- **自然**：读出声来不拗口
- **克制**：不替读者做情绪判断
- **中文原生**：不翻译腔

### `config/battle-design.yaml`

五步战斗设计法：
1. 能力清单（双方完整能力矩阵和信息差）
2. 方案发散（至少5种胜负方案，含反直觉方案）
3. 淘汰显而易见
4. 人类选方向
5. 信息揭示设计（错误预期→转折→真相→回扣前文）

---

## 模型路由

| 场景 | 模型 | 原因 |
|------|------|------|
| 主会话（所有 Phase） | Claude Sonnet | 速度与质量的平衡 |
| Phase 2.1 写作子代理 | Claude Opus（固定） | 中文文学创作质量，每章都用，不降级 |
| Phase 1.5 情绪板子代理 | Claude Sonnet | 篇幅短，Sonnet 足够 |
| 战斗设计工作坊（可选） | Claude Opus | 方案发散质量，触发时提醒人类确认 |

---

## 常见问题

**Q: 可以用于非修仙题材吗？**

可以。框架是题材无关的。`config/world-settings.yaml`、`config/glossary.yaml` 等文件根据你的题材填写。`config/anti-ai-patterns.yaml` 和 `config/prose-reference.yaml` 是通用的，开箱即用。

**Q: 必须用番茄小说平台吗？**

不必须。`config/writing-rules.yaml` 里的平台规则（字数范围、更新频率等）可以根据你的目标平台调整。框架本身是平台无关的。

**Q: 向量检索（vector-search.py）是必须的吗？**

不是强制的，但强烈建议在超过30章后使用。前30章用 `search-facts.py` 的关键词检索已经足够。后期事实积累多了，向量语义检索能找到关键词检索漏掉的隐式关联。

首次使用需要下载模型（bge-base-zh-v1.5，约400MB），之后自动缓存。

---

## License

MIT License — 自由使用、修改、分发。

---

