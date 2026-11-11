// Screens the host app renders into the workbench (the SOW screen). The workbench nests its own Refine data provider, so a
// screen that needs the app's provider cannot be rendered inside it. The workbench draws an empty slot where the screen
// belongs; the host renders the screen through a React portal into that element, so the DOM sits inside the workbench pane
// while the React context stays the app's.
import { useCallback, useSyncExternalStore } from 'react'
import './host.css'

export type SlotId = 'scope' | 'grid'

let elements: Partial<Record<SlotId, HTMLElement>> = {}
const subs = new Set<() => void>()
const emit = () => { elements = { ...elements }; subs.forEach(f => f()) }

/** The element a slot currently has (undefined while that screen is not on show). */
export const useSlotElement = (id: SlotId): HTMLElement | undefined =>
  useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => elements[id], () => undefined)

/** Where the host's screen appears. Registers its element while mounted and releases it when the screen changes. */
export function HostSlot({ id }: { id: SlotId }) {
  const ref = useCallback((el: HTMLDivElement | null) => {
    if (el) elements[id] = el
    else delete elements[id]
    emit()
  }, [id])
  return <div ref={ref} className="host-slot" data-slot={id} />
}
