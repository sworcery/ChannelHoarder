import { useEffect, useRef, useState, type ReactNode, type KeyboardEvent } from "react"
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
  /** "inline" is a lighter pill style for a tab row inside another tab. */
  variant?: "page" | "inline"
  /** Applied to the wrapper around the tablist (layout, not look). */
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

export function Tabs<K extends string>({ tabs, value, onChange, storageKey, label, idPrefix, variant = "page", className }: TabsProps<K>) {
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

  // Show an edge fade on the side(s) where more tabs are scrolled out of view
  const [fade, setFade] = useState({ left: false, right: false })
  useEffect(() => {
    const list = listRef.current
    if (!list) return
    const update = () => {
      const left = list.scrollLeft > 2
      const right = list.scrollLeft + list.clientWidth < list.scrollWidth - 2
      setFade((f) => (f.left === left && f.right === right ? f : { left, right }))
    }
    update()
    list.addEventListener("scroll", update, { passive: true })
    window.addEventListener("resize", update)
    return () => {
      list.removeEventListener("scroll", update)
      window.removeEventListener("resize", update)
    }
  }, [tabs.length, value])

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

  const inline = variant === "inline"

  return (
    <div className={cn("relative min-w-0", className)}>
      <div
        ref={listRef}
        role="tablist"
        aria-label={label}
        onKeyDown={onKeyDown}
        className={cn("flex gap-1 overflow-x-auto whitespace-nowrap", inline ? "" : "border-b")}
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
                "shrink-0 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                inline
                  ? cn(
                      "rounded-md px-3 py-1.5",
                      selected ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
                    )
                  : cn(
                      "px-4 py-2 border-b-2",
                      selected
                        ? "border-primary text-red-700 dark:text-red-400"
                        : "border-transparent text-muted-foreground hover:text-foreground",
                    ),
              )}
            >
              {t.label}
              {t.badge}
            </button>
          )
        })}
      </div>
      {/* Fades on a side with more tabs hidden past the edge */}
      {fade.left && (
        <span aria-hidden="true" className="pointer-events-none absolute inset-y-0 left-0 w-8 bg-gradient-to-r from-background to-transparent" />
      )}
      {fade.right && (
        <span aria-hidden="true" className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-background to-transparent" />
      )}
    </div>
  )
}
