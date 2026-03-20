import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatDateTime, formatBytes, formatSpeed } from "@/lib/utils"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useEffect, useState } from "react"
import {
  Tv,
  Download,
  AlertCircle,
  HardDrive,
  Clock,
  CheckCircle2,
  XCircle,
  Loader2,
  RefreshCw,
} from "lucide-react"

export default function DashboardPage() {
  const { data: stats, refetch: refetchStats } = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: api.getStats,
    refetchInterval: 30000,
    staleTime: 10000,
  })

  const { data: recent } = useQuery({
    queryKey: ["recent-downloads"],
    queryFn: () => api.getRecentDownloads(10),
    refetchInterval: 30000,
    staleTime: 10000,
  })

  const { data: queueData } = useQuery({
    queryKey: ["download-queue-dashboard"],
    queryFn: () => api.getQueue({ limit: 10 }),
    refetchInterval: 15000,
    staleTime: 5000,
  })
  const queue = queueData?.items

  const { subscribe } = useWebSocket()
  const [activeProgress, setActiveProgress] = useState<Record<string, any>>({})

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
        refetchStats()
      }
    })
  }, [subscribe, refetchStats])

  if (!stats) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      {/* Stats Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard icon={Tv} label="Channels" value={stats.total_channels} sub={`${stats.active_channels} active`} />
        <StatCard icon={Download} label="Downloaded" value={stats.total_downloaded} sub={`of ${stats.total_videos_known} known`} />
        <StatCard icon={AlertCircle} label="Failed" value={stats.total_failed} sub={`${stats.total_pending} pending`} color={stats.total_failed > 0 ? "text-red-500" : undefined} />
        <StatCard icon={HardDrive} label="Storage" value={stats.storage_used_formatted} sub={`yt-dlp ${stats.ytdlp_version}`} />
      </div>

      {/* Health Banner */}
      {stats.pot_status !== "enabled" && (
        <div className="rounded-lg border border-yellow-300 bg-yellow-50 dark:bg-yellow-900/20 dark:border-yellow-700 p-4">
          <p className="text-sm text-yellow-800 dark:text-yellow-200">
            PO token server is {stats.pot_status}. Downloads may fail without PO tokens.
          </p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Download Queue */}
        <div className="rounded-lg border bg-card p-4">
          <h2 className="text-lg font-semibold mb-3">Download Queue ({stats.queue_length})</h2>
          {queue && queue.length > 0 ? (
            <div className="space-y-3">
              {queue.slice(0, 5).map((entry: any) => {
                const progress = activeProgress[entry.video?.video_id]
                return (
                  <div key={entry.id} className="flex flex-col gap-1">
                    <div className="flex items-center justify-between">
                      <span className="text-sm truncate flex-1">{entry.video?.title || "Unknown"}</span>
                      <span className="text-xs text-muted-foreground ml-2">
                        {progress
                          ? progress.downloaded_bytes > 0 && progress.total_bytes > 0
                            ? `${progress.percent}% \u2022 ${formatBytes(progress.downloaded_bytes)} / ${formatBytes(progress.total_bytes)}`
                            : `${progress.percent}%`
                          : entry.started_at ? "Starting..." : "Queued"}
                      </span>
                    </div>
                    {(progress || entry.started_at) && (
                      <div className="h-2 bg-secondary rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all duration-500 ${
                            progress?.percent >= 99.5
                              ? "bg-gradient-to-r from-green-500 to-emerald-400"
                              : "bg-gradient-to-r from-blue-600 to-cyan-400"
                          }`}
                          style={{ width: `${progress?.percent || 0}%` }}
                        />
                      </div>
                    )}
                    {progress && (
                      <div className="flex justify-between text-xs text-muted-foreground">
                        <span>{formatSpeed(progress.speed_bytes || 0)}</span>
                        <span>ETA: {progress.eta}</span>
                      </div>
                    )}
                  </div>
                )
              })}
              {queue.length > 5 && (
                <p className="text-sm text-muted-foreground">+{queue.length - 5} more in queue</p>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No downloads in queue</p>
          )}
        </div>

        {/* Recent Downloads */}
        <div className="rounded-lg border bg-card p-4">
          <h2 className="text-lg font-semibold mb-3">Recent Downloads</h2>
          {recent && recent.length > 0 ? (
            <div className="space-y-2">
              {recent.map((video: any) => (
                <div key={video.id} className="flex items-center gap-2 text-sm">
                  <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0" />
                  <div className="truncate flex-1">
                    <span>{video.title}</span>
                    {video.channel_name && (
                      <span className="text-xs text-muted-foreground ml-1.5">— {video.channel_name}</span>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground flex-shrink-0">
                    {formatDateTime(video.downloaded_at)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No downloads yet</p>
          )}
        </div>
      </div>

      {/* Last Scan */}
      {stats.last_scan_at && (
        <p className="text-sm text-muted-foreground flex items-center gap-1">
          <Clock className="h-4 w-4" />
          Last scan: {formatDateTime(stats.last_scan_at)}
        </p>
      )}
    </div>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  color,
}: {
  icon: any
  label: string
  value: string | number
  sub: string
  color?: string
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-3">
        <Icon className={`h-8 w-8 ${color || "text-primary"}`} />
        <div>
          <p className="text-2xl font-bold">{value}</p>
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-muted-foreground">{sub}</p>
        </div>
      </div>
    </div>
  )
}
