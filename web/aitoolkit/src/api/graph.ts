export interface GraphNode {
  id: string
  name: string
  type: string
  importance?: number
  pinned?: boolean
  /** 是否为根实体（is_root=1），关系网用于绘制层级顶端标记。 */
  is_root?: boolean
}

export interface GraphEdge {
  /** 子节点 id（关系方向：子 → 父） */
  source: string
  /** 父节点 id */
  target: string
  rel_type: string
}

export interface GraphFact {
  id?: number
  content: string
  type: string
  about_entities: string[]
  ts?: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** 全量接口不再返回 facts，事实详情按实体查询。 */
  facts?: GraphFact[]
}

export async function fetchFullGraph(): Promise<GraphData> {
  const res = await fetch('/api/graph', { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchRoots(): Promise<{ nodes: GraphNode[]; edges?: GraphEdge[] }> {
  const res = await fetch('/api/graph/roots', { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchChildren(type: string, name: string): Promise<{ nodes: GraphNode[]; edges: GraphEdge[]; has_children: Record<string, boolean> }> {
  const res = await fetch(`/api/graph/children?type=${encodeURIComponent(type)}&name=${encodeURIComponent(name)}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchFacts(type: string, name: string): Promise<{ facts: GraphFact[]; entity?: { content: string; type: string; name: string } }> {
  const res = await fetch(`/api/graph/facts?type=${encodeURIComponent(type)}&name=${encodeURIComponent(name)}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchOrphans(): Promise<{ nodes: GraphNode[] }> {
  const res = await fetch('/api/graph/orphans', { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
