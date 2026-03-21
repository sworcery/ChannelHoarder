import { useState, useEffect, useCallback, useRef } from "react"
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatDateTime, formatBytes, formatDuration, formatSpeed, formatEta, formatElapsed } from "@/lib/utils"
import { STATUS_COLORS } from "@/lib/types"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useDebounce } from "@/hooks/useDebounce"
import { useToast } from "@/components/ui/toaster"
import {
  RotateCcw,
  Loader2,
  Search,
  XCircle,
  Pause,
  Play,
  Trash2,
  AlertTriangle,
  CheckCircle,
  Clock,
  ChevronDown,
  ChevronUp,
  Info,
  ExternalLink,
  CheckSquare,
  Square,
  MinusSquare,
} from "lucide-react"

/** Human-readable error descriptions shown inline */
const ERROR_DESCRIPTIONS: Record<string, { label: string; color: string; tip: string }> = {
  AUTH_EXPIRED: {
    label: "Authentication Required",
    color: "text-red-400",
    tip: "YouTube is requiring sign-in. Upload fresh cookies in Settings > Authentication.",
  },
  RATE_LIMITED: {
    label: "Rate Limited",
    color: "text-yellow-400",
    tip: "Too many requests. Will auto-retry with increased delay.",
  },
  GEO_BLOCKED: {
    label: "Geo-Blocked",
    color: "text-gray-400",
    tip: "Video not available in your region.",
  },
  VIDEO_UNAVAILABLE: {
    label: "Unavailable",
    color: "text-gray-400",
    tip: "Video was removed or made unavailable by YouTube.",
  },
  VIDEO_PRIVATE: {
    label: "Private",
    color: "text-gray-400",
    tip: "Video is set to private by the uploader.",
  },
  VIDEO_REMOVED: {
    label: "Removed",
    color: "text-gray-400",
    tip: "Video has been permanently removed from YouTube.",
  },
  NETWORK_ERROR: {
    label: "Network Error",
    color: "text-yellow-400",
    tip: "Connection issue. Will auto-retry.",
  },
  YTDLP_OUTDATED: {
    label: "yt-dlp Outdated",
    color: "text-orange-400",
    tip: "Update yt-dlp in Settings > System.",
  },
  FFMPEG_ERROR: {
    label: "Processing Error",
    color: "text-orange-400",
    tip: "Video/audio merge failed. Will auto-retry.",
  },
  DISK_FULL: {
    label: "Disk Full",
    color: "text-red-500",
    tip: "No disk space remaining. Free space and retry.",
  },
  PO_TOKEN_FAILURE: {
    label: "PO Token Failed",
    color: "text-orange-400",
    tip: "Token server issue. Check PO token server status in Settings.",
  },
  FORMAT_UNAVAILABLE: {
    label: "Format Unavailable",
    color: "text-yellow-400",
    tip: "Requested quality not available. Try 'best' quality.",
  },
  AGE_RESTRICTED: {
    label: "Age Restricted",
    color: "text-yellow-400",
    tip: "Requires cookies from a logged-in account.",
  },
  UNKNOWN: {
    label: "Unknown Error",
    color: "text-red-400",
    tip: "Check diagnostics for details.",
  },
}

export default function DownloadsPage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<"queue" | "history" | "failed">("queue")
  const [search, setSearch] = useState("")
  const [queueSearch, setQueueSearch] = useState("")
  const debouncedSearch = useDebounce(search, 300)
  const debouncedQueueSearch = useDebounce(queueSearch, 300)
  const [page, setPage] = useState(0)
  const [queuePage, setQueuePage] = useState(0)
  const [activeProgress, setActiveProgress] = useState<Record<string, any>>({})
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set())
  const [selectedQueueIds, setSelectedQueueIds] = useState<Set<number>>(new Set())
  const [lastSelectedIdx, setLastSelectedIdx] = useState<number | null>(null)

  // Smoothing state: max percent (no-backward), speed history (rolling avg)
  const maxPercentRef = useRef<Record<string, number>>({})
  const speedHistoryRef = useRef<Record<string, number[]>>({})

  const { subscribe } = useWebSocket()

  const toggleExpanded = (id: number) => {
    setExpandedRows((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  useEffect(() => {
    return subscribe((msg) => {
      if (msg.type === "download_progress") {
        const p = msg.payload
        const vid = p.video_id

        // No-backward: enforce monotonic progress
        const prevMax = maxPercentRef.current[vid] || 0
        const smoothedPercent = Math.max(prevMax, p.percent || 0)
        maxPercentRef.current[vid] = smoothedPercent

        // Speed smoothing: rolling average of last 3 values
        const rawSpeed = p.speed_bytes || 0
        if (!speedHistoryRef.current[vid]) speedHistoryRef.current[vid] = []
        const hist = speedHistoryRef.current[vid]
        if (rawSpeed > 0) {
          hist.push(rawSpeed)
          if (hist.length > 3) hist.shift()
        }
        const smoothedSpeed = hist.length > 0
          ? hist.reduce((a, b) => a + b, 0) / hist.length
          : rawSpeed

        setActiveProgress((prev) => ({
          ...prev,
          [vid]: {
            ...p,
            percent: Math.round(smoothedPercent * 10) / 10,
            smoothed_speed: smoothedSpeed,
          },
        }))
      }
      if (msg.type === "download_complete" || msg.type === "download_failed") {
        const vid = msg.payload.video_id
        setActiveProgress((prev) => {
          const next = { ...prev }
          delete next[vid]
          return next
        })
        delete maxPercentRef.current[vid]
        delete speedHistoryRef.current[vid]
        queryClient.invalidateQueries({ queryKey: ["download-queue"] })
        queryClient.invalidateQueries({ queryKey: ["download-history"] })
      }
    })
  }, [subscribe, queryClient])

  const PAGE_SIZE = 25

  const { data: queueData, isFetching: queueFetching, isPlaceholderData } = useQuery({
    queryKey: ["download-queue", queuePage, debouncedQueueSearch],
    queryFn: () => api.getQueue({ skip: queuePage * PAGE_SIZE, limit: PAGE_SIZE, search: debouncedQueueSearch || undefined }),
    refetchInterval: 15000,
    staleTime: 5000,
    enabled: tab === "queue",
    placeholderData: keepPreviousData,
  })

  const queue = queueData?.items
  const queueTotal = queueData?.total ?? 0

  // Prefetch next page so navigation feels instant
  useEffect(() => {
    if (tab === "queue" && queueTotal > (queuePage + 1) * PAGE_SIZE) {
      queryClient.prefetchQuery({
        queryKey: ["download-queue", queuePage + 1, debouncedQueueSearch],
        queryFn: () => api.getQueue({ skip: (queuePage + 1) * PAGE_SIZE, limit: PAGE_SIZE, search: debouncedQueueSearch || undefined }),
        staleTime: 30000,
      })
    }
  }, [queuePage, queueTotal, debouncedQueueSearch, tab, queryClient])

  const { data: pauseStatus } = useQuery({
    queryKey: ["download-paused"],
    queryFn: api.getPauseStatus,
    refetchInterval: 30000,
    staleTime: 10000,
  })

  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: ["download-history", page, debouncedSearch, tab === "failed" ? "failed" : undefined],
    queryFn: () =>
      api.getHistory({
        skip: page * PAGE_SIZE,
        limit: PAGE_SIZE,
        search: debouncedSearch || undefined,
        status: tab === "failed" ? "failed" : undefined,
      }),
    staleTime: 10000,
    enabled: tab !== "queue",
  })

  const { toast } = useToast()

  const isPaused = pauseStatus?.paused ?? false

  const pauseMutation = useMutation({
    mutationFn: api.pauseQueue,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["download-paused"] })
      toast("Queue paused")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const resumeMutation = useMutation({
    mutationFn: api.resumeQueue,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["download-paused"] })
      toast("Queue resumed")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const clearQueueMutation = useMutation({
    mutationFn: api.clearQueue,
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast(data.message || "Queue cleared")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const retryMutation = useMutation({
    mutationFn: (videoId: number) => api.retryDownload(videoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["download-history"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast("Video queued for retry")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const retryAllMutation = useMutation({
    mutationFn: api.retryAllFailed,
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["download-history"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast(data.message || "All failed downloads queued for retry")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const removeFromQueue = useMutation({
    mutationFn: (queueId: number) => api.removeFromQueue(queueId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast("Removed from queue")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const bulkRemoveMutation = useMutation({
    mutationFn: (ids: number[]) => api.bulkRemoveFromQueue(ids),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      setSelectedQueueIds(new Set())
      setLastSelectedIdx(null)
      toast(data.message || `Removed ${data.removed} items`)
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  // Clear selection when switching tabs
  const handleTabChange = useCallback((newTab: typeof tab) => {
    setTab(newTab)
    setPage(0)
    setQueuePage(0)
    setSelectedQueueIds(new Set())
    setLastSelectedIdx(null)
  }, [])

  /** Toggle selection of a queue item, with shift+click for range select */
  const toggleQueueSelection = useCallback((entryId: number, idx: number, shiftKey: boolean) => {
    setSelectedQueueIds((prev) => {
      const next = new Set(prev)
      if (shiftKey && lastSelectedIdx !== null && queue) {
        const start = Math.min(lastSelectedIdx, idx)
        const end = Math.max(lastSelectedIdx, idx)
        for (let i = start; i <= end; i++) {
          next.add(queue[i].id)
        }
      } else {
        if (next.has(entryId)) next.delete(entryId)
        else next.add(entryId)
      }
      return next
    })
    setLastSelectedIdx(idx)
  }, [lastSelectedIdx, queue])

  const toggleSelectAll = useCallback(() => {
    if (!queue) return
    setSelectedQueueIds((prev) => {
      if (prev.size === queue.length) return new Set()
      return new Set(queue.map((e: any) => e.id))
    })
  }, [queue])

  const tabs = [
    { key: "queue", label: "Queue" },
    { key: "history", label: "History" },
    { key: "failed", label: "Failed" },
  ] as const

  /** Determine what state label to show for a queue entry */
  const getQueueEntryState = (entry: any, progress: any) => {
    if (progress && progress.percent >= 99.5) {
      return { label: "Processing...", icon: <Loader2 className="h-3.5 w-3.5 animate-spin text-green-500" />, color: "text-green-500" }
    }
    if (progress && progress.percent > 0) {
      return { label: "Downloading", icon: <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />, color: "text-primary" }
    }
    if (entry.started_at && !progress) {
      return { label: "Starting...", icon: <Clock className="h-3.5 w-3.5 text-yellow-500 animate-pulse" />, color: "text-yellow-500" }
    }
    if (entry.started_at) {
      return { label: "Downloading", icon: <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />, color: "text-primary" }
    }
    return { label: "Queued", icon: <Clock className="h-3.5 w-3.5 text-muted-foreground" />, color: "text-muted-foreground" }
  }

  /** Time since a date as human-readable string */
  const timeSince = (dateStr: string | null) => {
    if (!dateStr) return null
    const diff = (Date.now() - new Date(dateStr).getTime()) / 1000
    if (diff < 60) return `${Math.round(diff)}s ago`
    if (diff < 3600) return `${Math.round(diff / 60)}m ago`
    return `${Math.round(diff / 3600)}h ago`
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Downloads</h1>
        <div className="flex items-center gap-2">
          {isPaused ? (
            <button
              onClick={() => resumeMutation.mutate()}
              disabled={resumeMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
            >
              <Play className="h-4 w-4" />
              Resume
            </button>
          ) : (
            <button
              onClick={() => pauseMutation.mutate()}
              disabled={pauseMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md bg-yellow-600 text-white hover:bg-yellow-700 disabled:opacity-50"
            >
              <Pause className="h-4 w-4" />
              Pause
            </button>
          )}
          <button
            onClick={() => {
              if (window.confirm("Clear all queued (non-active) downloads?")) {
                clearQueueMutation.mutate()
              }
            }}
            disabled={clearQueueMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border border-red-500/30 text-red-500 hover:bg-red-500/10 disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" />
            Clear Queue
          </button>
        </div>
      </div>

      {/* Paused banner */}
      {isPaused && (
        <div className="rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-4 py-3 flex items-center gap-3">
          <Pause className="h-5 w-5 text-yellow-500 shrink-0" />
          <div>
            <p className="text-sm font-medium text-yellow-500">Queue Paused</p>
            <p className="text-xs text-muted-foreground">
              No new downloads will start. In-flight downloads will finish.
            </p>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => handleTabChange(t.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
            {t.key === "queue" && queueTotal > 0 && (
              <span className="ml-1.5 text-xs bg-primary/20 text-primary px-1.5 py-0.5 rounded-full">
                {queueTotal}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Queue Tab */}
      {tab === "queue" && (
        <div className="space-y-3">
          {/* Search */}
          <div className="relative max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search queue..."
              value={queueSearch}
              onChange={(e) => { setQueueSearch(e.target.value); setQueuePage(0) }}
              className="w-full pl-9 pr-3 py-2 rounded-md border bg-background"
            />
          </div>

          {/* Select all + bulk action bar */}
          {queue && queue.length > 0 && (
            <div className="flex items-center justify-between">
              <button
                onClick={toggleSelectAll}
                className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
              >
                {selectedQueueIds.size === 0 ? (
                  <Square className="h-4 w-4" />
                ) : selectedQueueIds.size === queue.length ? (
                  <CheckSquare className="h-4 w-4 text-primary" />
                ) : (
                  <MinusSquare className="h-4 w-4 text-primary" />
                )}
                {selectedQueueIds.size > 0
                  ? `${selectedQueueIds.size} selected`
                  : "Select all"}
              </button>
              {selectedQueueIds.size > 0 && (
                <button
                  onClick={() => {
                    if (window.confirm(`Remove ${selectedQueueIds.size} items from queue?`)) {
                      bulkRemoveMutation.mutate(Array.from(selectedQueueIds))
                    }
                  }}
                  disabled={bulkRemoveMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border border-red-500/30 text-red-500 hover:bg-red-500/10 disabled:opacity-50"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Remove Selected ({selectedQueueIds.size})
                </button>
              )}
            </div>
          )}

          {queueFetching && !queueData ? (
            <div className="text-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground/50 mx-auto mb-2" />
              <p className="text-muted-foreground">Loading queue...</p>
            </div>
          ) : queue && queue.length > 0 ? (
            <>
              {queue.map((entry: any, idx: number) => {
                const progress = activeProgress[entry.video?.video_id]
                const state = getQueueEntryState(entry, progress)
                const video = entry.video
                const isSelected = selectedQueueIds.has(entry.id)

                return (
                  <div key={entry.id} className={`rounded-lg border bg-card overflow-hidden ${isSelected ? "ring-2 ring-primary/50" : ""}`}>
                    <div className="p-4">
                      <div className="flex items-start gap-3">
                        {/* Checkbox */}
                        <button
                          onClick={(e) => toggleQueueSelection(entry.id, idx, e.shiftKey)}
                          className="mt-1 shrink-0"
                        >
                          {isSelected ? (
                            <CheckSquare className="h-4 w-4 text-primary" />
                          ) : (
                            <Square className="h-4 w-4 text-muted-foreground" />
                          )}
                        </button>

                        {/* Thumbnail */}
                        {video?.thumbnail_url && (
                          <img
                            src={video.thumbnail_url}
                            alt=""
                            className="w-28 h-16 object-cover rounded shrink-0 bg-muted"
                          />
                        )}
                        <div className="flex-1 min-w-0">
                          {/* Title + state badge */}
                          <div className="flex items-center gap-2">
                            <p className="font-medium truncate flex-1">{video?.title || "Unknown"}</p>
                            <div className={`flex items-center gap-1 text-xs font-medium shrink-0 ${state.color}`}>
                              {state.icon}
                              {state.label}
                            </div>
                          </div>

                          {/* Meta row */}
                          <div className="flex items-center gap-3 text-xs text-muted-foreground mt-1 flex-wrap">
                            {video?.channel_name && (
                              <span className="font-medium text-foreground/70">{video.channel_name}</span>
                            )}
                            {video?.upload_date && (
                              <span>Uploaded: {video.upload_date}</span>
                            )}
                            {video?.duration != null && video.duration > 0 && (
                              <span>{formatDuration(video.duration)}</span>
                            )}
                            <span>Queued {timeSince(entry.queued_at) || formatDateTime(entry.queued_at)}</span>
                            {entry.started_at && (
                              <span>Started {timeSince(entry.started_at)}</span>
                            )}
                            {entry.priority !== 0 && (
                              <span className="text-primary">Priority: {entry.priority}</span>
                            )}
                          </div>

                          {/* Video ID link */}
                          <a
                            href={`https://www.youtube.com/watch?v=${video?.video_id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 mt-1"
                          >
                            {video?.video_id}
                            <ExternalLink className="h-3 w-3" />
                          </a>
                        </div>

                        {/* Remove button */}
                        <button
                          onClick={() => removeFromQueue.mutate(entry.id)}
                          className="p-1.5 hover:bg-accent rounded shrink-0"
                          title="Remove from queue"
                        >
                          <XCircle className="h-4 w-4 text-muted-foreground" />
                        </button>
                      </div>

                      {/* Progress bar */}
                      {(progress || entry.started_at) && (
                        <div className="mt-3">
                          <div className="h-3 bg-secondary rounded-full overflow-hidden relative">
                            <div
                              className={`h-full rounded-full transition-all duration-700 ease-out ${
                                progress?.percent >= 99.5
                                  ? "bg-gradient-to-r from-green-500 to-emerald-400"
                                  : progress?.percent > 0
                                    ? "bg-gradient-to-r from-blue-600 via-blue-500 to-cyan-400"
                                    : "bg-yellow-500/50 animate-pulse"
                              }`}
                              style={{ width: `${progress?.percent || (entry.started_at ? 2 : 0)}%` }}
                            />
                            {progress?.percent > 15 && (
                              <span className="absolute inset-0 flex items-center justify-center text-[10px] font-bold text-white drop-shadow-sm">
                                {progress.percent}%
                              </span>
                            )}
                          </div>
                          {progress ? (
                            <div className="flex items-center gap-4 text-xs text-muted-foreground mt-1.5">
                              {progress.percent < 15 && (
                                <span className="font-semibold text-foreground">{progress.percent}%</span>
                              )}
                              {progress.downloaded_bytes > 0 && (
                                <span>
                                  {formatBytes(progress.downloaded_bytes)}
                                  {progress.total_bytes > 0 && <> / {formatBytes(progress.total_bytes)}</>}
                                </span>
                              )}
                              <span>{formatSpeed(progress.smoothed_speed || progress.speed_bytes || 0)}</span>
                              {progress.eta_seconds > 0 && (
                                <span>ETA: {formatEta(progress.eta_seconds)}</span>
                              )}
                              {progress.elapsed_seconds > 0 && (
                                <span className="ml-auto">{formatElapsed(progress.elapsed_seconds)}</span>
                              )}
                            </div>
                          ) : entry.started_at ? (
                            <p className="text-xs text-yellow-500 mt-1">
                              Waiting for download to begin (rate limiting, extracting info, or acquiring PO token)...
                            </p>
                          ) : null}
                        </div>
                      )}

                      {/* Inline warning if video has previous errors */}
                      {video?.error_code && video?.status === "queued" && (
                        <div className="mt-2 flex items-start gap-2 rounded-md bg-yellow-500/10 border border-yellow-500/20 px-3 py-2">
                          <AlertTriangle className="h-4 w-4 text-yellow-500 shrink-0 mt-0.5" />
                          <div className="text-xs">
                            <span className="font-medium text-yellow-500">
                              Previous attempt failed: {ERROR_DESCRIPTIONS[video.error_code]?.label || video.error_code}
                            </span>
                            {video.retry_count > 0 && (
                              <span className="text-muted-foreground ml-1">(retry #{video.retry_count})</span>
                            )}
                            <p className="text-muted-foreground mt-0.5">
                              {ERROR_DESCRIPTIONS[video.error_code]?.tip || video.error_message}
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}

              {/* Queue Pagination */}
              {queueTotal > PAGE_SIZE && (
                <div className="flex items-center justify-between">
                  <p className="text-sm text-muted-foreground">
                    Showing {queuePage * PAGE_SIZE + 1}-{Math.min((queuePage + 1) * PAGE_SIZE, queueTotal)} of {queueTotal}
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => { setQueuePage(Math.max(0, queuePage - 1)); setSelectedQueueIds(new Set()) }}
                      disabled={queuePage === 0}
                      className="px-3 py-1 rounded-md border text-sm disabled:opacity-50"
                    >
                      Previous
                    </button>
                    <button
                      onClick={() => { setQueuePage(queuePage + 1); setSelectedQueueIds(new Set()) }}
                      disabled={(queuePage + 1) * PAGE_SIZE >= queueTotal}
                      className="px-3 py-1 rounded-md border text-sm disabled:opacity-50"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-12">
              <CheckCircle className="h-8 w-8 text-muted-foreground/50 mx-auto mb-2" />
              <p className="text-muted-foreground">Download queue is empty</p>
              <p className="text-xs text-muted-foreground/70 mt-1">New videos will appear here when channels are scanned</p>
            </div>
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

          {historyLoading && !history ? (
            <div className="text-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground/50 mx-auto mb-2" />
              <p className="text-muted-foreground">Loading...</p>
            </div>
          ) : history && history.items.length > 0 ? (
            <>
              <div className="space-y-2">
                {history.items.map((video: any) => {
                  const isExpanded = expandedRows.has(video.id)
                  const errorInfo = video.error_code ? ERROR_DESCRIPTIONS[video.error_code] : null
                  let errorDetails: any = null
                  if (video.error_details) {
                    try { errorDetails = JSON.parse(video.error_details) } catch { /* ignore */ }
                  }

                  return (
                    <div key={video.id} className="rounded-lg border bg-card overflow-hidden">
                      <div className="p-3">
                        <div className="flex items-start gap-3">
                          {/* Thumbnail */}
                          {video.thumbnail_url && (
                            <img
                              src={video.thumbnail_url}
                              alt=""
                              className="w-24 h-14 object-cover rounded shrink-0 bg-muted"
                            />
                          )}
                          <div className="flex-1 min-w-0">
                            {/* Title row */}
                            <div className="flex items-center gap-2">
                              <p className="font-medium text-sm truncate flex-1">{video.title}</p>
                              <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ${STATUS_COLORS[video.status] || ""}`}>
                                {video.status}
                              </span>
                            </div>

                            {/* Meta row */}
                            <div className="flex items-center gap-3 text-xs text-muted-foreground mt-1 flex-wrap">
                              {video.channel_name && (
                                <span className="font-medium text-foreground/70">{video.channel_name}</span>
                              )}
                              {video.upload_date && <span>{video.upload_date}</span>}
                              {video.duration != null && video.duration > 0 && <span>{formatDuration(video.duration)}</span>}
                              {video.file_size ? <span>{formatBytes(video.file_size)}</span> : null}
                              <span>{formatDateTime(video.downloaded_at || video.discovered_at)}</span>
                              {video.retry_count > 0 && (
                                <span className="text-yellow-500">Retries: {video.retry_count}</span>
                              )}
                            </div>

                            {/* Inline error summary for failed/skipped */}
                            {video.error_code && (
                              <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                                <AlertTriangle className={`h-3.5 w-3.5 shrink-0 ${errorInfo?.color || "text-red-400"}`} />
                                <span className={`text-xs font-medium ${errorInfo?.color || "text-red-400"}`}>
                                  {errorInfo?.label || video.error_code}
                                </span>
                                <span className="text-xs text-muted-foreground truncate">
                                  {video.error_message}
                                </span>
                                {(errorDetails || errorInfo) && tab !== "failed" && (
                                  <button
                                    onClick={() => toggleExpanded(video.id)}
                                    className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-0.5 ml-auto shrink-0"
                                  >
                                    {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                                    Details
                                  </button>
                                )}
                              </div>
                            )}
                          </div>

                          {/* Actions */}
                          <div className="flex items-center gap-1 shrink-0">
                            <a
                              href={`https://www.youtube.com/watch?v=${video.video_id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="p-1.5 hover:bg-accent rounded"
                              title="Open on YouTube"
                            >
                              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                            </a>
                            {(video.status === "failed" || video.status === "skipped") && (
                              <button
                                onClick={() => retryMutation.mutate(video.id)}
                                className="p-1.5 hover:bg-accent rounded"
                                title="Retry download"
                              >
                                <RotateCcw className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </div>
                        </div>

                        {/* Expanded error details */}
                        {(isExpanded || tab === "failed") && (
                          <div className="mt-3 rounded-md bg-muted/50 border p-3 text-xs space-y-2">
                            {errorDetails?.explanation && (
                              <div>
                                <p className="font-medium text-foreground mb-0.5">What happened</p>
                                <p className="text-muted-foreground">{errorDetails.explanation}</p>
                              </div>
                            )}
                            {errorDetails?.suggested_fix && (
                              <div>
                                <p className="font-medium text-foreground mb-0.5">Suggested fix</p>
                                <p className="text-muted-foreground">{errorDetails.suggested_fix}</p>
                              </div>
                            )}
                            {errorInfo?.tip && (
                              <div className="flex items-start gap-1.5 bg-blue-500/10 rounded px-2 py-1.5">
                                <Info className="h-3.5 w-3.5 text-blue-400 shrink-0 mt-0.5" />
                                <p className="text-blue-300">{errorInfo.tip}</p>
                              </div>
                            )}
                            {!errorDetails && errorInfo && (
                              <p className="text-muted-foreground">{errorInfo.tip}</p>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Showing {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, history.total)} of {history.total}
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
                    disabled={(page + 1) * PAGE_SIZE >= history.total}
                    className="px-3 py-1 rounded-md border text-sm disabled:opacity-50"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          ) : (
            <div className="text-center py-12">
              <p className="text-muted-foreground">
                {tab === "failed" ? "No failed downloads" : "No downloads found"}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
