import { useParams, useNavigate } from "react-router-dom"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatDate, formatDateTime, formatBytes, formatDuration } from "@/lib/utils"
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
} from "lucide-react"
import { useState } from "react"

export default function ChannelDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const channelId = Number(id)
  const [statusFilter, setStatusFilter] = useState("")
  const { toast } = useToast()

  const { data: channel } = useQuery({
    queryKey: ["channel", channelId],
    queryFn: () => api.getChannel(channelId),
  })

  const { data: videos, isLoading: videosLoading } = useQuery({
    queryKey: ["channel-videos", channelId, statusFilter],
    queryFn: () => api.getChannelVideos(channelId, { limit: 100, status: statusFilter || undefined }),
  })

  const scanMutation = useMutation({
    mutationFn: () => api.scanChannel(channelId),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["channel", channelId] })
      queryClient.invalidateQueries({ queryKey: ["channel-videos", channelId] })
      toast(data.message || "Scan complete")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const downloadAllMutation = useMutation({
    mutationFn: () => api.downloadAllChannel(channelId),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["channel-videos", channelId] })
      toast(data.message || "All videos queued for download")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteChannel(channelId, false),
    onSuccess: () => { navigate("/channels"); toast("Channel deleted") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const retryMutation = useMutation({
    mutationFn: (videoId: number) => api.retryDownload(videoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["channel-videos", channelId] })
      toast("Video queued for retry")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const updateMutation = useMutation({
    mutationFn: (data: any) => api.updateChannel(channelId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["channel", channelId] })
      toast("Channel updated")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  if (!channel) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start gap-4">
        <button onClick={() => navigate("/channels")} className="p-2 hover:bg-accent rounded-md mt-1">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold">{channel.channel_name}</h1>
            <Circle
              className={`h-3 w-3 fill-current ${HEALTH_COLORS[channel.health_status] || HEALTH_COLORS.unknown}`}
            />
          </div>
          <p className="text-sm text-muted-foreground">
            {channel.downloaded_count}/{channel.total_videos} videos downloaded |
            Quality: {channel.quality} |
            Last scan: {formatDateTime(channel.last_scanned_at)}
          </p>
          {channel.last_error_code && (
            <p className="text-sm text-red-500 mt-1">Last error: {channel.last_error_code}</p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => scanMutation.mutate()}
            disabled={scanMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${scanMutation.isPending ? "animate-spin" : ""}`} />
            Scan
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

      {/* Channel Settings */}
      <div className="rounded-lg border bg-card p-4">
        <h2 className="text-sm font-semibold mb-3">Channel Settings</h2>
        <div className="grid gap-4 sm:grid-cols-3">
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
            <label className="block text-xs text-muted-foreground mb-1">Status</label>
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
          </div>
          <div>
            <label className="block text-xs text-muted-foreground mb-1">Channel URL</label>
            <a
              href={channel.channel_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-sm text-primary hover:underline"
            >
              Open on YouTube <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        </div>
      </div>

      {/* Videos */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Videos</h2>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-2 py-1 rounded-md border bg-background text-sm"
          >
            <option value="">All Status</option>
            <option value="completed">Completed</option>
            <option value="pending">Pending</option>
            <option value="queued">Queued</option>
            <option value="downloading">Downloading</option>
            <option value="failed">Failed</option>
            <option value="skipped">Skipped</option>
          </select>
        </div>

        {videosLoading ? (
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground mx-auto" />
        ) : videos && videos.length > 0 ? (
          <div className="rounded-lg border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="text-left px-3 py-2">#</th>
                  <th className="text-left px-3 py-2">Title</th>
                  <th className="text-left px-3 py-2">Date</th>
                  <th className="text-left px-3 py-2">Status</th>
                  <th className="text-left px-3 py-2">Size</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {videos.map((video: any) => (
                  <tr key={video.id} className="hover:bg-muted/30">
                    <td className="px-3 py-2 text-muted-foreground">
                      S{video.season}E{String(video.episode).padStart(3, "0")}
                    </td>
                    <td className="px-3 py-2 max-w-xs truncate">{video.title}</td>
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
                    <td className="px-3 py-2">
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
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-center py-8 text-muted-foreground">
            No videos found. Try scanning the channel.
          </p>
        )}
      </div>
    </div>
  )
}
