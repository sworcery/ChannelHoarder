import { useEffect, useRef, type ReactNode, type KeyboardEvent } from "react"
import { cn } from "@/lib/utils"

export interface TabItem<K extends string = string> {
  key: K
  label: ReactNode
  /** Optional count/badge shown after the label. */
  badge?: ReactNode
}

interface TabsProps<K extends string> {
  tabs: readonly TabItem<K>[]
  value: K
  onChange: (key: K) => void
  /** When set, the selected tab is written to localStorage under this key on change (e.g. "settings_tab"). */
  storageKey?: string
  /** Accessible name for the tablist. */
  label?: string
  /** Makes tab ids `${idPrefix}-tab-${key}`; give the panel `id={panelId(idPrefix, key)}` and `aria-labelledby={tabId(idPrefix, key)}`. */
  idPrefix?: string
  className?: string
}

/** Read a saved tab; returns fallback if nothing valid is stored. Use for the initial state. */
export function readSavedTab<K extends string>(
  storageKey: string,
  valid: readonly K[],
  fallback: K,
  /** Old saved key -> current key, e.g. { naming: "media" }. */
  migrations?: Record<string, K>,
): K {
  try {
    const raw = localStorage.getItem(storageKey)
    const saved = (raw && migrations?.[raw]) || (raw as K | null)
    if (saved && valid.includes(saved)) return saved
  } catch { /* ignore */ }
  return fallback
}

export const tabId = (idPrefix: string, key: string) => `${idPrefix}-tab-${key}`
export const panelId = (idPrefix: string, key: string) => `${idPrefix}-panel-${key}`

export function Tabs<K extends string>({ tabs, value, onChange, storageKey, label, idPrefix, className }: TabsProps<K>) {
  const listRef = useRef<HTMLDivElement>(null)

  const select = (key: K) => {
    onChange(key)
    if (storageKey) {
      try { localStorage.setItem(storageKey, key) } catch { /* ignore */ }
    }
  }

  // Keep the selected tab visible when the list scrolls horizontally
  useEffect(() => {
    const list = listRef.current
    const el = list?.querySelector<HTMLElement>('[aria-selected="true"]')
    if (!list || !el) return
    if (el.offsetLeft < list.scrollLeft) list.scrollLeft = el.offsetLeft
    else if (el.offsetLeft + el.offsetWidth > list.scrollLeft + list.clientWidth) {
      list.scrollLeft = el.offsetLeft + el.offsetWidth - list.clientWidth
    }
  }, [value])

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const idx = tabs.findIndex((t) => t.key === value)
    let next = -1
    if (e.key === "ArrowRight") next = (idx + 1) % tabs.length
    else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length
    else if (e.key === "Home") next = 0
    else if (e.key === "End") next = tabs.length - 1
    if (next < 0) return
    e.preventDefault()
    select(tabs[next].key)
    listRef.current?.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus()
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cn("flex gap-1 border-b overflow-x-auto whitespace-nowrap", className)}
    >
      {tabs.map((t) => {
        const selected = t.key === value
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={idPrefix ? tabId(idPrefix, t.key) : undefined}
            aria-controls={idPrefix ? panelId(idPrefix, t.key) : undefined}
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => select(t.key)}
            className={cn(
              "shrink-0 px-4 py-2 text-sm font-medium border-b-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected
                ? "border-primary text-red-700 dark:text-red-400"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
            {t.badge}
          </button>
        )
      })}
    </div>
  )
}
