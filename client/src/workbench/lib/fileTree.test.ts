import { assert, test } from 'vitest'
import { buildFileTree, visibleFileRows } from './fileTree'
test('file tree sorts folders first, preserves paths and deduplicates', () => {
  const tree = buildFileTree(['z.py', 'skills/b.py', 'skills/a.py', 'skills/a.py', 'tests/test.py'])
  assert.deepEqual(tree.map(n => n.name), ['skills', 'tests', 'z.py'])
  assert.deepEqual(tree[0]!.children.map(n => n.path), ['skills/a.py', 'skills/b.py'])
})
test('collapsed folders hide only their descendants and retain depth/parents', () => {
  const tree = buildFileTree(['generated/skills/bom/validate.py', 'tests/test.py'])
  const rows = visibleFileRows(tree, new Set(['generated/skills']))
  assert.deepEqual(rows.map(r => r.node.path), ['generated', 'generated/skills', 'tests', 'tests/test.py'])
  assert.equal(rows[1]!.depth, 1); assert.equal(rows[1]!.parent, 'generated')
})
test('large directories retain every file without flattening collapsed branches', () => {
  const paths = Array.from({ length: 10000 }, (_, i) => `generated/team${i % 20}/skills/file${i}.py`)
  const tree = buildFileTree(paths)
  assert.equal(visibleFileRows(tree, new Set()).filter(r => !r.node.folder).length, 10000)
  assert.equal(visibleFileRows(tree, new Set(['generated'])).length, 1)
})
