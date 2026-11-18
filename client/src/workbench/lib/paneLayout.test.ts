import { assert, test } from 'vitest'
import {
  DEFAULT_PANE_ORDER, DEFAULT_PANE_WIDTHS, MIN_PANE_PCT, clampWidths, dragWidths, paneOrderKey, type PaneOrder,
  paneOrder, paneWidths, swapPanes, workspacePct,
} from './paneLayout'

test('pane order: invalid values fall back to the default order', () => {
  assert.deepEqual(paneOrder(null), ['nav', 'assistant', 'workspace'])
  assert.deepEqual(paneOrder(['nav', 'nav', 'workspace']), ['nav', 'assistant', 'workspace'])
  assert.deepEqual(paneOrder(['workspace', 'nav', 'assistant']), ['workspace', 'nav', 'assistant'])
  assert.deepEqual(paneOrder(['nav', 'assistant']), ['nav', 'assistant', 'workspace'])
})

test('swapPanes: swaps two panes, keeps the third in place', () => {
  assert.deepEqual(swapPanes(['nav', 'assistant', 'workspace'], 'nav', 'workspace'), ['workspace', 'assistant', 'nav'])
  assert.deepEqual(swapPanes(['nav', 'assistant', 'workspace'], 'assistant', 'assistant'), ['nav', 'assistant', 'workspace'])
})

test('pane widths: default is 20 / 40 / 40 (approved wireframes)', () => {
  assert.deepEqual(DEFAULT_PANE_WIDTHS, { nav: 20, assist: 40 })
  assert.equal(workspacePct(DEFAULT_PANE_WIDTHS), 40)
  assert.deepEqual(paneWidths(null), DEFAULT_PANE_WIDTHS)
  assert.deepEqual(paneWidths({ nav: 24 }), DEFAULT_PANE_WIDTHS) // incomplete -> default
  assert.deepEqual(paneWidths({ nav: 'x', assist: 40 }), DEFAULT_PANE_WIDTHS)
})

test('clampWidths: no pane ever goes below the minimum share', () => {
  assert.deepEqual(clampWidths({ nav: 2, assist: 40 }), { nav: MIN_PANE_PCT, assist: 40 })
  assert.deepEqual(clampWidths({ nav: 90, assist: 40 }), { nav: 80, assist: MIN_PANE_PCT })
  assert.deepEqual(clampWidths({ nav: 20.4, assist: 39.6 }), { nav: 20, assist: 40 })
})

test('dragWidths: dragging a divider grows one pane and shrinks its neighbour; the workspace compensates', () => {
  // nav | assistant: positive delta grows the left pane.
  assert.deepEqual(dragWidths({ nav: 20, assist: 40 }, 'nav', 'assistant', 5), { nav: 25, assist: 35 })
  // assistant | workspace: growing the assistant shrinks the workspace remainder.
  assert.deepEqual(dragWidths({ nav: 20, assist: 40 }, 'assistant', 'workspace', -2), { nav: 20, assist: 38 })
  assert.equal(workspacePct(dragWidths({ nav: 20, assist: 40 }, 'assistant', 'workspace', 8)), 32)
  // workspace | assistant (swapped order): growing the workspace shrinks the assistant.
  assert.deepEqual(dragWidths({ nav: 20, assist: 40 }, 'workspace', 'assistant', 6), { nav: 20, assist: 34 })
  // A drag stops where a pane hits the minimum: nav can only give 2 of the requested 10.
  assert.deepEqual(dragWidths({ nav: 12, assist: 40 }, 'nav', 'assistant', -10), { nav: MIN_PANE_PCT, assist: 42 })
})

// Shell arrangement: browser-persisted values cannot hide, duplicate or invent panes.
test('pane layout validates saved data and isolates accounts', () => {
  for (const bad of [null, {}, [], ['nav'], ['nav', 'nav', 'workspace'], ['nav', 'assistant', 'other']])
    assert.deepEqual(paneOrder(bad), [...DEFAULT_PANE_ORDER])
  assert.equal(paneOrderKey(' A@Example.com '), paneOrderKey('a@example.com'))
  assert.notEqual(paneOrderKey('a@example.com'), paneOrderKey('b@example.com'))
})
test('all six pane orders swap without changing the source or losing a pane', () => {
  const orders = new Set<string>()
  for (const a of DEFAULT_PANE_ORDER) for (const b of DEFAULT_PANE_ORDER) {
    if (a === b) continue
    const c = DEFAULT_PANE_ORDER.find(x => x !== a && x !== b)!
    const order: PaneOrder = [a, b, c]
    orders.add(order.join(','))
    assert.deepEqual(paneOrder(order), order)
    assert.deepEqual(swapPanes(swapPanes(order, a, b), a, b), order)
    assert.deepEqual(swapPanes(order, a, a), order)
    assert.deepEqual([...order].sort(), [...DEFAULT_PANE_ORDER].sort())
  }
  assert.equal(orders.size, 6)
})
