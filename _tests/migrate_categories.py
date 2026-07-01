"""一次性迁移脚本：将实体从直接连根中心改为连接分类节点。

运行方式：cd backend && python scripts/migrate_categories.py
"""

import sys
sys.path.insert(0, '.')

from pathlib import Path
from app.core.config_loader import get_all_categories, get_category_center_map
import sqlite3
import json

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "graph.db"

# 根中心实际数据库名称 → 完整名称
CENTER_DB_NAMES = {"AXIS": "User|AXIS", "Midnight": "AI|Midnight", "AIChat": "Project|AIChat"}

# ============================================================
# 归类映射：实体名 → [分类节点名列表]
# 基于数据库实际数据 + 已有 rel_type 分析得出
# ============================================================

MIGRATION_MAP = {
    # ── AXIS·偏好习惯 ──
    "AI主动记忆写入":              ["AXIS·偏好习惯"],
    "先确认再动手原则":             ["AXIS·偏好习惯", "Midnight·行为规则"],
    "修正记忆原则":                ["AXIS·偏好习惯", "Midnight·行为规则"],
    "读文件限制原则":              ["AXIS·偏好习惯", "Midnight·行为规则"],
    "可折叠工具调用与代码块":       ["AXIS·偏好习惯"],
    "可视化文件diff视图":          ["AXIS·偏好习惯"],
    "图谱不使用emoji":             ["AXIS·偏好习惯", "Midnight·行为规则"],
    "对话不使用emoji":             ["AXIS·偏好习惯", "Midnight·行为规则"],
    "美观的知识图谱界面":          ["AXIS·偏好习惯"],

    # ── AXIS·计划目标 ──
    "文件操作工具集成方案":         ["AXIS·计划目标"],
    "设置功能计划":               ["AXIS·计划目标"],
    "设置功能计划v2":             ["AXIS·计划目标"],
    "多Provider设置功能v2":       ["AXIS·计划目标"],
    "AIChat设置功能":             ["AXIS·计划目标"],
    "缓存落盘优化":               ["AXIS·计划目标"],
    "网页访问工具":               ["AXIS·计划目标"],

    # ── AXIS·事实经历 ──
    "AI系统架构":                 ["AXIS·事实经历"],
    "AI记忆使用机制":             ["AXIS·事实经历"],
    "知识图谱记忆系统":            ["AXIS·事实经历"],
    "知识图谱可视化":              ["AXIS·事实经历"],
    "fetch_url工具":              ["AXIS·事实经历"],
    "未经确认直接改代码违规":       ["AXIS·事实经历", "Midnight·经验教训"],

    # ── Midnight·行为规则 (已在上面合并) ──

    # ── Midnight·经验教训 ──
    "犯错教训":                   ["Midnight·经验教训"],
    "编辑文件前未验证路径导致多轮空改": ["Midnight·经验教训"],

    # ── Midnight·能力边界 ──
    # (暂无)

    # ── AIChat·项目架构 ──
    "AIChat前端架构":              ["AIChat·项目架构"],
    "AIChat后端架构":              ["AIChat·项目架构"],
    "AIChat对话存储":              ["AIChat·项目架构"],
    "AIChat工具系统":              ["AIChat·项目架构"],
    "AIChat知识图谱存储":          ["AIChat·项目架构"],
    "AIChat缓存系统":              ["AIChat·项目架构"],

    # ── AIChat·项目真实结构 ──
    "GraphCanvas.vue":            ["AIChat·项目真实结构"],
    "ChatView.vue":               ["AIChat·项目真实结构"],
    "key_file_paths":             ["AIChat·项目真实结构"],
    "GraphCanvas.vue路径":        ["AIChat·项目真实结构"],
    "ChatView归档修复":            ["AIChat·项目真实结构"],
    "ChatView滚动修复":           ["AIChat·项目真实结构"],
    "图谱可视化参数调优":          ["AIChat·项目真实结构"],
    "main.py":                    ["AIChat·项目真实结构"],
    "ChatView.vue归档位置修复":    ["AIChat·项目真实结构"],
    "GraphCanvas.vue 正确路径":   ["AIChat·项目真实结构"],
    "图谱可视化参数调优记录":      ["AIChat·项目真实结构"],

    # ── AIChat·项目规范 ──
    "缓存key计算规则":             ["AIChat·项目规范"],
    "请求缓存key计算修复":         ["AIChat·项目规范"],
    "通用API配置命名":             ["AIChat·项目规范"],

    # ── 跨中心实体（同时属于AXIS和AIChat）──
    "AIChat设置功能":             ["AXIS·计划目标", "AIChat·项目架构"],
    "多Provider设置功能v2":       ["AXIS·计划目标", "AIChat·项目架构"],
    "设置功能计划":               ["AXIS·计划目标", "AIChat·项目架构"],
    "设置功能计划v2":             ["AXIS·计划目标", "AIChat·项目架构"],
    "缓存落盘优化":               ["AXIS·计划目标", "AIChat·项目架构"],
    "知识图谱可视化":              ["AXIS·事实经历", "AIChat·项目真实结构"],
}

CATEGORY_NAMES = set(get_category_center_map().keys())


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 获取所有实体名
    cur.execute("SELECT name, type FROM entities")
    all_entities = {row["name"]: row["type"] for row in cur.fetchall()}

    stats = {"deleted": 0, "created": 0, "skipped": 0, "errors": []}

    # 1. 找出所有根中心→实体的直接关系
    cur.execute("""
        SELECT r.id as rid, r.from_name, r.to_name, r.rel_type
        FROM relations r
        WHERE r.from_name IN ('User|AXIS','AI|Midnight','Project|AIChat')
    """)
    all_root_rels = cur.fetchall()

    print(f"Found {len(all_root_rels)} root->entity relations")

    for rel in all_root_rels:
        from_name = rel["from_name"]
        to_name = rel["to_name"]
        rid = rel["rid"]

        # 跳过根中心→分类节点
        if to_name in CATEGORY_NAMES:
            stats["skipped"] += 1
            continue

        # 跳过根中心→其他根中心（如 AXIS→Midnight, AXIS→AIChat）
        if to_name in CENTER_DB_NAMES:
            stats["skipped"] += 1
            continue

        if to_name not in all_entities:
            stats["skipped"] += 1
            continue

        # 查归类映射
        target_categories = MIGRATION_MAP.get(to_name, [])
        if not target_categories:
            stats["skipped"] += 1
            continue

        valid_categories = [c for c in target_categories if c in all_entities]
        if not valid_categories:
            continue

        # 删除旧关系
        cur.execute("DELETE FROM relations WHERE id = ?", (rid,))
        stats["deleted"] += 1

        # 创建分类→实体新关系
        for cat_name in valid_categories:
            cur.execute("""
                SELECT id FROM relations
                WHERE from_name = ? AND to_name = ? AND rel_type = ?
            """, (cat_name, to_name, "contains"))
            if cur.fetchone():
                continue  # already exists

            to_type = all_entities[to_name]
            cur.execute("""
                INSERT INTO relations (from_type, from_name, to_name, rel_type, to_type, properties)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("category", cat_name, to_name, "contains", to_type, "{}"))
            stats["created"] += 1

    conn.commit()

    # 2. 检查遗漏
    cur.execute(f"""
        SELECT r.from_name, r.to_name, r.rel_type
        FROM relations r
        WHERE r.from_name IN ('User|AXIS','AI|Midnight','Project|AIChat')
          AND r.to_name NOT IN ({','.join(repr(c) for c in CATEGORY_NAMES)})
          AND r.to_name NOT IN ('User|AXIS','AI|Midnight','Project|AIChat')
    """)
    remaining = cur.fetchall()
    if remaining:
        print(f"\nStill directly connected ({len(remaining)}):")
        for r in remaining:
            print(f"  {r['from_name']} --[{r['rel_type']}]--> {r['to_name']}")
    else:
        print("\nAll entities migrated from roots to categories")

    conn.close()

    print(f"\nDone: deleted {stats['deleted']}, created {stats['created']}, skipped {stats['skipped']}")
    if stats["errors"]:
        print(f"Errors: {stats['errors']}")


if __name__ == "__main__":
    migrate()
