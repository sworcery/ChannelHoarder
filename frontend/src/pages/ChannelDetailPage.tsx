import { useParams, useNavigate } from "react-router-dom"
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { cn, formatDate, formatBytes, formatDuration } from "@/lib/utils"
import { HEALTH_COLORS } from "@/lib/types"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { ConfirmDialog } from "@/components/ui/ConfirmDialog"
import { Dialog } from "@/components/ui/Dialog"
import { Button } from "@/components/ui/Button"
import { Badge, BADGE_TONES } from "@/components/ui/Badge"
import { Banner } from "@/components/ui/Banner"
import { EmptyState } from "@/components/ui/EmptyState"
import { Field } from "@/components/ui/Field"
import { DateTime } from "@/components/ui/DateTime"
import { StatusIcon } from "@/components/ui/StatusIcon"
import { DropdownMenu, DropdownItem, DropdownSeparator } from "@/components/ui/DropdownMenu"
import {
  ArrowLeft,
  RefreshCw,
  Download,
  Trash2,
  Circle,
  Loader2,
  ExternalLink,
  RotateCcw,
  FolderInput,
  X,
  CheckSquare,
  Square,
  Search,
  ChevronDown,
  ChevronRight,
  SkipForward,
  Play,
  ListX,
  ListOrdered,
  Bookmark,
  BookmarkX,
  MoreVertical,
  RefreshCcw,
  FileX,
  FileEdit,
  Tv,
  Pencil,
  CalendarDays,
} from "lucide-react"
import { useState, useCallback, useMemo } from "react"

export default function ChannelDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const channelId = Number(id)
  const [statusFilter, setStatusFilter] = useState("")
  const [typeFilter, setTypeFilter] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [editModalOpen, setEditModalOpen] = useState(false)
  const [editDownloadDir, setEditDownloadDir] = useState<string | null>(null)
  const [editMinDuration, setEditMinDuration] = useState<string | null>(null)
  const [editFromDate, setEditFromDate] = useState<string | null>(null)
  const [editRetention, setEditRetention] = useState<string | null>(null)
  const [retentionConfirm, setRetentionConfirm] = useState<{ days: number; count: number; bytes: number } | null>(null)
  const [retentionChecking, setRetentionChecking] = useState(false)

  const saveRetention = async () => {
    if (editRetention === null) return
    const raw = editRetention.trim()
    const days = raw === "" ? null : parseInt(raw, 10)
    const current = channel?.retention_days ?? null
    // Only setting or lowering retention can delete existing files
    if (days !== null && (current === null || days < current)) {
      setRetentionChecking(true)
      try {
        const preview = await api.getRetentionPreview(channelId, days)
        if (preview.count > 0) {
          setRetentionConfirm({ days, count: preview.count, bytes: preview.total_bytes })
          return
        }
      } catch (e) {
        toast((e as Error).message, "error")
        return
      } finally {
        setRetentionChecking(false)
      }
    }
    updateMutation.mutate({ retention_days: days })
    setEditRetention(null)
  }
  const [editTitleFilter, setEditTitleFilter] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importFolder, setImportFolder] = useState("")
  const [importMatches, setImportMatches] = useState<any[] | null>(null)
  const [importSelected, setImportSelected] = useState<Set<number>>(new Set())
  const [importScanning, setImportScanning] = useState(false)
  const [importRunning, setImportRunning] = useState(false)
  const [descExpanded, setDescExpanded] = useState(false)
  const [shortsDeleteOpen, setShortsDeleteOpen] = useState(false)
  const [shortsToDelete, setShortsToDelete] = useState<any[] | null>(null)
  const [shortsLoading, setShortsLoading] = useState(false)
  const [renumberOpen, setRenumberOpen] = useState(false)
  const [renumberPreview, setRenumberPreview] = useState<any | null>(null)
  const [renumberLoading, setRenumberLoading] = useState(false)
  const [renumberApplying, setRenumberApplying] = useState(false)
  const [repairOpen, setRepairOpen] = useState(false)
  const [repairPreview, setRepairPreview] = useState<any | null>(null)
  const [repairLoading, setRepairLoading] = useState(false)
  const [repairApplying, setRepairApplying] = useState(false)

  const [expandedSeasons, setExpandedSeasons] = useState<Set<number>>(new Set())

  const [selectedVideoIds, setSelectedVideoIds] = useState<Set<number>>(new Set())
  const [lastSelectedIdx, setLastSelectedIdx] = useState<number | null>(null)
  const [fileConfirm, setFileConfirm] = useState<
    { kind: "bulk-delete"; ids: number[] } | { kind: "delete-file" | "redownload"; video: any } | null
  >(null)
  const [livestreamsConfirmOpen, setLivestreamsConfirmOpen] = useState(false)

  const { toast } = useToast()

  const { data: channel } = useQuery({
    queryKey: ["channel", channelId],
    queryFn: () => api.getChannel(channelId),
  })

  const { data: videosData, isLoading: videosLoading } = useQuery({
    queryKey: ["channel-videos", channelId, statusFilter, searchQuery],
    queryFn: () => api.getChannelVideos(channelId, {
      limit: 50000,
      status: statusFilter && !["monitored", "unmonitored"].includes(statusFilter) ? statusFilter : undefined,
      monitored: statusFilter === "monitored" ? true : statusFilter === "unmonitored" ? false : undefined,
      search: searchQuery || undefined,
    }),
    placeholderData: keepPreviousData,
  })

  const { data: appSettings } = useQuery({
    queryKey: ["app-settings"],
    queryFn: api.getSettings,
    staleTime: 60000,
  })
  const shortsGloballyEnabled = appSettings?.shorts_enabled === true || appSettings?.shorts_enabled === "true"
  const livestreamsGloballyEnabled = appSettings?.livestreams_enabled === true || appSettings?.livestreams_enabled === "true"

  const videos = videosData?.items || []
  const totalVideos = videosData?.total || 0

  const totalChannelSize = useMemo(() => videos.reduce((sum: number, v: any) => sum + (v.file_size || 0), 0), [videos])

  const filteredVideos = useMemo(() => {
    if (!typeFilter) return videos
    switch (typeFilter) {
      case "short": return videos.filter((v: any) => v.is_short)
      case "livestream": return videos.filter((v: any) => v.is_livestream)
      case "regular": return videos.filter((v: any) => !v.is_short && !v.is_livestream)
      default: return videos
    }
  }, [videos, typeFilter])

  const { seasonGroups, seasons, videoIndexMap } = useMemo(() => {
    const groups: Record<number, any[]> = {}
    const indexMap = new Map<number, number>()
    filteredVideos.forEach((v: any, i: number) => {
      if (!groups[v.season]) groups[v.season] = []
      groups[v.season].push(v)
      indexMap.set(v.id, i)
    })
    const sorted = Object.keys(groups).map(Number).sort((a, b) => b - a)
    return { seasonGroups: groups, seasons: sorted, videoIndexMap: indexMap }
  }, [filteredVideos])

  const invalidateVideos = () => {
    queryClient.invalidateQueries({ queryKey: ["channel", channelId] })
    queryClient.invalidateQueries({ queryKey: ["channel-videos", channelId] })
  }

  const scanMutation = useMutation({
    mutationFn: () => api.scanChannel(channelId),
    onSuccess: (data: any) => { invalidateVideos(); toast(data.message || "Scan complete") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const downloadAllMutation = useMutation({
    mutationFn: () => api.downloadAllChannel(channelId),
    onSuccess: (data: any) => { invalidateVideos(); toast(data.message || "All videos queued for download") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const deleteMutation = useMutation({
    mutationFn: (deleteFiles: boolean) => api.deleteChannel(channelId, deleteFiles),
    onSuccess: () => { navigate("/channels"); toast("Channel deleted") },
    onError: (e: Error) => toast(e.message, "error"),
  })
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [movingFiles, setMovingFiles] = useState(false)
  const [movePreview, setMovePreview] = useState<any>(null)
  const [movePreviewOpen, setMovePreviewOpen] = useState(false)
  const [detectCleanPreview, setDetectCleanPreview] = useState<any>(null)
  const [detectCleanOpen, setDetectCleanOpen] = useState(false)
  const [detectCleanLoading, setDetectCleanLoading] = useState(false)
  const [forceRescanOpen, setForceRescanOpen] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(() => {
    try { return localStorage.getItem("ch_show_advanced") === "true" } catch { return false }
  })
  const toggleAdvanced = useCallback(() => {
    setShowAdvanced(prev => {
      const next = !prev
      try { localStorage.setItem("ch_show_advanced", String(next)) } catch { /* ignore */ }
      return next
    })
  }, [])

  const retryMutation = useMutation({
    mutationFn: (videoId: number) => api.retryDownload(videoId),
    onSuccess: () => { invalidateVideos(); toast("Video queued for retry") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const downloadAnywayMutation = useMutation({
    mutationFn: (videoId: number) => api.downloadAnyway(videoId),
    onSuccess: () => { invalidateVideos(); toast("Video queued at the best available quality") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const toggleMonitorMutation = useMutation({
    mutationFn: ({ videoId, monitored }: { videoId: number; monitored: boolean }) =>
      api.toggleVideoMonitored(channelId, videoId, monitored),
    onSuccess: () => invalidateVideos(),
    onError: (e: Error) => toast(e.message, "error"),
  })

  const bulkMonitorMutation = useMutation({
    mutationFn: ({ monitored }: { monitored: boolean }) =>
      api.bulkMonitorVideos(channelId, Array.from(selectedVideoIds), monitored),
    onSuccess: (data: any) => { invalidateVideos(); setSelectedVideoIds(new Set()); toast(data.message) },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const deleteVideoMutation = useMutation({
    mutationFn: ({ videoId, deleteFiles }: { videoId: number; deleteFiles: boolean }) =>
      api.deleteVideo(channelId, videoId, deleteFiles),
    onSuccess: (data: any) => { invalidateVideos(); toast(data.message) },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const updateMutation = useMutation({
    mutationFn: (data: any) => api.updateChannel(channelId, data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["channel", channelId] }); toast("Channel updated") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const refreshMetadataMutation = useMutation({
    mutationFn: () => api.refreshChannelMetadata(channelId),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["channel", channelId] })
      toast(data.message || "Metadata refreshed")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const detectShortsMutation = useMutation({
    mutationFn: () => api.detectChannelShorts(channelId),
    onSuccess: (data: any) => { invalidateVideos(); toast(data.message) },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const deleteShortsMutation = useMutation({
    mutationFn: () => api.deleteChannelShorts(channelId),
    onSuccess: (data: any) => { invalidateVideos(); setShortsDeleteOpen(false); setShortsToDelete(null); toast(data.message) },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const bulkQueueMutation = useMutation({
    mutationFn: (ids: number[]) => api.bulkQueueVideos(channelId, ids),
    onSuccess: (data: any) => {
      invalidateVideos()
      setSelectedVideoIds(new Set())
      toast(`Queued ${data.queued} videos for download`)
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const bulkSkipMutation = useMutation({
    mutationFn: (ids: number[]) => api.bulkSkipVideos(channelId, ids),
    onSuccess: (data: any) => {
      invalidateVideos()
      setSelectedVideoIds(new Set())
      toast(`Skipped ${data.skipped} videos`)
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const bulkUnskipMutation = useMutation({
    mutationFn: (ids: number[]) => api.bulkUnskipVideos(channelId, ids),
    onSuccess: (data: any) => {
      invalidateVideos()
      setSelectedVideoIds(new Set())
      toast(`Unskipped ${data.unskipped} videos`)
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const bulkDeleteMutation = useMutation({
    mutationFn: ({ ids, deleteFiles }: { ids: number[]; deleteFiles: boolean }) =>
      api.bulkDeleteVideos(channelId, ids, deleteFiles),
    onSuccess: (data: any) => { invalidateVideos(); setSelectedVideoIds(new Set()); toast(data.message) },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const bulkReclassifyMutation = useMutation({
    mutationFn: ({ setShort, setLivestream }: { setShort?: boolean; setLivestream?: boolean }) =>
      api.bulkReclassifyVideos(channelId, Array.from(selectedVideoIds), setShort, setLivestream),
    onSuccess: (data: any) => {
      invalidateVideos()
      setSelectedVideoIds(new Set())
      toast(data.message)
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const bulkRenameMutation = useMutation({
    mutationFn: () => api.bulkRenameVideos(channelId, Array.from(selectedVideoIds)),
    onSuccess: (data: any) => {
      invalidateVideos()
      setSelectedVideoIds(new Set())
      toast(data.message)
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const toggleVideoSelection = useCallback((videoId: number, idx: number, shiftKey: boolean) => {
    setSelectedVideoIds((prev) => {
      const next = new Set(prev)
      if (shiftKey && lastSelectedIdx !== null) {
        const start = Math.min(lastSelectedIdx, idx)
        const end = Math.max(lastSelectedIdx, idx)
        for (let i = start; i <= end; i++) {
          if (filteredVideos[i]) next.add(filteredVideos[i].id)
        }
      } else {
        if (next.has(videoId)) next.delete(videoId)
        else next.add(videoId)
      }
      return next
    })
    setLastSelectedIdx(idx)
  }, [lastSelectedIdx, filteredVideos])

  const toggleSelectAll = () => {
    if (selectedVideoIds.size === filteredVideos.length) {
      setSelectedVideoIds(new Set())
    } else {
      setSelectedVideoIds(new Set(filteredVideos.map((v: any) => v.id)))
    }
  }

  const handleSearch = () => {
    setSearchQuery(searchInput)
    setSelectedVideoIds(new Set())
  }

  const handleStatusChange = (val: string) => {
    setStatusFilter(val)
    setSelectedVideoIds(new Set())
  }

  const handleTypeChange = (val: string) => {
    setTypeFilter(val)
    setSelectedVideoIds(new Set())
  }

  if (!channel) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const selectedCount = selectedVideoIds.size
  const isBulkLoading = bulkQueueMutation.isPending || bulkSkipMutation.isPending || bulkUnskipMutation.isPending || bulkMonitorMutation.isPending || bulkDeleteMutation.isPending || bulkReclassifyMutation.isPending || bulkRenameMutation.isPending

  // Close handlers for the dialogs that can't be dismissed while their work runs.
  const closeShortsDelete = () => { setShortsDeleteOpen(false); setShortsToDelete(null) }
  const shortsBusy = deleteShortsMutation.isPending

  return (
    <div className="space-y-6">
      {/* Back button */}
      <Button variant="ghost" size="sm" onClick={() => navigate("/channels")} className="-ml-3">
        <ArrowLeft className="h-4 w-4" /> Back to Channels
      </Button>

      {/* Banner + Channel Info */}
      <div className="rounded-lg border bg-card overflow-hidden">
        <div className="relative h-48 w-full">
          {channel.banner_url ? (
            <img src={channel.banner_url} alt="" className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full bg-gradient-to-br from-primary/20 via-primary/10 to-background" />
          )}
          <div className="absolute inset-0 bg-gradient-to-t from-card via-card/40 to-transparent" />
        </div>

        <div className="relative px-6 pb-5 -mt-16">
          <div className="flex items-end gap-4">
            {channel.thumbnail_url ? (
              <img src={channel.thumbnail_url} alt={channel.channel_name} className="h-24 w-24 rounded-full object-cover border-4 border-card flex-shrink-0 shadow-lg" />
            ) : (
              <div className="h-24 w-24 rounded-full bg-muted border-4 border-card flex items-center justify-center flex-shrink-0 shadow-lg">
                <Tv className="h-12 w-12 text-muted-foreground" />
              </div>
            )}
            <div className="flex-1 min-w-0 pb-1">
              <div className="flex items-center gap-2">
                <h1 className="text-2xl font-bold truncate">{channel.channel_name}</h1>
                {channel.platform && channel.platform !== "youtube" && (
                  <Badge tone="info" className="capitalize">{channel.platform}</Badge>
                )}
                <Circle className={`h-3 w-3 flex-shrink-0 fill-current ${HEALTH_COLORS[channel.health_status] || HEALTH_COLORS.unknown}`} />
                <Badge tone={channel.enabled ? "success" : "neutral"}>
                  {!channel.enabled ? "Not scanned automatically" : channel.auto_download ? "Scanning, auto-download" : "Scanning, manual queue"}
                </Badge>
              </div>
              <a href={channel.channel_url} target="_blank" rel="noopener noreferrer" className="text-sm text-muted-foreground hover:text-primary flex items-center gap-1 mt-0.5">
                {channel.channel_url} <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          </div>

          {channel.description && (
            <div className="mt-3">
              <p className={`text-sm text-muted-foreground ${descExpanded ? "" : "line-clamp-3"}`}>
                {channel.description}
              </p>
              {channel.description.length > 200 && (
                <Button variant="link" size="sm" onClick={() => setDescExpanded(!descExpanded)} className="mt-1">
                  {descExpanded ? "Show less" : "Show more"}
                </Button>
              )}
            </div>
          )}

          <div className="flex items-center gap-4 text-sm text-muted-foreground mt-3">
            <span>{channel.downloaded_count}/{channel.total_videos} videos downloaded</span>
            {totalChannelSize > 0 && (
              <>
                <span className="text-border">|</span>
                <span>{formatBytes(totalChannelSize)}</span>
              </>
            )}
            <span className="text-border">|</span>
            <span>Quality: {channel.quality}</span>
            <span className="text-border">|</span>
            <span>Last scan: <DateTime value={channel.last_scanned_at} /></span>
            {channel.next_scan_at && (
              <>
                <span className="text-border">|</span>
                <span>Next scan: <DateTime value={channel.next_scan_at} /></span>
              </>
            )}
          </div>

          {channel.last_error_code && (
            <Banner tone="danger" className="mt-3 px-3 py-2">
              Last error: {channel.last_error_code}
            </Banner>
          )}

          <div className="flex gap-2 flex-wrap mt-4">
            <Button
              onClick={() => scanMutation.mutate()}
              disabled={scanMutation.isPending}
            >
              <RefreshCw className={`h-4 w-4 ${scanMutation.isPending ? "animate-spin" : ""}`} />
              Scan
            </Button>
            <Button
              variant="primary"
              onClick={() => downloadAllMutation.mutate()}
              disabled={downloadAllMutation.isPending}
            >
              <Download className="h-4 w-4" />
              Download All
            </Button>
            <Button onClick={() => setEditModalOpen(true)}>
              <Pencil className="h-4 w-4" />
              Edit
            </Button>
            <DropdownMenu label="More actions" trigger={<MoreVertical className="h-4 w-4" />}>
              <DropdownItem onClick={() => refreshMetadataMutation.mutate()} disabled={refreshMetadataMutation.isPending}>
                <RotateCcw className="h-3.5 w-3.5" /> Refresh Metadata
              </DropdownItem>
              <DropdownItem onClick={() => { setImportOpen(true); setImportMatches(null); setImportFolder(""); setImportSelected(new Set()) }}>
                <FolderInput className="h-3.5 w-3.5" /> Import Existing
              </DropdownItem>
              <DropdownItem onClick={async () => {
                setRenumberOpen(true)
                setRenumberLoading(true)
                setRenumberPreview(null)
                try {
                  const data = await api.renumberPreview(channelId)
                  setRenumberPreview(data)
                } catch (e: any) {
                  toast(e.message, "error")
                  setRenumberOpen(false)
                } finally {
                  setRenumberLoading(false)
                }
              }}>
                <ListOrdered className="h-3.5 w-3.5" /> Fix Episode Numbers
              </DropdownItem>
              <DropdownItem onClick={async () => {
                setRepairOpen(true)
                setRepairLoading(true)
                setRepairPreview(null)
                try {
                  const data = await api.repairDatesPreview(channelId)
                  setRepairPreview(data)
                } catch (e: any) {
                  toast(e.message, "error")
                  setRepairOpen(false)
                } finally {
                  setRepairLoading(false)
                }
              }}>
                <CalendarDays className="h-3.5 w-3.5" /> Repair Dates
              </DropdownItem>
              <DropdownSeparator />
              <DropdownItem onClick={() => setDeleteDialogOpen(true)} variant="danger">
                <Trash2 className="h-3.5 w-3.5" /> Delete Channel
              </DropdownItem>
            </DropdownMenu>
          </div>
        </div>
      </div>

      {/* Episodes */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">
            Episodes
            <span className="text-sm font-normal text-muted-foreground ml-2">
              ({typeFilter ? `${filteredVideos.length} of ${totalVideos}` : totalVideos})
            </span>
          </h2>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-2 mb-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder="Search episodes..."
              aria-label="Search episodes"
              className="w-full pl-9 pr-3 py-1.5 rounded-md border bg-background text-sm"
            />
          </div>
          <Button onClick={handleSearch}>
            Search
          </Button>
          {searchQuery && (
            <Button variant="ghost" onClick={() => { setSearchInput(""); setSearchQuery("") }}>
              Clear
            </Button>
          )}
          <select
            value={statusFilter}
            onChange={(e) => handleStatusChange(e.target.value)}
            aria-label="Filter by status"
            className="px-2 py-1.5 rounded-md border bg-background text-sm"
          >
            <option value="">All Status</option>
            <option value="completed">Completed</option>
            <option value="pending">Pending</option>
            <option value="pending_review">Needs Review</option>
            <option value="queued">Queued</option>
            <option value="downloading">Downloading</option>
            <option value="failed">Failed</option>
            <option value="skipped">Skipped</option>
            <option value="monitored">Monitored</option>
            <option value="unmonitored">Unmonitored</option>
          </select>
          <select
            value={typeFilter}
            onChange={(e) => handleTypeChange(e.target.value)}
            aria-label="Filter by type"
            className="px-2 py-1.5 rounded-md border bg-background text-sm"
          >
            <option value="">All Types</option>
            <option value="regular">Regular</option>
            <option value="short">Shorts</option>
            <option value="livestream">Livestreams</option>
          </select>
        </div>

        {/* Bulk action bar */}
        {selectedCount > 0 && (
          <div className="flex items-center gap-2 mb-3 p-2 rounded-lg border bg-muted/50 flex-wrap">
            <span className="text-sm font-medium">{selectedCount} selected</span>
            <div className="h-4 w-px bg-border" />
            <Button variant="primary" size="sm" onClick={() => bulkQueueMutation.mutate([...selectedVideoIds])} disabled={isBulkLoading}>
              <Play className="h-3 w-3" /> Queue
            </Button>
            <Button size="sm" onClick={() => bulkSkipMutation.mutate([...selectedVideoIds])} disabled={isBulkLoading}>
              <SkipForward className="h-3 w-3" /> Skip
            </Button>
            <Button size="sm" onClick={() => bulkUnskipMutation.mutate([...selectedVideoIds])} disabled={isBulkLoading}>
              <RotateCcw className="h-3 w-3" /> Unskip
            </Button>
            <Button size="sm" onClick={() => bulkMonitorMutation.mutate({ monitored: true })} disabled={isBulkLoading}>
              <Bookmark className="h-3 w-3" /> Monitor
            </Button>
            <Button size="sm" onClick={() => bulkMonitorMutation.mutate({ monitored: false })} disabled={isBulkLoading}>
              <BookmarkX className="h-3 w-3" /> Unmonitor
            </Button>
            <div className="h-4 w-px bg-border" />
            <Button size="sm" onClick={() => bulkReclassifyMutation.mutate({ setShort: true, setLivestream: false })} disabled={isBulkLoading}>
              Mark Short
            </Button>
            <Button size="sm" onClick={() => bulkReclassifyMutation.mutate({ setLivestream: true, setShort: false })} disabled={isBulkLoading}>
              Mark Livestream
            </Button>
            <Button size="sm" onClick={() => bulkReclassifyMutation.mutate({ setShort: false, setLivestream: false })} disabled={isBulkLoading}>
              Mark Regular
            </Button>
            <div className="h-4 w-px bg-border" />
            <Button size="sm" onClick={() => bulkRenameMutation.mutate()} disabled={isBulkLoading} title="Rename selected files on disk to match the current naming template">
              <FileEdit className="h-3 w-3" /> Rename
            </Button>
            <div className="h-4 w-px bg-border" />
            <Button variant="danger-outline" size="sm" onClick={() => setFileConfirm({ kind: "bulk-delete", ids: [...selectedVideoIds] })} disabled={isBulkLoading}>
              <Trash2 className="h-3 w-3" /> Delete
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setSelectedVideoIds(new Set())}>
              <X className="h-3 w-3" /> Clear
            </Button>
            {isBulkLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
          </div>
        )}

        {videosLoading && !videosData ? (
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground mx-auto" />
        ) : filteredVideos.length > 0 ? (
          <>
            <div className="rounded-lg border overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="px-3 py-2 w-8">
                      <Button variant="ghost" size="icon" onClick={toggleSelectAll} aria-label="Select all episodes" className="!p-0">
                        {selectedCount === filteredVideos.length && filteredVideos.length > 0 ? (
                          <CheckSquare className="h-4 w-4 text-primary" />
                        ) : (
                          <Square className="h-4 w-4 text-muted-foreground" />
                        )}
                      </Button>
                    </th>
                    <th className="text-left px-3 py-2">#</th>
                    <th className="text-left px-3 py-2">Title</th>
                    <th className="text-left px-3 py-2">Date</th>
                    <th className="text-left px-3 py-2">Duration</th>
                    <th className="text-left px-3 py-2">Status</th>
                    <th className="text-left px-3 py-2">Size</th>
                    <th className="px-3 py-2"></th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {(() => {
                    return seasons.flatMap((season) => {
                      const seasonVideos = seasonGroups[season]
                      const isCollapsed = !expandedSeasons.has(season)
                      const downloaded = seasonVideos.filter((v: any) => v.status === "completed").length
                      const monitored = seasonVideos.filter((v: any) => v.monitored).length
                      const seasonSize = seasonVideos.reduce((sum: number, v: any) => sum + (v.file_size || 0), 0)
                      const pct = seasonVideos.length > 0 ? Math.round((downloaded / seasonVideos.length) * 100) : 0

                      const rows: React.ReactNode[] = []

                      rows.push(
                        <tr key={`season-${season}`} className="bg-muted/30">
                          <td colSpan={8} className="px-3 py-2">
                            <div className="flex items-center justify-between">
                              {/* Plain element on purpose: the whole season header is the expand/collapse click target, not a styled button. */}
                              <button
                                onClick={() => setExpandedSeasons(prev => {
                                  const next = new Set(prev)
                                  next.has(season) ? next.delete(season) : next.add(season)
                                  return next
                                })}
                                aria-expanded={!isCollapsed}
                                className="flex items-center gap-2 hover:text-foreground text-muted-foreground"
                              >
                                <ChevronDown className={`h-4 w-4 transition-transform ${isCollapsed ? "-rotate-90" : ""}`} />
                                <span className="font-semibold text-sm">Season {season}</span>
                                <span className="text-xs text-muted-foreground">
                                  {downloaded}/{seasonVideos.length} episodes
                                  {seasonSize > 0 && <> &middot; {formatBytes(seasonSize)}</>}
                                  {` · ${monitored} monitored`}
                                </span>
                              </button>
                              <div className="flex items-center gap-2">
                                <div className="w-24 h-1.5 bg-muted rounded-full overflow-hidden" title={`${pct}% downloaded`}>
                                  <div
                                    className="h-full bg-primary rounded-full transition-all"
                                    style={{ width: `${pct}%` }}
                                  />
                                </div>
                                <div className="flex items-center gap-1">
                                  {(() => {
                                    const seasonIds = seasonVideos.map((v: any) => v.id)
                                    const allSelected = seasonIds.every((id: number) => selectedVideoIds.has(id))
                                    return (
                                      <Button
                                        variant={allSelected ? "primary" : "outline"}
                                        size="sm"
                                        onClick={() => {
                                          setSelectedVideoIds(prev => {
                                            const next = new Set(prev)
                                            if (allSelected) {
                                              seasonIds.forEach((id: number) => next.delete(id))
                                            } else {
                                              seasonIds.forEach((id: number) => next.add(id))
                                            }
                                            return next
                                          })
                                        }}
                                        title={allSelected ? "Deselect all in this season" : "Select all in this season"}
                                      >
                                        {allSelected ? "Deselect" : "Select"}
                                      </Button>
                                    )
                                  })()}
                                  <Button
                                    size="sm"
                                    onClick={() => { api.monitorSeason(channelId, season, true).then(() => invalidateVideos()) }}
                                    title="Monitor all in this season"
                                  >
                                    Monitor
                                  </Button>
                                  <Button
                                    size="sm"
                                    onClick={() => { api.downloadMissingSeason(channelId, season).then((r) => { invalidateVideos(); toast(r.message) }) }}
                                    title="Download all monitored missing videos in this season"
                                  >
                                    Download Missing
                                  </Button>
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )

                      if (!isCollapsed) {
                        seasonVideos.forEach((video: any) => {
                          const globalIdx = videoIndexMap.get(video.id) ?? 0
                          const isSelected = selectedVideoIds.has(video.id)
                          rows.push(
                      <tr
                        key={video.id}
                        className={`hover:bg-muted/30 cursor-pointer ${isSelected ? "bg-primary/5 ring-1 ring-inset ring-primary/20" : ""}`}
                        onClick={(e) => toggleVideoSelection(video.id, globalIdx, e.shiftKey)}
                      >
                        <td className="px-3 py-2">
                          {isSelected ? (
                            <CheckSquare className="h-4 w-4 text-primary" />
                          ) : (
                            <Square className="h-4 w-4 text-muted-foreground" />
                          )}
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">
                          S{video.season}E{String(video.episode).padStart(3, "0")}
                        </td>
                        <td className="px-3 py-2 max-w-xs truncate" title={video.title}>
                          {video.title}
                          {video.is_short && <Badge tone="warning" className="ml-1.5 text-[10px]">SHORT</Badge>}
                          {video.is_livestream && <Badge tone="info" className="ml-1.5 text-[10px]">LIVE</Badge>}
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">{formatDate(video.upload_date)}</td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {video.duration ? formatDuration(video.duration) : "-"}
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-2">
                            <StatusIcon
                              status={video.status}
                              monitored={video.monitored}
                              qualityDownloaded={video.quality_downloaded}
                              targetQuality={channel.quality_cutoff || channel.quality}
                              errorCode={video.error_code}
                              errorMessage={video.error_message}
                            />
                            <span className="text-xs text-muted-foreground capitalize">{video.status}</span>
                          </div>
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {video.file_size ? formatBytes(video.file_size) : "-"}
                        </td>
                        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                          <div className="flex items-center gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => toggleMonitorMutation.mutate({ videoId: video.id, monitored: !video.monitored })}
                              aria-label={video.monitored ? "Monitored (click to unmonitor)" : "Unmonitored (click to monitor)"}
                              title={video.monitored ? "Monitored (click to unmonitor)" : "Unmonitored (click to monitor)"}
                            >
                              <Bookmark className={cn("h-3.5 w-3.5", video.monitored ? "fill-current text-primary" : "text-muted-foreground")} />
                            </Button>
                            <DropdownMenu label="Video actions" trigger={<MoreVertical className="h-3.5 w-3.5" />}>
                              {video.status === "failed" && (
                                <DropdownItem onClick={() => retryMutation.mutate(video.id)}>
                                  <RotateCcw className="h-3.5 w-3.5" /> Retry
                                </DropdownItem>
                              )}
                              {video.status === "failed" && video.error_code === "QUALITY_TOO_LOW" && (
                                <DropdownItem onClick={() => downloadAnywayMutation.mutate(video.id)}>
                                  <Download className="h-3.5 w-3.5" /> Download anyway
                                </DropdownItem>
                              )}
                              {(video.status === "completed" || video.status === "failed") && (
                                <DropdownItem onClick={() => setFileConfirm({ kind: "redownload", video })}>
                                  <RefreshCcw className="h-3.5 w-3.5" /> Re-download
                                </DropdownItem>
                              )}
                              {video.status === "completed" && video.file_path && (
                                <>
                                  <DropdownItem onClick={() => api.renameVideoFile(channelId, video.id).then((r) => { invalidateVideos(); toast(r.message) })}>
                                    <FileEdit className="h-3.5 w-3.5" /> Rename File
                                  </DropdownItem>
                                  <DropdownItem onClick={() => api.downloadVideoSubtitles(channelId, video.id).then((r) => toast(r.message)).catch((e: any) => toast(e.message, "error"))}>
                                    <Download className="h-3.5 w-3.5" /> Download Subtitles
                                  </DropdownItem>
                                  <DropdownItem onClick={() => setFileConfirm({ kind: "delete-file", video })} variant="danger">
                                    <FileX className="h-3.5 w-3.5" /> Delete File
                                  </DropdownItem>
                                </>
                              )}
                              <DropdownSeparator />
                              <DropdownItem onClick={() => api.toggleVideoShort(channelId, video.id, !video.is_short).then((r) => { invalidateVideos(); toast(r.message) }).catch((e: any) => toast(e.message, "error"))}>
                                <Circle className="h-3.5 w-3.5" /> {video.is_short ? "Unmark as Short" : "Mark as Short"}
                              </DropdownItem>
                              <DropdownItem onClick={() => api.toggleVideoLivestream(channelId, video.id, !video.is_livestream).then((r) => { invalidateVideos(); toast(r.message) }).catch((e: any) => toast(e.message, "error"))}>
                                <Circle className="h-3.5 w-3.5" /> {video.is_livestream ? "Unmark as Livestream" : "Mark as Livestream"}
                              </DropdownItem>
                              {video.status !== "downloading" && (
                                // Skip keeps any downloaded file on disk; "Delete File" above
                                // is the explicit, separate action for removing it.
                                <DropdownItem onClick={() => deleteVideoMutation.mutate({ videoId: video.id, deleteFiles: false })} variant="danger">
                                  <Trash2 className="h-3.5 w-3.5" /> Skip Episode
                                </DropdownItem>
                              )}
                            </DropdownMenu>
                          </div>
                        </td>
                      </tr>
                          )
                        })
                      }

                      return rows
                    })
                  })()}
                </tbody>
              </table>
            </div>

          </>
        ) : (
          <EmptyState
            title={searchQuery || statusFilter || typeFilter
              ? "No episodes match your filters."
              : "No videos found. Try scanning the channel."}
          />
        )}
      </div>

      {/* Edit Channel Dialog */}
      <Dialog
        open={editModalOpen}
        onClose={() => setEditModalOpen(false)}
        title="Edit Channel"
        size="lg"
        footer={<Button onClick={() => setEditModalOpen(false)}>Close</Button>}
      >
        <div className="space-y-6">
          {/* Download Settings */}
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Download Settings</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex items-end gap-1">
                <Field
                  className="flex-1"
                  label="Quality"
                  help={<HelpIcon text="Target quality for new downloads. Also used as the cutoff for upgrade searches." anchor="channel-management" />}
                >
                  <select
                    value={channel.quality}
                    onChange={(e) => updateMutation.mutate({ quality: e.target.value })}
                    className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
                  >
                    <option value="best">Best Available</option>
                    <option value="2160p">4K (2160p)</option>
                    <option value="1080p">1080p</option>
                    <option value="720p">720p</option>
                    <option value="480p">480p</option>
                  </select>
                </Field>
                <Button
                  size="sm"
                  onClick={() => api.upgradeQuality(channelId).then((r) => { invalidateVideos(); toast(r.message) })}
                  className="whitespace-nowrap py-1.5"
                  title="Re-queue completed videos below the target quality"
                >
                  Search Upgrades
                </Button>
              </div>
              <Field
                label="Minimum Quality"
                help={<HelpIcon text="Skip videos if the best available quality is below this threshold. Leave as 'None' to download regardless of quality." anchor="channel-management" />}
              >
                <select
                  value={channel.min_quality || ""}
                  onChange={(e) => updateMutation.mutate({ min_quality: e.target.value || null })}
                  className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
                >
                  <option value="">None (download any quality)</option>
                  <option value="2160p">4K (2160p)</option>
                  <option value="1080p">1080p</option>
                  <option value="720p">720p</option>
                  <option value="480p">480p</option>
                </select>
              </Field>
              <Field
                label="Preferred Codec"
                help={<HelpIcon text="Preferred video codec for this channel. H.264 is the most compatible option for devices like Apple TV, but on YouTube it is usually capped at 1080p, so choosing it can limit a 4K channel to 1080p. If the preferred codec is not available for a video, the best available format is downloaded instead. Nothing is re-encoded." anchor="channel-management" />}
              >
                <select
                  value={channel.preferred_codec || ""}
                  onChange={(e) => updateMutation.mutate({ preferred_codec: e.target.value || null })}
                  className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
                >
                  <option value="">Any (best quality)</option>
                  <option value="h264">H.264 / AVC (compatibility)</option>
                  <option value="vp9">VP9</option>
                </select>
              </Field>
              <div className="sm:col-span-2 flex items-end gap-1">
                <Field className="flex-1" label="Download Directory">
                  <input
                    type="text"
                    value={editDownloadDir ?? channel.download_dir ?? ""}
                    placeholder="/downloads (default)"
                    onChange={(e) => setEditDownloadDir(e.target.value)}
                    className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
                  />
                </Field>
                {editDownloadDir !== null && editDownloadDir !== (channel.download_dir ?? "") && (
                  <>
                    <Button
                      variant="primary"
                      size="sm"
                      className="py-1.5"
                      onClick={() => {
                        updateMutation.mutate({ download_dir: editDownloadDir || null })
                        setEditDownloadDir(null)
                      }}
                    >
                      Save
                    </Button>
                    <Button
                      size="sm"
                      className="whitespace-nowrap py-1.5"
                      loading={movingFiles}
                      onClick={async () => {
                        const targetDir = editDownloadDir || "/downloads"
                        setMovingFiles(true)
                        try {
                          const preview = await api.moveFilesPreview(channelId, targetDir)
                          if (preview.same_path) {
                            toast("Files are already in that directory")
                            return
                          }
                          setMovePreview(preview)
                          setMovePreviewOpen(true)
                        } catch (e: any) {
                          toast(e.message, "error")
                        } finally {
                          setMovingFiles(false)
                        }
                      }}
                      title="Preview and move existing files to the new directory"
                    >
                      {movingFiles ? "Loading..." : "Save & Move"}
                    </Button>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Start Date Filter */}
          <div className="flex items-end gap-1 max-w-xs">
            <Field
              label="Download From Date"
              help={<HelpIcon text="Only videos published on or after this date are downloaded. Applies to future scans. Already-queued videos are not affected. Leave empty to download all videos." anchor="channel-management" />}
            >
              <input
                type="date"
                min="2005-01-01"
                value={editFromDate ?? (channel.download_from_date || "")}
                onChange={(e) => setEditFromDate(e.target.value)}
                className="w-44 px-2 py-1.5 rounded-md border bg-background text-sm"
              />
            </Field>
            {editFromDate !== null && editFromDate !== (channel.download_from_date || "") && (
              <Button
                variant="primary"
                size="sm"
                className="py-1.5"
                onClick={() => {
                  updateMutation.mutate({ download_from_date: editFromDate || null })
                  setEditFromDate(null)
                }}
              >
                Save
              </Button>
            )}
          </div>

          {/* Minimum length */}
          <div className="flex items-end gap-1 max-w-[260px]">
            <Field
              label="Skip videos shorter than (seconds)"
              help={<HelpIcon text="Videos shorter than this are skipped. Separate from Shorts (60 seconds or less, or listed on the channel's Shorts tab). No default; leave empty to skip nothing." anchor="episode-management" />}
            >
              <input
                type="number"
                value={editMinDuration ?? (channel.min_video_duration || "")}
                placeholder=""
                onChange={(e) => setEditMinDuration(e.target.value)}
                className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
                min={0}
              />
            </Field>
            {editMinDuration !== null && editMinDuration !== String(channel.min_video_duration || "") && (
              <Button
                variant="primary"
                size="sm"
                className="py-1.5"
                onClick={() => {
                  updateMutation.mutate({ min_video_duration: Number(editMinDuration) || null })
                  setEditMinDuration(null)
                }}
              >
                Save
              </Button>
            )}
          </div>

          {/* Retention */}
          <div className="flex items-end gap-1 max-w-xs">
            <Field
              label="Delete videos after (days)"
              help={<HelpIcon text="Deletes the downloaded files this many days after each video was downloaded. The videos stay listed as skipped and will not download again, including after Force Rescan. Leave empty to keep videos forever." anchor="channel-management" />}
            >
              <input
                type="number"
                min="1"
                max="36500"
                value={editRetention ?? (channel.retention_days || "")}
                placeholder="Keep forever"
                onChange={(e) => setEditRetention(e.target.value)}
                className="w-32 px-2 py-1.5 rounded-md border bg-background text-sm"
              />
            </Field>
            {editRetention !== null && editRetention !== String(channel.retention_days || "") && (
              <Button
                variant="primary"
                size="sm"
                className="py-1.5"
                onClick={saveRetention}
                disabled={retentionChecking || !(editRetention.trim() === "" || (/^\d+$/.test(editRetention.trim()) && parseInt(editRetention, 10) >= 1 && parseInt(editRetention, 10) <= 36500))}
              >
                Save
              </Button>
            )}
          </div>

          {/* Title Filter */}
          <div>
            <div className="flex items-end gap-1">
              <Field
                className="flex-1"
                label="Title Filter"
                help={<HelpIcon text="Filter videos by title. Include mode downloads only matching videos. Exclude mode skips matching videos. Leave empty to download all. Works on future scans only." anchor="channel-management" />}
              >
                <input
                  type="text"
                  value={editTitleFilter ?? channel.title_filter ?? ""}
                  placeholder={channel.title_filter_is_regex ? "e.g. rust|ark\\s*raiders" : "e.g. Rust, Arc Raiders, Minecraft"}
                  onChange={(e) => setEditTitleFilter(e.target.value)}
                  className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
                />
              </Field>
              {editTitleFilter !== null && editTitleFilter !== (channel.title_filter ?? "") && (
                <Button
                  variant="primary"
                  size="sm"
                  className="py-1.5"
                  onClick={() => {
                    if (channel.title_filter_is_regex && editTitleFilter) {
                      try { new RegExp(editTitleFilter, "i") } catch {
                        toast("Invalid regular expression", "error")
                        return
                      }
                    }
                    updateMutation.mutate({ title_filter: editTitleFilter || null })
                    setEditTitleFilter(null)
                  }}
                >
                  Save
                </Button>
              )}
            </div>
            <div className="flex items-center gap-3 mt-2">
              {/* Custom on/off switches: the shared set has no Switch yet, so these stay hand-made. */}
              <label className="flex items-center gap-1.5 cursor-pointer">
                <button
                  type="button"
                  role="switch"
                  aria-checked={channel.title_filter_mode === "exclude"}
                  aria-label="Exclude mode"
                  onClick={() => updateMutation.mutate({ title_filter_mode: channel.title_filter_mode === "include" ? "exclude" : "include" })}
                  className={`relative inline-flex h-4 w-8 items-center rounded-full transition-colors ${channel.title_filter_mode === "exclude" ? "bg-orange-500" : "bg-primary"}`}
                >
                  <span className={`inline-block h-3 w-3 rounded-full bg-white transition-transform ${channel.title_filter_mode === "exclude" ? "translate-x-4" : "translate-x-0.5"}`} />
                </button>
                <span className="text-xs text-muted-foreground">{channel.title_filter_mode === "exclude" ? "Exclude mode" : "Include mode"}</span>
              </label>
              <label className="flex items-center gap-1.5 cursor-pointer">
                <button
                  type="button"
                  role="switch"
                  aria-checked={!!channel.title_filter_is_regex}
                  aria-label="Regex mode"
                  onClick={() => updateMutation.mutate({ title_filter_is_regex: !channel.title_filter_is_regex })}
                  className={`relative inline-flex h-4 w-8 items-center rounded-full transition-colors ${channel.title_filter_is_regex ? "bg-primary" : "bg-gray-300 dark:bg-gray-600"}`}
                >
                  <span className={`inline-block h-3 w-3 rounded-full bg-white transition-transform ${channel.title_filter_is_regex ? "translate-x-4" : "translate-x-0.5"}`} />
                </button>
                <span className="text-xs text-muted-foreground">Regex mode</span>
              </label>
            </div>
            <p className="text-xs text-muted-foreground mt-1.5">
              {channel.title_filter_mode === "exclude" ? (
                <>
                  <span className="font-medium">Exclude mode:</span> Skip videos whose title matches.{" "}
                  {channel.title_filter_is_regex ? (
                    <>
                      <span className="text-muted-foreground/70">Example: </span>
                      <code className="text-[11px] bg-muted px-1 rounded">shorts|compilation</code>{" "}skips videos with "shorts" or "compilation" in the title
                    </>
                  ) : (
                    <>
                      <span className="text-muted-foreground/70">Example: </span>
                      <code className="text-[11px] bg-muted px-1 rounded">shorts, compilation, clip</code>{" "}skips videos containing any of those words
                    </>
                  )}
                </>
              ) : channel.title_filter_is_regex ? (
                <>
                  <span className="font-medium">Include + Regex:</span> Uses regular expression matching (case-insensitive).{" "}
                  <span className="text-muted-foreground/70">Examples: </span>
                  <code className="text-[11px] bg-muted px-1 rounded">rust|minecraft</code>{" "}matches either word, {" "}
                  <code className="text-[11px] bg-muted px-1 rounded">rust(?!\s*bucket)</code>{" "}matches "Rust" but not "Rust Bucket"
                </>
              ) : (
                <>
                  <span className="font-medium">Include + Keyword:</span> Comma-separated list, matches if any keyword appears in the title (case-insensitive).{" "}
                  <span className="text-muted-foreground/70">Example: </span>
                  <code className="text-[11px] bg-muted px-1 rounded">Rust, Arc Raiders</code>{" "}downloads videos with "Rust" or "Arc Raiders" in the title
                </>
              )}
            </p>
          </div>

          {/* Monitoring */}
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Monitoring</p>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Auto-download"
                caption
                hint="New videos found by scans are queued automatically. When off, they're listed and you queue them yourself."
              >
                <Button
                  variant="ghost"
                  aria-pressed={!!channel.auto_download}
                  className={BADGE_TONES[channel.auto_download ? "success" : "neutral"]}
                  onClick={() => updateMutation.mutate({ auto_download: !channel.auto_download })}
                >
                  {channel.auto_download ? "On" : "Off"}
                </Button>
              </Field>
              <Field
                label="Scan this channel"
                caption
                hint="When off, the channel isn't scanned on schedule, by RSS checks or by Scan All. Scan Now still works."
              >
                <Button
                  variant="ghost"
                  aria-pressed={!!channel.enabled}
                  className={BADGE_TONES[channel.enabled ? "success" : "neutral"]}
                  onClick={() => updateMutation.mutate({ enabled: !channel.enabled })}
                >
                  {channel.enabled ? "On" : "Off"}
                </Button>
              </Field>
              <Field
                label="Download first"
                caption
                hint="Queued videos from this channel download before other channels' videos."
              >
                <Button
                  variant="ghost"
                  aria-pressed={!!channel.prioritize_downloads}
                  className={BADGE_TONES[channel.prioritize_downloads ? "success" : "neutral"]}
                  onClick={() => updateMutation.mutate({ prioritize_downloads: !channel.prioritize_downloads })}
                >
                  {channel.prioritize_downloads ? "On" : "Off"}
                </Button>
              </Field>
              <div role="group" aria-labelledby="scan-for-videos-label">
                <p id="scan-for-videos-label" className="mb-1 text-sm font-medium">Scan for Videos</p>
                <div className="flex items-center gap-2">
                  <Button
                    onClick={() => scanMutation.mutate()}
                    disabled={scanMutation.isPending}
                  >
                    <RefreshCw className={`h-3.5 w-3.5 ${scanMutation.isPending ? "animate-spin" : ""}`} />
                    Scan Now
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => setForceRescanOpen(true)}
                    title="Rebuild records of videos that are not downloaded and re-scan"
                  >
                    <RotateCcw className="h-3 w-3" />
                    Force Re-scan
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Last scan: <DateTime value={channel.last_scanned_at} />
                  {channel.next_scan_at && (
                    <> &middot; Next scan: <DateTime value={channel.next_scan_at} /></>
                  )}
                </p>
              </div>
            </div>
          </div>

          {/* Advanced toggle */}
          <div className="pt-3 border-t">
            <Button
              variant="ghost"
              size="sm"
              onClick={toggleAdvanced}
              aria-expanded={showAdvanced}
            >
              {showAdvanced ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              Advanced Options
            </Button>
          </div>

          {/* Shorts Management */}
          {showAdvanced && channel.platform === "youtube" && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">YouTube Shorts</p>
              <div className="space-y-3">
                {shortsGloballyEnabled ? (
                  <>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={channel.include_shorts}
                        onChange={(e) => updateMutation.mutate({ include_shorts: e.target.checked })}
                        className="rounded"
                      />
                      <span className="text-sm">Include shorts when downloading</span>
                      <HelpIcon text={"Videos of 60 seconds or less, or listed on the channel's Shorts tab."} anchor="episode-management" />
                    </label>
                    <p className="text-xs text-muted-foreground">
                      {channel.include_shorts
                        ? "Shorts (60 seconds or less, or listed on the channel's Shorts tab) will be included in downloads."
                        : "Shorts are excluded from downloads for this channel."}
                    </p>
                  </>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Shorts downloading is disabled globally. Enable it in Settings to allow per-channel configuration.
                  </p>
                )}
                <div className="flex items-center gap-2 flex-wrap">
                  <Button
                    size="sm"
                    loading={detectShortsMutation.isPending}
                    onClick={() => detectShortsMutation.mutate()}
                  >
                    {!detectShortsMutation.isPending && <Search className="h-3 w-3" />}
                    Detect Shorts
                  </Button>
                  <Button
                    size="sm"
                    loading={detectCleanLoading}
                    onClick={async () => {
                      setDetectCleanLoading(true)
                      try {
                        const preview = await api.detectCleanShortsPreview(channelId)
                        setDetectCleanPreview(preview)
                        setDetectCleanOpen(true)
                      } catch (e: any) {
                        toast(e.message, "error")
                      } finally {
                        setDetectCleanLoading(false)
                      }
                    }}
                  >
                    {!detectCleanLoading && <ListX className="h-3 w-3" />}
                    Detect & Clean
                  </Button>
                  <Button
                    variant="danger-outline"
                    size="sm"
                    loading={deleteShortsMutation.isPending}
                    onClick={async () => {
                      setShortsLoading(true)
                      setShortsDeleteOpen(true)
                      setShortsToDelete(null)
                      try {
                        const res = await api.getChannelShorts(channelId, "completed")
                        setShortsToDelete(res.items)
                      } catch (e: any) {
                        toast(e.message, "error")
                        setShortsDeleteOpen(false)
                      } finally {
                        setShortsLoading(false)
                      }
                    }}
                  >
                    {!deleteShortsMutation.isPending && <Trash2 className="h-3 w-3" />}
                    Delete Downloaded Shorts
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Livestreams Management  - all platforms: scans skip livestreams on any
              channel that hasn't opted in, so the toggle must be reachable everywhere */}
          {showAdvanced && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Livestreams</p>
              <div className="space-y-3">
                {livestreamsGloballyEnabled ? (
                  <>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={channel.include_livestreams}
                        onChange={(e) => updateMutation.mutate({ include_livestreams: e.target.checked })}
                        className="rounded"
                      />
                      <span className="text-sm">Include livestreams when downloading</span>
                      <HelpIcon text="Live streams and premieres from this channel." anchor="episode-management" />
                    </label>
                    <p className="text-xs text-muted-foreground">
                      {channel.include_livestreams
                        ? "Livestreams will be included in downloads."
                        : "Livestreams are excluded from downloads for this channel."}
                    </p>
                  </>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Livestream downloading is disabled globally. Enable it in Settings to allow per-channel configuration.
                  </p>
                )}
                <div className="flex items-center gap-2 flex-wrap">
                  <Button
                    size="sm"
                    onClick={() => api.detectChannelLivestreams(channelId).then((r) => { invalidateVideos(); toast(r.message) }).catch((e: any) => toast(e.message, "error"))}
                  >
                    <Search className="h-3 w-3" />
                    Detect Livestreams
                  </Button>
                  <Button
                    variant="danger-outline"
                    size="sm"
                    onClick={() => setLivestreamsConfirmOpen(true)}
                  >
                    <Trash2 className="h-3 w-3" />
                    Delete Downloaded Livestreams
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Subtitles */}
          {showAdvanced && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Subtitles</p>
              <Button
                size="sm"
                onClick={() => {
                  api.downloadChannelSubtitles(channelId).then((r) => toast(r.message)).catch((e: any) => toast(e.message, "error"))
                }}
                title="Download subtitles for all completed videos that don't have them yet"
              >
                <Download className="h-3 w-3" />
                Download Missing Subtitles
              </Button>
              <p className="text-xs text-muted-foreground mt-1">
                Fetches English subtitles and auto-generated captions for completed videos without existing subtitle files.
              </p>
            </div>
          )}
        </div>
      </Dialog>

      {/* Delete Shorts Confirmation Dialog. Can't be closed while the delete runs. */}
      <Dialog
        open={shortsDeleteOpen}
        onClose={() => { if (!shortsBusy) closeShortsDelete() }}
        closeOnEsc={!shortsBusy}
        closeOnBackdrop={!shortsBusy}
        showClose={!shortsBusy}
        size="md"
        title={
          <span className="flex items-center gap-2 text-danger">
            <Trash2 className="h-5 w-5" />
            Delete Downloaded Shorts
          </span>
        }
        footer={
          <>
            <Button onClick={closeShortsDelete} disabled={shortsBusy}>
              Cancel
            </Button>
            {shortsToDelete && shortsToDelete.length > 0 && (
              <Button
                variant="danger"
                onClick={() => deleteShortsMutation.mutate()}
                loading={shortsBusy}
              >
                {!shortsBusy && <Trash2 className="h-4 w-4" />}
                Delete {shortsToDelete.length} Short{shortsToDelete.length !== 1 ? "s" : ""}
              </Button>
            )}
          </>
        }
      >
        {shortsLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            <span className="ml-2 text-sm text-muted-foreground">Loading shorts...</span>
          </div>
        ) : shortsToDelete && shortsToDelete.length > 0 ? (
          <div className="space-y-3">
            <Banner tone="danger" title={`${shortsToDelete.length} downloaded short${shortsToDelete.length !== 1 ? "s" : ""} will be permanently deleted from disk and marked as skipped.`}>
              This action cannot be undone.
            </Banner>

            <div className="rounded-lg border overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="text-left px-3 py-2">Title</th>
                    <th className="text-left px-3 py-2 w-20">Duration</th>
                    <th className="text-right px-3 py-2 w-20">Size</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {shortsToDelete.map((short: any) => (
                    <tr key={short.id} className="hover:bg-muted/30">
                      <td className="px-3 py-2">
                        <p className="truncate max-w-[280px]" title={short.title}>{short.title}</p>
                        {short.file_path && (
                          <p className="text-[10px] text-muted-foreground truncate max-w-[280px]" title={short.file_path}>
                            {short.file_path}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground text-xs">
                        {short.duration ? formatDuration(short.duration) : "-"}
                      </td>
                      <td className="px-3 py-2 text-muted-foreground text-xs text-right">
                        {short.file_size ? formatBytes(short.file_size) : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {shortsToDelete.some((s: any) => s.file_size) && (
              <p className="text-xs text-muted-foreground text-right">
                Total size: {formatBytes(shortsToDelete.reduce((sum: number, s: any) => sum + (s.file_size || 0), 0))}
              </p>
            )}
          </div>
        ) : (
          <EmptyState
            title="No downloaded shorts found for this channel."
            text={'Try running "Detect Shorts" first to identify shorts by duration.'}
          />
        )}
      </Dialog>

      {/* Import Dialog */}
      <Dialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        size="lg"
        title={
          <span className="flex items-center gap-2">
            Import Existing Videos
            <HelpIcon text="Scan a folder and match files by title." anchor="importing-existing-videos" />
          </span>
        }
        footer={importMatches && importMatches.length > 0 ? (
          <>
            <p className="mr-auto self-center text-sm text-muted-foreground">{importSelected.size} of {importMatches.length} selected</p>
            <Button
              variant="primary"
              loading={importRunning}
              disabled={importSelected.size === 0}
              onClick={async () => {
                if (importSelected.size === 0) return
                setImportRunning(true)
                try {
                  const selectedMatches = importMatches
                    .filter((m: any) => importSelected.has(m.matched_video_id))
                    .map((m: any) => ({ file_path: m.file_path, matched_video_id: m.matched_video_id }))
                  const res = await api.importConfirm(channelId, selectedMatches)
                  toast(`Imported ${res.imported} videos${res.errors.length > 0 ? ` (${res.errors.length} errors)` : ""}`)
                  invalidateVideos()
                  queryClient.invalidateQueries({ queryKey: ["download-queue"] })
                  setImportOpen(false)
                } catch (e: any) {
                  toast(e.message, "error")
                } finally {
                  setImportRunning(false)
                }
              }}
            >
              {!importRunning && <FolderInput className="h-4 w-4" />}
              Import Selected ({importSelected.size})
            </Button>
          </>
        ) : undefined}
      >
        <div className="space-y-4">
          <div>
            <div className="flex items-end gap-2">
              <Field className="flex-1" label="Folder path (on server)">
                <input
                  type="text"
                  autoFocus
                  value={importFolder}
                  onChange={(e) => setImportFolder(e.target.value)}
                  placeholder="/path/to/existing/videos"
                  className="w-full px-3 py-2 rounded-md border bg-background text-sm"
                />
              </Field>
              <Button
                variant="primary"
                loading={importScanning}
                disabled={!importFolder.trim()}
                onClick={async () => {
                  if (!importFolder.trim()) return
                  setImportScanning(true)
                  setImportMatches(null)
                  try {
                    const res = await api.importScan(channelId, importFolder.trim())
                    setImportMatches(res.matches)
                    setImportSelected(new Set(res.matches.map((m: any) => m.matched_video_id)))
                    if (res.matches.length === 0) toast("No matches found. Try a different folder or lower the threshold.", "error")
                  } catch (e: any) {
                    toast(e.message, "error")
                  } finally {
                    setImportScanning(false)
                  }
                }}
              >
                Scan
              </Button>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Enter the path to a folder containing video files. Files will be matched by title against un-downloaded videos.
            </p>
          </div>

          {importMatches && importMatches.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-medium">{importMatches.length} matches found</p>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    if (importSelected.size === importMatches.length) setImportSelected(new Set())
                    else setImportSelected(new Set(importMatches.map((m: any) => m.matched_video_id)))
                  }}
                >
                  {importSelected.size === importMatches.length ? "Deselect All" : "Select All"}
                </Button>
              </div>
              <div className="rounded-lg border overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      <th className="px-3 py-2 w-8"></th>
                      <th className="text-left px-3 py-2">File</th>
                      <th className="text-left px-3 py-2">Matched Video</th>
                      <th className="text-right px-3 py-2">Confidence</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {importMatches.map((match: any) => {
                      const selected = importSelected.has(match.matched_video_id)
                      return (
                        <tr
                          key={match.matched_video_id}
                          className="hover:bg-muted/30 cursor-pointer"
                          onClick={() => {
                            setImportSelected((prev) => {
                              const next = new Set(prev)
                              if (next.has(match.matched_video_id)) next.delete(match.matched_video_id)
                              else next.add(match.matched_video_id)
                              return next
                            })
                          }}
                        >
                          <td className="px-3 py-2">
                            {selected ? (
                              <CheckSquare className="h-4 w-4 text-primary" />
                            ) : (
                              <Square className="h-4 w-4 text-muted-foreground" />
                            )}
                          </td>
                          <td className="px-3 py-2 max-w-[200px] truncate" title={match.file_name}>
                            {match.file_name}
                          </td>
                          <td className="px-3 py-2 max-w-[200px] truncate" title={match.video_title}>
                            <span className="text-muted-foreground mr-1">S{match.season}E{String(match.episode).padStart(3, "0")}</span>
                            {match.video_title}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <span className={`font-medium ${match.match_confidence >= 90 ? "text-success" : match.match_confidence >= 80 ? "text-warning" : "text-orange-700 dark:text-orange-300"}`}>
                              {match.match_confidence}%
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </Dialog>

      {/* Renumber Episodes Dialog. Can't be closed while the renumber is applying. */}
      <Dialog
        open={renumberOpen}
        onClose={() => { if (!renumberApplying) setRenumberOpen(false) }}
        closeOnEsc={!renumberApplying}
        closeOnBackdrop={!renumberApplying}
        showClose={!renumberApplying}
        size="lg"
        title="Fix Episode Numbers"
        footer={renumberPreview && renumberPreview.total_changes > 0 ? (
          <>
            <Button onClick={() => setRenumberOpen(false)} disabled={renumberApplying}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={renumberApplying}
              onClick={async () => {
                setRenumberApplying(true)
                try {
                  const result = await api.renumberConfirm(channelId)
                  toast(result.message)
                  invalidateVideos()
                  setRenumberOpen(false)
                } catch (e: any) {
                  toast(e.message, "error")
                } finally {
                  setRenumberApplying(false)
                }
              }}
            >
              {!renumberApplying && <ListOrdered className="h-4 w-4" />}
              Apply Changes
            </Button>
          </>
        ) : undefined}
      >
        {renumberLoading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin mr-2" />
            Checking episode numbering...
          </div>
        )}

        {renumberPreview && renumberPreview.total_changes === 0 && (
          <EmptyState
            title="All episodes are already numbered correctly."
            text="No changes needed."
          />
        )}

        {renumberPreview && renumberPreview.total_changes > 0 && (
          <div>
            <p className="text-sm text-muted-foreground mb-4">
              {renumberPreview.total_changes} episode{renumberPreview.total_changes !== 1 ? "s" : ""} will be renumbered
              (out of {renumberPreview.total_videos} total). Files on disk will be renamed to match.
            </p>
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted">
                  <tr>
                    <th className="text-left px-3 py-2">Title</th>
                    <th className="text-left px-3 py-2">Date</th>
                    <th className="text-left px-3 py-2">Current</th>
                    <th className="text-left px-3 py-2">New</th>
                  </tr>
                </thead>
                <tbody>
                  {renumberPreview.changes.map((c: any) => (
                    <tr key={c.video_id} className="border-t">
                      <td className="px-3 py-2 truncate max-w-[250px]" title={c.title}>{c.title}</td>
                      <td className="px-3 py-2 text-muted-foreground">{formatDate(c.upload_date)}</td>
                      <td className="px-3 py-2 text-danger">{c.old_episode}</td>
                      <td className="px-3 py-2 text-success">{c.new_episode}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Dialog>

      {/* Repair Dates Dialog. Can't be closed while the repair is applying. */}
      <Dialog
        open={repairOpen}
        onClose={() => { if (!repairApplying) setRepairOpen(false) }}
        closeOnEsc={!repairApplying}
        closeOnBackdrop={!repairApplying}
        showClose={!repairApplying}
        size="lg"
        title="Repair Upload Dates"
        footer={repairPreview && repairPreview.total_changes > 0 ? (
          <>
            <Button onClick={() => setRepairOpen(false)} disabled={repairApplying}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={repairApplying}
              onClick={async () => {
                setRepairApplying(true)
                try {
                  const result = await api.repairDatesConfirm(
                    channelId,
                    repairPreview.changes.map((c: any) => ({ video_id: c.video_id, source_id: c.source_id, new_date: c.new_date }))
                  )
                  invalidateVideos()
                  toast(result.message)
                  setRepairOpen(false)
                } catch (e: any) {
                  toast(e.message, "error")
                } finally {
                  setRepairApplying(false)
                }
              }}
            >
              {!repairApplying && <CalendarDays className="h-4 w-4" />}
              Apply Changes
            </Button>
          </>
        ) : undefined}
      >
        {repairLoading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin mr-2" />
            Checking upload dates...
          </div>
        )}

        {repairPreview && repairPreview.total_changes === 0 && (
          <div className="space-y-3">
            <EmptyState title={repairPreview.stopped_early ? "No wrong dates found so far." : "No wrong dates found."} />
            {repairPreview.stopped_early && (
              <Banner tone="warning">{repairPreview.message}</Banner>
            )}
          </div>
        )}

        {repairPreview && repairPreview.total_changes > 0 && (
          <div>
            <p className="text-sm text-muted-foreground mb-4">
              {repairPreview.total_changes} video{repairPreview.total_changes !== 1 ? "s" : ""} have a wrong upload date
              (out of {repairPreview.checked} checked). Episodes will be renumbered and files on disk renamed to match.
            </p>
            {repairPreview.stopped_early && (
              <Banner tone="warning" className="mb-4">{repairPreview.message}</Banner>
            )}
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted">
                  <tr>
                    <th className="text-left px-3 py-2">Title</th>
                    <th className="text-left px-3 py-2">Current</th>
                    <th className="text-left px-3 py-2">Corrected</th>
                    <th className="text-left px-3 py-2">Episode</th>
                  </tr>
                </thead>
                <tbody>
                  {repairPreview.changes.map((c: any) => (
                    <tr key={c.video_id} className="border-t">
                      <td className="px-3 py-2 truncate max-w-[250px]" title={c.title}>
                        {c.title}
                        {c.has_file && <FileEdit className="inline-block h-3 w-3 ml-1.5 text-muted-foreground" />}
                      </td>
                      <td className="px-3 py-2 text-danger">{formatDate(c.old_date)}</td>
                      <td className="px-3 py-2 text-success">{formatDate(c.new_date)}</td>
                      <td className="px-3 py-2 text-muted-foreground">{c.old_episode}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Dialog>

      {/* Delete Channel Dialog */}
      <Dialog
        open={deleteDialogOpen}
        onClose={() => setDeleteDialogOpen(false)}
        size="sm"
        title="Delete Channel"
        description={<>Are you sure you want to delete <strong>{channel.channel_name}</strong>?</>}
      >
        <div className="flex flex-col gap-2">
          <Button
            className="w-full"
            onClick={() => { deleteMutation.mutate(false); setDeleteDialogOpen(false) }}
            disabled={deleteMutation.isPending}
          >
            Delete channel only (keep files)
          </Button>
          <Button
            variant="danger"
            className="w-full"
            onClick={() => { deleteMutation.mutate(true); setDeleteDialogOpen(false) }}
            disabled={deleteMutation.isPending}
          >
            Delete channel and its downloaded videos
          </Button>
          <p className="text-xs text-muted-foreground">
            Other files in the channel folder are kept.
          </p>
          <Button variant="ghost" className="w-full" onClick={() => setDeleteDialogOpen(false)}>
            Cancel
          </Button>
        </div>
      </Dialog>

      {/* Move Preview Dialog */}
      <Dialog
        open={movePreviewOpen && !!movePreview}
        onClose={() => setMovePreviewOpen(false)}
        size="md"
        title="Move Files"
        footer={movePreview && (
          <>
            <Button onClick={() => setMovePreviewOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                const targetDir = editDownloadDir || "/downloads"
                setMovePreviewOpen(false)
                setMovingFiles(true)
                api.moveChannelFiles(channelId, targetDir).then((r) => {
                  invalidateVideos()
                  queryClient.invalidateQueries({ queryKey: ["channel", channelId] })
                  setEditDownloadDir(null)
                  toast(r.message)
                }).catch((e: any) => toast(e.message, "error")).finally(() => setMovingFiles(false))
              }}
            >
              Move {movePreview.file_count} Files
            </Button>
          </>
        )}
      >
        {movePreview && (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">From:</span>
              <span className="font-mono text-xs">{movePreview.source_dir}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">To:</span>
              <span className="font-mono text-xs">{movePreview.dest_dir}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Files to move:</span>
              <span className="font-semibold">{movePreview.file_count}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total size:</span>
              <span>{formatBytes(movePreview.total_size)}</span>
            </div>
            {movePreview.missing_count > 0 && (
              <p className="text-xs text-warning">
                {movePreview.missing_count} files in database but missing from disk (paths will be updated anyway)
              </p>
            )}
          </div>
        )}
      </Dialog>

      {/* Detect & Clean Shorts Dialog. Confirm closes the dialog and runs in the background, as before. */}
      <Dialog
        open={detectCleanOpen && !!detectCleanPreview}
        onClose={() => setDetectCleanOpen(false)}
        size="md"
        title="Detect & Clean Shorts"
        footer={detectCleanPreview && (
          <>
            <Button onClick={() => setDetectCleanOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setDetectCleanOpen(false)
                const ids: number[] = [
                  ...detectCleanPreview.new_shorts.map((s: any) => s.video_id),
                  ...(detectCleanPreview.existing_shorts ?? []).map((s: any) => s.video_id),
                ]
                api.detectCleanShortsConfirm(channelId, ids).then((r) => {
                  invalidateVideos()
                  queryClient.invalidateQueries({ queryKey: ["channel", channelId] })
                  toast(r.message)
                }).catch((e: any) => toast(e.message, "error"))
              }}
              disabled={detectCleanPreview.new_shorts_count === 0 && detectCleanPreview.files_to_delete === 0}
            >
              Detect & Clean
            </Button>
          </>
        )}
      >
        {detectCleanPreview && (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Threshold:</span>
              <span>{detectCleanPreview.threshold}s or shorter, or listed on the Shorts tab</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">New shorts to detect:</span>
              <span className="font-semibold">{detectCleanPreview.new_shorts_count}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Files to delete:</span>
              <span className="font-semibold text-danger">{detectCleanPreview.files_to_delete}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Disk space freed:</span>
              <span>{formatBytes(detectCleanPreview.disk_space_freed)}</span>
            </div>
            {detectCleanPreview.will_renumber && (
              <p className="text-xs text-muted-foreground">
                Episodes will be renumbered after removing shorts.
              </p>
            )}
            {detectCleanPreview.new_shorts_count > 0 && (
              <div className="mt-2 max-h-32 overflow-y-auto text-xs border rounded p-2">
                {detectCleanPreview.new_shorts.map((s: any) => (
                  <div key={s.video_id} className="flex justify-between py-0.5">
                    <span className="truncate mr-2">{s.title}</span>
                    <span className="text-muted-foreground whitespace-nowrap">{s.duration}s</span>
                  </div>
                ))}
              </div>
            )}
            {(detectCleanPreview.existing_shorts?.length ?? 0) > 0 && (
              <>
                <p className="text-xs text-muted-foreground mt-2">
                  Already-flagged shorts whose downloaded files will also be deleted. To keep one, cancel and use Unmark as Short on it first.
                </p>
                <div className="max-h-32 overflow-y-auto text-xs border rounded p-2">
                  {detectCleanPreview.existing_shorts.map((s: any) => (
                    <div key={s.video_id} className="py-0.5">
                      <div className="flex justify-between">
                        <span className="truncate mr-2">{s.title}</span>
                        <span className="text-muted-foreground whitespace-nowrap">{s.duration ?? "?"}s</span>
                      </div>
                      <div className={s.matches_rule ? "text-muted-foreground" : "text-warning"}>{s.reason}</div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </Dialog>

      {/* Force Re-scan Dialog. Confirm closes the dialog and runs in the background, as before. */}
      <Dialog
        open={forceRescanOpen}
        onClose={() => setForceRescanOpen(false)}
        size="sm"
        title="Force Re-scan"
        footer={
          <>
            <Button onClick={() => setForceRescanOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="warning"
              onClick={() => {
                setForceRescanOpen(false)
                api.forceRescan(channelId).then((r) => {
                  invalidateVideos()
                  queryClient.invalidateQueries({ queryKey: ["channel", channelId] })
                  toast(r.message)
                }).catch((e: any) => toast(e.message, "error"))
              }}
            >
              Force Re-scan
            </Button>
          </>
        }
      >
        <p className="text-sm text-muted-foreground mb-2">
          This will <strong>rebuild the records of videos that are not downloaded</strong> and re-scan the channel.
        </p>
        <p className="text-sm text-warning">
          Downloaded videos, their files, and videos removed by "Delete videos after" are kept as they are. Use this to recover from stuck channels.
        </p>
      </Dialog>
      <ConfirmDialog
        open={livestreamsConfirmOpen}
        title="Delete downloaded livestreams?"
        message="The downloaded files of this channel's livestreams and premieres will be permanently deleted from disk, and the videos marked as skipped."
        confirmLabel="Delete files"
        onConfirm={() => {
          setLivestreamsConfirmOpen(false)
          api.deleteChannelLivestreams(channelId)
            .then((r) => { invalidateVideos(); toast(r.message) })
            .catch((e: any) => toast(e.message, "error"))
        }}
        onCancel={() => setLivestreamsConfirmOpen(false)}
      />
      {(() => {
        if (!fileConfirm) return null
        const bulkIds = fileConfirm.kind === "bulk-delete" ? fileConfirm.ids : []
        const targets: any[] = fileConfirm.kind === "bulk-delete"
          ? videos.filter((v: any) => bulkIds.includes(v.id))
          : [fileConfirm.video]
        const withFiles = targets.filter((v: any) => v.file_path)
        const n = withFiles.length
        const size = formatBytes(withFiles.reduce((sum: number, v: any) => sum + (v.file_size || 0), 0))
        const plural = n === 1 ? "video" : "videos"
        const close = () => setFileConfirm(null)
        if (fileConfirm.kind === "redownload") {
          return (
            <ConfirmDialog
              open
              title="Re-download this video?"
              message={n > 0
                ? `"${fileConfirm.video.title}" (${size}) will be downloaded again. The current file is kept until the new download finishes, and kept if it fails.`
                : `"${fileConfirm.video.title}" will be downloaded again.`}
              confirmLabel="Re-download"
              onConfirm={() => {
                api.redownloadVideo(channelId, fileConfirm.video.id)
                  .then(() => { invalidateVideos(); toast("Re-queued the video for download") })
                  .catch((e: any) => toast(e.message, "error"))
                close()
              }}
              onCancel={close}
            />
          )
        }
        if (fileConfirm.kind === "delete-file") {
          return (
            <ConfirmDialog
              open
              title="Delete this file?"
              message={`The file of "${fileConfirm.video.title}" (${size}) will be permanently deleted from disk and the video goes back to pending.`}
              confirmLabel="Delete file"
              onConfirm={() => {
                api.deleteVideoFile(channelId, fileConfirm.video.id)
                  .then(() => { invalidateVideos(); toast("Deleted the file of 1 video") })
                  .catch((e: any) => toast(e.message, "error"))
                close()
              }}
              onCancel={close}
            />
          )
        }
        return (
          <ConfirmDialog
            open
            title={n > 0 ? `Delete the files of ${n} ${plural}?` : `Skip ${targets.length} ${targets.length === 1 ? "video" : "videos"}?`}
            message={n > 0
              ? `${targets.length} selected ${targets.length === 1 ? "video" : "videos"} will be marked skipped and removed from the download queue; ${n} ${n === 1 ? "file" : "files"} (${size}) will be permanently deleted from disk. This includes videos inside collapsed seasons.`
              : `${targets.length} selected ${targets.length === 1 ? "video" : "videos"} will be marked skipped and removed from the download queue. None of them has a downloaded file, so nothing is deleted from disk.`}
            confirmLabel={n > 0 ? "Delete files" : "Skip videos"}
            onConfirm={() => {
              bulkDeleteMutation.mutate({ ids: bulkIds, deleteFiles: true })
              close()
            }}
            onCancel={close}
          />
        )
      })()}
      <ConfirmDialog
        open={!!retentionConfirm}
        title={`Delete ${retentionConfirm?.count ?? 0} downloaded video${retentionConfirm?.count === 1 ? "" : "s"}?`}
        message={`${retentionConfirm?.count ?? 0} downloaded video${retentionConfirm?.count === 1 ? " is" : "s are"} already older than ${retentionConfirm?.days ?? 0} days (${formatBytes(retentionConfirm?.bytes ?? 0)}). Their files will be permanently deleted within the next 6 hours, and later videos ${retentionConfirm?.days ?? 0} days after they download.`}
        confirmLabel="Save and delete"
        onConfirm={() => {
          if (retentionConfirm) updateMutation.mutate({ retention_days: retentionConfirm.days })
          setRetentionConfirm(null)
          setEditRetention(null)
        }}
        onCancel={() => setRetentionConfirm(null)}
      />
    </div>
  )
}
