import { useState, useEffect, useCallback, useRef } from "react"
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatDate, formatBytes, formatDuration, formatSpeed, formatEta, formatElapsed, buildVideoUrl } from "@/lib/utils"
import { STATUS_COLORS } from "@/lib/types"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useDebounce } from "@/hooks/useDebounce"
import { useToast } from "@/components/ui/toaster"
import { ConfirmDialog } from "@/components/ui/ConfirmDialog"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { StatusIcon } from "@/components/ui/StatusIcon"
import { Badge } from "@/components/ui/Badge"
import { Banner } from "@/components/ui/Banner"
import { Button, buttonClasses } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { DateTime } from "@/components/ui/DateTime"
import { EmptyState } from "@/components/ui/EmptyState"
import { PageHeader } from "@/components/ui/PageHeader"
import { Tabs, readSavedTab, panelId, tabId, type TabItem } from "@/components/ui/Tabs"
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
  Zap,
  ArrowUpToLine,
} from "lucide-react"

/** Human-readable error descriptions shown inline */
const ERROR_DESCRIPTIONS: Record<string, { label: string; color: string; tip: string }> = {
  AUTH_EXPIRED: {
    label: "Authentication Required",
    color: "text-danger",
    tip: "YouTube is requiring sign-in. Upload fresh cookies in Settings > Authentication.",
  },
  RATE_LIMITED: {
    label: "Rate Limited",
    color: "text-warning",
    tip: "Too many requests. Will auto-retry with increased delay.",
  },
  GEO_BLOCKED: {
    label: "Geo-Blocked",
    color: "text-muted-foreground",
    tip: "Video not available in your region.",
  },
  VIDEO_UNAVAILABLE: {
    label: "Unavailable",
    color: "text-muted-foreground",
    tip: "Video was removed or made unavailable by YouTube.",
  },
  VIDEO_PRIVATE: {
    label: "Private",
    color: "text-muted-foreground",
    tip: "Video is set to private by the uploader.",
  },
  VIDEO_REMOVED: {
    label: "Removed",
    color: "text-muted-foreground",
    tip: "Video has been permanently removed from YouTube.",
  },
  NETWORK_ERROR: {
    label: "Network Error",
    color: "text-warning",
    tip: "Connection issue. Will auto-retry.",
  },
  YTDLP_OUTDATED: {
    label: "yt-dlp Outdated",
    color: "text-orange-700 dark:text-orange-300",
    tip: "Update yt-dlp in Settings > System.",
  },
  FFMPEG_ERROR: {
    label: "Processing Error",
    color: "text-orange-700 dark:text-orange-300",
    tip: "Video/audio merge failed. Will auto-retry.",
  },
  DISK_FULL: {
    label: "Disk Full",
    color: "text-danger",
    tip: "No disk space remaining. Free space and retry.",
  },
  PO_TOKEN_FAILURE: {
    label: "PO Token Failed",
    color: "text-orange-700 dark:text-orange-300",
    tip: "Token server issue. Check PO token server status in Settings.",
  },
  FORMAT_UNAVAILABLE: {
    label: "Format Unavailable",
    color: "text-warning",
    tip: "Requested quality not available. Try 'best' quality.",
  },
  AGE_RESTRICTED: {
    label: "Age Restricted",
    color: "text-warning",
    tip: "Requires cookies from a logged-in account.",
  },
  UNKNOWN: {
    label: "Unknown Error",
    color: "text-danger",
    tip: "Check diagnostics for details.",
  },
}

type DownloadsTab = "queue" | "history" | "failed"

export default function DownloadsPage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<DownloadsTab>(() =>
    readSavedTab("downloads_tab", ["queue", "history", "failed"] as const, "queue"),
  )
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
  const [confirmAction, setConfirmAction] = useState<(() => void) | null>(null)

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
        queryClient.invalidateQueries({ queryKey: ["channel-videos"] })
        queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] })
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

  const downloadAnywayMutation = useMutation({
    mutationFn: (videoId: number) => api.downloadAnyway(videoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["download-history"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast("Video queued at the best available quality")
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

  const downloadNowMutation = useMutation({
    mutationFn: (queueId: number) => api.downloadNow(queueId),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast(data.message || "Download started")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const moveToTopMutation = useMutation({
    mutationFn: (queueId: number) => api.moveQueueItemToTop(queueId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const { data: appSettings } = useQuery({
    queryKey: ["app-settings"],
    queryFn: api.getSettings,
  })
  const queueOrder = (appSettings?.download_queue_order as string) || "queued"

  const queueOrderMutation = useMutation({
    mutationFn: (order: string) => api.updateSettings({ download_queue_order: order }),
    onSuccess: (_data, order) => {
      queryClient.setQueryData(["app-settings"], (old: Record<string, unknown> | undefined) =>
        old ? { ...old, download_queue_order: order } : old)
      queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
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
          // Guard: the list may have shrunk (search/pagination) since lastSelectedIdx was set.
          if (queue[i]) next.add(queue[i].id)
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

  const tabs: readonly TabItem<DownloadsTab>[] = [
    {
      key: "queue",
      label: "Queue",
      badge: queueTotal > 0 ? <Badge tone="info" className="ml-1.5 px-1.5">{queueTotal}</Badge> : undefined,
    },
    { key: "history", label: "History" },
    { key: "failed", label: "Failed" },
  ]

  /** Determine what state label to show for a queue entry */
  const getQueueEntryState = (entry: any, progress: any) => {
    if (progress && progress.percent >= 99.5) {
      return { label: "Processing...", icon: <Loader2 className="h-3.5 w-3.5 animate-spin text-success" />, color: "text-success" }
    }
    if (progress && progress.percent > 0) {
      return { label: "Downloading", icon: <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />, color: "text-primary" }
    }
    if (entry.stuck && !progress) {
      return { label: "Stuck - remove it or download now", icon: <Clock className="h-3.5 w-3.5 text-danger" />, color: "text-danger" }
    }
    if (entry.started_at && !progress) {
      return { label: "Starting...", icon: <Clock className="h-3.5 w-3.5 text-warning animate-pulse" />, color: "text-warning" }
    }
    if (entry.started_at) {
      return { label: "Downloading", icon: <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />, color: "text-primary" }
    }
    if (entry.retry_after && new Date(entry.retry_after).getTime() > Date.now()) {
      return {
        label: entry.quality_attempts > 0
          ? <>Waiting for higher quality, retrying <DateTime value={entry.retry_after} /></>
          : <>Retrying <DateTime value={entry.retry_after} /></>,
        icon: <Clock className="h-3.5 w-3.5 text-warning" />,
        color: "text-warning",
      }
    }
    return { label: "Queued", icon: <Clock className="h-3.5 w-3.5 text-muted-foreground" />, color: "text-muted-foreground" }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            Downloads
            <HelpIcon text="Pause stops new downloads; in-progress downloads finish." anchor="downloads" />
          </span>
        }
        actions={
          <>
            {isPaused ? (
              <Button
                variant="success"
                onClick={() => resumeMutation.mutate()}
                disabled={resumeMutation.isPending}
              >
                <Play className="h-4 w-4" />
                Resume
              </Button>
            ) : (
              <Button variant="warning" onClick={() => pauseMutation.mutate()} disabled={pauseMutation.isPending}>
                <Pause className="h-4 w-4" />
                Pause
              </Button>
            )}
            <Button
              variant="danger-outline"
              onClick={() => setConfirmAction(() => () => clearQueueMutation.mutate())}
              disabled={clearQueueMutation.isPending}
            >
              <Trash2 className="h-4 w-4" />
              Clear Queue
            </Button>
          </>
        }
      />

      {/* Paused banner */}
      {isPaused && (
        <Banner tone="warning" title="Queue Paused">
          {pauseStatus?.reason === "disk_full"
            ? "Paused: disk full. Free space, then Resume."
            : "No new downloads will start. In-flight downloads will finish."}
        </Banner>
      )}

      {/* Tabs */}
      <Tabs
        tabs={tabs}
        value={tab}
        onChange={handleTabChange}
        storageKey="downloads_tab"
        label="Downloads sections"
        idPrefix="downloads"
      />

      {/* Queue Tab */}
      {tab === "queue" && (
        <div role="tabpanel" id={panelId("downloads", "queue")} aria-labelledby={tabId("downloads", "queue")} className="space-y-3">
          {/* Queue summary */}
          {queueTotal > 0 && (
            <div className="flex items-center gap-4 text-sm text-muted-foreground px-1">
              <span>{queueTotal} in queue</span>
              {queue && (
                <span>{queue.filter((e: any) => e.running).length} downloading</span>
              )}
            </div>
          )}

          {/* Download order */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1">
            <label htmlFor="queue-order" className="text-sm text-muted-foreground">Download order</label>
            <select
              id="queue-order"
              value={queueOrderMutation.isPending ? queueOrderMutation.variables : queueOrder}
              onChange={(e) => queueOrderMutation.mutate(e.target.value)}
              disabled={queueOrderMutation.isPending}
              className="px-2 py-1 text-sm rounded-md border bg-background"
            >
              <option value="queued">Order added</option>
              <option value="newest">Newest videos first</option>
              <option value="oldest">Oldest videos first</option>
              <option value="shortest">Shortest first</option>
              <option value="longest">Longest first</option>
            </select>
            <span className="text-xs text-muted-foreground">
              Items you move to the top and channels set to download first always go first.
            </span>
          </div>

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
              <Button variant="ghost" onClick={toggleSelectAll} className="text-muted-foreground hover:text-foreground">
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
              </Button>
              {selectedQueueIds.size > 0 && (
                <Button
                  variant="danger-outline"
                  onClick={() => setConfirmAction(() => () => bulkRemoveMutation.mutate(Array.from(selectedQueueIds)))}
                  disabled={bulkRemoveMutation.isPending}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Remove Selected ({selectedQueueIds.size})
                </Button>
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
                  <Card key={entry.id} className={`overflow-hidden p-0 space-y-0 ${isSelected ? "ring-2 ring-primary/50" : ""}`}>
                    <div className="p-4">
                      <div className="flex flex-wrap items-start gap-3">
                        {/* Position + Checkbox */}
                        <div className="flex flex-col items-center gap-0.5 shrink-0 mt-1">
                          <span className="text-[10px] font-mono text-muted-foreground w-5 text-center">
                            {queuePage * PAGE_SIZE + idx + 1}
                          </span>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="p-0"
                            aria-label={isSelected ? "Deselect video" : "Select video"}
                            aria-pressed={isSelected}
                            onClick={(e) => toggleQueueSelection(entry.id, idx, e.shiftKey)}
                          >
                            {isSelected ? (
                              <CheckSquare className="h-4 w-4 text-primary" />
                            ) : (
                              <Square className="h-4 w-4 text-muted-foreground" />
                            )}
                          </Button>
                        </div>

                        {/* Thumbnail */}
                        {video?.thumbnail_url && (
                          <img
                            src={video.thumbnail_url}
                            alt=""
                            className="hidden sm:block w-28 h-16 object-cover rounded shrink-0 bg-muted"
                          />
                        )}
                        <div className="flex-1 min-w-[10rem]">
                          {/* Title + state badge */}
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                            <p className="font-medium break-words sm:truncate flex-1 min-w-0 basis-40">{video?.title || "Unknown"}</p>
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
                              <span>Uploaded: {formatDate(video.upload_date)}</span>
                            )}
                            {video?.duration != null && video.duration > 0 && (
                              <span>{formatDuration(video.duration)}</span>
                            )}
                            <span>Queued <DateTime value={entry.queued_at} /></span>
                            {entry.started_at && (
                              <span>Started <DateTime value={entry.started_at} /></span>
                            )}
                            {entry.target_quality && (
                              <Badge tone="info" className="px-1.5">{entry.target_quality}</Badge>
                            )}
                            {entry.estimated_size && (
                              <span>{formatBytes(entry.estimated_size)}</span>
                            )}
                            {entry.priority !== 0 && (
                              <span className="text-primary">Priority: {entry.priority}</span>
                            )}
                          </div>

                          {/* Video ID link */}
                          <a
                            href={buildVideoUrl(video?.platform || "youtube", video?.video_id || "")}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 mt-1"
                          >
                            {video?.video_id}
                            <ExternalLink className="h-3 w-3" />
                          </a>
                        </div>

                        {/* Queue controls */}
                        <div className="flex flex-wrap items-center justify-end gap-0.5 w-full sm:w-auto sm:shrink-0">
                          {!entry.started_at && (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => moveToTopMutation.mutate(entry.id)}
                              title="Move to top"
                              aria-label="Move to top"
                            >
                              <ArrowUpToLine className="h-4 w-4 text-muted-foreground" />
                            </Button>
                          )}
                          {!entry.started_at && entry.quality_attempts > 0 && entry.retry_after && (
                            <Button
                              size="sm"
                              onClick={() => downloadAnywayMutation.mutate(video.id)}
                              title="Download at the best available quality"
                            >
                              Download anyway
                            </Button>
                          )}
                          {(!entry.started_at || entry.stuck) && (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => downloadNowMutation.mutate(entry.id)}
                              title="Download now"
                              aria-label="Download now"
                            >
                              <Zap className="h-4 w-4 text-success" />
                            </Button>
                          )}
                          {(!entry.started_at || entry.stuck) && (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => removeFromQueue.mutate(entry.id)}
                              title="Remove from queue"
                              aria-label="Remove from queue"
                            >
                              <XCircle className="h-4 w-4 text-muted-foreground" />
                            </Button>
                          )}
                        </div>
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
                            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground mt-1.5">
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
                            <p className="text-xs text-warning mt-1">
                              Waiting for download to begin (extracting info, rate limiting)...
                            </p>
                          ) : null}
                        </div>
                      )}

                      {/* Inline warning if video has previous errors */}
                      {video?.error_code && video?.status === "queued" && (
                        <div className="mt-2 flex items-start gap-2 rounded-md bg-warning-soft border border-warning-border px-3 py-2">
                          <AlertTriangle className="h-4 w-4 text-warning shrink-0 mt-0.5" />
                          <div className="text-xs">
                            <span className="font-medium text-warning">
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
                  </Card>
                )
              })}

              {/* Queue Pagination */}
              {queueTotal > PAGE_SIZE && (
                <div className="flex items-center justify-between">
                  <p className="text-sm text-muted-foreground">
                    Showing {queuePage * PAGE_SIZE + 1}-{Math.min((queuePage + 1) * PAGE_SIZE, queueTotal)} of {queueTotal}
                  </p>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      onClick={() => { setQueuePage(Math.max(0, queuePage - 1)); setSelectedQueueIds(new Set()) }}
                      disabled={queuePage === 0}
                    >
                      Previous
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => { setQueuePage(queuePage + 1); setSelectedQueueIds(new Set()) }}
                      disabled={(queuePage + 1) * PAGE_SIZE >= queueTotal}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <EmptyState
              icon={<CheckCircle />}
              title="Download queue is empty"
              text="New videos will appear here when channels are scanned"
            />
          )}
        </div>
      )}

      {/* History / Failed Tab */}
      {tab !== "queue" && (
        <div role="tabpanel" id={panelId("downloads", tab)} aria-labelledby={tabId("downloads", tab)} className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[180px] max-w-sm">
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
              <Button variant="primary" onClick={() => retryAllMutation.mutate()} disabled={retryAllMutation.isPending}>
                <RotateCcw className="h-4 w-4" />
                Retry All Failed
              </Button>
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
                    <Card key={video.id} className="overflow-hidden p-0 space-y-0">
                      <div className="p-3">
                        <div className="flex flex-wrap items-start gap-3">
                          {/* Thumbnail */}
                          {video.thumbnail_url && (
                            <img
                              src={video.thumbnail_url}
                              alt=""
                              className="hidden sm:block w-24 h-14 object-cover rounded shrink-0 bg-muted"
                            />
                          )}
                          <div className="flex-1 min-w-[10rem]">
                            {/* Title row */}
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                              <p className="font-medium text-sm break-words sm:truncate flex-1 min-w-0 basis-40">{video.title}</p>
                              <span className="inline-flex items-center gap-1 shrink-0">
                                <StatusIcon
                                  status={video.status}
                                  monitored={true}
                                  errorCode={video.error_code}
                                  errorMessage={video.error_message}
                                />
                                <span className="text-xs text-muted-foreground capitalize">{video.status}</span>
                              </span>
                            </div>

                            {/* Meta row */}
                            <div className="flex items-center gap-x-3 gap-y-1 text-xs text-muted-foreground mt-1 flex-wrap">
                              {video.channel_name && (
                                <span className="font-medium text-foreground/70">{video.channel_name}</span>
                              )}
                              {video.upload_date && <span>{formatDate(video.upload_date)}</span>}
                              {video.duration != null && video.duration > 0 && <span>{formatDuration(video.duration)}</span>}
                              {video.file_size ? <span>{formatBytes(video.file_size)}</span> : null}
                              <DateTime value={video.downloaded_at || video.discovered_at} />
                              {video.retry_count > 0 && (
                                <span className="text-warning">Retries: {video.retry_count}</span>
                              )}
                            </div>

                            {/* Inline error summary for failed/skipped */}
                            {video.error_code && (
                              <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                                <AlertTriangle className={`h-3.5 w-3.5 shrink-0 ${errorInfo?.color || "text-danger"}`} />
                                <span className={`text-xs font-medium ${errorInfo?.color || "text-danger"}`}>
                                  {errorInfo?.label || video.error_code}
                                </span>
                                <span className="text-xs text-muted-foreground truncate">
                                  {video.error_message}
                                </span>
                                {(errorDetails || errorInfo) && tab !== "failed" && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => toggleExpanded(video.id)}
                                    aria-expanded={isExpanded}
                                    className="ml-auto shrink-0 gap-0.5 px-1 py-0 text-muted-foreground hover:text-foreground"
                                  >
                                    {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                                    Details
                                  </Button>
                                )}
                              </div>
                            )}
                          </div>

                          {/* Actions */}
                          <div className="flex flex-wrap items-center justify-end gap-1 w-full sm:w-auto sm:shrink-0">
                            <a
                              href={buildVideoUrl(video.platform || "youtube", video.video_id)}
                              target="_blank"
                              rel="noopener noreferrer"
                              className={buttonClasses("ghost", "icon")}
                              aria-label={`Open on ${(video.platform || "youtube").charAt(0).toUpperCase() + (video.platform || "youtube").slice(1)}`}
                              title={`Open on ${(video.platform || "youtube").charAt(0).toUpperCase() + (video.platform || "youtube").slice(1)}`}
                            >
                              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                            </a>
                            {(video.status === "failed" || video.status === "skipped") && (
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => retryMutation.mutate(video.id)}
                                title="Retry download"
                                aria-label="Retry download"
                              >
                                <RotateCcw className="h-3.5 w-3.5" />
                              </Button>
                            )}
                            {video.status === "failed" && video.error_code === "QUALITY_TOO_LOW" && (
                              <Button
                                size="sm"
                                onClick={() => downloadAnywayMutation.mutate(video.id)}
                                title="Download at the best available quality"
                              >
                                Download anyway
                              </Button>
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
                              <div className="flex items-start gap-1.5 bg-info-soft rounded px-2 py-1.5">
                                <Info className="h-3.5 w-3.5 text-info shrink-0 mt-0.5" />
                                <p className="text-info">{errorInfo.tip}</p>
                              </div>
                            )}
                            {!errorDetails && errorInfo && (
                              <p className="text-muted-foreground">{errorInfo.tip}</p>
                            )}
                          </div>
                        )}
                      </div>
                    </Card>
                  )
                })}
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Showing {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, history.total)} of {history.total}
                </p>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0}>
                    Previous
                  </Button>
                  <Button size="sm" onClick={() => setPage(page + 1)} disabled={(page + 1) * PAGE_SIZE >= history.total}>
                    Next
                  </Button>
                </div>
              </div>
            </>
          ) : (
            <EmptyState title={tab === "failed" ? "No failed downloads" : "No downloads found"} />
          )}
        </div>
      )}
      <ConfirmDialog
        open={!!confirmAction}
        title="Confirm Action"
        message="Are you sure you want to proceed?"
        onConfirm={() => {
          confirmAction?.()
          setConfirmAction(null)
        }}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  )
}
