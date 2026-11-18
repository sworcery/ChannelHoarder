import { useState, useMemo, useRef, useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { extractChannelUrls } from "@/lib/bulkImport"
import { useToast } from "@/components/ui/toaster"
import { Button } from "@/components/ui/Button"
import { Dialog } from "@/components/ui/Dialog"
import { Field } from "@/components/ui/Field"

type Outcome = { url: string; status: "added" | "exists" | "failed"; detail: string }

// Spacing between adds, and a run of failures that means YouTube is likely rate-limiting
const ADD_DELAY_MS = 3000
const MAX_CONSECUTIVE_FAILURES = 5

export default function BulkImportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [text, setText] = useState("")
  const [quality, setQuality] = useState("best")
  const [downloadDir, setDownloadDir] = useState("")
  // Off by default: with it on, every imported channel's first scan queues its whole
  // back catalogue at once.
  const [autoDownload, setAutoDownload] = useState(false)
  const [fromDate, setFromDate] = useState("")
  const [running, setRunning] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [haltReason, setHaltReason] = useState("")
  const [done, setDone] = useState(false)
  const [current, setCurrent] = useState(0)
  const [total, setTotal] = useState(0)
  const [outcomes, setOutcomes] = useState<Outcome[]>([])
  const stopRef = useRef(false)

  const urls = useMemo(() => extractChannelUrls(text), [text])

  // Leaving the page stops the import instead of letting it run on unseen
  useEffect(() => () => { stopRef.current = true }, [])

  // Start at the saved Default Quality each time the dialog opens
  const { data: appSettings } = useQuery({
    queryKey: ["app-settings"],
    queryFn: api.getSettings,
  })
  const defaultQuality = appSettings?.default_quality ? String(appSettings.default_quality) : "best"
  // Applied once per open, after the settings have loaded, so a refetch can't undo the user's pick
  const qualitySeeded = useRef(false)
  useEffect(() => {
    if (!open) { qualitySeeded.current = false; return }
    if (!appSettings || qualitySeeded.current) return
    qualitySeeded.current = true
    setQuality(defaultQuality)
  }, [open, appSettings, defaultQuality])

  useEffect(() => {
    if (!running) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = "" }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [running])

  const close = () => {
    setText("")
    setQuality("best")
    setDownloadDir("")
    setAutoDownload(false)
    setFromDate("")
    setDone(false)
    setHaltReason("")
    setCurrent(0)
    setTotal(0)
    setOutcomes([])
    onClose()
  }

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ""
    if (!file) return
    try {
      const content = await file.text()
      setText((prev) => (prev ? prev.replace(/\s*$/, "\n") : "") + content)
    } catch {
      toast("Could not read file", "error")
    }
  }

  const start = async () => {
    const queue = urls
    stopRef.current = false
    setRunning(true)
    setStopping(false)
    setHaltReason("")
    setDone(false)
    setOutcomes([])
    setTotal(queue.length)
    setCurrent(0)
    const results: Outcome[] = []
    let consecutiveFailures = 0
    for (let i = 0; i < queue.length; i++) {
      if (i > 0) await new Promise((r) => setTimeout(r, ADD_DELAY_MS))
      if (stopRef.current) break
      setCurrent(i + 1)
      const url = queue[i]
      try {
        const ch = await api.addChannel({
          url,
          quality,
          auto_download: autoDownload,
          ...(fromDate ? { download_from_date: fromDate } : {}),
          ...(downloadDir ? { download_dir: downloadDir } : {}),
        })
        results.push({ url, status: "added", detail: ch.channel_name })
        consecutiveFailures = 0
        queryClient.invalidateQueries({ queryKey: ["channels"] })
      } catch (e: any) {
        const msg = e?.message || "Unknown error"
        const exists = /already subscribed/i.test(msg)
        results.push({ url, status: exists ? "exists" : "failed", detail: msg })
        consecutiveFailures = exists ? 0 : consecutiveFailures + 1
      }
      setOutcomes([...results])
      if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
        setHaltReason(
          `Stopped after ${MAX_CONSECUTIVE_FAILURES} failures in a row. YouTube may be rate-limiting this server; try the rest again later.`
        )
        break
      }
    }
    setRunning(false)
    setStopping(false)
    setDone(true)
    const count = (s: Outcome["status"]) => results.filter((r) => r.status === s).length
    toast(`Bulk import: ${count("added")} added, ${count("exists")} already subscribed, ${count("failed")} failed`)
  }

  const added = outcomes.filter((o) => o.status === "added").length
  const exists = outcomes.filter((o) => o.status === "exists").length
  const failed = outcomes.filter((o) => o.status === "failed")
  const started = running || done
  const pct = total > 0 ? Math.round((outcomes.length / total) * 100) : 0

  return (
    <Dialog
      open={open}
      onClose={() => { if (!running) close() }}
      title="Bulk Import Channels"
      // An import in progress can't be closed: no Esc, no backdrop click, no X
      closeOnEsc={!running}
      closeOnBackdrop={false}
      showClose={!running}
      footer={
        <>
          {running && (
            <Button
              onClick={() => { stopRef.current = true; setStopping(true) }}
              disabled={stopping}
            >
              {stopping ? "Stopping..." : "Stop"}
            </Button>
          )}
          <Button onClick={close} disabled={running}>
            {done ? "Close" : "Cancel"}
          </Button>
          <Button
            variant="primary"
            onClick={start}
            disabled={urls.length === 0}
            loading={running}
          >
            {running ? "Importing..." : `Import ${urls.length}`}
          </Button>
        </>
      }
    >
        <div className="space-y-4">
          <div>
            <Field label="Paste channel URLs or @handles, one per line or comma-separated">
              <textarea
                value={text}
                autoFocus
                onChange={(e) => setText(e.target.value)}
                disabled={running}
                rows={6}
                placeholder={"https://youtube.com/@channel\n@anotherchannel"}
                className="w-full px-3 py-2 rounded-md border bg-background text-sm font-mono disabled:opacity-50"
              />
            </Field>
            <Field
              className="mt-2"
              label="Or upload a file"
              hint="Upload a CSV or text file, such as subscriptions.csv from YouTube Takeout."
            >
              <input
                type="file"
                accept=".csv,.txt,text/csv,text/plain"
                onChange={onFile}
                disabled={running}
                className="block w-full text-sm file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border file:bg-background file:text-sm hover:file:bg-accent disabled:opacity-50"
              />
            </Field>
          </div>

          <div>
            <p className="text-sm font-medium mb-1">Found {urls.length} channel{urls.length === 1 ? "" : "s"}</p>
            {urls.length > 0 && (
              <div className="max-h-40 overflow-y-auto text-xs border rounded p-2 space-y-0.5">
                {urls.map((u) => (
                  <div key={u} className="truncate">{u}</div>
                ))}
              </div>
            )}
          </div>

          <Field label="Quality">
            <select
              value={quality}
              onChange={(e) => setQuality(e.target.value)}
              disabled={running}
              className="w-full px-3 py-2 rounded-md border bg-background disabled:opacity-50"
            >
              <option value="best">Best Available</option>
              <option value="2160p">4K (2160p)</option>
              <option value="1080p">1080p</option>
              <option value="720p">720p</option>
              <option value="480p">480p</option>
            </select>
          </Field>
          <Field label="Download Directory">
            <input
              type="text"
              placeholder="Leave blank for default (/downloads)"
              value={downloadDir}
              onChange={(e) => setDownloadDir(e.target.value)}
              disabled={running}
              className="w-full px-3 py-2 rounded-md border bg-background disabled:opacity-50"
            />
          </Field>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={autoDownload}
              onChange={(e) => setAutoDownload(e.target.checked)}
              disabled={running}
              className="rounded"
            />
            <span className="text-sm">Auto-download videos</span>
          </label>
          <p className="text-xs text-muted-foreground -mt-2">
            When on, each channel downloads every video it has published unless you set a date. Off by default for bulk imports, since every channel would queue its whole back catalogue at once; when off, videos are listed and you pick what to download, or turn auto-download on per channel later.
          </p>
          <Field label="Download from date (optional)" hint="Leave empty to include everything.">
            <input
              type="date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              disabled={running}
              className="w-full px-3 py-2 rounded-md border bg-background disabled:opacity-50"
            />
          </Field>

          <p className="text-xs text-muted-foreground">
            Channels are added a few seconds apart, then scanned automatically by the scheduler one at a time. Keep this tab open until the import finishes.
          </p>

          {started && (
            <div className="space-y-2">
              {running && <p className="text-sm">Adding {current} of {total}</p>}
              <div className="h-1.5 rounded-full bg-primary/20 overflow-hidden">
                <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
              </div>
              <div className="max-h-40 overflow-y-auto text-xs border rounded p-2 space-y-0.5">
                {outcomes.map((o) => (
                  <div key={o.url} className="flex gap-2">
                    <span
                      className={`shrink-0 ${
                        o.status === "added" ? "text-success" : o.status === "exists" ? "text-muted-foreground" : "text-danger"
                      }`}
                    >
                      {o.status === "added" ? "Added" : o.status === "exists" ? "Already subscribed" : "Failed"}
                    </span>
                    <span className="truncate" title={`${o.url} - ${o.detail}`}>
                      {o.status === "added" ? o.detail : `${o.url} - ${o.detail}`}
                    </span>
                  </div>
                ))}
              </div>
              {haltReason && <p className="text-sm text-danger">{haltReason}</p>}
              {done && (
                <p className="text-sm">
                  {added} added, {exists} already subscribed, {failed.length} failed
                  {outcomes.length < total ? ` (stopped after ${outcomes.length} of ${total})` : ""}
                </p>
              )}
              {done && failed.length > 0 && (
                <div>
                  <p className="text-sm font-medium mb-1">Failed URLs</p>
                  <textarea
                    readOnly
                    rows={4}
                    value={failed.map((f) => f.url).join("\n")}
                    onFocus={(e) => e.currentTarget.select()}
                    className="w-full px-3 py-2 rounded-md border bg-background text-xs font-mono"
                  />
                </div>
              )}
            </div>
          )}
        </div>
    </Dialog>
  )
}
