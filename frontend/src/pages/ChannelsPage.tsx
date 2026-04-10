import { useState, useMemo } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { formatDateTime } from "@/lib/utils"
import { HEALTH_COLORS } from "@/lib/types"
import type { Channel } from "@/lib/types"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { useDebounce } from "@/hooks/useDebounce"
import {
  Plus,
  Loader2,
  Tv,
  RefreshCw,
  Circle,
  ExternalLink,
  LayoutGrid,
  List,
  ArrowUpDown,
  Download,
} from "lucide-react"

type ViewMode = "grid" | "list"
type CardSize = "small" | "medium" | "large"
type SortBy = "name_asc" | "name_desc" | "recent" | "videos" | "health"

function getStored<T extends string>(key: string, fallback: T): T {
  try { return (localStorage.getItem(key) as T) || fallback } catch { return fallback }
}

function sortChannels(channels: Channel[], sort: SortBy): Channel[] {
  const sorted = [...channels]
  switch (sort) {
    case "name_asc": return sorted.sort((a, b) => a.channel_name.localeCompare(b.channel_name))
    case "name_desc": return sorted.sort((a, b) => b.channel_name.localeCompare(a.channel_name))
    case "recent": return sorted.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    case "videos": return sorted.sort((a, b) => b.total_videos - a.total_videos)
    case "health": {
      const order: Record<string, number> = { unhealthy: 0, warning: 1, unknown: 2, healthy: 3 }
      return sorted.sort((a, b) => (order[a.health_status] ?? 2) - (order[b.health_status] ?? 2))
    }
    default: return sorted
  }
}

export default function ChannelsPage() {
  const queryClient = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)
  const [addUrl, setAddUrl] = useState("")
  const [addQuality, setAddQuality] = useState("best")
  const [addDownloadDir, setAddDownloadDir] = useState("")
  const [scanAfterAdd, setScanAfterAdd] = useState(true)
  const [autoDownload, setAutoDownload] = useState(true)
  const [search, setSearch] = useState("")
  const [viewMode, setViewMode] = useState<ViewMode>(() => getStored("ch_view", "grid"))
  const [cardSize, setCardSize] = useState<CardSize>(() => getStored("ch_size", "medium"))
  const [sortBy, setSortBy] = useState<SortBy>(() => getStored("ch_sort", "name_asc"))

  const { toast } = useToast()

  const debouncedSearch = useDebounce(search, 300)

  const { data: channels, isLoading } = useQuery({
    queryKey: ["channels", debouncedSearch],
    queryFn: () => api.getChannels(debouncedSearch || undefined),
    staleTime: 15000,
  })

  const addMutation = useMutation({
    mutationFn: (data: { url: string; quality: string; download_dir?: string; auto_download?: boolean }) => api.addChannel(data),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["channels"] })
      setShowAdd(false)
      setAddUrl("")
      setAddDownloadDir("")
      toast(`Added channel: ${data.channel_name}`)
      if (scanAfterAdd && data.id) {
        toast("Starting initial scan...")
        scanMutation.mutate(data.id)
      }
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const scanMutation = useMutation({
    mutationFn: (id: number) => api.scanChannel(id),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["channels"] })
      toast(data.message || "Scan complete")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const downloadAllMissingMutation = useMutation({
    mutationFn: () => api.downloadAllMissing(),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["channels"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast(data.message || "All missing videos queued")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const downloadChannelMutation = useMutation({
    mutationFn: (id: number) => api.downloadAllChannel(id),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["channels"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast(data.message || "Videos queued for download")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const setView = (v: ViewMode) => { setViewMode(v); localStorage.setItem("ch_view", v) }
  const setSize = (s: CardSize) => { setCardSize(s); localStorage.setItem("ch_size", s) }
  const setSort = (s: SortBy) => { setSortBy(s); localStorage.setItem("ch_sort", s) }

  const sortedChannels = useMemo(() => channels ? sortChannels(channels, sortBy) : [], [channels, sortBy])

  // Thumbnail sizes per card size
  const thumbSize = { small: "h-10 w-10", medium: "h-16 w-16", large: "h-20 w-20" }
  const thumbIconSize = { small: "h-5 w-5", medium: "h-8 w-8", large: "h-10 w-10" }
  const gridCols = {
    small: "grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4",
    medium: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
    large: "grid-cols-1 sm:grid-cols-2",
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Channels</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => downloadAllMissingMutation.mutate()}
            disabled={downloadAllMissingMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-md border hover:bg-accent transition-colors disabled:opacity-50"
          >
            {downloadAllMissingMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Download All Missing
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
          >
            <Plus className="h-4 w-4" /> Add Channel
          </button>
        </div>
      </div>

      {/* Search + Display Controls */}
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="text"
          placeholder="Search channels..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 min-w-[200px] max-w-sm px-3 py-2 rounded-md border bg-background text-sm"
        />

        {/* Sort */}
        <div className="flex items-center gap-1">
          <ArrowUpDown className="h-4 w-4 text-muted-foreground" />
          <select
            value={sortBy}
            onChange={(e) => setSort(e.target.value as SortBy)}
            className="px-2 py-2 rounded-md border bg-background text-sm"
          >
            <option value="name_asc">Name (A-Z)</option>
            <option value="name_desc">Name (Z-A)</option>
            <option value="recent">Recently Added</option>
            <option value="videos">Most Videos</option>
            <option value="health">Health Status</option>
          </select>
        </div>

        {/* Card size (grid only) */}
        {viewMode === "grid" && (
          <select
            value={cardSize}
            onChange={(e) => setSize(e.target.value as CardSize)}
            className="px-2 py-2 rounded-md border bg-background text-sm"
          >
            <option value="small">Small</option>
            <option value="medium">Medium</option>
            <option value="large">Large</option>
          </select>
        )}

        {/* View mode toggle */}
        <div className="flex items-center border rounded-md overflow-hidden">
          <button
            onClick={() => setView("grid")}
            className={`p-2 ${viewMode === "grid" ? "bg-primary text-primary-foreground" : "hover:bg-accent"}`}
            title="Grid view"
          >
            <LayoutGrid className="h-4 w-4" />
          </button>
          <button
            onClick={() => setView("list")}
            className={`p-2 ${viewMode === "list" ? "bg-primary text-primary-foreground" : "hover:bg-accent"}`}
            title="List view"
          >
            <List className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Add Channel Dialog */}
      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-card rounded-lg p-6 w-full max-w-md mx-4 border shadow-lg">
            <div className="flex items-center gap-2 mb-4">
              <h2 className="text-lg font-semibold">Add Channel</h2>
              <HelpIcon text="Add by URL, @handle, or playlist link." anchor="adding-channels" />
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-1">Channel URL or @handle</label>
                <input
                  type="text"
                  placeholder="https://youtube.com/@channel or @channel"
                  value={addUrl}
                  onChange={(e) => setAddUrl(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background"
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Quality</label>
                <select
                  value={addQuality}
                  onChange={(e) => setAddQuality(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background"
                >
                  <option value="best">Best Available</option>
                  <option value="1080p">1080p</option>
                  <option value="720p">720p</option>
                  <option value="480p">480p</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Download Directory</label>
                <input
                  type="text"
                  placeholder="Leave blank for default (/downloads)"
                  value={addDownloadDir}
                  onChange={(e) => setAddDownloadDir(e.target.value)}
                  className="w-full px-3 py-2 rounded-md border bg-background"
                />
                <p className="text-xs text-muted-foreground mt-1">Custom path inside the container, e.g. /media/youtube</p>
              </div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={scanAfterAdd}
                  onChange={(e) => setScanAfterAdd(e.target.checked)}
                  className="rounded"
                />
                <span className="text-sm">Scan for videos after adding</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoDownload}
                  onChange={(e) => setAutoDownload(e.target.checked)}
                  className="rounded"
                />
                <span className="text-sm">Auto-download new videos</span>
                <span className="text-xs text-muted-foreground">(uncheck to browse before downloading)</span>
              </label>
              {addMutation.error && (
                <p className="text-sm text-red-500">{(addMutation.error as Error).message}</p>
              )}
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => { setShowAdd(false); setAddUrl("") }}
                  className="px-4 py-2 rounded-md border hover:bg-accent"
                >
                  Cancel
                </button>
                <button
                  onClick={() => addMutation.mutate({ url: addUrl, quality: addQuality, auto_download: autoDownload, ...(addDownloadDir ? { download_dir: addDownloadDir } : {}) })}
                  disabled={!addUrl || addMutation.isPending}
                  className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2"
                >
                  {addMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                  Add
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Channel Display */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : sortedChannels.length > 0 ? (
        viewMode === "list" ? (
          /* List View */
          <div className="rounded-lg border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="text-left px-4 py-2.5">Channel</th>
                  <th className="text-left px-4 py-2.5">Videos</th>
                  <th className="text-left px-4 py-2.5">Quality</th>
                  <th className="text-left px-4 py-2.5">Status</th>
                  <th className="text-left px-4 py-2.5">Last Scan</th>
                  <th className="px-4 py-2.5"></th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {sortedChannels.map((channel: any) => {
                  const progress = channel.total_videos > 0
                    ? Math.min(100, Math.round((channel.downloaded_count / channel.total_videos) * 100))
                    : 0
                  return (
                    <tr key={channel.id} className="hover:bg-muted/30">
                      <td className="px-4 py-3">
                        <Link to={`/channels/${channel.id}`} className="flex items-center gap-3 hover:text-primary">
                          {channel.thumbnail_url ? (
                            <img src={channel.thumbnail_url} alt="" className="h-10 w-10 rounded-full object-cover flex-shrink-0" />
                          ) : (
                            <div className="h-10 w-10 rounded-full bg-muted flex items-center justify-center flex-shrink-0">
                              <Tv className="h-5 w-5 text-muted-foreground" />
                            </div>
                          )}
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="font-medium truncate">{channel.channel_name}</span>
                              {channel.platform !== "youtube" && (
                                <span className="inline-flex px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300 capitalize">
                                  {channel.platform}
                                </span>
                              )}
                            </div>
                            {channel.description && (
                              <p className="text-xs text-muted-foreground truncate max-w-md">{channel.description}</p>
                            )}
                          </div>
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">
                        {channel.downloaded_count}/{channel.total_videos}
                        <span className="text-xs ml-1">({progress}%)</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="inline-flex px-2 py-0.5 rounded-full text-[10px] font-medium border">{channel.quality}</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <Circle className={`h-2.5 w-2.5 fill-current ${HEALTH_COLORS[channel.health_status] || HEALTH_COLORS.unknown}`} />
                          <span className={`text-xs ${channel.enabled ? "text-green-600 dark:text-green-400" : "text-muted-foreground"}`}>
                            {channel.enabled ? "Active" : "Paused"}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">{formatDateTime(channel.last_scanned_at)}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => downloadChannelMutation.mutate(channel.id)}
                            className="p-1 hover:bg-accent rounded" title="Download all missing"
                          >
                            <Download className="h-3.5 w-3.5" />
                          </button>
                          <button
                            onClick={() => scanMutation.mutate(channel.id)}
                            className="p-1 hover:bg-accent rounded" title="Scan now"
                          >
                            <RefreshCw className="h-3.5 w-3.5" />
                          </button>
                          <a href={channel.channel_url} target="_blank" rel="noopener noreferrer" className="p-1 hover:bg-accent rounded" title="Open channel">
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          /* Grid View */
          <div className={`grid gap-4 ${gridCols[cardSize]}`}>
            {sortedChannels.map((channel: any) => {
              const progress = channel.total_videos > 0
                ? Math.min(100, Math.round((channel.downloaded_count / channel.total_videos) * 100))
                : 0
              return (
                <Link
                  key={channel.id}
                  to={`/channels/${channel.id}`}
                  className="rounded-lg border bg-card hover:border-primary/50 transition-colors group flex flex-col overflow-hidden"
                >
                  {/* Banner for large cards */}
                  {cardSize === "large" && (
                    <div className="h-24 w-full relative">
                      {channel.banner_url ? (
                        <img src={channel.banner_url} alt="" className="w-full h-full object-cover" />
                      ) : (
                        <div className="w-full h-full bg-gradient-to-br from-primary/20 via-primary/10 to-background" />
                      )}
                    </div>
                  )}

                  <div className="p-4 flex flex-col flex-1">
                    <div className="flex items-start gap-3">
                      {channel.thumbnail_url ? (
                        <img
                          src={channel.thumbnail_url}
                          alt={channel.channel_name}
                          className={`${thumbSize[cardSize]} rounded-full object-cover flex-shrink-0`}
                        />
                      ) : (
                        <div className={`${thumbSize[cardSize]} rounded-full bg-muted flex items-center justify-center flex-shrink-0`}>
                          <Tv className={`${thumbIconSize[cardSize]} text-muted-foreground`} />
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <h3 className="font-semibold truncate">{channel.channel_name}</h3>
                          {channel.platform && channel.platform !== "youtube" && (
                            <span className="inline-flex px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300 capitalize">
                              {channel.platform}
                            </span>
                          )}
                          <Circle
                            className={`h-2.5 w-2.5 flex-shrink-0 fill-current ${HEALTH_COLORS[channel.health_status] || HEALTH_COLORS.unknown}`}
                          />
                        </div>
                        {cardSize !== "small" && channel.description && (
                          <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{channel.description}</p>
                        )}
                      </div>
                    </div>

                    {/* Progress bar */}
                    <div className="mt-3">
                      <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                        <span>{channel.downloaded_count}/{channel.total_videos} videos</span>
                        <span>{progress}%</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-primary/20 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-primary transition-all"
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                    </div>

                    {/* Metadata badges + actions */}
                    <div className="mt-3 flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span className="inline-flex px-2 py-0.5 rounded-full text-[10px] font-medium border">
                          {channel.quality}
                        </span>
                        <span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-medium ${
                          channel.enabled
                            ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                            : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
                        }`}>
                          {channel.enabled ? "Active" : "Paused"}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={(e) => { e.preventDefault(); downloadChannelMutation.mutate(channel.id) }}
                          className="p-1 hover:bg-accent rounded"
                          title="Download all missing"
                        >
                          <Download className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={(e) => { e.preventDefault(); scanMutation.mutate(channel.id) }}
                          className="p-1 hover:bg-accent rounded"
                          title="Scan now"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </button>
                        <a
                          href={channel.channel_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="p-1 hover:bg-accent rounded"
                          title="Open channel"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      </div>
                    </div>

                    {cardSize !== "small" && (
                      <div className="mt-2 text-xs text-muted-foreground">
                        Last scan: {formatDateTime(channel.last_scanned_at)}
                      </div>
                    )}
                  </div>
                </Link>
              )
            })}
          </div>
        )
      ) : (
        <div className="text-center py-12 text-muted-foreground">
          <Tv className="h-12 w-12 mx-auto mb-3 opacity-50" />
          <p>No channels yet. Add one to get started.</p>
        </div>
      )}
    </div>
  )
}
