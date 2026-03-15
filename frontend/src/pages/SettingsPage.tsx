import { useState, useRef, useEffect } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import {
  Upload,
  Key,
  Shield,
  FileText,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Loader2,
  Save,
} from "lucide-react"

export default function SettingsPage() {
  const [tab, setTab] = useState<"general" | "auth" | "naming" | "ytdlp" | "antidetect">("general")

  const tabs = [
    { key: "general", label: "General" },
    { key: "auth", label: "Authentication" },
    { key: "naming", label: "Naming" },
    { key: "ytdlp", label: "yt-dlp" },
    { key: "antidetect", label: "Anti-Detection" },
  ] as const

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Settings</h1>

      <div className="flex gap-1 border-b">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "general" && <GeneralTab />}
      {tab === "auth" && <AuthTab />}
      {tab === "naming" && <NamingTab />}
      {tab === "ytdlp" && <YtdlpTab />}
      {tab === "antidetect" && <AntiDetectTab />}
    </div>
  )
}

function GeneralTab() {
  const { toast } = useToast()
  const { data: stats } = useQuery({ queryKey: ["dashboard-stats"], queryFn: api.getStats })

  const scanAllMutation = useMutation({
    mutationFn: api.scanAll,
    onSuccess: (data: any) => toast(data.message || "Scan started"),
    onError: (e: Error) => toast(e.message, "error"),
  })

  return (
    <div className="space-y-6">
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <h3 className="font-semibold">Scan Controls</h3>
        <p className="text-sm text-muted-foreground">
          Channels are scanned daily at 3 AM by default. You can trigger a manual scan here.
        </p>
        <button
          onClick={() => scanAllMutation.mutate()}
          disabled={scanAllMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
        >
          {scanAllMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Scan All Channels Now
        </button>
      </div>

      <div className="rounded-lg border bg-card p-4 space-y-2">
        <h3 className="font-semibold">System Info</h3>
        <div className="grid gap-2 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Total Channels</span>
            <span>{stats?.total_channels || 0}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Total Downloaded</span>
            <span>{stats?.total_downloaded || 0}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Storage Used</span>
            <span>{stats?.storage_used_formatted || "0 B"}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">yt-dlp Version</span>
            <span>{stats?.ytdlp_version || "unknown"}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function AuthTab() {
  const { toast } = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [apiKey, setApiKey] = useState("")

  const { data: authStatus, refetch: refetchAuth } = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.getAuthStatus,
  })

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadCookies(file),
    onSuccess: () => { refetchAuth(); toast("Cookies uploaded") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const apiKeyMutation = useMutation({
    mutationFn: (key: string) => api.setApiKey(key),
    onSuccess: () => { refetchAuth(); setApiKey(""); toast("API key saved") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const deleteCookiesMutation = useMutation({
    mutationFn: api.deleteCookies,
    onSuccess: () => { refetchAuth(); toast("Cookies removed") },
  })

  return (
    <div className="space-y-6">
      {/* PO Tokens */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">PO Tokens (Primary)</h3>
        </div>
        <p className="text-sm text-muted-foreground">
          PO tokens are generated automatically by the built-in server. No configuration needed.
        </p>
        <div className="flex items-center gap-2">
          {authStatus?.pot_status === "enabled" ? (
            <CheckCircle2 className="h-4 w-4 text-green-500" />
          ) : (
            <XCircle className="h-4 w-4 text-red-500" />
          )}
          <span className="text-sm">{authStatus?.pot_message || "Checking..."}</span>
        </div>
      </div>

      {/* YouTube API Key */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Key className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">YouTube Data API Key (Optional)</h3>
        </div>
        <p className="text-sm text-muted-foreground">
          Provides more reliable channel/video discovery. Free at Google Cloud Console. Falls back to yt-dlp if not set.
        </p>
        <div className="flex items-center gap-2">
          {authStatus?.api_key_configured ? (
            <CheckCircle2 className="h-4 w-4 text-green-500" />
          ) : (
            <span className="text-sm text-muted-foreground">Not configured</span>
          )}
          {authStatus?.api_key_configured && <span className="text-sm">API key configured</span>}
        </div>
        <div className="flex gap-2">
          <input
            type="password"
            placeholder="Enter API key..."
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="flex-1 px-3 py-2 rounded-md border bg-background text-sm"
          />
          <button
            onClick={() => apiKeyMutation.mutate(apiKey)}
            disabled={!apiKey || apiKeyMutation.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 disabled:opacity-50"
          >
            Save
          </button>
        </div>
        {apiKeyMutation.error && (
          <p className="text-sm text-red-500">{(apiKeyMutation.error as Error).message}</p>
        )}
      </div>

      {/* Cookies (Optional) */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <FileText className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">Cookies (Optional Fallback)</h3>
        </div>
        <p className="text-sm text-muted-foreground">
          Only needed if PO tokens alone aren't sufficient. Export cookies.txt from your browser.
        </p>
        <div className="flex items-center gap-2">
          <span className="text-sm">{authStatus?.cookies_message || "Not configured"}</span>
        </div>
        <div className="flex gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".txt"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) uploadMutation.mutate(file)
            }}
            className="hidden"
          />
          <button
            onClick={() => fileRef.current?.click()}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent"
          >
            <Upload className="h-4 w-4" />
            Upload cookies.txt
          </button>
          {authStatus?.cookies_status !== "not_configured" && (
            <button
              onClick={() => deleteCookiesMutation.mutate()}
              className="px-3 py-2 text-sm rounded-md border border-red-300 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
            >
              Remove
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function NamingTab() {
  const { toast } = useToast()
  const [template, setTemplate] = useState(
    "{channel_name}/Season {season}/S{season}E{episode} - {title} - {upload_date} - [{video_id}]"
  )

  const { data: settings } = useQuery({
    queryKey: ["app-settings"],
    queryFn: api.getSettings,
  })

  useEffect(() => {
    if (settings?.naming_template) {
      setTemplate(settings.naming_template)
    }
  }, [settings])

  const saveMutation = useMutation({
    mutationFn: () => api.updateSettings({ naming_template: template }),
    onSuccess: () => toast("Naming template saved"),
    onError: (e: Error) => toast(e.message, "error"),
  })

  const previewMutation = useMutation({
    mutationFn: (tmpl: string) => api.previewNaming({ template: tmpl }),
  })

  return (
    <div className="space-y-6">
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <h3 className="font-semibold">Default Naming Template</h3>
        <p className="text-sm text-muted-foreground">
          Available variables: {"{channel_name}"}, {"{season}"}, {"{episode}"}, {"{title}"}, {"{upload_date}"}, {"{video_id}"}
        </p>
        <textarea
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          rows={2}
          className="w-full px-3 py-2 rounded-md border bg-background font-mono text-sm"
        />
        <div className="flex gap-2">
          <button
            onClick={() => previewMutation.mutate(template)}
            className="px-4 py-2 rounded-md border text-sm hover:bg-accent"
          >
            Preview
          </button>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 disabled:opacity-50"
          >
            <Save className="h-4 w-4" />
            Save
          </button>
        </div>
        {previewMutation.data && (
          <div className="rounded-md bg-muted p-3">
            <p className="text-sm font-mono">{previewMutation.data.full_path}</p>
          </div>
        )}
      </div>
    </div>
  )
}

function YtdlpTab() {
  const { toast } = useToast()
  const { data: version, refetch } = useQuery({
    queryKey: ["ytdlp-version"],
    queryFn: api.getYtdlpVersion,
  })

  const updateMutation = useMutation({
    mutationFn: api.updateYtdlp,
    onSuccess: (data: any) => {
      refetch()
      toast(data.success ? `Updated to ${data.version}` : data.message, data.success ? "success" : "warning")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  return (
    <div className="space-y-6">
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <h3 className="font-semibold">yt-dlp</h3>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm">Current version: <span className="font-mono">{version?.version || "unknown"}</span></p>
            <p className="text-xs text-muted-foreground">
              yt-dlp is checked for updates daily at 4 AM
            </p>
          </div>
          <button
            onClick={() => updateMutation.mutate()}
            disabled={updateMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Update Now
          </button>
        </div>
      </div>
    </div>
  )
}

function AntiDetectTab() {
  const { toast } = useToast()
  const [minDelay, setMinDelay] = useState(10)
  const [maxDelay, setMaxDelay] = useState(30)
  const [uaRotation, setUaRotation] = useState(true)
  const [jitter, setJitter] = useState(true)

  const { data: settings } = useQuery({
    queryKey: ["app-settings"],
    queryFn: api.getSettings,
  })

  useEffect(() => {
    if (settings) {
      if (settings.download_delay_min != null) setMinDelay(Number(settings.download_delay_min))
      if (settings.download_delay_max != null) setMaxDelay(Number(settings.download_delay_max))
      if (settings.user_agent_rotation != null) setUaRotation(settings.user_agent_rotation === true || settings.user_agent_rotation === "true")
      if (settings.jitter_enabled != null) setJitter(settings.jitter_enabled === true || settings.jitter_enabled === "true")
    }
  }, [settings])

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        download_delay_min: minDelay,
        download_delay_max: maxDelay,
        user_agent_rotation: uaRotation,
        jitter_enabled: jitter,
      }),
    onSuccess: () => toast("Anti-detection settings saved"),
    onError: (e: Error) => toast(e.message, "error"),
  })

  return (
    <div className="space-y-6">
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <h3 className="font-semibold">Download Delays</h3>
        <p className="text-sm text-muted-foreground">
          Add delays between downloads to avoid YouTube rate limiting.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-sm mb-1">Min Delay (seconds)</label>
            <input
              type="number"
              value={minDelay}
              onChange={(e) => setMinDelay(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background"
            />
          </div>
          <div>
            <label className="block text-sm mb-1">Max Delay (seconds)</label>
            <input
              type="number"
              value={maxDelay}
              onChange={(e) => setMaxDelay(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background"
            />
          </div>
        </div>
      </div>

      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="font-semibold">User-Agent Rotation</h3>
        <p className="text-sm text-muted-foreground">
          Rotate user-agent strings between downloads to appear as different browsers.
        </p>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={uaRotation}
            onChange={(e) => setUaRotation(e.target.checked)}
            className="rounded"
          />
          <span className="text-sm">Enable user-agent rotation</span>
        </label>
      </div>

      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="font-semibold">Random Jitter</h3>
        <p className="text-sm text-muted-foreground">
          Add random extra delay (0-10s) to make download timing less predictable.
        </p>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={jitter}
            onChange={(e) => setJitter(e.target.checked)}
            className="rounded"
          />
          <span className="text-sm">Enable jitter</span>
        </label>
      </div>

      <button
        onClick={() => saveMutation.mutate()}
        disabled={saveMutation.isPending}
        className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 disabled:opacity-50"
      >
        {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save Settings
      </button>
    </div>
  )
}
