import { useState, useEffect, useRef } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import { useWebSocket } from "@/hooks/useWebSocket"
import { formatBytes, formatSpeed, formatEta, formatElapsed, formatDuration } from "@/lib/utils"
import {
  Download,
  Loader2,
  Link as LinkIcon,
  FolderOpen,
  CheckCircle,
  AlertCircle,
  Save,
  RotateCcw,
  Clock,
  XCircle,
} from "lucide-react"

interface QueuedVideo {
  videoId: number
  videoDbId: string
  title: string
  thumbnail?: string
  duration?: number
  channel?: string
}

export default function StandaloneDownloadPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { subscribe } = useWebSocket()

  const [url, setUrl] = useState("")
  const [quality, setQuality] = useState("best")
  const [customDir, setCustomDir] = useState("")
  const [useCustomDir, setUseCustomDir] = useState(false)
  const [lastResult, setLastResult] = useState<{ message: string; success: boolean } | null>(null)
  const [defaultDir, setDefaultDir] = useState("")

  // Tracked downloads on this page
  const [trackedVideos, setTrackedVideos] = useState<QueuedVideo[]>([])
  const trackedVideosRef = useRef<QueuedVideo[]>(trackedVideos)
  const [progress, setProgress] = useState<Record<string, any>>({})
  const [completedVideos, setCompletedVideos] = useState<Set<string>>(new Set())
  const [failedVideos, setFailedVideos] = useState<Record<string, string>>({})
  const maxPercentRef = useRef<Record<string, number>>({})
  const speedHistoryRef = useRef<Record<string, number[]>>({})
  const startTimeRef = useRef<Record<string, number>>({})

  const { data: settings } = useQuery({
    queryKey: ["standalone-settings"],
    queryFn: api.getStandaloneSettings,
    staleTime: 60000,
  })

  // Sync default directory from query data
  useEffect(() => {
    if (settings?.download_dir) setDefaultDir(settings.download_dir)
  }, [settings?.download_dir])

  // Keep ref in sync with state
  useEffect(() => { trackedVideosRef.current = trackedVideos }, [trackedVideos])

  // Subscribe to WebSocket for progress on tracked videos
  useEffect(() => {
    return subscribe((msg) => {
      if (msg.type === "download_progress") {
        const p = msg.payload
        const vid = String(p.video_id)

        // Only track videos we queued from this page
        if (!trackedVideosRef.current.some((v) => v.videoDbId === vid)) return

        if (!startTimeRef.current[vid]) startTimeRef.current[vid] = Date.now()

        const prevMax = maxPercentRef.current[vid] || 0
        const smoothedPercent = Math.max(prevMax, p.percent || 0)
        maxPercentRef.current[vid] = smoothedPercent

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

        setProgress((prev) => ({
          ...prev,
          [vid]: {
            ...p,
            percent: Math.round(smoothedPercent * 10) / 10,
            smoothed_speed: smoothedSpeed,
            startTime: startTimeRef.current[vid],
          },
        }))
      }

      if (msg.type === "download_complete") {
        const vid = String(msg.payload.video_id)
        setCompletedVideos((prev) => new Set(prev).add(vid))
        setProgress((prev) => { const next = { ...prev }; delete next[vid]; return next })
        delete maxPercentRef.current[vid]
        delete speedHistoryRef.current[vid]
        queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      }

      if (msg.type === "download_failed") {
        const vid = String(msg.payload.video_id)
        setFailedVideos((prev) => ({ ...prev, [vid]: msg.payload.error || "Download failed" }))
        setProgress((prev) => { const next = { ...prev }; delete next[vid]; return next })
        delete maxPercentRef.current[vid]
        delete speedHistoryRef.current[vid]
        queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      }
    })
  }, [subscribe, queryClient])

  const downloadMutation = useMutation({
    mutationFn: () =>
      api.downloadStandalone({
        url: url.trim(),
        quality,
        ...(useCustomDir && customDir.trim() ? { download_dir: customDir.trim() } : {}),
      }),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      setLastResult({ message: data.message, success: true })

      // Track this video for progress
      if (data.video_id && !data.already_exists) {
        const newVideo: QueuedVideo = {
          videoId: data.video_id,
          videoDbId: String(data.video_id),
          title: data.title || "Unknown",
          thumbnail: data.thumbnail,
          duration: data.duration,
          channel: data.channel,
        }
        setTrackedVideos((prev) => [newVideo, ...prev])
      }

      setUrl("")
      toast(data.message)
    },
    onError: (e: Error) => {
      setLastResult({ message: e.message, success: false })
      toast(e.message, "error")
    },
  })

  const retryMutation = useMutation({
    mutationFn: (videoId: number) => api.retryDownload(videoId),
    onSuccess: (_, videoId) => {
      const vid = String(videoId)
      setFailedVideos((prev) => { const next = { ...prev }; delete next[vid]; return next })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast("Queued for retry")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const saveDirMutation = useMutation({
    mutationFn: (dir: string) => api.updateStandaloneSettings(dir),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["standalone-settings"] })
      toast(data.message || "Download directory updated")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const dismissVideo = (videoDbId: string) => {
    setTrackedVideos((prev) => prev.filter((v) => v.videoDbId !== videoDbId))
    setCompletedVideos((prev) => { const next = new Set(prev); next.delete(videoDbId); return next })
    setFailedVideos((prev) => { const next = { ...prev }; delete next[videoDbId]; return next })
    setProgress((prev) => { const next = { ...prev }; delete next[videoDbId]; return next })
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Download Video</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Download individual videos by URL. These are not tied to any channel subscription.
        </p>
      </div>

      {/* Download Form */}
      <div className="rounded-lg border bg-card p-6 space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1.5">
            <LinkIcon className="h-4 w-4 inline mr-1.5" />
            Video URL
          </label>
          <input
            type="text"
            value={url}
            onChange={(e) => { setUrl(e.target.value); setLastResult(null) }}
            placeholder="https://youtube.com/watch?v=... or any supported URL"
            className="w-full px-3 py-2.5 rounded-md border bg-background text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter" && url.trim()) downloadMutation.mutate()
            }}
          />
          <p className="text-xs text-muted-foreground mt-1">
            Supports YouTube, Rumble, and other platforms supported by yt-dlp.
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-sm font-medium mb-1.5">Quality</label>
            <select
              value={quality}
              onChange={(e) => setQuality(e.target.value)}
              className="w-full px-3 py-2.5 rounded-md border bg-background text-sm"
            >
              <option value="best">Best Available</option>
              <option value="1080p">1080p</option>
              <option value="720p">720p</option>
              <option value="480p">480p</option>
            </select>
          </div>
        </div>

        <div>
          <label className="flex items-center gap-2 cursor-pointer mb-2">
            <input
              type="checkbox"
              checked={useCustomDir}
              onChange={(e) => setUseCustomDir(e.target.checked)}
              className="rounded"
            />
            <span className="text-sm font-medium">Use custom download directory</span>
          </label>
          {useCustomDir && (
            <input
              type="text"
              value={customDir}
              onChange={(e) => setCustomDir(e.target.value)}
              placeholder={settings?.download_dir || "/downloads"}
              className="w-full px-3 py-2.5 rounded-md border bg-background text-sm"
            />
          )}
        </div>

        {lastResult && (
          <div className={`flex items-center gap-2 p-3 rounded-md text-sm ${
            lastResult.success
              ? "bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400"
              : "bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400"
          }`}>
            {lastResult.success ? (
              <CheckCircle className="h-4 w-4 flex-shrink-0" />
            ) : (
              <AlertCircle className="h-4 w-4 flex-shrink-0" />
            )}
            {lastResult.message}
          </div>
        )}

        <button
          onClick={() => downloadMutation.mutate()}
          disabled={!url.trim() || downloadMutation.isPending}
          className="w-full flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {downloadMutation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Fetching video info...
            </>
          ) : (
            <>
              <Download className="h-4 w-4" />
              Download Video
            </>
          )}
        </button>
      </div>

      {/* Active / Recent Downloads */}
      {trackedVideos.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Downloads</h2>
          {trackedVideos.map((video) => {
            const vid = video.videoDbId
            const prog = progress[vid]
            const isComplete = completedVideos.has(vid)
            const failError = failedVideos[vid]
            const isActive = !!prog
            const percent = prog?.percent || 0
            const isProcessing = percent >= 99.5 && isActive
            const elapsed = prog?.startTime ? Math.floor((Date.now() - prog.startTime) / 1000) : 0

            return (
              <div key={vid} className="rounded-lg border bg-card overflow-hidden">
                <div className="p-4">
                  <div className="flex items-start gap-3">
                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">{video.title}</p>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground mt-1">
                        {video.channel && <span>{video.channel}</span>}
                        {video.duration && <span>{formatDuration(video.duration)}</span>}
                      </div>
                    </div>

                    {/* Status badge + dismiss */}
                    <div className="flex items-center gap-2 shrink-0">
                      {isComplete && (
                        <span className="flex items-center gap-1 text-xs font-medium text-green-500">
                          <CheckCircle className="h-3.5 w-3.5" /> Downloaded
                        </span>
                      )}
                      {failError && (
                        <span className="flex items-center gap-1 text-xs font-medium text-red-500">
                          <AlertCircle className="h-3.5 w-3.5" /> Failed
                        </span>
                      )}
                      {!isComplete && !failError && !isActive && (
                        <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
                          <Clock className="h-3.5 w-3.5" /> Queued
                        </span>
                      )}
                      {isActive && !isProcessing && (
                        <span className="flex items-center gap-1 text-xs font-medium text-blue-500">
                          <Download className="h-3.5 w-3.5" /> {percent}%
                        </span>
                      )}
                      {isProcessing && (
                        <span className="flex items-center gap-1 text-xs font-medium text-yellow-500">
                          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Processing...
                        </span>
                      )}
                      {(isComplete || failError) && (
                        <button
                          onClick={() => dismissVideo(vid)}
                          className="p-1 hover:bg-accent rounded"
                          title="Dismiss"
                        >
                          <XCircle className="h-4 w-4 text-muted-foreground" />
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Progress bar */}
                  {isActive && (
                    <div className="mt-3 space-y-1.5">
                      <div className="h-2.5 bg-muted rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all duration-700 ease-out ${
                            isProcessing ? "bg-yellow-500 animate-pulse" : "bg-primary"
                          }`}
                          style={{ width: `${Math.min(percent, 100)}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <div className="flex items-center gap-3">
                          {prog?.downloaded_bytes > 0 && prog?.total_bytes > 0 && (
                            <span>{formatBytes(prog.downloaded_bytes)} / {formatBytes(prog.total_bytes)}</span>
                          )}
                          {prog?.smoothed_speed > 0 && <span>{formatSpeed(prog.smoothed_speed)}</span>}
                        </div>
                        <div className="flex items-center gap-3">
                          {prog?.eta > 0 && <span>ETA: {formatEta(prog.eta)}</span>}
                          {elapsed > 0 && <span>{formatElapsed(elapsed)}</span>}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Failed: show error + retry */}
                  {failError && (
                    <div className="mt-3 flex items-center justify-between p-2.5 rounded-md bg-red-50 dark:bg-red-900/20">
                      <p className="text-xs text-red-600 dark:text-red-400 truncate flex-1">{failError}</p>
                      <button
                        onClick={() => retryMutation.mutate(video.videoId)}
                        disabled={retryMutation.isPending}
                        className="flex items-center gap-1 px-2.5 py-1 text-xs rounded-md border border-red-300 text-red-600 hover:bg-red-100 dark:hover:bg-red-900/40 disabled:opacity-50 shrink-0 ml-2"
                      >
                        <RotateCcw className="h-3 w-3" /> Retry
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Default Download Directory Setting */}
      <div className="rounded-lg border bg-card p-6 space-y-3">
        <h2 className="text-sm font-semibold flex items-center gap-2">
          <FolderOpen className="h-4 w-4" />
          Default Download Directory
        </h2>
        <p className="text-xs text-muted-foreground">
          Set the default directory for standalone video downloads. Individual downloads can override this.
        </p>
        <div className="flex gap-2">
          <input
            type="text"
            value={defaultDir}
            onChange={(e) => setDefaultDir(e.target.value)}
            placeholder={settings?.default_dir || "/downloads"}
            className="flex-1 px-3 py-2 rounded-md border bg-background text-sm"
          />
          <button
            onClick={() => {
              if (defaultDir.trim()) saveDirMutation.mutate(defaultDir.trim())
            }}
            disabled={saveDirMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
          >
            {saveDirMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
