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
  CheckCircle,
  AlertCircle,
  Clock,
  Trash2,
  FileVideo,
} from "lucide-react"

interface ActiveDownload {
  downloadId: string
  title: string
  thumbnail: string | null
  duration: number | null
}

interface DownloadProgress {
  percent: number
  speed_bytes: number
  downloaded_bytes: number
  total_bytes: number
  eta: number
  startTime: number
}

export default function StandaloneDownloadPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { subscribe } = useWebSocket()

  const [url, setUrl] = useState("")
  const [quality, setQuality] = useState("best")

  const [activeDownloads, setActiveDownloads] = useState<ActiveDownload[]>([])
  const activeRef = useRef<ActiveDownload[]>([])
  const [progress, setProgress] = useState<Record<string, DownloadProgress>>({})
  const [completed, setCompleted] = useState<Set<string>>(new Set())
  const [failed, setFailed] = useState<Record<string, string>>({})
  const maxPercentRef = useRef<Record<string, number>>({})
  const speedHistoryRef = useRef<Record<string, number[]>>({})
  const startTimeRef = useRef<Record<string, number>>({})

  const { data: files, isFetching: filesFetching } = useQuery({
    queryKey: ["quick-download-files"],
    queryFn: api.getQuickDownloadFiles,
    refetchInterval: 10000,
  })

  useEffect(() => { activeRef.current = activeDownloads }, [activeDownloads])

  useEffect(() => {
    return subscribe((msg) => {
      if (msg.type === "quick_download_progress") {
        const p = msg.payload
        const id = p.download_id
        if (!activeRef.current.some((d) => d.downloadId === id)) return

        if (!startTimeRef.current[id]) startTimeRef.current[id] = Date.now()

        const prevMax = maxPercentRef.current[id] || 0
        const smoothed = Math.max(prevMax, p.percent || 0)
        maxPercentRef.current[id] = smoothed

        const rawSpeed = p.speed_bytes || 0
        if (!speedHistoryRef.current[id]) speedHistoryRef.current[id] = []
        const hist = speedHistoryRef.current[id]
        if (rawSpeed > 0) {
          hist.push(rawSpeed)
          if (hist.length > 3) hist.shift()
        }
        const avgSpeed = hist.length > 0 ? hist.reduce((a, b) => a + b, 0) / hist.length : rawSpeed

        setProgress((prev) => ({
          ...prev,
          [id]: {
            percent: Math.round(smoothed * 10) / 10,
            speed_bytes: avgSpeed,
            downloaded_bytes: p.downloaded_bytes || 0,
            total_bytes: p.total_bytes || 0,
            eta: p.eta || 0,
            startTime: startTimeRef.current[id],
          },
        }))
      }

      if (msg.type === "quick_download_complete") {
        const id = msg.payload.download_id
        setCompleted((prev) => new Set(prev).add(id))
        setProgress((prev) => { const next = { ...prev }; delete next[id]; return next })
        delete maxPercentRef.current[id]
        delete speedHistoryRef.current[id]
        queryClient.invalidateQueries({ queryKey: ["quick-download-files"] })
      }

      if (msg.type === "quick_download_failed") {
        const id = msg.payload.download_id
        setFailed((prev) => ({ ...prev, [id]: msg.payload.error || "Download failed" }))
        setProgress((prev) => { const next = { ...prev }; delete next[id]; return next })
        delete maxPercentRef.current[id]
        delete speedHistoryRef.current[id]
      }
    })
  }, [subscribe, queryClient])

  const downloadMutation = useMutation({
    mutationFn: () => api.startQuickDownload({ url: url.trim(), quality }),
    onSuccess: (data) => {
      setActiveDownloads((prev) => [
        { downloadId: data.download_id, title: data.title, thumbnail: data.thumbnail, duration: data.duration },
        ...prev,
      ])
      setUrl("")
      toast(`Downloading: ${data.title}`)
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const deleteMutation = useMutation({
    mutationFn: (filename: string) => api.deleteQuickDownloadFile(filename),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["quick-download-files"] })
      toast("File deleted")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const daysUntilExpiry = (expiresAt: string) => {
    const diff = new Date(expiresAt).getTime() - Date.now()
    const days = Math.ceil(diff / (1000 * 60 * 60 * 24))
    return Math.max(0, days)
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Quick Download</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Download any video by URL. Files are stored temporarily and available for download from your browser for 7 days.
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
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Paste a video URL from any supported platform"
            className="w-full px-3 py-2.5 rounded-md border bg-background text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter" && url.trim() && !downloadMutation.isPending) downloadMutation.mutate()
            }}
          />
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
              <option value="2160p">4K (2160p)</option>
              <option value="1080p">1080p</option>
              <option value="720p">720p</option>
              <option value="480p">480p</option>
            </select>
          </div>
        </div>

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
              Download
            </>
          )}
        </button>
      </div>

      {/* Active Downloads */}
      {activeDownloads.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Active Downloads</h2>
          {activeDownloads.map((dl) => {
            const id = dl.downloadId
            const prog = progress[id]
            const isComplete = completed.has(id)
            const failError = failed[id]
            const isActive = !!prog
            const percent = prog?.percent || 0
            const isProcessing = percent >= 99.5 && isActive
            const elapsed = prog?.startTime ? Math.floor((Date.now() - prog.startTime) / 1000) : 0

            return (
              <div key={id} className="rounded-lg border bg-card overflow-hidden">
                <div className="p-4">
                  <div className="flex items-start gap-3">
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">{dl.title}</p>
                      {dl.duration && (
                        <p className="text-xs text-muted-foreground mt-1">{formatDuration(dl.duration)}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {isComplete && (
                        <span className="flex items-center gap-1 text-xs font-medium text-green-500">
                          <CheckCircle className="h-3.5 w-3.5" /> Ready
                        </span>
                      )}
                      {failError && (
                        <span className="flex items-center gap-1 text-xs font-medium text-red-500">
                          <AlertCircle className="h-3.5 w-3.5" /> Failed
                        </span>
                      )}
                      {!isComplete && !failError && !isActive && (
                        <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
                          <Clock className="h-3.5 w-3.5" /> Starting...
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
                    </div>
                  </div>

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
                          {prog.downloaded_bytes > 0 && prog.total_bytes > 0 && (
                            <span>{formatBytes(prog.downloaded_bytes)} / {formatBytes(prog.total_bytes)}</span>
                          )}
                          {prog.speed_bytes > 0 && <span>{formatSpeed(prog.speed_bytes)}</span>}
                        </div>
                        <div className="flex items-center gap-3">
                          {prog.eta > 0 && <span>ETA: {formatEta(prog.eta)}</span>}
                          {elapsed > 0 && <span>{formatElapsed(elapsed)}</span>}
                        </div>
                      </div>
                    </div>
                  )}

                  {failError && (
                    <div className="mt-3 p-2.5 rounded-md bg-red-50 dark:bg-red-900/20">
                      <p className="text-xs text-red-600 dark:text-red-400 truncate">{failError}</p>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Downloaded Files */}
      <div className="space-y-3">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
          <FileVideo className="h-4 w-4" />
          Downloaded Files
        </h2>
        <p className="text-xs text-muted-foreground">
          Files are automatically removed after 7 days. Download them to your computer before they expire.
        </p>

        {filesFetching && !files && (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading...
          </div>
        )}

        {files && files.length === 0 && (
          <div className="text-center py-8 text-muted-foreground text-sm">
            No files yet. Download a video to get started.
          </div>
        )}

        {files && files.length > 0 && (
          <div className="space-y-2">
            {files.map((file) => {
              const days = daysUntilExpiry(file.expires_at)
              return (
                <div key={file.filename} className="rounded-lg border bg-card p-4 flex items-center gap-3">
                  <FileVideo className="h-5 w-5 text-muted-foreground shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{file.filename}</p>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                      <span>{formatBytes(file.size_bytes)}</span>
                      <span className={days <= 1 ? "text-red-500 font-medium" : ""}>
                        {days === 0 ? "Expires today" : `Expires in ${days} day${days !== 1 ? "s" : ""}`}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <a
                      href={`/api/v1/quick-download/files/${encodeURIComponent(file.filename)}`}
                      download
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
                    >
                      <Download className="h-3.5 w-3.5" /> Save
                    </a>
                    <button
                      onClick={() => deleteMutation.mutate(file.filename)}
                      disabled={deleteMutation.isPending}
                      className="p-1.5 rounded-md hover:bg-accent text-muted-foreground hover:text-red-500 transition-colors"
                      title="Delete file"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
