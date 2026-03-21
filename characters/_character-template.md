---
# ============================================================
# 人物档案模板 - 复制此文件为 {角色名}.md 使用
# 新书启动 Stage 3 创建主要人物，后续按需创建新人物
#
# 字段标记:
#   [REQUIRED] -- 必填，check-schema.py 会校验非空
#   [OPTIONAL] -- 选填，留空不报错
# ============================================================

id: "char-XXX"                           # [REQUIRED] 唯一标识（如 char-001）
name: ""                                 # [REQUIRED] 角色名
aliases: []                              # [OPTIONAL] 别称/外号
role: ""                                 # [REQUIRED] protagonist/heroine/villain/antagonist/supporting/minor/neutral
status: "alive"                          # [REQUIRED] alive/dead/missing/unknown
first_appearance: 0                      # [REQUIRED] 首次登场章节号

# 基础属性（随剧情更新）
age: 0                                   # [OPTIONAL]
cultivation_level: ""                    # [OPTIONAL] 力量等级（根据世界观设定）
affiliation: ""                          # [OPTIONAL] 所属组织
location: ""                             # [OPTIONAL] 当前所在地

# 持有物/关键道具
inventory:                               # [OPTIONAL]
  # - item: "物品名"
  #   significance: "重要性说明"
  #   revealed: true                     # 是否已向读者揭露
  []

# 人物关系（用ID关联，方便交叉校验）
relationships:                           # [OPTIONAL]
  # - target: "char-002"
  #   name: "对方名字"
  #   type: "love_interest"              # 关系类型
  #   status: "当前状态描述"
  #   since_chapter: 1
  []

# 已知秘密（伏笔管理核心字段）
secrets:                                 # [OPTIONAL]
  # - content: "秘密内容"
  #   known_by: ["char-008"]             # 谁知道这个秘密
  #   reveal_plan: "arc-003"             # 计划在哪一卷揭露
  []

last_updated_chapter: 0                  # [OPTIONAL]
---

## 性格内核

（核心性格特征，2-3句话概括。写清楚这个角色的内在驱动力。）



## 说话风格

（不同状态下的语言特征，配 2-3 句示例台词）

- 日常状态：
- 战斗/紧张状态：
- 示例台词：
  - ""
  - ""
  - ""

## 行为模式

（遇到不同情况时的典型反应模式）

- 遇到危险时：
- 遇到利益时：
- 遇到感情时：

## 剧情摘要日志

| 章节范围 | 关键事件 | 状态变化 |
|---------|---------|---------|
