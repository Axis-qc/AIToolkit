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
  content: string
  type: string
  about_entities: string[]
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
