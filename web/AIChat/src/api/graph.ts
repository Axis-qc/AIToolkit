export interface GraphNode {
  id: string
  name: string
  type: string
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

export async function fetchGraph(): Promise<GraphData> {
  const res = await fetch('/api/graph')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchRoots(): Promise<{ nodes: GraphNode[], edges: GraphEdge[] }> {
  const res = await fetch('/api/graph/roots')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchChildren(type: string, name: string): Promise<{ nodes: GraphNode[], edges: GraphEdge[], has_children: Record<string, boolean> }> {
  const res = await fetch(`/api/graph/children?type=${encodeURIComponent(type)}&name=${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchFacts(type: string, name: string): Promise<{ facts: GraphFact[] }> {
  const res = await fetch(`/api/graph/facts?type=${encodeURIComponent(type)}&name=${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchOrphans(): Promise<{ nodes: GraphNode[] }> {
  const res = await fetch('/api/graph/orphans')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
