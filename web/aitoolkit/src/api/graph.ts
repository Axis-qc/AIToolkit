export interface GraphNode {
  id: string
  name: string
  type: string
  importance?: number
  pinned?: boolean
}

export interface GraphEdge {
  source: string
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
  facts: GraphFact[]
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
