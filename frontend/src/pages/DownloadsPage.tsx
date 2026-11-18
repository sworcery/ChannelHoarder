import { useState, useEffect, useCallback, useRef, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatDate, formatBytes, formatDuration, formatSpeed, formatEta, formatElapsed, buildVideoUrl } from "@/lib/utils"
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
import { Input, Select } from "@/components/ui/Input"
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
  ExternalLink,
  CheckSquare,
  Square,
  MinusSquare,
  Zap,
  ArrowUpToLine,
} from "lucide-react"

/** One error code as the backend catalogue describes it (label and tip are plain words). */
interface ErrorCatalogEntry {
  label: string
  tip: string
  /** The tip for the Failed tab, where the automatic retries are used up. */
  failed_tip: string
  /** The suggested fix for the Failed tab (empty when the normal fix is right there too). */
  failed_fix: string
  explanation: string
  suggested_fix: string
  severity: string
  retryable: boolean
  /** The video itself is gone or blocked, so a retry never helps. */
  permanent: boolean
}
type ErrorCatalog = Record<string, ErrorCatalogEntry>

const fetchErrorCatalog = (): Promise<ErrorCatalog> => api.getErrorCatalog()
const retryFixableFailed = () => api.retryAllFailed({ retryableOnly: true })

const PLATFORM_NAMES: Record<string, string> = {
  youtube: "YouTube",
  rumble: "Rumble",
  twitch: "Twitch",
  dailymotion: "Dailymotion",
  vimeo: "Vimeo",
  odysee: "Odysee",
}

function platformName(platform: string | null | undefined): string {
  const key = (platform || "youtube").toLowerCase()
  return PLATFORM_NAMES[key] || key.charAt(0).toUpperCase() + key.slice(1)
}

/** "SOME_CODE" -> "Some code", for a code the catalogue does not know. */
function humanizeCode(code: string): string {
  const words = code.toLowerCase().replace(/_/g, " ")
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function errorLabel(catalog: ErrorCatalog | undefined, code: string | null | undefined): string {
  if (!code) return "Unknown error"
  return catalog?.[code]?.label || humanizeCode(code)
}

/** The group's tip with "{site}" filled in: the platform's name, or "The site" when mixed. */
function errorTip(
  catalog: ErrorCatalog | undefined,
  code: string | null | undefined,
  platforms: string[],
  failed = false,
): string {
  const entry = code ? catalog?.[code] : undefined
  return fillSite(failed ? entry?.failed_tip || entry?.tip : entry?.tip, platforms)
}

/** "{site}" is the platform's name, or "The site" when the group mixes platforms (tips start with it). */
function fillSite(raw: string | undefined, platforms: string[]): string {
  if (!raw) return ""
  const unique = Array.from(new Set(platforms.map((p) => (p || "youtube").toLowerCase())))
  const site = unique.length === 1 ? platformName(unique[0]) : "The site"
  return raw.replace(/\{site\}/g, site)
}

function errorColor(catalog: ErrorCatalog | undefined, code: string | null | undefined): string {
  switch (code ? catalog?.[code]?.severity : undefined) {
    case "info": return "text-muted-foreground"
    case "warning": return "text-warning"
    default: return "text-danger"
  }
}

/** The reason line under the "Queue paused" title, for each reason the API reports. */
function pauseExplanation(reason: string | null | undefined): ReactNode {
  if (reason === "disk_full") {
    return "The queue paused because the disk filled up. Once there's free space on the downloads volume, Resume appears here. Downloads in progress finish if they can."
  }
  if (reason === "cookies_expired") {
    return (
      <>
        Your cookies expired and the site is asking for sign-in. Upload fresh cookies in{" "}
        <Link to="/settings?tab=auth" className="underline">Settings &gt; Authentication</Link>
        ; the queue resumes by itself once they work. Downloads in progress finish.
      </>
    )
  }
  return "Paused by you. Nothing new starts until you press Resume. Downloads in progress finish."
}

type DownloadsTab = "queue" | "history" | "failed"

/** One row for the queue, history and failed lists: same padding, title size and layout. */
function DownloadRow({
  lead, thumbnail, title, status, meta, details, actions, footer, selected,
}: {
  lead?: ReactNode
  thumbnail?: string | null
  title: ReactNode
  status?: ReactNode
  meta?: ReactNode
  details?: ReactNode
  actions?: ReactNode
  footer?: ReactNode
  selected?: boolean
}) {
  return (
    <Card className={`overflow-hidden p-0 space-y-0 ${selected ? "ring-2 ring-primary/50" : ""}`}>
      <div className="p-4">
        <div className="flex flex-wrap items-start gap-3">
          {lead}
          {thumbnail && (
            <img
              src={thumbnail}
              alt=""
              className="hidden sm:block w-28 h-16 object-cover rounded shrink-0 bg-muted"
            />
          )}
          <div className="flex-1 min-w-[10rem]">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <p className="font-medium break-words sm:truncate flex-1 min-w-0 basis-full sm:basis-40">{title}</p>
              {status}
            </div>
            {(meta || actions) && (
              <div className="flex items-center gap-x-3 gap-y-1 text-xs text-muted-foreground mt-1 flex-wrap">
                {meta}
                {/* On phones the buttons sit on the meta line; from sm up they sit at the row's right edge */}
                {actions && <div className="ml-auto flex items-center gap-0.5 sm:hidden">{actions}</div>}
              </div>
            )}
            {details}
          </div>
          {actions && (
            <div className="hidden sm:flex flex-wrap items-center justify-end gap-0.5 sm:w-auto sm:shrink-0">{actions}</div>
          )}
        </div>
        {footer}
      </div>
    </Card>
  )
}

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
  const [moreGroups, setMoreGroups] = useState<Set<string>>(new Set())
  const [selectedQueueIds, setSelectedQueueIds] = useState<Set<number>>(new Set())
  const [lastSelectedIdx, setLastSelectedIdx] = useState<number | null>(null)
  const [confirm, setConfirm] = useState<{ title: string; message: string; confirmLabel: string; run: () => void } | null>(null)

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

  const { data: queueData, isFetching: queueFetching } = useQuery({
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

  const { data: errorCatalog } = useQuery({
    queryKey: ["error-catalog"],
    queryFn: fetchErrorCatalog,
    staleTime: Infinity,
  })

  // While paused for a full disk, Resume only shows once the dashboard's disk alert says space is back
  const { data: pauseAlerts } = useQuery({
    queryKey: ["dashboard-alerts"],
    queryFn: api.getAlerts,
    refetchInterval: 30000,
    staleTime: 10000,
    enabled: !!pauseStatus?.paused && pauseStatus?.reason === "disk_full",
  })
  const diskStillFull =
    !!pauseStatus?.paused && pauseStatus?.reason === "disk_full" &&
    (pauseAlerts ?? []).some((a) => a.id === "disk_full" && a.action.kind !== "resume_queue")

  // Failed videos per cause across all pages (the sets "Retry all" re-queues)
  const { data: failedCounts } = useQuery({
    queryKey: ["download-history", "failed-counts"],
    queryFn: api.getFailedCounts,
    staleTime: 10000,
    enabled: tab === "failed",
  })

  // Channels whose queued videos are held back (a move, file work, a scan, or a finishing download)
  const { data: heldChannels } = useQuery({
    queryKey: ["download-held-channels"],
    queryFn: api.getHeldChannels,
    refetchInterval: 15000,
    staleTime: 5000,
    enabled: tab === "queue",
  })
  const busyChannels = heldChannels ?? []
  const busyReasonById = new Map(busyChannels.map((c) => [c.id, c.reason]))

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
      queryClient.invalidateQueries({ queryKey: ["dashboard-alerts"] })
      toast("Queue paused")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const resumeMutation = useMutation({
    mutationFn: api.resumeQueue,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["download-paused"] })
      queryClient.invalidateQueries({ queryKey: ["dashboard-alerts"] })
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

  const retryFixableMutation = useMutation({
    mutationFn: retryFixableFailed,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["download-history"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast(data.message || "Failed downloads queued for retry")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const retryGroupMutation = useMutation({
    mutationFn: (code: string) => api.retryAllFailed({ errorCode: code }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["download-history"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast(data.message || "Failed downloads queued for retry")
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

  /** Ask before clearing: count what is waiting (downloads in progress stay). */
  const askClearQueue = async () => {
    // Clear removes every waiting row, whatever the search box shows, so count without the search.
    let total = queueTotal
    let inProgress = 0
    try {
      const [all, active] = await Promise.all([api.getQueue({ limit: 1 }), api.getActiveDownloads()])
      total = all.total
      inProgress = active.filter((e: any) => e.running).length
    } catch { /* fall back to the numbers on screen */ }
    const waiting = Math.max(0, total - inProgress)
    if (waiting === 0) {
      toast("Nothing is waiting in the queue")
      return
    }
    const searching = !!debouncedQueueSearch
    setConfirm({
      title: "Clear the queue?",
      message:
        (searching ? `Clear removes all ${waiting} waiting ${waiting === 1 ? "video" : "videos"}, not just the ones shown. ` : "") +
        `Remove ${waiting} waiting ${waiting === 1 ? "video" : "videos"} from the queue. ` +
        "They go back to pending and nothing is deleted; you can queue them again later. " +
        "Downloads in progress are left alone.",
      confirmLabel: "Clear Queue",
      run: () => clearQueueMutation.mutate(),
    })
  }

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
      badge: queueTotal > 0 ? <Badge tone="info" className="ml-1.5 px-1.5 py-0 align-middle">{queueTotal}</Badge> : undefined,
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
      return { label: "In progress", icon: <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />, color: "text-primary" }
    }
    if (entry.stuck && !progress) {
      return { label: "Stuck - remove it or download now", icon: <Clock className="h-3.5 w-3.5 text-danger" />, color: "text-danger" }
    }
    if (entry.started_at && !progress) {
      return { label: "Starting...", icon: <Clock className="h-3.5 w-3.5 text-warning animate-pulse" />, color: "text-warning" }
    }
    if (entry.started_at) {
      return { label: "In progress", icon: <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />, color: "text-primary" }
    }
    const busy = busyReasonById.get(entry.video?.channel_id)
    if (busy) {
      return { label: `Waiting: channel ${busy}`, icon: <Clock className="h-3.5 w-3.5 text-warning" />, color: "text-warning" }
    }
    if (entry.retry_after && new Date(entry.retry_after).getTime() > Date.now()) {
      return {
        label: entry.quality_attempts > 0 && entry.video?.error_code === "QUALITY_TOO_LOW"
          ? <>Waiting for higher quality, retrying <DateTime value={entry.retry_after} /></>
          : <>Retrying <DateTime value={entry.retry_after} /></>,
        icon: <Clock className="h-3.5 w-3.5 text-warning" />,
        color: "text-warning",
      }
    }
    return { label: "Queued", icon: <Clock className="h-3.5 w-3.5 text-muted-foreground" />, color: "text-muted-foreground" }
  }

  /** One history or failed row. */
  const renderHistoryRow = (video: any) => {
    const isExpanded = expandedRows.has(video.id)
    const code: string | null = video.error_code || null
    let errorDetails: any = null
    if (video.error_details) {
      try { errorDetails = JSON.parse(video.error_details) } catch { /* ignore */ }
    }
    const color = errorColor(errorCatalog, code)
    const platform = platformName(video.platform)
    const tip = errorTip(errorCatalog, code, [video.platform])
    const onFailed = tab === "failed"
    // On Failed the group banner already names the cause, so a row shows its own text only behind Details.
    const permanent = onFailed && !!(code && errorCatalog?.[code]?.permanent)
    const hasDetails = !!(
      errorDetails?.explanation || errorDetails?.suggested_fix || (!onFailed && tip) ||
      (onFailed && video.error_message) || permanent
    )
    const showDetails = hasDetails && isExpanded

    return (
      <DownloadRow
        key={video.id}
        thumbnail={video.thumbnail_url}
        title={video.title}
        status={
          <span className="inline-flex items-center gap-1 shrink-0">
            <StatusIcon
              status={video.status}
              monitored={true}
              errorCode={video.error_code}
              errorMessage={video.error_message}
            />
            <span className="text-xs text-muted-foreground capitalize">{video.status}</span>
          </span>
        }
        meta={
          <>
            {video.channel_name && <span className="font-medium text-foreground/70">{video.channel_name}</span>}
            {video.upload_date && <span>Uploaded {formatDate(video.upload_date)}</span>}
            {video.duration != null && video.duration > 0 && <span>{formatDuration(video.duration)}</span>}
            {video.file_size ? <span>{formatBytes(video.file_size)}</span> : null}
            {video.downloaded_at ? (
              <span>Downloaded <DateTime value={video.downloaded_at} /></span>
            ) : video.discovered_at ? (
              <span>Found <DateTime value={video.discovered_at} /></span>
            ) : null}
            {video.retry_count > 0 && <span className="text-warning">Retries: {video.retry_count}</span>}
          </>
        }
        details={
          <>
            {code && (!onFailed || hasDetails) && (
              <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                {!onFailed && <AlertTriangle className={`h-3.5 w-3.5 shrink-0 ${color}`} />}
                {!onFailed && (
                  <>
                    <span className={`text-xs font-medium ${color}`}>{errorLabel(errorCatalog, code)}</span>
                    <span className="text-xs text-muted-foreground truncate">{video.error_message}</span>
                  </>
                )}
                {hasDetails && (
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
            {showDetails && (
              <div className="mt-2 rounded-md bg-muted/50 border p-3 text-xs space-y-2">
                {onFailed && video.error_message && (
                  <p className="text-muted-foreground break-words">{video.error_message}</p>
                )}
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
                {!onFailed && tip && <p className="text-muted-foreground">{tip}</p>}
                {permanent && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => retryMutation.mutate(video.id)}
                    disabled={retryMutation.isPending}
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    Retry anyway
                  </Button>
                )}
              </div>
            )}
          </>
        }
        actions={
          <>
            <a
              href={buildVideoUrl(video.platform || "youtube", video.video_id)}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonClasses("ghost", "icon")}
              aria-label={`Open on ${platform}`}
              title={`Open on ${platform}`}
            >
              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
            </a>
            {(video.status === "failed" || video.status === "skipped") && !permanent && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => retryMutation.mutate(video.id)}
                disabled={retryMutation.isPending}
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Retry
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
          </>
        }
      />
    )
  }

  /** Failed rows grouped by cause (largest group first), one tip per group. */
  const failedGroups = (() => {
    if (tab !== "failed" || !history) return []
    const groups = new Map<string, any[]>()
    for (const v of history.items) {
      const key = v.error_code || "UNKNOWN"
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key)!.push(v)
    }
    return Array.from(groups.entries()).sort((a, b) => b[1].length - a[1].length)
  })()

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            Downloads
            <HelpIcon text="Pause stops new downloads; downloads in progress finish." anchor="downloads" />
          </span>
        }
        actions={
          tab === "queue" ? (
            <>
              {!isPaused && (
                <Button variant="warning" onClick={() => pauseMutation.mutate()} disabled={pauseMutation.isPending}>
                  <Pause className="h-4 w-4" />
                  Pause
                </Button>
              )}
              <Button
                variant="danger-outline"
                onClick={askClearQueue}
                disabled={clearQueueMutation.isPending}
              >
                <Trash2 className="h-4 w-4" />
                Clear Queue
              </Button>
            </>
          ) : undefined
        }
      />

      {/* Paused banner: why, and what fixes it */}
      {isPaused && (
        <Banner
          tone="warning"
          title="Queue paused"
          action={diskStillFull ? undefined : (
            <Button variant="success" size="sm" onClick={() => resumeMutation.mutate()} disabled={resumeMutation.isPending}>
              <Play className="h-3.5 w-3.5" />
              Resume
            </Button>
          )}
        >
          {pauseExplanation(pauseStatus?.reason)}
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
          {/* Channels holding their queued videos back (a hung move keeps its claim) */}
          {busyChannels.length > 0 && (
            <Banner tone="warning" title="Some channels are not starting downloads">
              <ul className="space-y-0.5">
                {busyChannels.map((c) => (
                  <li key={c.id}>
                    <Link to={`/channels/${c.id}`} className="font-medium underline">{c.name}</Link> {c.reason}.
                  </li>
                ))}
              </ul>
              <p className="mt-1">
                Their queued videos start when that finishes. If one has been stuck for a long time, open the channel
                to see what it is doing.
              </p>
            </Banner>
          )}

          {/* Queue summary */}
          {queueTotal > 0 && (
            <div className="flex items-center gap-4 text-sm text-muted-foreground px-1">
              <span>{queueTotal} in queue</span>
              {queue && (
                <span>{queue.filter((e: any) => e.running).length} in progress</span>
              )}
            </div>
          )}

          {/* Download order */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1">
            <label htmlFor="queue-order" className="text-sm text-muted-foreground">Download order</label>
            <div className="w-56">
              <Select
                id="queue-order"
                value={queueOrderMutation.isPending ? queueOrderMutation.variables : queueOrder}
                onChange={(e) => queueOrderMutation.mutate(e.target.value)}
                disabled={queueOrderMutation.isPending}
              >
                <option value="queued">Order added</option>
                <option value="newest">Newest videos first</option>
                <option value="oldest">Oldest videos first</option>
                <option value="shortest">Shortest first</option>
                <option value="longest">Longest first</option>
              </Select>
            </div>
            <span className="text-xs text-muted-foreground">
              Items you move to the top and channels set to download first always go first.
            </span>
          </div>

          {/* Search */}
          <div className="relative max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              type="text"
              aria-label="Search queue"
              placeholder="Search queue..."
              value={queueSearch}
              onChange={(e) => { setQueueSearch(e.target.value); setQueuePage(0) }}
              className="pl-9"
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
                  onClick={() => setConfirm({
                    title: "Remove selected videos?",
                    message:
                      `Remove ${selectedQueueIds.size} selected ${selectedQueueIds.size === 1 ? "video" : "videos"} from the queue. ` +
                      "They go back to pending. Downloads in progress are left alone.",
                    confirmLabel: "Remove",
                    run: () => bulkRemoveMutation.mutate(Array.from(selectedQueueIds)),
                  })}
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
                const prevCode: string | null = video?.error_code && video?.status === "queued" ? video.error_code : null

                return (
                  <DownloadRow
                    key={entry.id}
                    selected={isSelected}
                    lead={
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
                    }
                    thumbnail={video?.thumbnail_url}
                    title={video?.title || "Unknown"}
                    status={
                      <div className={`flex items-center gap-1 text-xs font-medium shrink-0 ${state.color}`}>
                        {state.icon}
                        {state.label}
                      </div>
                    }
                    meta={
                      <>
                        {video?.channel_name && (
                          <span className="font-medium text-foreground/70">{video.channel_name}</span>
                        )}
                        {video?.upload_date && (
                          <span>Uploaded {formatDate(video.upload_date)}</span>
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
                      </>
                    }
                    details={
                      <a
                        href={buildVideoUrl(video?.platform || "youtube", video?.video_id || "")}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 mt-1"
                      >
                        {video?.video_id}
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    }
                    actions={
                      <>
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
                        {!entry.started_at && entry.quality_attempts > 0 && entry.video?.error_code === "QUALITY_TOO_LOW" && entry.retry_after && (
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
                      </>
                    }
                    footer={
                      <>
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
                        {prevCode && (
                          <div className="mt-2 flex items-start gap-2 rounded-md bg-warning-soft border border-warning-border px-3 py-2">
                            <AlertTriangle className="h-4 w-4 text-warning shrink-0 mt-0.5" />
                            <div className="text-xs">
                              <span className="font-medium text-warning">
                                Previous attempt failed: {errorLabel(errorCatalog, prevCode)}
                              </span>
                              {video.retry_count > 0 && (
                                <span className="text-muted-foreground ml-1">(retry #{video.retry_count})</span>
                              )}
                              <p className="text-muted-foreground mt-0.5">
                                {errorTip(errorCatalog, prevCode, [video.platform]) || video.error_message}
                              </p>
                            </div>
                          </div>
                        )}
                      </>
                    }
                  />
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
              <Input
                type="text"
                aria-label={tab === "failed" ? "Search failed videos" : "Search history"}
                placeholder="Search videos..."
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(0) }}
                className="pl-9"
              />
            </div>
            {tab === "failed" && failedGroups.some(([c]) => errorCatalog?.[c]?.retryable) && (
              <Button variant="primary" onClick={() => retryFixableMutation.mutate()} disabled={retryFixableMutation.isPending}>
                <RotateCcw className="h-4 w-4" />
                Retry All Fixable
              </Button>
            )}
          </div>
          {tab === "failed" && failedGroups.some(([c]) => errorCatalog?.[c]?.retryable) && (
            <p className="text-xs text-muted-foreground">
              Retry All Fixable skips causes that need a change first, such as expired cookies or an old yt-dlp. Once
              you have made that change, use Retry all on its group.
            </p>
          )}

          {historyLoading && !history ? (
            <div className="text-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground/50 mx-auto mb-2" />
              <p className="text-muted-foreground">Loading...</p>
            </div>
          ) : history && history.items.length > 0 ? (
            <>
              {tab === "failed" ? (
                <div className="space-y-6">
                  {failedGroups.map(([code, items]) => {
                    const platforms = items.map((v: any) => v.platform)
                    const tip = errorTip(errorCatalog, code, platforms, true)
                    const entry = errorCatalog?.[code]
                    const explanation = fillSite(entry?.explanation, platforms)
                    const fix = fillSite(entry?.failed_fix || entry?.suggested_fix, platforms)
                    const total = Math.max(failedCounts?.[code] ?? 0, items.length)
                    const permanent = !!entry?.permanent
                    const showMore = moreGroups.has(code)
                    return (
                      <section key={code} className="space-y-2" aria-label={errorLabel(errorCatalog, code)}>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <h2 className="text-sm font-semibold">
                            {errorLabel(errorCatalog, code)}
                            <span className="ml-2 font-normal text-muted-foreground">
                              {total} {total === 1 ? "video" : "videos"}
                              {total > items.length && ` (${items.length} on this page)`}
                            </span>
                          </h2>
                          {!permanent && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => retryGroupMutation.mutate(code)}
                              disabled={retryGroupMutation.isPending}
                              title={debouncedSearch ? "Retries every failure of this cause; the search box is ignored" : undefined}
                            >
                              <RotateCcw className="h-3.5 w-3.5" />
                              Retry all {total}
                            </Button>
                          )}
                        </div>
                        {!permanent && debouncedSearch && (
                          <p className="text-xs text-muted-foreground">
                            The retry button ignores the search and re-queues all {total} failures of this cause.
                          </p>
                        )}
                        {(tip || explanation || fix) && (
                          <Banner tone="info" title={tip || undefined}>
                            {(explanation || fix) && (
                              <>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() =>
                                    setMoreGroups((prev) => {
                                      const next = new Set(prev)
                                      if (next.has(code)) next.delete(code)
                                      else next.add(code)
                                      return next
                                    })
                                  }
                                  aria-expanded={showMore}
                                  className="gap-0.5 px-1 py-0"
                                >
                                  {showMore ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                                  More
                                </Button>
                                {showMore && (
                                  <>
                                    {explanation && <p>{explanation}</p>}
                                    {fix && <p className="mt-1">{fix}</p>}
                                  </>
                                )}
                              </>
                            )}
                          </Banner>
                        )}
                        <div className="space-y-2">{items.map(renderHistoryRow)}</div>
                      </section>
                    )
                  })}
                </div>
              ) : (
                <div className="space-y-2">{history.items.map(renderHistoryRow)}</div>
              )}

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
        open={!!confirm}
        title={confirm?.title ?? "Confirm"}
        message={confirm?.message ?? ""}
        confirmLabel={confirm?.confirmLabel}
        onConfirm={() => {
          confirm?.run()
          setConfirm(null)
        }}
        onCancel={() => setConfirm(null)}
      />
    </div>
  )
}
