/**
 * 图谱视图共享主题：保证力导向图、树形视图和详情面板使用同一套分类语义。
 */
const TYPE_COLORS: Record<string, string> = {
  User: '#f59e0b',
  preference: '#a78bfa',
  fact: '#34d399',
  event: '#60a5fa',
  plan: '#f87171',
  topic: '#fb923c',
  todo: '#fbbf24',
  conflict: '#ef4444',
  pending: '#14b8a6',
  habit: '#06b6d4',
  interest: '#ec4899',
  project: '#6366f1',
  skill: '#22c55e',
  AI: '#8b5cf6',
  category: '#818cf8',
  rule: '#f97316',
  incident: '#fb7185',
  component: '#84cc16',
}

// 未知类型也要保持可区分，不能统一退回灰色。
const FALLBACK_COLORS = [
  '#22d3ee', '#fb7185', '#a3e635', '#f97316', '#c084fc', '#2dd4bf',
  '#facc15', '#38bdf8', '#e879f9', '#4ade80', '#fb923c', '#818cf8',
]

const TYPE_LABELS: Record<string, string> = {
  User: '用户',
  AI: 'AI',
  preference: '偏好',
  fact: '事实',
  event: '事件',
  plan: '计划',
  topic: '主题',
  todo: '待办',
  conflict: '冲突',
  pending: '待跟进',
  habit: '习惯',
  interest: '兴趣',
  project: '项目',
  skill: '技能',
  category: '分类',
  rule: '规则',
  incident: '事件',
  component: '组件',
}

function hashType(type: string): number {
  let hash = 0
  for (const char of type) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return hash % FALLBACK_COLORS.length
}

export function nodeColor(type: string): string {
  const normalized = type.trim()
  return TYPE_COLORS[normalized] || FALLBACK_COLORS[hashType(normalized)] || '#22d3ee'
}

export function typeLabel(type: string): string {
  return TYPE_LABELS[type] || type
}

const HIERARCHY_RELATIONS = new Set([
  'parent', 'parent_of', 'has_parent', 'child', 'child_of', 'has_child',
  'contains', 'contain', 'includes', 'include', 'belongs_to', 'part_of',
  'subcategory', 'subclass', '上位', '上位概念', '下位', '下位概念',
  '父节点', '子节点', '包含', '归属', '属于', '隶属于', '层级', '分类',
])

/** 判断关系是否表达父子/包含层级。其他关系仍会显示方向箭头。 */
export function isHierarchyRelation(relType: string): boolean {
  const normalized = relType.trim().toLowerCase()
  return HIERARCHY_RELATIONS.has(normalized)
    || /(parent|child|contain|include|belongs|part[ _-]?of|subclass|subcategory|父|子|包含|归属|属于|隶属|上位|下位|层级)/i.test(normalized)
}
