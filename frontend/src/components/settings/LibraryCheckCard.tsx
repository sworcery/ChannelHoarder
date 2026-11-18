import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { LibraryCheckItem, LibraryCheckKind } from "@/lib/types"
import { ConfirmDialog } from "@/components/ui/ConfirmDialog"
import { Loader2, FolderSearch } from "lucide-react"

const GROUPS: { kind: LibraryCheckKind; title: string; hint: string }[] = [
  { kind: "wrong_file", title: "Certain: file holds a different video", hint: "The file's own tag or name says it belongs to another video." },
  { kind: "likely", title: "Likely: file length fits a different video", hint: "Fixed one at a time. Check that the match makes sense first." },
  { kind: "lost", title: "Missing: completed videos whose file is gone", hint: "Re-download them, or skip them." },
  { kind: "manual", title: "Check by hand", hint: "Wrong length and no match found. Never fixed automatically." },
]

export default function LibraryCheckCard() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [confirmAll, setConfirmAll] = useState(false)
  const [confirmLost, setConfirmLost] = useState(false)
  const [busy, setBusy] = useState(false)
  const [lostAction, setLostAction] = useState<Record<string, "redownload" | "skip">>({})
  const [messages, setMessages] = useState<Record<string, string>>({})

  const { data } = useQuery({
    queryKey: ["library-check"],
    queryFn: api.getLibraryCheck,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : false),
  })

  const running = data?.status === "running"
  const items = data?.items ?? []
  const certainOpen = items.filter((i) => i.kind === "wrong_file" && i.state === "open")
  // Missing videos a pending move would restore are left out of the bulk re-download:
  // re-downloading one first would block the fix that puts its real file back.
  const restoredByFix = new Set(
    items
      .filter((i) => (i.kind === "wrong_file" || i.kind === "likely") && i.state === "open")
      .map((i) => i.real_video_id),
  )
  const lostToRedownload = items.filter(
    (i) => i.kind === "lost" && i.state === "open" && !restoredByFix.has(i.video_id),
  )

  const start = async () => {
    setError(null)
    setMessages({})
    try {
      await api.startLibraryCheck()
      qc.invalidateQueries({ queryKey: ["library-check"] })
    } catch (e: any) {
      setError(e.message || "Could not start the check")
    }
  }

  const apply = async (body: Parameters<typeof api.applyLibraryCheck>[0]) => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.applyLibraryCheck(body)
      setMessages((m) => ({ ...m, ...Object.fromEntries(res.results.map((r) => [r.id, r.message])) }))
      qc.invalidateQueries({ queryKey: ["library-check"] })
    } catch (e: any) {
      setError(e.message || "Could not apply the fix")
    } finally {
      setBusy(false)
    }
  }

  const fixLabel = (i: LibraryCheckItem) =>
    i.kind === "lost" ? (lostAction[i.id] === "skip" ? "Skip it" : "Re-download") : "Move file to its video"

  return (
    <div className="rounded-lg border bg-card p-4 space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h3 className="font-semibold flex items-center gap-2">
          <FolderSearch className="h-4 w-4" /> Library check
        </h3>
        <button
          onClick={start}
          disabled={running}
          className="px-3 py-1.5 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
        >
          {running ? "Checking..." : "Start check"}
        </button>
      </div>

      <p className="text-sm text-muted-foreground">
        Before version 1.9.50, a scan could move a downloaded file onto a newer video with a similar title
        (for example "Week 11" and "Week 12") and rename it. This check finds files that sit under the wrong
        video, files whose length does not fit their video, and completed videos whose file is missing.
        Nothing changes until you press a fix button. A fix moves the file to the video it really belongs to,
        then queues the video it was wrongly filed under to download again, since that video's own file is gone.
      </p>

      {error && <p className="text-sm text-red-500">{error}</p>}
      {data?.status === "error" && <p className="text-sm text-red-500">Check failed: {data.error}</p>}

      {running && (
        <div className="space-y-1">
          <div className="h-2 rounded bg-muted overflow-hidden">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${data && data.total ? Math.round((data.processed / data.total) * 100) : 0}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" /> {data?.processed ?? 0} of {data?.total ?? 0} videos
          </p>
        </div>
      )}

      {data?.status === "done" && (data.notes ?? []).map((n) => (
        <p key={n} className="text-xs text-muted-foreground">{n}</p>
      ))}

      {data?.status === "done" && !items.some((i) => i.kind === "wrong_file" || i.kind === "likely") && (
        <p className="text-sm text-muted-foreground">
          No provable problems found. Files renamed by the old bug may show only as Missing below.
        </p>
      )}

      {data?.status === "done" && items.length > 0 && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {certainOpen.length > 0 && (
              <button
                onClick={() => setConfirmAll(true)}
                disabled={busy}
                className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground disabled:opacity-50"
              >
                Fix all certain ({certainOpen.length})
              </button>
            )}
            {lostToRedownload.length > 0 && (
              <button
                onClick={() => setConfirmLost(true)}
                disabled={busy}
                className="px-3 py-1.5 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
              >
                Re-download all missing ({lostToRedownload.length})
              </button>
            )}
          </div>
          {GROUPS.map((g) => {
            const list = items.filter((i) => i.kind === g.kind)
            if (list.length === 0) return null
            return (
              <div key={g.kind} className="space-y-2">
                <div>
                  <p className="text-sm font-medium">{g.title} ({list.length})</p>
                  <p className="text-xs text-muted-foreground">{g.hint}</p>
                </div>
                {list.map((i) => (
                  <div key={i.id} className="rounded border p-2 text-sm space-y-1">
                    <p className="font-medium">{i.channel_name}: {i.title}</p>
                    <p className="text-xs text-muted-foreground">{i.detail}</p>
                    {i.file_path && <p className="text-xs font-mono break-all text-muted-foreground">{i.file_path}</p>}
                    {(messages[i.id] || i.result) && (
                      <p className="text-xs">{messages[i.id] || i.result}</p>
                    )}
                    {i.kind !== "manual" && i.state === "open" && (
                      <div className="flex items-center gap-2 pt-1">
                        {i.kind === "lost" && (
                          <select
                            value={lostAction[i.id] ?? "redownload"}
                            onChange={(e) =>
                              setLostAction((a) => ({ ...a, [i.id]: e.target.value as "redownload" | "skip" }))
                            }
                            className="px-2 py-1 rounded-md border bg-background text-xs"
                          >
                            <option value="redownload">Re-download</option>
                            <option value="skip">Skip</option>
                          </select>
                        )}
                        <button
                          onClick={() => apply({ item_ids: [i.id], actions: { [i.id]: lostAction[i.id] ?? "redownload" } })}
                          disabled={busy}
                          className="px-2 py-1 text-xs rounded-md border hover:bg-accent disabled:opacity-50"
                        >
                          {fixLabel(i)}
                        </button>
                      </div>
                    )}
                    {i.state !== "open" && <p className="text-xs text-muted-foreground">Done: {i.state}</p>}
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}

      <ConfirmDialog
        open={confirmAll}
        title="Fix all certain problems?"
        message={`This moves ${certainOpen.length} file(s) to the video each really belongs to and queues ${certainOpen.length} video(s) for download again. Files are never overwritten or deleted; any that cannot be moved safely are skipped and reported.`}
        confirmLabel="Fix all certain"
        onConfirm={() => {
          setConfirmAll(false)
          apply({ all_certain: true })
        }}
        onCancel={() => setConfirmAll(false)}
      />
      <ConfirmDialog
        open={confirmLost}
        title="Re-download all missing videos?"
        message={`This queues ${lostToRedownload.length} downloaded video(s) whose file is gone to download again. Missing videos that a fix above would restore from a misplaced file are left out; fix those first.`}
        confirmLabel="Re-download all"
        onConfirm={() => {
          setConfirmLost(false)
          apply({
            item_ids: lostToRedownload.map((i) => i.id),
            actions: Object.fromEntries(lostToRedownload.map((i) => [i.id, "redownload" as const])),
          })
        }}
        onCancel={() => setConfirmLost(false)}
      />
    </div>
  )
}
