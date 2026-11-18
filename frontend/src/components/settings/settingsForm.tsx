import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react"

/** What the Settings page shell needs to know about the tab that is open. */
export interface FormStatus {
  dirty: boolean
  saving: boolean
  /** Resolves true when the save went through. */
  save: () => Promise<boolean>
  discard: () => void
}

export const SettingsFormContext = createContext<(status: FormStatus | null) => void>(() => {})

export type ServerSettings = Record<string, any>

export const asBool = (v: unknown, fallback: boolean): boolean =>
  v == null ? fallback : v === true || v === "true"

/**
 * Keeps the edited values of one tab next to what the server holds.
 * `fromServer` maps the settings object to this tab's values and must supply a default for every key,
 * so a missing setting and an untouched field compare equal.
 */
export function useSettingsDraft<T extends Record<string, unknown>>(
  settings: ServerSettings | undefined,
  fromServer: (s: ServerSettings) => T,
) {
  const baseline = useMemo(() => fromServer(settings ?? {}), [settings, fromServer])
  const key = JSON.stringify(baseline)
  const [values, setValues] = useState<T>(baseline)

  // A fresh copy from the server (first load, or after a save) replaces the edits.
  useEffect(() => {
    setValues(baseline)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const dirty = settings !== undefined && JSON.stringify(values) !== key
  const set = <K extends keyof T>(k: K, v: T[K]) => setValues((p) => ({ ...p, [k]: v }))
  const reset = () => setValues(baseline)
  return { values, set, dirty, reset }
}

/** Tells the page shell about this tab's unsaved state; clears it when the tab goes away. */
export function useReportForm(dirty: boolean, saving: boolean, save: () => Promise<boolean>, discard: () => void) {
  const report = useContext(SettingsFormContext)
  const latest = useRef({ save, discard })
  latest.current = { save, discard }

  useEffect(() => {
    report({ dirty, saving, save: () => latest.current.save(), discard: () => latest.current.discard() })
  }, [dirty, saving, report])

  useEffect(() => () => report(null), [report])
}
