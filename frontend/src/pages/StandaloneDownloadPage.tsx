import { useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import {
  Download,
  Loader2,
  Link as LinkIcon,
  FolderOpen,
  CheckCircle,
  AlertCircle,
  Save,
} from "lucide-react"

export default function StandaloneDownloadPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [url, setUrl] = useState("")
  const [quality, setQuality] = useState("best")
  const [customDir, setCustomDir] = useState("")
  const [useCustomDir, setUseCustomDir] = useState(false)
  const [lastResult, setLastResult] = useState<{ message: string; success: boolean } | null>(null)

  const { data: settings } = useQuery({
    queryKey: ["standalone-settings"],
    queryFn: api.getStandaloneSettings,
    staleTime: 60000,
  })

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
      setUrl("")
      toast(data.message)
    },
    onError: (e: Error) => {
      setLastResult({ message: e.message, success: false })
      toast(e.message, "error")
    },
  })

  const saveDirMutation = useMutation({
    mutationFn: (dir: string) => api.updateStandaloneSettings(dir),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["standalone-settings"] })
      toast(data.message || "Download directory updated")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

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
            defaultValue={settings?.download_dir || ""}
            placeholder={settings?.default_dir || "/downloads"}
            id="default-dir-input"
            className="flex-1 px-3 py-2 rounded-md border bg-background text-sm"
          />
          <button
            onClick={() => {
              const input = document.getElementById("default-dir-input") as HTMLInputElement
              if (input?.value.trim()) saveDirMutation.mutate(input.value.trim())
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
