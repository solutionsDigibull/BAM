export interface FileNode { path: string; name: string; folder: boolean; children: FileNode[] }
export interface FileTreeRow { node: FileNode; depth: number; parent?: string }
export function buildFileTree(paths: string[]): FileNode[] {
  const root: FileNode[] = [], nodes = new Map<string, FileNode>()
  for (const path of new Set(paths)) {
    const parts = path.split('/').filter(Boolean)
    let children = root, parent = ''
    parts.forEach((name, index) => {
      const key = parent ? `${parent}/${name}` : name
      let node = nodes.get(key)
      if (!node) { node = { path: key, name, folder: index < parts.length - 1, children: [] }; nodes.set(key, node); children.push(node) }
      children = node.children; parent = key
    })
  }
  const sort = (nodes: FileNode[]) => { nodes.sort((a, b) => Number(b.folder) - Number(a.folder) || a.name.localeCompare(b.name, undefined, { numeric: true })); nodes.forEach(n => sort(n.children)) }
  sort(root)
  return root
}
export function visibleFileRows(nodes: FileNode[], collapsed: Set<string>, depth = 0, parent?: string): FileTreeRow[] {
  return nodes.flatMap(node => [{ node, depth, parent }, ...(node.folder && !collapsed.has(node.path) ? visibleFileRows(node.children, collapsed, depth + 1, node.path) : [])])
}
