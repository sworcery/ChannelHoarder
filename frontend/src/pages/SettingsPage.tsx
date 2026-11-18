import { useState, useEffect, useMemo, useRef } from "react"
import { useBlocker, useSearchParams } from "react-router-dom"
import { Search } from "lucide-react"
import { Button } from "@/components/ui/Button"
import { Dialog } from "@/components/ui/Dialog"
import { Input } from "@/components/ui/Input"
import { PageHeader } from "@/components/ui/PageHeader"
import { Tabs, readSavedTab, panelId, tabId, type TabItem } from "@/components/ui/Tabs"
import GeneralTab from "@/components/settings/GeneralTab"
import ScanningTab from "@/components/settings/ScanningTab"
import MediaTab from "@/components/settings/MediaTab"
import AuthTab from "@/components/settings/AuthTab"
import AntiDetectTab from "@/components/settings/AntiDetectTab"
import NotificationsTab from "@/components/settings/NotificationsTab"
import DiagnosticsTab from "@/components/settings/DiagnosticsTab"
import { SettingsFormContext, type FormStatus } from "@/components/settings/settingsForm"
import { TAB_LABELS, searchSettings, type SettingsTab } from "@/components/settings/searchIndex"

const STORAGE_KEY = "settings_tab"

const TABS: readonly TabItem<SettingsTab>[] = (
  ["general", "scanning", "media", "auth", "antidetect", "notifications", "diagnostics"] as const
).map((key) => ({ key, label: TAB_LABELS[key] }))
const TAB_KEYS = TABS.map((t) => t.key)

// Old saved tab keys -> current ones
const MIGRATIONS: Record<string, SettingsTab> = { naming: "media", ytdlp: "general", connect: "notifications" }

const isTab = (v: string | null): v is SettingsTab => !!v && (TAB_KEYS as string[]).includes(v)

function remember(tab: SettingsTab) {
  try { localStorage.setItem(STORAGE_KEY, tab) } catch { /* ignore */ }
}

/** Scroll to a setting and ring it for a moment. Waits briefly for tabs that load their content. */
function useReveal(anchor: string | null, onDone: () => void, deps: unknown[]) {
  useEffect(() => {
    if (!anchor) return
    let tries = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const ring = ["ring-2", "ring-primary", "ring-offset-2", "ring-offset-background", "rounded-md"]
    const run = () => {
      const el = document.getElementById(anchor)
      if (el) {
        el.scrollIntoView({ block: "center" })
        el.classList.add(...ring)
        setTimeout(() => el.classList.remove(...ring), 2000)
        onDone()
        return
      }
      if (++tries < 20) timer = setTimeout(run, 150)
      else onDone()
    }
    run()
    // The ring timer is left running: it removes the ring even after the anchor state resets.
    return () => {
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchor, ...deps])
}

export default function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const rawUrlTab = params.get("tab")
  const urlAnchor = params.get("anchor")
  // Old keys work in links too (?tab=connect opens Notifications)
  const urlTab = rawUrlTab ? (MIGRATIONS[rawUrlTab] ?? rawUrlTab) : null

  const [tab, setTab] = useState<SettingsTab>(() => {
    if (isTab(urlTab)) {
      remember(urlTab)
      return urlTab
    }
    return readSavedTab(STORAGE_KEY, TAB_KEYS, "general", MIGRATIONS)
  })
  const [form, setForm] = useState<FormStatus | null>(null)
  const [pending, setPending] = useState<{ tab: SettingsTab; anchor?: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [anchor, setAnchor] = useState<string | null>(null)
  const [panelKey, setPanelKey] = useState(0)
  const [query, setQuery] = useState("")

  const results = useMemo(() => searchSettings(query), [query])

  const doSwitch = (next: SettingsTab, to?: string) => {
    setTab(next)
    remember(next)
    setAnchor(to ?? null)
    if (next === "diagnostics" && to) {
      // The settings in Diagnostics sit on its overview; a remount shows it even if a log view was open.
      try { localStorage.setItem("diagnostics_tab", "overview") } catch { /* ignore */ }
      setPanelKey((k) => k + 1)
    }
  }

  const goTo = (next: SettingsTab, to?: string) => {
    if (next !== tab && form?.dirty) {
      setPending({ tab: next, anchor: to })
      return
    }
    doSwitch(next, to)
  }

  // /settings?tab=<key> (links from the dashboard alerts): switch, then drop the parameter
  useEffect(() => {
    if (!rawUrlTab) return
    if (isTab(urlTab)) goTo(urlTab, urlAnchor ?? undefined)
    setParams((p) => { p.delete("tab"); p.delete("anchor"); return p }, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawUrlTab, urlAnchor])

  // Close the search results on an outside click
  const searchRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!query) return
    const onDown = (e: PointerEvent) => {
      if (!searchRef.current?.contains(e.target as Node)) setQuery("")
    }
    document.addEventListener("pointerdown", onDown)
    return () => document.removeEventListener("pointerdown", onDown)
  }, [query])

  useReveal(anchor, () => setAnchor(null), [tab, panelKey])

  // Leaving the page (reload, close tab, address bar) with unsaved changes
  useEffect(() => {
    if (!form?.dirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ""
    }
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => window.removeEventListener("beforeunload", onBeforeUnload)
  }, [form?.dirty])

  const save = async () => {
    if (!form) return false
    setSaving(true)
    try {
      return await form.save()
    } finally {
      setSaving(false)
    }
  }

  const pick = (entryTab: SettingsTab, anchorId: string) => {
    setQuery("")
    goTo(entryTab, anchorId)
  }

  // Leaving Settings inside the app (sidebar, links) with unsaved changes asks first.
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    !!form?.dirty && currentLocation.pathname !== nextLocation.pathname,
  )
  const leaving = blocker.state === "blocked"

  const closePending = () => {
    setPending(null)
    if (blocker.state === "blocked") blocker.reset()
  }
  const continueAfterSave = (p: typeof pending) => {
    closePending()
    if (p) doSwitch(p.tab, p.anchor)
  }

  return (
    <SettingsFormContext.Provider value={setForm}>
      <div className="space-y-6">
        <PageHeader title="Settings" />

        <div
          ref={searchRef}
          className="relative max-w-md"
          onBlur={(e) => {
            // Keyboard focus moving out of the search box and its results closes the list
            if (e.relatedTarget && !e.currentTarget.contains(e.relatedTarget as Node)) setQuery("")
          }}
        >
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input
            type="search"
            aria-label="Search settings"
            placeholder="Search settings (e.g. cookies, quality, Discord)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setQuery("")
              else if (e.key === "Enter" && results[0]) pick(results[0].tab, results[0].anchorId)
            }}
            className="pl-9"
          />
          {query.trim() && (
            <div className="absolute left-0 right-0 top-full z-30 mt-1 max-h-72 overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md">
              {results.length === 0 ? (
                <p className="px-3 py-2 text-sm text-muted-foreground">No settings match "{query.trim()}".</p>
              ) : (
                <ul>
                  {results.map((r) => (
                    <li key={r.anchorId}>
                      <button
                        type="button"
                        onClick={() => pick(r.tab, r.anchorId)}
                        className="flex min-h-10 w-full items-center justify-between gap-3 rounded px-3 py-1.5 text-left text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:min-h-0"
                      >
                        <span>{r.label}</span>
                        <span className="shrink-0 text-xs text-muted-foreground">{TAB_LABELS[r.tab]}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        <Tabs tabs={TABS} value={tab} onChange={(k) => goTo(k)} label="Settings sections" idPrefix="settings" />

        <div key={panelKey} role="tabpanel" id={panelId("settings", tab)} aria-labelledby={tabId("settings", tab)}>
          {tab === "general" && <GeneralTab />}
          {tab === "scanning" && <ScanningTab />}
          {tab === "media" && <MediaTab />}
          {tab === "auth" && <AuthTab />}
          {tab === "antidetect" && <AntiDetectTab />}
          {tab === "notifications" && <NotificationsTab />}
          {tab === "diagnostics" && <DiagnosticsTab />}
        </div>

        {form?.dirty && (
          <div
            role="region"
            aria-label="Unsaved changes"
            className="sticky bottom-0 z-20 -mx-4 -mb-4 flex flex-wrap items-center justify-between gap-3 border-t bg-background px-4 py-3 shadow-[0_-4px_12px_rgba(0,0,0,0.08)] sm:-mx-6 sm:-mb-6 sm:px-6"
          >
            <p className="text-sm font-medium">You have unsaved changes</p>
            <div className="flex gap-2">
              <Button variant="outline" onClick={form.discard} disabled={saving || form.saving}>
                Discard
              </Button>
              <Button variant="primary" onClick={save} loading={saving || form.saving}>
                Save
              </Button>
            </div>
          </div>
        )}
      </div>

      <Dialog
        open={pending !== null || leaving}
        onClose={closePending}
        title="Unsaved changes"
        description={`You have unsaved changes on ${TAB_LABELS[tab]}. Save them before you leave, or discard them.`}
        size="sm"
        footer={
          <>
            <Button variant="outline" onClick={closePending}>Keep Editing</Button>
            <Button
              variant="danger-outline"
              onClick={() => {
                if (leaving) {
                  setPending(null)
                  blocker.proceed?.()
                  return
                }
                continueAfterSave(pending)
              }}
            >
              Discard
            </Button>
            <Button
              variant="primary"
              onClick={async () => {
                if (leaving) {
                  if (await save()) {
                    setPending(null)
                    blocker.proceed?.()
                  }
                  return
                }
                const p = pending
                if (p && (await save())) continueAfterSave(p)
              }}
              loading={saving}
            >
              Save
            </Button>
          </>
        }
      />
    </SettingsFormContext.Provider>
  )
}
