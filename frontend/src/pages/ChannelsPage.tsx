import { useState, useMemo, useEffect, useRef } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { formatBytes } from "@/lib/utils"
import { HEALTH_COLORS } from "@/lib/types"
import type { Channel } from "@/lib/types"
import { useToast } from "@/components/ui/toaster"
import BulkImportDialog from "@/components/channels/BulkImportDialog"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { Button, buttonClasses } from "@/components/ui/Button"
import { Dialog } from "@/components/ui/Dialog"
import { Field } from "@/components/ui/Field"
import { PageHeader } from "@/components/ui/PageHeader"
import { Badge } from "@/components/ui/Badge"
import { EmptyState } from "@/components/ui/EmptyState"
import { DateTime } from "@/components/ui/DateTime"
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
  Upload,
} from "lucide-react"

type ViewMode = "grid" | "list"
type CardSize = "small" | "medium" | "large"
type SortBy = "name_asc" | "name_desc" | "recent" | "videos" | "health" | "completion_asc" | "completion_desc"

function getStored<T extends string>(key: string, fallback: T): T {
  try { return (localStorage.getItem(key) as T) || fallback } catch { return fallback }
}

// Fraction of a channel's known videos that are downloaded. A channel with no
// videos yet has nothing missing, so it counts as complete rather than 0% -
// otherwise every empty channel would crowd the top of "Least Complete".
function completionPct(c: Channel): number {
  if (!c.total_videos) return 1
  return Math.min(1, c.downloaded_count / c.total_videos)
}

function sortChannels(channels: Channel[], sort: SortBy): Channel[] {
  const sorted = [...channels]
  switch (sort) {
    case "name_asc": return sorted.sort((a, b) => a.channel_name.localeCompare(b.channel_name))
    case "name_desc": return sorted.sort((a, b) => b.channel_name.localeCompare(a.channel_name))
    case "recent": return sorted.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    case "videos": return sorted.sort((a, b) => b.total_videos - a.total_videos)
    case "completion_asc":
      // Least complete first, so channels still missing videos surface at the top.
      // Ties break on the larger absolute gap.
      return sorted.sort((a, b) =>
        completionPct(a) - completionPct(b) ||
        (b.total_videos - b.downloaded_count) - (a.total_videos - a.downloaded_count))
    case "completion_desc":
      return sorted.sort((a, b) => completionPct(b) - completionPct(a))
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
  const [moveAllDir, setMoveAllDir] = useState("")
  const [showBulk, setShowBulk] = useState(false)
  const [showMoveAll, setShowMoveAll] = useState(false)
  const [movingAll, setMovingAll] = useState(false)
  const [moveAllPreview, setMoveAllPreview] = useState<any>(null)
  const [moveAllPreviewLoading, setMoveAllPreviewLoading] = useState(false)
  const [autoDownload, setAutoDownload] = useState(true)
  const [addFromDate, setAddFromDate] = useState("")
  const [addTitleFilter, setAddTitleFilter] = useState("")
  const [addTitleFilterIsRegex, setAddTitleFilterIsRegex] = useState(false)
  const [addTitleFilterMode, setAddTitleFilterMode] = useState<"include" | "exclude">("include")
  const [search, setSearch] = useState("")
  const [viewMode, setViewMode] = useState<ViewMode>(() => getStored("ch_view", "grid"))
  const [cardSize, setCardSize] = useState<CardSize>(() => getStored("ch_size", "medium"))
  const [sortBy, setSortBy] = useState<SortBy>(() => getStored("ch_sort", "name_asc"))

  const { toast } = useToast()

  const debouncedSearch = useDebounce(search, 300)

  // Add Channel starts at the saved Default Quality
  const { data: appSettings } = useQuery({
    queryKey: ["app-settings"],
    queryFn: api.getSettings,
  })
  const defaultQuality = appSettings?.default_quality ? String(appSettings.default_quality) : "best"
  // Applied once per open, after the settings have loaded, so a refetch can't undo the user's pick
  const qualitySeeded = useRef(false)
  useEffect(() => {
    if (!showAdd) { qualitySeeded.current = false; return }
    if (!appSettings || qualitySeeded.current) return
    qualitySeeded.current = true
    setAddQuality(defaultQuality)
  }, [showAdd, appSettings, defaultQuality])

  const { data: channels, isLoading } = useQuery({
    queryKey: ["channels", debouncedSearch],
    queryFn: () => api.getChannels(debouncedSearch || undefined),
    staleTime: 15000,
  })

  // After a successful add: clear the form. Auto-download and scan-after-add stay as the
  // user left them (re-ticking auto-download can queue a whole back catalogue).
  const resetAdd = () => {
    setShowAdd(false)
    setAddUrl("")
    setAddQuality(defaultQuality)
    setAddDownloadDir("")
    setAddTitleFilter("")
    setAddTitleFilterIsRegex(false)
    setAddTitleFilterMode("include")
    setAddFromDate("")
  }

  // Cancel / close: clear URL, from-date and title filter; keep the folder and quality
  const closeAdd = () => {
    setShowAdd(false)
    setAddUrl("")
    setAddFromDate("")
    setAddTitleFilter("")
    setAddTitleFilterIsRegex(false)
    setAddTitleFilterMode("include")
  }

  const addMutation = useMutation({
    mutationFn: (data: { url: string; quality: string; download_dir?: string; auto_download?: boolean; download_from_date?: string; title_filter?: string; title_filter_is_regex?: boolean; title_filter_mode?: string }) => api.addChannel(data),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["channels"] })
      resetAdd()
      toast(`Added channel: ${data.channel_name}`)
      if (scanAfterAdd && data.id) {
        toast("Starting initial scan...")
        scanMutation.mutate(data.id)
      }
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const submitAdd = () => {
    if (addTitleFilterIsRegex && addTitleFilter) {
      try { new RegExp(addTitleFilter, "i") } catch {
        toast("Invalid regular expression", "error")
        return
      }
    }
    addMutation.mutate({ url: addUrl, quality: addQuality, auto_download: autoDownload, ...(addFromDate ? { download_from_date: addFromDate } : {}), ...(addDownloadDir ? { download_dir: addDownloadDir } : {}), ...(addTitleFilter ? { title_filter: addTitleFilter, title_filter_is_regex: addTitleFilterIsRegex, title_filter_mode: addTitleFilterMode } : {}) })
  }

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

  const closeMoveAll = () => { setShowMoveAll(false); setMoveAllDir(""); setMoveAllPreview(null) }

  const previewMoveAll = async () => {
    if (!moveAllDir) return
    setMoveAllPreviewLoading(true)
    try {
      const preview = await api.moveAllPreview(moveAllDir)
      if (preview.channels_to_move === 0) {
        toast("All channels are already in that directory")
        return
      }
      setMoveAllPreview(preview)
    } catch (e: any) {
      toast(e.message, "error")
    } finally {
      setMoveAllPreviewLoading(false)
    }
  }

  const runMoveAll = () => {
    setMovingAll(true)
    api.moveAllChannels(moveAllDir).then((r) => {
      queryClient.invalidateQueries({ queryKey: ["channels"] })
      closeMoveAll()
      toast(r.message)
    }).catch((e: any) => toast(e.message, "error")).finally(() => setMovingAll(false))
  }

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
      <PageHeader
        title="Channels"
        actions={
          <>
            <Button
              onClick={() => downloadAllMissingMutation.mutate()}
              loading={downloadAllMissingMutation.isPending}
            >
              {!downloadAllMissingMutation.isPending && <Download className="h-4 w-4" />}
              Download All Missing
            </Button>
            <Button variant="primary" onClick={() => setShowAdd(true)}>
              <Plus className="h-4 w-4" /> Add Channel
            </Button>
            <Button onClick={() => setShowBulk(true)} title="Add many channels from a list or CSV file">
              <Upload className="h-4 w-4" /> Bulk Import
            </Button>
            <Button onClick={() => setShowMoveAll(true)} title="Move all channel files to a new directory">
              Move All
            </Button>
          </>
        }
      />

      {/* Search + Display Controls */}
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="text"
          placeholder="Search channels..."
          aria-label="Search channels"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 min-w-[200px] max-w-sm px-3 py-2 rounded-md border bg-background text-sm"
        />

        {/* Sort */}
        <div className="flex items-center gap-1">
          <ArrowUpDown className="h-4 w-4 text-muted-foreground" />
          <select
            value={sortBy}
            aria-label="Sort channels"
            onChange={(e) => setSort(e.target.value as SortBy)}
            className="px-2 py-2 rounded-md border bg-background text-sm"
          >
            <option value="name_asc">Name (A-Z)</option>
            <option value="name_desc">Name (Z-A)</option>
            <option value="recent">Recently Added</option>
            <option value="videos">Most Videos</option>
            <option value="completion_asc">Least Complete</option>
            <option value="completion_desc">Most Complete</option>
            <option value="health">Health Status</option>
          </select>
        </div>

        {/* Card size (grid only) */}
        {viewMode === "grid" && (
          <select
            value={cardSize}
            aria-label="Card size"
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
          <Button
            variant={viewMode === "grid" ? "primary" : "ghost"}
            size="icon"
            className="rounded-none"
            onClick={() => setView("grid")}
            title="Grid view"
            aria-label="Grid view"
            aria-pressed={viewMode === "grid"}
          >
            <LayoutGrid className="h-4 w-4" />
          </Button>
          <Button
            variant={viewMode === "list" ? "primary" : "ghost"}
            size="icon"
            className="rounded-none"
            onClick={() => setView("list")}
            title="List view"
            aria-label="List view"
            aria-pressed={viewMode === "list"}
          >
            <List className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Add Channel Dialog */}
      <Dialog
        open={showAdd}
        onClose={closeAdd}
        closeOnBackdrop={false}
        size="sm"
        title={
          <span className="flex items-center gap-2">
            Add Channel
            <HelpIcon text="Add by URL, @handle, or playlist link." anchor="adding-channels" />
          </span>
        }
        footer={
          <>
            <Button onClick={closeAdd}>Cancel</Button>
            <Button
              type="submit"
              form="add-channel-form"
              variant="primary"
              disabled={!addUrl}
              loading={addMutation.isPending}
            >
              Add
            </Button>
          </>
        }
      >
        <form
          id="add-channel-form"
          className="space-y-4"
          onSubmit={(e) => { e.preventDefault(); if (addUrl && !addMutation.isPending) submitAdd() }}
        >
          <Field label="Channel URL or @handle">
            <input
              type="text"
              autoFocus
              placeholder="https://youtube.com/@channel or @channel"
              value={addUrl}
              onChange={(e) => setAddUrl(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background"
            />
          </Field>
          <Field label="Quality">
            <select
              value={addQuality}
              onChange={(e) => setAddQuality(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background"
            >
              <option value="best">Best Available</option>
              <option value="2160p">4K (2160p)</option>
              <option value="1080p">1080p</option>
              <option value="720p">720p</option>
              <option value="480p">480p</option>
            </select>
          </Field>
          <Field label="Download Directory" hint="Custom path inside the container, e.g. /media/youtube">
            <input
              type="text"
              placeholder="Leave blank for default (/downloads)"
              value={addDownloadDir}
              onChange={(e) => setAddDownloadDir(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background"
            />
          </Field>
          <div>
            <Field label="Title Filter (optional)">
              <input
                type="text"
                placeholder={addTitleFilterIsRegex ? "e.g. rust|ark\\s*raiders" : "e.g. Rust, Arc Raiders, Minecraft"}
                value={addTitleFilter}
                onChange={(e) => setAddTitleFilter(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background"
              />
            </Field>
            <div className="flex items-center gap-3 mt-1.5">
              <div className="flex items-center gap-1.5">
                {/* Switch control, not a labelled action button, so it keeps its own track styling */}
                <button
                  type="button"
                  role="switch"
                  aria-checked={addTitleFilterMode === "exclude"}
                  aria-label="Exclude matching titles"
                  onClick={() => setAddTitleFilterMode(addTitleFilterMode === "include" ? "exclude" : "include")}
                  className={`relative inline-flex h-4 w-8 items-center rounded-full transition-colors ${addTitleFilterMode === "exclude" ? "bg-orange-500" : "bg-primary"}`}
                >
                  <span className={`inline-block h-3 w-3 rounded-full bg-white transition-transform ${addTitleFilterMode === "exclude" ? "translate-x-4" : "translate-x-0.5"}`} />
                </button>
                <span className="text-xs text-muted-foreground">{addTitleFilterMode === "exclude" ? "Exclude" : "Include"}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  role="switch"
                  aria-checked={addTitleFilterIsRegex}
                  aria-label="Treat the filter as a regular expression"
                  onClick={() => setAddTitleFilterIsRegex(!addTitleFilterIsRegex)}
                  className={`relative inline-flex h-4 w-8 items-center rounded-full transition-colors ${addTitleFilterIsRegex ? "bg-primary" : "bg-gray-300 dark:bg-gray-600"}`}
                >
                  <span className={`inline-block h-3 w-3 rounded-full bg-white transition-transform ${addTitleFilterIsRegex ? "translate-x-4" : "translate-x-0.5"}`} />
                </button>
                <span className="text-xs text-muted-foreground">Regex</span>
              </div>
            </div>
            <p className="text-xs text-muted-foreground mt-1">{addTitleFilterMode === "exclude" ? "Skip" : "Only download"} videos {addTitleFilterMode === "exclude" ? "containing" : "matching"} these {addTitleFilterIsRegex ? "patterns" : "keywords"}. Leave blank to download all.</p>
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
            <span className="text-sm">Auto-download videos</span>
          </label>
          <p className="text-xs text-muted-foreground -mt-2">
            Downloads every video the channel has published unless you set a date. Uncheck to list videos and pick what to download.
          </p>
          <Field label="Download from date (optional)" hint="Leave empty to include everything.">
            <input
              type="date"
              value={addFromDate}
              onChange={(e) => setAddFromDate(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background"
            />
          </Field>
          {addMutation.error && (
            <p className="text-sm text-danger">{(addMutation.error as Error).message}</p>
          )}
        </form>
      </Dialog>

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
                                <Badge tone="info" className="text-[10px] px-1.5 capitalize">
                                  {channel.platform}
                                </Badge>
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
                        <Badge className="text-[10px]">{channel.quality}</Badge>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <Circle className={`h-2.5 w-2.5 fill-current ${HEALTH_COLORS[channel.health_status] || HEALTH_COLORS.unknown}`} />
                          <span className={`text-xs ${channel.enabled ? "text-success" : "text-muted-foreground"}`}>
                            {channel.enabled ? "Active" : "Paused"}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground"><DateTime value={channel.last_scanned_at} /></td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost" size="icon"
                            onClick={() => downloadChannelMutation.mutate(channel.id)}
                            title="Download all missing" aria-label="Download all missing"
                          >
                            <Download className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost" size="icon"
                            onClick={() => scanMutation.mutate(channel.id)}
                            title="Scan now" aria-label="Scan now"
                          >
                            <RefreshCw className="h-3.5 w-3.5" />
                          </Button>
                          {/* Link, not a button: it opens another site */}
                          <a href={channel.channel_url} target="_blank" rel="noopener noreferrer" className={buttonClasses("ghost", "icon")} title="Open channel" aria-label="Open channel">
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
                            <Badge tone="info" className="text-[10px] px-1.5 capitalize">
                              {channel.platform}
                            </Badge>
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
                        <Badge className="text-[10px]">{channel.quality}</Badge>
                        <Badge tone={channel.enabled ? "success" : "neutral"} className="text-[10px]">
                          {channel.enabled ? "Active" : "Paused"}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                        <Button
                          variant="ghost" size="icon"
                          onClick={(e) => { e.preventDefault(); downloadChannelMutation.mutate(channel.id) }}
                          title="Download all missing" aria-label="Download all missing"
                        >
                          <Download className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost" size="icon"
                          onClick={(e) => { e.preventDefault(); scanMutation.mutate(channel.id) }}
                          title="Scan now" aria-label="Scan now"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </Button>
                        {/* Link, not a button: it opens another site */}
                        <a
                          href={channel.channel_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className={buttonClasses("ghost", "icon")}
                          title="Open channel"
                          aria-label="Open channel"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      </div>
                    </div>

                    {cardSize !== "small" && (
                      <div className="mt-2 text-xs text-muted-foreground">
                        Last scan: <DateTime value={channel.last_scanned_at} />
                      </div>
                    )}
                  </div>
                </Link>
              )
            })}
          </div>
        )
      ) : (
        <EmptyState
          icon={<Tv />}
          title="No channels yet"
          text="Add one to get started."
        />
      )}
      <BulkImportDialog open={showBulk} onClose={() => setShowBulk(false)} />

      {/* Move All Dialog */}
      <Dialog
        open={showMoveAll}
        onClose={() => { if (!movingAll) closeMoveAll() }}
        title="Move All Channels"
        closeOnEsc={!movingAll}
        closeOnBackdrop={false}
        showClose={!movingAll}
        footer={
          !moveAllPreview ? (
            <>
              <Button onClick={closeMoveAll}>Cancel</Button>
              <Button
                variant="primary"
                onClick={previewMoveAll}
                disabled={!moveAllDir}
                loading={moveAllPreviewLoading}
              >
                {moveAllPreviewLoading ? "Loading..." : "Preview"}
              </Button>
            </>
          ) : (
            <>
              <Button onClick={closeMoveAll} disabled={movingAll}>Cancel</Button>
              <Button variant="primary" onClick={runMoveAll} loading={movingAll}>
                {movingAll ? "Moving..." : `Move ${moveAllPreview.total_files} Files`}
              </Button>
            </>
          )
        }
      >
        {!moveAllPreview ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Move all channel files to a new root directory. File paths in the database will be updated automatically.
            </p>
            <Field label="New Download Directory">
              <input
                type="text"
                autoFocus
                placeholder="/downloads/youtube"
                value={moveAllDir}
                onChange={(e) => setMoveAllDir(e.target.value)}
                className="w-full px-3 py-2 rounded-md border bg-background"
              />
            </Field>
          </div>
        ) : (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Channels to move:</span>
              <span className="font-semibold">{moveAllPreview.channels_to_move}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total files:</span>
              <span className="font-semibold">{moveAllPreview.total_files}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total size:</span>
              <span>{formatBytes(moveAllPreview.total_size)}</span>
            </div>
            {moveAllPreview.channels.length > 0 && (
              <div className="mt-2 max-h-48 overflow-y-auto text-xs border rounded p-2">
                {moveAllPreview.channels.map((ch: any) => (
                  <div key={ch.channel_id} className="flex justify-between py-0.5">
                    <span className="truncate mr-2">{ch.channel_name}</span>
                    <span className="text-muted-foreground whitespace-nowrap">{ch.file_count} files</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Dialog>
    </div>
  )
}
