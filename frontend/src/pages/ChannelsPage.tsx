import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { formatDateTime } from "@/lib/utils"
import { HEALTH_COLORS } from "@/lib/types"
import { useToast } from "@/components/ui/toaster"
import {
  Plus,
  Loader2,
  Tv,
  RefreshCw,
  Download,
  Trash2,
  Circle,
  ExternalLink,
} from "lucide-react"

export default function ChannelsPage() {
  const queryClient = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)
  const [addUrl, setAddUrl] = useState("")
  const [addQuality, setAddQuality] = useState("best")
  const [addDownloadDir, setAddDownloadDir] = useState("")
  const [scanAfterAdd, setScanAfterAdd] = useState(true)
  const [search, setSearch] = useState("")

  const { toast } = useToast()

  const { data: channels, isLoading } = useQuery({
    queryKey: ["channels", search],
    queryFn: () => api.getChannels(search || undefined),
  })

  const addMutation = useMutation({
    mutationFn: (data: { url: string; quality: string; download_dir?: string }) => api.addChannel(data),
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

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteChannel(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["channels"] })
      toast("Channel deleted")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Channels</h1>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" /> Add Channel
        </button>
      </div>

      {/* Search */}
      <input
        type="text"
        placeholder="Search channels..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="w-full max-w-sm px-3 py-2 rounded-md border bg-background"
      />

      {/* Add Channel Dialog */}
      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-card rounded-lg p-6 w-full max-w-md mx-4 border shadow-lg">
            <h2 className="text-lg font-semibold mb-4">Add Channel</h2>
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
                  onClick={() => addMutation.mutate({ url: addUrl, quality: addQuality, ...(addDownloadDir ? { download_dir: addDownloadDir } : {}) })}
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

      {/* Channel Grid */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : channels && channels.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {channels.map((channel: any) => {
            const progress = channel.total_videos > 0
              ? Math.round((channel.downloaded_count / channel.total_videos) * 100)
              : 0
            return (
              <Link
                key={channel.id}
                to={`/channels/${channel.id}`}
                className="rounded-lg border bg-card p-4 hover:border-primary/50 transition-colors group flex flex-col"
              >
                <div className="flex items-start gap-3">
                  {channel.thumbnail_url ? (
                    <img
                      src={channel.thumbnail_url}
                      alt={channel.channel_name}
                      className="h-16 w-16 rounded-full object-cover flex-shrink-0"
                    />
                  ) : (
                    <div className="h-16 w-16 rounded-full bg-muted flex items-center justify-center flex-shrink-0">
                      <Tv className="h-8 w-8 text-muted-foreground" />
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
                    {channel.description && (
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

                <div className="mt-2 text-xs text-muted-foreground">
                  Last scan: {formatDateTime(channel.last_scanned_at)}
                </div>
              </Link>
            )
          })}
        </div>
      ) : (
        <div className="text-center py-12 text-muted-foreground">
          <Tv className="h-12 w-12 mx-auto mb-3 opacity-50" />
          <p>No channels yet. Add one to get started.</p>
        </div>
      )}
    </div>
  )
}
