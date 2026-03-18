import { useParams, useNavigate } from "react-router-dom"
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatDate, formatDateTime, formatBytes } from "@/lib/utils"
import { STATUS_COLORS, HEALTH_COLORS } from "@/lib/types"
import { useToast } from "@/components/ui/toaster"
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
  ChevronLeft,
  ChevronRight,
  SkipForward,
  Play,
  ListX,
  Tv,
} from "lucide-react"
import { useState, useCallback } from "react"

const PAGE_SIZE = 50

export default function ChannelDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const channelId = Number(id)
  const [statusFilter, setStatusFilter] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [page, setPage] = useState(0)
  const [editDownloadDir, setEditDownloadDir] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importFolder, setImportFolder] = useState("")
  const [importMatches, setImportMatches] = useState<any[] | null>(null)
  const [importSelected, setImportSelected] = useState<Set<number>>(new Set())
  const [importScanning, setImportScanning] = useState(false)
  const [importRunning, setImportRunning] = useState(false)
  const [descExpanded, setDescExpanded] = useState(false)

  // Video multi-select
  const [selectedVideoIds, setSelectedVideoIds] = useState<Set<number>>(new Set())
  const [lastSelectedIdx, setLastSelectedIdx] = useState<number | null>(null)

  const { toast } = useToast()

  const { data: channel } = useQuery({
    queryKey: ["channel", channelId],
    queryFn: () => api.getChannel(channelId),
  })

  const { data: videosData, isLoading: videosLoading } = useQuery({
    queryKey: ["channel-videos", channelId, statusFilter, searchQuery, page],
    queryFn: () => api.getChannelVideos(channelId, {
      limit: PAGE_SIZE,
      skip: page * PAGE_SIZE,
      status: statusFilter || undefined,
      search: searchQuery || undefined,
    }),
    placeholderData: keepPreviousData,
  })

  const videos = videosData?.items || []
  const totalVideos = videosData?.total || 0
  const totalPages = Math.ceil(totalVideos / PAGE_SIZE)

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
    mutationFn: () => api.deleteChannel(channelId, false),
    onSuccess: () => { navigate("/channels"); toast("Channel deleted") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const retryMutation = useMutation({
    mutationFn: (videoId: number) => api.retryDownload(videoId),
    onSuccess: () => { invalidateVideos(); toast("Video queued for retry") },
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

  const toggleVideoSelection = useCallback((videoId: number, idx: number, shiftKey: boolean) => {
    setSelectedVideoIds((prev) => {
      const next = new Set(prev)
      if (shiftKey && lastSelectedIdx !== null) {
        const start = Math.min(lastSelectedIdx, idx)
        const end = Math.max(lastSelectedIdx, idx)
        for (let i = start; i <= end; i++) {
          if (videos[i]) next.add(videos[i].id)
        }
      } else {
        if (next.has(videoId)) next.delete(videoId)
        else next.add(videoId)
      }
      return next
    })
    setLastSelectedIdx(idx)
  }, [lastSelectedIdx, videos])

  const toggleSelectAll = () => {
    if (selectedVideoIds.size === videos.length) {
      setSelectedVideoIds(new Set())
    } else {
      setSelectedVideoIds(new Set(videos.map((v: any) => v.id)))
    }
  }

  const handleSearch = () => {
    setSearchQuery(searchInput)
    setPage(0)
    setSelectedVideoIds(new Set())
  }

  const handleStatusChange = (val: string) => {
    setStatusFilter(val)
    setPage(0)
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
  const isBulkLoading = bulkQueueMutation.isPending || bulkSkipMutation.isPending || bulkUnskipMutation.isPending

  return (
    <div className="space-y-6">
      {/* Back button */}
      <button onClick={() => navigate("/channels")} className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to Channels
      </button>

      {/* Banner + Channel Info */}
      <div className="rounded-lg border bg-card overflow-hidden">
        {/* Banner */}
        <div className="relative h-48 w-full">
          {channel.banner_url ? (
            <img
              src={channel.banner_url}
              alt=""
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="w-full h-full bg-gradient-to-br from-primary/20 via-primary/10 to-background" />
          )}
          <div className="absolute inset-0 bg-gradient-to-t from-card via-card/40 to-transparent" />
        </div>

        {/* Channel info overlapping banner */}
        <div className="relative px-6 pb-5 -mt-16">
          <div className="flex items-end gap-4">
            {channel.thumbnail_url ? (
              <img
                src={channel.thumbnail_url}
                alt={channel.channel_name}
                className="h-24 w-24 rounded-full object-cover border-4 border-card flex-shrink-0 shadow-lg"
              />
            ) : (
              <div className="h-24 w-24 rounded-full bg-muted border-4 border-card flex items-center justify-center flex-shrink-0 shadow-lg">
                <Tv className="h-12 w-12 text-muted-foreground" />
              </div>
            )}
            <div className="flex-1 min-w-0 pb-1">
              <div className="flex items-center gap-2">
                <h1 className="text-2xl font-bold truncate">{channel.channel_name}</h1>
                {channel.platform && channel.platform !== "youtube" && (
                  <span className="inline-flex px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300 capitalize">
                    {channel.platform}
                  </span>
                )}
                <Circle
                  className={`h-3 w-3 flex-shrink-0 fill-current ${HEALTH_COLORS[channel.health_status] || HEALTH_COLORS.unknown}`}
                />
              </div>
              <a
                href={channel.channel_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-muted-foreground hover:text-primary flex items-center gap-1 mt-0.5"
              >
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
                <button
                  onClick={() => setDescExpanded(!descExpanded)}
                  className="text-xs text-primary hover:underline mt-1"
                >
                  {descExpanded ? "Show less" : "Show more"}
                </button>
              )}
            </div>
          )}

          {/* Stats row */}
          <div className="flex items-center gap-4 text-sm text-muted-foreground mt-3">
            <span>{channel.downloaded_count}/{channel.total_videos} videos downloaded</span>
            <span className="text-border">|</span>
            <span>Quality: {channel.quality}</span>
            <span className="text-border">|</span>
            <span>Last scan: {formatDateTime(channel.last_scanned_at)}</span>
          </div>

          {channel.last_error_code && (
            <div className="flex items-center gap-2 px-3 py-2 mt-3 rounded-md bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 text-sm">
              Last error: {channel.last_error_code}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex gap-2 flex-wrap mt-4">
            <button
              onClick={() => scanMutation.mutate()}
              disabled={scanMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${scanMutation.isPending ? "animate-spin" : ""}`} />
              Scan
            </button>
            <button
              onClick={() => refreshMetadataMutation.mutate()}
              disabled={refreshMetadataMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
              title="Re-fetch thumbnail, banner, and description from the platform"
            >
              <RotateCcw className={`h-4 w-4 ${refreshMetadataMutation.isPending ? "animate-spin" : ""}`} />
              Refresh Metadata
            </button>
            <button
              onClick={() => { setImportOpen(true); setImportMatches(null); setImportFolder(""); setImportSelected(new Set()) }}
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent"
            >
              <FolderInput className="h-4 w-4" />
              Import Existing
            </button>
            <button
              onClick={() => downloadAllMutation.mutate()}
              disabled={downloadAllMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <Download className="h-4 w-4" />
              Download All
            </button>
            <button
              onClick={() => { if (confirm("Delete this channel?")) deleteMutation.mutate() }}
              className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border border-red-300 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Series Management */}
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <h2 className="text-sm font-semibold">Series Management</h2>

        {/* Download Settings */}
        <div>
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Download Settings</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Quality</label>
              <select
                value={channel.quality}
                onChange={(e) => updateMutation.mutate({ quality: e.target.value })}
                className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
              >
                <option value="best">Best</option>
                <option value="1080p">1080p</option>
                <option value="720p">720p</option>
                <option value="480p">480p</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Download Directory</label>
              <div className="flex gap-1">
                <input
                  type="text"
                  value={editDownloadDir ?? channel.download_dir ?? ""}
                  placeholder="/downloads (default)"
                  onChange={(e) => setEditDownloadDir(e.target.value)}
                  className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
                />
                {editDownloadDir !== null && editDownloadDir !== (channel.download_dir ?? "") && (
                  <button
                    onClick={() => {
                      updateMutation.mutate({ download_dir: editDownloadDir || null })
                      setEditDownloadDir(null)
                    }}
                    className="px-2 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
                  >
                    Save
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Monitoring */}
        <div>
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Monitoring</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Auto-Download</label>
              <button
                onClick={() => updateMutation.mutate({ enabled: !channel.enabled })}
                className={`px-3 py-1.5 rounded-md text-sm ${
                  channel.enabled
                    ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                    : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
                }`}
              >
                {channel.enabled ? "Active" : "Paused"}
              </button>
              <p className="text-xs text-muted-foreground mt-1">
                {channel.enabled
                  ? "New videos will be automatically queued for download."
                  : "New videos are discovered but not queued for download."}
              </p>
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Scan for Videos</label>
              <button
                onClick={() => scanMutation.mutate()}
                disabled={scanMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${scanMutation.isPending ? "animate-spin" : ""}`} />
                Scan Now
              </button>
              <p className="text-xs text-muted-foreground mt-1">
                Last scan: {formatDateTime(channel.last_scanned_at)}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Videos */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">
            Videos
            <span className="text-sm font-normal text-muted-foreground ml-2">
              ({totalVideos} total)
            </span>
          </h2>
        </div>

        {/* Search + Filter bar */}
        <div className="flex items-center gap-2 mb-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder="Search videos by title..."
              className="w-full pl-9 pr-3 py-1.5 rounded-md border bg-background text-sm"
            />
          </div>
          <button
            onClick={handleSearch}
            className="px-3 py-1.5 text-sm rounded-md border hover:bg-accent"
          >
            Search
          </button>
          {searchQuery && (
            <button
              onClick={() => { setSearchInput(""); setSearchQuery(""); setPage(0) }}
              className="px-2 py-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              Clear
            </button>
          )}
          <select
            value={statusFilter}
            onChange={(e) => handleStatusChange(e.target.value)}
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
          </select>
        </div>

        {/* Bulk action bar */}
        {selectedCount > 0 && (
          <div className="flex items-center gap-2 mb-3 p-2 rounded-lg border bg-muted/50">
            <span className="text-sm font-medium">{selectedCount} selected</span>
            <div className="h-4 w-px bg-border" />
            <button
              onClick={() => bulkQueueMutation.mutate([...selectedVideoIds])}
              disabled={isBulkLoading}
              className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <Play className="h-3 w-3" />
              Queue Selected
            </button>
            <button
              onClick={() => bulkSkipMutation.mutate([...selectedVideoIds])}
              disabled={isBulkLoading}
              className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-md border hover:bg-accent disabled:opacity-50"
            >
              <SkipForward className="h-3 w-3" />
              Skip Selected
            </button>
            <button
              onClick={() => bulkUnskipMutation.mutate([...selectedVideoIds])}
              disabled={isBulkLoading}
              className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-md border hover:bg-accent disabled:opacity-50"
            >
              <RotateCcw className="h-3 w-3" />
              Unskip Selected
            </button>
            <button
              onClick={() => setSelectedVideoIds(new Set())}
              className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-md text-muted-foreground hover:text-foreground"
            >
              <ListX className="h-3 w-3" />
              Clear Selection
            </button>
            {isBulkLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
          </div>
        )}

        {videosLoading && !videosData ? (
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground mx-auto" />
        ) : videos.length > 0 ? (
          <>
            <div className="rounded-lg border overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="px-3 py-2 w-8">
                      <button onClick={toggleSelectAll} className="hover:text-foreground text-muted-foreground">
                        {selectedCount === videos.length && videos.length > 0 ? (
                          <CheckSquare className="h-4 w-4 text-primary" />
                        ) : (
                          <Square className="h-4 w-4" />
                        )}
                      </button>
                    </th>
                    <th className="text-left px-3 py-2">#</th>
                    <th className="text-left px-3 py-2">Title</th>
                    <th className="text-left px-3 py-2">Date</th>
                    <th className="text-left px-3 py-2">Status</th>
                    <th className="text-left px-3 py-2">Size</th>
                    <th className="px-3 py-2"></th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {videos.map((video: any, idx: number) => {
                    const isSelected = selectedVideoIds.has(video.id)
                    return (
                      <tr
                        key={video.id}
                        className={`hover:bg-muted/30 cursor-pointer ${isSelected ? "bg-primary/5 ring-1 ring-inset ring-primary/20" : ""}`}
                        onClick={(e) => toggleVideoSelection(video.id, idx, e.shiftKey)}
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
                        <td className="px-3 py-2 max-w-xs truncate" title={video.title}>{video.title}</td>
                        <td className="px-3 py-2 text-muted-foreground">{formatDate(video.upload_date)}</td>
                        <td className="px-3 py-2">
                          <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[video.status] || ""}`}>
                            {video.status}
                          </span>
                          {video.error_code && (
                            <span className="ml-1 text-xs text-red-500">{video.error_code}</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {video.file_size ? formatBytes(video.file_size) : "-"}
                        </td>
                        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                          {video.status === "failed" && (
                            <button
                              onClick={() => retryMutation.mutate(video.id)}
                              className="p-1 hover:bg-accent rounded"
                              title="Retry"
                            >
                              <RotateCcw className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="flex items-center justify-between mt-3">
                <p className="text-sm text-muted-foreground">
                  Showing {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, totalVideos)} of {totalVideos}
                </p>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => { setPage(p => Math.max(0, p - 1)); setSelectedVideoIds(new Set()) }}
                    disabled={page === 0}
                    className="p-1.5 rounded-md border hover:bg-accent disabled:opacity-30"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <span className="px-3 text-sm">
                    Page {page + 1} of {totalPages}
                  </span>
                  <button
                    onClick={() => { setPage(p => Math.min(totalPages - 1, p + 1)); setSelectedVideoIds(new Set()) }}
                    disabled={page >= totalPages - 1}
                    className="p-1.5 rounded-md border hover:bg-accent disabled:opacity-30"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>
              </div>
            )}
          </>
        ) : (
          <p className="text-center py-8 text-muted-foreground">
            {searchQuery || statusFilter
              ? "No videos match your search/filter."
              : "No videos found. Try scanning the channel."}
          </p>
        )}
      </div>

      {/* Import Modal */}
      {importOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setImportOpen(false)}>
          <div className="bg-card border rounded-lg shadow-lg w-full max-w-2xl max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b">
              <h3 className="text-lg font-semibold">Import Existing Videos</h3>
              <button onClick={() => setImportOpen(false)} className="p-1 hover:bg-accent rounded">
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="p-4 space-y-4 overflow-y-auto flex-1">
              {/* Scan input */}
              <div>
                <label className="block text-sm font-medium mb-1">Folder path (on server)</label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={importFolder}
                    onChange={(e) => setImportFolder(e.target.value)}
                    placeholder="/path/to/existing/videos"
                    className="flex-1 px-3 py-2 rounded-md border bg-background text-sm"
                  />
                  <button
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
                    disabled={importScanning || !importFolder.trim()}
                    className="px-4 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                  >
                    {importScanning ? <Loader2 className="h-4 w-4 animate-spin" /> : "Scan"}
                  </button>
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Enter the path to a folder containing video files. Files will be matched by title against un-downloaded videos.
                </p>
              </div>

              {/* Match results */}
              {importMatches && importMatches.length > 0 && (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-sm font-medium">{importMatches.length} matches found</p>
                    <button
                      onClick={() => {
                        if (importSelected.size === importMatches.length) setImportSelected(new Set())
                        else setImportSelected(new Set(importMatches.map((m: any) => m.matched_video_id)))
                      }}
                      className="text-xs text-muted-foreground hover:text-foreground"
                    >
                      {importSelected.size === importMatches.length ? "Deselect All" : "Select All"}
                    </button>
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
                                <span className={`font-medium ${match.match_confidence >= 90 ? "text-green-500" : match.match_confidence >= 80 ? "text-yellow-500" : "text-orange-500"}`}>
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

            {/* Footer */}
            {importMatches && importMatches.length > 0 && (
              <div className="p-4 border-t flex items-center justify-between">
                <p className="text-sm text-muted-foreground">{importSelected.size} of {importMatches.length} selected</p>
                <button
                  onClick={async () => {
                    if (importSelected.size === 0) return
                    setImportRunning(true)
                    try {
                      const selectedMatches = importMatches
                        .filter((m: any) => importSelected.has(m.matched_video_id))
                        .map((m: any) => ({ file_path: m.file_path, matched_video_id: m.matched_video_id }))
                      const res = await api.importConfirm(channelId, selectedMatches)
                      toast(`Imported ${res.imported} videos${res.errors.length > 0 ? ` (${res.errors.length} errors)` : ""}`)
                      if (res.errors.length > 0) {
                        console.warn("Import errors:", res.errors)
                      }
                      invalidateVideos()
                      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
                      setImportOpen(false)
                    } catch (e: any) {
                      toast(e.message, "error")
                    } finally {
                      setImportRunning(false)
                    }
                  }}
                  disabled={importRunning || importSelected.size === 0}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {importRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderInput className="h-4 w-4" />}
                  Import Selected ({importSelected.size})
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
