// Which SOW version the editor compares against (pure, unit tested). The default baseline is the previous
// saved version; "generated" audits against the model output. With no earlier saved version there is no
// comparison at all: never a fake diff, never a manufactured earlier version.
import type { Sow } from '../api/types'

export type CompareWith = 'previous' | 'generated'
export type SowComparison =
  | { kind: 'diff'; html: string; from: string; to: string }
  | { kind: 'none'; reason: string }

export function sowComparison(sow: Sow, liveHtml: string, compareWith: CompareWith): SowComparison {
  const dirty = liveHtml !== sow.html
  if (compareWith === 'generated') return { kind: 'diff', html: sow.generatedHtml, from: `v${sow.generatedVersion} (model-generated)`, to: dirty ? `v${sow.version} + unsaved edits` : `v${sow.version}` }
  if (dirty) return { kind: 'diff', html: sow.html, from: `v${sow.version} (saved)`, to: 'unsaved edits' }
  if (sow.previous) return { kind: 'diff', html: sow.previous.html, from: `v${sow.previous.version}`, to: `v${sow.version}` }
  return {
    kind: 'none',
    reason: `v${sow.version} is the first saved version, so there is no earlier version to compare with. Your next saved edit becomes v${sow.version + 1} and is colour-coded against v${sow.version}. To audit against the model's output, compare with the model-generated version.`,
  }
}
