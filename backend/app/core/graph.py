"""
知识图谱 Facade —— 统一 re-export 所有子模块的公共 API。

外部调用方（memory.py、api/graph.py、mcp_server.py 等）继续用
`from . import graph` 或 `from .graph import xxx`，无需任何改动。

内部结构：
  core/db.py           → DB 连接、init_db、close、cleanup_expired
  core/graph_crud.py   → 实体/关系/事实 CRUD、软删除、恢复、合并、邻域查询
  core/graph_search.py → 搜索引擎、pinned 实体查询
  core/graph_view.py   → 前端可视化专用查询
"""

# DB 连接 & 生命周期
from .db import DB_PATH, init_db, close, cleanup_expired, _connect

# CRUD
from .graph_crud import (
    upsert_entity,
    update_entity,
    delete_entity,
    list_entity_keywords,
    list_entity_types_summary,
    list_entities_by_type,
    upsert_relation,
    delete_relation,
    create_fact,
    update_fact,
    get_fact_by_id,
    delete_fact,
    search_facts_by_keyword,
    index_conversation,
    mark_archived,
    delete_conversation,
    add_mention,
    bump_importance,
    set_pinned,
    set_importance,
    soft_delete_entity,
    soft_delete_fact,
    restore_entity,
    restore_fact,
    list_deprecated,
    merge_entities,
    get_entity_neighborhood,
)

# 搜索
from .graph_search import (
    search_entities,
    get_pinned_entities,
)

# 前端可视化
from .graph_view import (
    get_all_graph,
    get_roots,
    get_children,
    get_orphans,
    get_facts,
)