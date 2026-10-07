"""
知识图谱 Facade —— 统一 re-export 所有子模块的公共 API。

外部调用方（memory.py、api/graph.py、mcp_server.py 等）继续用
`from . import graph` 或 `from .graph import xxx`，无需任何改动。

内部结构：
  core/db.py             → DB 连接、init_db、close、cleanup_expired
  core/graph_crud.py     → 实体/关系/事实 CRUD、软删除、恢复、合并、邻域查询、关系索引维护、pinned 实体查询
  core/embedding.py      → 本地嵌入模型（向量化），模型缓存限定在工作区内
  core/semantic_search.py → 纯向量语义检索（原始文本 → 全库余弦）
  core/recall.py         → 意向检索（选择式选取 + 向量臂降级）
  core/weak_link.py      → 弱关联候选生成（干跑，用向量补「相关」类弱关系）
  core/graph_view.py     → 前端可视化专用查询（roots/children/facts/orphans/full_graph）
"""

# DB 连接 & 生命周期
from .db import (
    DB_PATH,
    init_db,
    close,
    cleanup_expired,
    list_tombstones,
    now_ts,
    parse_ts,
    retention_hours,
    _connect,
)

# CRUD
from .graph_crud import (
    upsert_entity,
    update_entity,
    delete_entity,
    list_entity_keywords,
    list_entity_types_summary,
    list_entities_by_type,
    get_entities_by_names,
    list_entities_paged,
    set_stale_mark,
    batch_update_entities,
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
    set_root,
    set_importance,
    soft_delete_entity,
    soft_delete_fact,
    restore_entity,
    restore_fact,
    list_deprecated,
    merge_entities,
    preview_merge,
    get_entity_neighborhood,
    get_entity_detail,
    get_pinned_entities,
    refresh_relation_index,
    rebuild_relation_index,
    migrate_from_old_schema,
)

# 语义检索（纯向量）
from .semantic_search import (
    semantic_search,
)

# 意向检索（选择式选取 + 向量臂降级）
from .recall import (
    recall,
    backfill,
    refresh_embeddings,
)

# 体检
from .graph_health import (
    health_check,
    find_duplicate_candidates,
    find_stale_entries,
    find_type_fragments,
    find_dangling_relations,
    run_lint,
)

# 弱关联候选（干跑，只读）
from .weak_link import (
    weak_links,
    build_report as weak_link_report,
    write_report as write_weak_link_report,
)

# 前端可视化查询
from .graph_view import (
    get_roots,
    get_children,
    get_facts,
    get_orphans,
    get_full_graph,
)

