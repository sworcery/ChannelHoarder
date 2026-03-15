import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatDateTime, formatBytes } from "@/lib/utils"
import { STATUS_COLORS } from "@/lib/types"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useEffect } from "react"
import {
  Download,
  RotateCcw,
  Loader2,
  Search,
  XCircle,
  ClipboardCopy,
} from "lucide-react"

export default function DownloadsPage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<"queue" | "history" | "failed">("queue")
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(0)
  const [activeProgress, setActiveProgress] = useState<Record<string, any>>({})

  const { subscribe } = useWebSocket()

  useEffect(() => {
    return subscribe((msg) => {
      if (msg.type === "download_progress") {
        setActiveProgress((prev) => ({ ...prev, [msg.payload.video_id]: msg.payload }))
      }
      if (msg.type === "download_complete" || msg.type === "download_failed") {
        setActiveProgress((prev) => {
          const next = { ...prev }
          delete next[msg.payload.video_id]
          return next
        })
        queryClient.invalidateQueries({ queryKey: ["download-queue"] })
        queryClient.invalidateQueries({ queryKey: ["download-history"] })
      }
    })
  }, [subscribe, queryClient])

  const { data: queue } = useQuery({
    queryKey: ["download-queue"],
    queryFn: api.getQueue,
    refetchInterval: 5000,
    enabled: tab === "queue",
  })

  const { data: history } = useQuery({
    queryKey: ["download-history", page, search, tab === "failed" ? "failed" : undefined],
    queryFn: () =>
      api.getHistory({
        skip: page * 50,
        limit: 50,
        search: search || undefined,
        status: tab === "failed" ? "failed" : undefined,
      }),
    enabled: tab !== "queue",
  })

  const retryMutation = useMutation({
    mutationFn: (videoId: number) => api.retryDownload(videoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["download-history"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
    },
  })

  const retryAllMutation = useMutation({
    mutationFn: api.retryAllFailed,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["download-history"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
    },
  })

  const removeFromQueue = useMutation({
    mutationFn: (queueId: number) => api.removeFromQueue(queueId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["download-queue"] }),
  })

  const tabs = [
    { key: "queue", label: "Queue" },
    { key: "history", label: "History" },
    { key: "failed", label: "Failed" },
  ] as const

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Downloads</h1>

      {/* Tabs */}
      <div className="flex gap-1 border-b">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); setPage(0) }}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Queue Tab */}
      {tab === "queue" && (
        <div className="space-y-3">
          {queue && queue.length > 0 ? (
            queue.map((entry: any) => {
              const progress = activeProgress[entry.video?.video_id]
              return (
                <div key={entry.id} className="rounded-lg border bg-card p-4">
                  <div className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">{entry.video?.title || "Unknown"}</p>
                      <p className="text-sm text-muted-foreground">
                        Queued: {formatDateTime(entry.queued_at)}
                        {entry.started_at && " | Started: " + formatDateTime(entry.started_at)}
                      </p>
                    </div>
                    <button
                      onClick={() => removeFromQueue.mutate(entry.id)}
                      className="p-1.5 hover:bg-accent rounded ml-2"
                      title="Remove from queue"
                    >
                      <XCircle className="h-4 w-4 text-muted-foreground" />
                    </button>
                  </div>
                  {(progress || entry.started_at) && (
                    <div className="mt-2">
                      <div className="h-2 bg-secondary rounded-full overflow-hidden">
                        <div
                          className="h-full bg-primary rounded-full transition-all"
                          style={{ width: `${progress?.percent || 0}%` }}
                        />
                      </div>
                      {progress && (
                        <div className="flex justify-between text-xs text-muted-foreground mt-1">
                          <span>{progress.percent}%</span>
                          <span>{progress.speed}</span>
                          <span>ETA: {progress.eta}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })
          ) : (
            <p className="text-center py-8 text-muted-foreground">Download queue is empty</p>
          )}
        </div>
      )}

      {/* History / Failed Tab */}
      {tab !== "queue" && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search videos..."
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(0) }}
                className="w-full pl-9 pr-3 py-2 rounded-md border bg-background"
              />
            </div>
            {tab === "failed" && (
              <button
                onClick={() => retryAllMutation.mutate()}
                disabled={retryAllMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                <RotateCcw className="h-4 w-4" />
                Retry All Failed
              </button>
            )}
          </div>

          {history && history.items.length > 0 ? (
            <>
              <div className="rounded-lg border overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      <th className="text-left px-3 py-2">Title</th>
                      <th className="text-left px-3 py-2">Status</th>
                      <th className="text-left px-3 py-2">Size</th>
                      <th className="text-left px-3 py-2">Date</th>
                      <th className="px-3 py-2"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {history.items.map((video: any) => (
                      <tr key={video.id} className="hover:bg-muted/30">
                        <td className="px-3 py-2 max-w-xs truncate">{video.title}</td>
                        <td className="px-3 py-2">
                          <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[video.status] || ""}`}>
                            {video.status}
                          </span>
                          {video.error_code && (
                            <span className="ml-1 text-xs text-red-500" title={video.error_message || ""}>
                              {video.error_code}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {video.file_size ? formatBytes(video.file_size) : "-"}
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {formatDateTime(video.downloaded_at || video.discovered_at)}
                        </td>
                        <td className="px-3 py-2">
                          {(video.status === "failed" || video.status === "skipped") && (
                            <button
                              onClick={() => retryMutation.mutate(video.id)}
                              className="p-1 hover:bg-accent rounded"
                              title="Retry download"
                            >
                              <RotateCcw className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Showing {page * 50 + 1}-{Math.min((page + 1) * 50, history.total)} of {history.total}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage(Math.max(0, page - 1))}
                    disabled={page === 0}
                    className="px-3 py-1 rounded-md border text-sm disabled:opacity-50"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setPage(page + 1)}
                    disabled={(page + 1) * 50 >= history.total}
                    className="px-3 py-1 rounded-md border text-sm disabled:opacity-50"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          ) : (
            <p className="text-center py-8 text-muted-foreground">No downloads found</p>
          )}
        </div>
      )}
    </div>
  )
}
