import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatBytes, formatSpeed } from "@/lib/utils"
import { AlertStrip } from "@/components/AlertStrip"
import { buttonClasses } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { DateTime } from "@/components/ui/DateTime"
import { EmptyState } from "@/components/ui/EmptyState"
import { PageHeader } from "@/components/ui/PageHeader"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import {
  Plus,
  ListOrdered,
  Tv,
  Download,
  AlertCircle,
  HardDrive,
  Clock,
  CheckCircle2,
  Loader2,
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

  const { data: health } = useQuery({
    queryKey: ["system-health"],
    queryFn: api.getHealth,
    staleTime: 300000,
  })

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
      <PageHeader title="Dashboard" />

      {/* What needs attention, above the cards so it is the first thing read */}
      <AlertStrip variant="full" />

      {stats.total_channels === 0 ? (
        <Card>
          <EmptyState
            icon={<Tv />}
            title="Welcome to ChannelHoarder"
            text={
              <>
                It watches the channels you add, finds their new videos and downloads them in a
                Plex-compatible layout. Downloaded files go to{" "}
                <code className="font-mono text-foreground">{health?.download_dir || "the download folder"}</code>
                {health?.temp_dir ? (
                  <>
                    , and downloads in progress use{" "}
                    <code className="font-mono text-foreground">{health.temp_dir}</code> until they finish
                  </>
                ) : null}
                . Add your first channel to begin.
              </>
            }
            action={
              <Link to="/channels?add=1" className={buttonClasses("primary")}>
                <Plus className="h-4 w-4" /> Add Channel
              </Link>
            }
          />
        </Card>
      ) : (
      <>
      {/* Stats Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <StatCard icon={Tv} label="Channels" value={stats.total_channels} sub={`${stats.active_channels} active`} />
        <StatCard icon={Download} label="Downloaded" value={stats.total_downloaded} sub={`of ${stats.total_videos_known} known`} />
        <StatCard icon={ListOrdered} label="Queued" value={stats.queued_count} sub={`${stats.active_downloads} downloading`} />
        <StatCard icon={AlertCircle} label="Failed" value={stats.total_failed} sub={stats.total_failed > 0 ? "see Downloads → Failed" : "none"} color={stats.total_failed > 0 ? "text-danger" : undefined} />
        <StatCard icon={HardDrive} label="Storage" value={`${formatBytes(stats.free_space_bytes)} free`} sub={`${stats.storage_used_formatted} used`} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Download Queue */}
        <Card as="h2" title={`Download Queue (${stats.queue_length})`}>
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
              {(stats?.queue_length ?? queue.length) > 5 && (
                <p className="text-sm text-muted-foreground">+{(stats?.queue_length ?? queue.length) - 5} more in queue</p>
              )}
            </div>
          ) : (
            <EmptyState
              icon={<Download />}
              title="No downloads in queue"
              text="New videos from your channels appear here while they download."
              action={<Link to="/downloads" className={buttonClasses("outline")}>Open Downloads</Link>}
            />
          )}
        </Card>

        {/* Recent Downloads */}
        <Card as="h2" title="Recent Downloads">
          {recent && recent.length > 0 ? (
            <div className="space-y-2">
              {recent.map((video: any) => (
                <div key={video.id} className="flex items-center gap-2 text-sm">
                  <CheckCircle2 className="h-4 w-4 text-success flex-shrink-0" />
                  <div className="truncate flex-1 min-w-0">
                    <span>{video.title}</span>
                    {video.channel_name && (
                      <span className="text-xs text-muted-foreground ml-1.5"> - {video.channel_name}</span>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground flex-shrink-0">
                    <DateTime value={video.downloaded_at} />
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<CheckCircle2 />}
              title="No downloads yet"
              text="Finished downloads are listed here. Open a channel to pick videos to download."
              action={<Link to="/channels" className={buttonClasses("outline")}>Open Channels</Link>}
            />
          )}
        </Card>
      </div>

      {/* Last Scan */}
      {stats.last_scan_at && (
        <p className="text-sm text-muted-foreground flex items-center gap-1">
          <Clock className="h-4 w-4" />
          Last scan: <DateTime value={stats.last_scan_at} />
        </p>
      )}
      </>
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
    <Card>
      <div className="flex items-center gap-3">
        <Icon className={`h-8 w-8 ${color || "text-primary"}`} />
        <div>
          <p className="text-2xl font-bold">{value}</p>
          <p className="text-sm font-medium">{label}</p>
          <p className="text-xs text-muted-foreground">{sub}</p>
        </div>
      </div>
    </Card>
  )
}
