import { useState, useRef, useEffect } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import DiagnosticsTab from "@/components/settings/DiagnosticsTab"
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
  AlertTriangle,
  Bell,
  Send,
  Download,
  Clock,
  ExternalLink,
  Globe,
  Settings,
} from "lucide-react"

type SettingsTab = "general" | "media" | "auth" | "antidetect" | "connect" | "diagnostics"

export default function SettingsPage() {
  const [tab, setTab] = useState<SettingsTab>(() => {
    const saved = localStorage.getItem("settings_tab")
    // Migrate old tab keys to new ones
    const migrations: Record<string, SettingsTab> = {
      naming: "media",
      ytdlp: "general",
      notifications: "connect",
    }
    return (migrations[saved || ""] || saved || "general") as SettingsTab
  })

  const tabs = [
    { key: "general", label: "General" },
    { key: "media", label: "Media Management" },
    { key: "auth", label: "Authentication" },
    { key: "antidetect", label: "Anti-Detection" },
    { key: "connect", label: "Connect" },
    { key: "diagnostics", label: "Diagnostics" },
  ] as const

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Settings</h1>

      <div className="flex gap-1 border-b">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); localStorage.setItem("settings_tab", t.key) }}
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
      {tab === "media" && <MediaManagementTab />}
      {tab === "auth" && <AuthTab />}
      {tab === "antidetect" && <AntiDetectTab />}
      {tab === "connect" && <NotificationsTab />}
      {tab === "diagnostics" && <DiagnosticsTab />}
    </div>
  )
}

const SCAN_PRESETS = [
  { label: "Every 6 hours", value: "0 */6 * * *" },
  { label: "Every 12 hours", value: "0 */12 * * *" },
  { label: "Daily at 3 AM", value: "0 3 * * *" },
  { label: "Twice daily (8 AM & 8 PM)", value: "0 8,20 * * *" },
  { label: "Custom", value: "custom" },
]

function GeneralTab() {
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const importRef = useRef<HTMLInputElement>(null)
  const { data: stats } = useQuery({ queryKey: ["dashboard-stats"], queryFn: api.getStats })
  const { data: ytdlpVersion, refetch: refetchYtdlp } = useQuery({
    queryKey: ["ytdlp-version"],
    queryFn: api.getYtdlpVersion,
  })
  const ytdlpUpdateMutation = useMutation({
    mutationFn: api.updateYtdlp,
    onSuccess: (data: any) => {
      refetchYtdlp()
      toast(data.success ? `Updated to ${data.version}` : data.message, data.success ? "success" : "warning")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  // Settings state
  const [scanCron, setScanCron] = useState("0 3 * * *")
  const [customCron, setCustomCron] = useState("")
  const [isCustomCron, setIsCustomCron] = useState(false)
  const [defaultQuality, setDefaultQuality] = useState("best")
  const [maxConcurrent, setMaxConcurrent] = useState(1)
  const [maxRetries, setMaxRetries] = useState(3)
  const [logLevel, setLogLevel] = useState("info")

  const { data: settings } = useQuery({
    queryKey: ["app-settings"],
    queryFn: api.getSettings,
  })

  useEffect(() => {
    if (settings) {
      const cron = String(settings.global_schedule_cron || "0 3 * * *")
      const preset = SCAN_PRESETS.find(p => p.value === cron)
      if (preset) {
        setScanCron(cron)
        setIsCustomCron(false)
      } else {
        setScanCron("custom")
        setCustomCron(cron)
        setIsCustomCron(true)
      }
      if (settings.default_quality) setDefaultQuality(String(settings.default_quality))
      if (settings.max_concurrent_downloads != null) setMaxConcurrent(Number(settings.max_concurrent_downloads))
      if (settings.max_retries != null) setMaxRetries(Number(settings.max_retries))
      if (settings.log_level) setLogLevel(String(settings.log_level))
    }
  }, [settings])

  const scanAllMutation = useMutation({
    mutationFn: api.scanAll,
    onSuccess: (data: any) => toast(data.message || "Scan started"),
    onError: (e: Error) => toast(e.message, "error"),
  })

  const saveSettingsMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        global_schedule_cron: isCustomCron ? customCron : scanCron,
        default_quality: defaultQuality,
        max_concurrent_downloads: maxConcurrent,
        max_retries: maxRetries,
        log_level: logLevel,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      toast("Settings saved  - changes take effect immediately")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const handleExport = async () => {
    try {
      const data = await api.exportConfig()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `channelhoarder-config-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      toast("Config exported")
    } catch (e: any) {
      toast(e.message, "error")
    }
  }

  const handleImport = async (file: File) => {
    try {
      const result = await api.importConfig(file)
      toast(`Imported ${result.imported_settings} settings, ${result.imported_channels} channels (${result.skipped_channels} already existed)`)
    } catch (e: any) {
      toast(e.message, "error")
    }
  }

  return (
    <div className="space-y-6">
      {/* Scan Controls */}
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <div className="flex items-center gap-2">
          <Clock className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">Scan Schedule</h3>
          <HelpIcon text="Cron schedule for checking channels." anchor="automatic-scanning" />
        </div>
        <p className="text-sm text-muted-foreground">
          How often to check subscribed channels for new uploads.
        </p>
        <div className="max-w-xs">
          <select
            value={isCustomCron ? "custom" : scanCron}
            onChange={(e) => {
              if (e.target.value === "custom") {
                setIsCustomCron(true)
                setScanCron("custom")
                setCustomCron(String(settings?.global_schedule_cron || "0 3 * * *"))
              } else {
                setIsCustomCron(false)
                setScanCron(e.target.value)
              }
            }}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm"
          >
            {SCAN_PRESETS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
          {isCustomCron && (
            <input
              type="text"
              placeholder="0 3 * * *"
              value={customCron}
              onChange={(e) => setCustomCron(e.target.value)}
              className="w-full mt-2 px-3 py-2 rounded-md border bg-background font-mono text-sm"
            />
          )}
          <p className="text-xs text-muted-foreground mt-1">
            {isCustomCron ? "Standard 5-field cron expression" : ""}
          </p>
        </div>
        <button
          onClick={() => scanAllMutation.mutate()}
          disabled={scanAllMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
        >
          {scanAllMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Scan All Channels Now
        </button>
      </div>

      {/* Download & System Settings */}
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <div className="flex items-center gap-2">
          <Settings className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">Download Settings</h3>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-sm mb-1">Default Quality</label>
            <select
              value={defaultQuality}
              onChange={(e) => setDefaultQuality(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            >
              <option value="best">Best Available</option>
              <option value="2160p">4K (2160p)</option>
              <option value="1080p">1080p</option>
              <option value="720p">720p</option>
              <option value="480p">480p</option>
            </select>
            <p className="text-xs text-muted-foreground mt-1">Default quality for new channels</p>
          </div>
          <div>
            <label className="block text-sm mb-1">Max Concurrent Downloads</label>
            <input
              type="number"
              min={1}
              max={5}
              value={maxConcurrent}
              onChange={(e) => setMaxConcurrent(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
            <p className="text-xs text-muted-foreground mt-1">Simultaneous downloads (1-5)</p>
          </div>
          <div>
            <label className="block text-sm mb-1">Max Retries</label>
            <input
              type="number"
              min={1}
              max={10}
              value={maxRetries}
              onChange={(e) => setMaxRetries(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
            <p className="text-xs text-muted-foreground mt-1">Retry attempts for failed downloads</p>
          </div>
          <div>
            <label className="block text-sm mb-1">Log Level</label>
            <select
              value={logLevel}
              onChange={(e) => setLogLevel(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            >
              <option value="debug">Debug</option>
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="error">Error</option>
            </select>
            <p className="text-xs text-muted-foreground mt-1">Set to debug for troubleshooting</p>
          </div>
        </div>
      </div>

      {/* Save button */}
      <button
        onClick={() => saveSettingsMutation.mutate()}
        disabled={saveSettingsMutation.isPending}
        className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 disabled:opacity-50"
      >
        {saveSettingsMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save Settings
      </button>

      {/* System Info */}
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
        </div>
      </div>

      {/* yt-dlp */}
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <div className="flex items-center gap-2">
          <h3 className="font-semibold">yt-dlp</h3>
          <HelpIcon text="Video download engine. Keep updated for YouTube compatibility." anchor="tech-stack" />
        </div>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm">Current version: <span className="font-mono">{ytdlpVersion?.version || "unknown"}</span></p>
            <p className="text-xs text-muted-foreground">
              yt-dlp is checked for updates daily at 4 AM
            </p>
          </div>
          <button
            onClick={() => ytdlpUpdateMutation.mutate()}
            disabled={ytdlpUpdateMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {ytdlpUpdateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Update Now
          </button>
        </div>
      </div>

      {/* Backup & Restore */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="font-semibold">Backup & Restore</h3>
        <p className="text-sm text-muted-foreground">
          Export all settings and channel subscriptions as JSON. Import to restore on a new instance.
        </p>
        <div className="flex gap-2">
          <button
            onClick={handleExport}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
          >
            <Download className="h-4 w-4" />
            Export Config
          </button>
          <input
            ref={importRef}
            type="file"
            accept=".json"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleImport(file)
            }}
            className="hidden"
          />
          <button
            onClick={() => importRef.current?.click()}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent"
          >
            <Upload className="h-4 w-4" />
            Import Config
          </button>
        </div>
      </div>
    </div>
  )
}

function AuthTab() {
  const { toast } = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [apiKey, setApiKey] = useState("")
  const queryClient = useQueryClient()

  const { data: authStatus, refetch: refetchAuth, error: authError, isLoading: authLoading } = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.getAuthStatus,
    retry: 2,
  })

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadCookies(file),
    onSuccess: (data: any) => {
      refetchAuth()
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      toast(data.message || "Cookies uploaded")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const apiKeyMutation = useMutation({
    mutationFn: (key: string) => api.setApiKey(key),
    onSuccess: (data: any) => { refetchAuth(); setApiKey(""); toast(data.valid ? "API key saved and validated" : "API key saved but validation failed - check the key") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const validateMutation = useMutation({
    mutationFn: api.validateCookies,
    onSuccess: (data: any) => {
      refetchAuth()
      toast(data.status === "healthy" ? "Cookies are valid" : `Cookies: ${data.message}`, data.status === "healthy" ? "success" : "error")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const deleteCookiesMutation = useMutation({
    mutationFn: api.deleteCookies,
    onSuccess: () => { refetchAuth(); toast("Cookies removed") },
  })

  const isExpired = authStatus?.cookies_status === "expired"
  const hasCookies = authStatus?.cookies_status === "present" || authStatus?.cookies_status === "warning" || isExpired

  return (
    <div className="space-y-6">
      {/* Error banner */}
      {authError && (
        <div className="rounded-lg border border-red-300 bg-red-50 dark:bg-red-900/20 p-4">
          <p className="text-sm text-red-600 dark:text-red-400 font-medium">Failed to load auth status</p>
          <p className="text-xs text-red-500 mt-1">{(authError as Error).message}</p>
          <button onClick={() => refetchAuth()} className="mt-2 text-xs text-red-600 underline">Retry</button>
        </div>
      )}

      {/* Cookie expired warning */}
      {isExpired && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-900/20 p-4 flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 text-amber-500 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-medium text-amber-700 dark:text-amber-300">Cookies Expired</p>
            <p className="text-sm text-amber-600 dark:text-amber-400 mt-1">
              Your YouTube cookies have expired and the download queue has been auto-paused.
              Export fresh cookies from your browser and upload them below to resume downloads.
            </p>
          </div>
        </div>
      )}

      {/* PO Tokens */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">PO Tokens</h3>
          <HelpIcon text="Auto-generated tokens for YouTube auth. Cookies take priority when available." anchor="authentication" />
        </div>
        <p className="text-sm text-muted-foreground">
          PO tokens are generated automatically by the built-in server. When cookies are available, they take priority.
        </p>
        <div className="flex items-center gap-2">
          {authLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : authStatus?.pot_status === "enabled" ? (
            <CheckCircle2 className="h-4 w-4 text-green-500" />
          ) : (
            <XCircle className="h-4 w-4 text-red-500" />
          )}
          <span className="text-sm">{authLoading ? "Checking..." : authStatus?.pot_message || "Unknown"}</span>
        </div>
      </div>

      {/* Cookie Upload */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <FileText className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">Cookie Authentication</h3>
          <HelpIcon text="Upload cookies from your browser for YouTube auth." anchor="cookie-authentication-optional" />
        </div>
        <p className="text-sm text-muted-foreground">
          Export cookies from your browser using a browser extension (e.g. "Get cookies.txt LOCALLY") and upload the file here.
        </p>
        <div className="flex items-center gap-2">
          {authLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : isExpired ? (
            <>
              <XCircle className="h-4 w-4 text-red-500" />
              <span className="text-sm text-red-500">{authStatus?.cookies_message}</span>
            </>
          ) : hasCookies ? (
            <>
              <CheckCircle2 className="h-4 w-4 text-green-500" />
              <span className="text-sm">{authStatus?.cookies_message}</span>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">{authStatus?.cookies_message || "No cookies uploaded"}</span>
          )}
        </div>

        {/* Cookie health bar */}
        {hasCookies && authStatus?.cookies_age_hours != null && (
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                Cookie Age
              </span>
              <span>
                {authStatus.cookies_age_hours < 24
                  ? `${Math.round(authStatus.cookies_age_hours)}h`
                  : `${Math.round(authStatus.cookies_age_hours / 24)}d`}
              </span>
            </div>
            <div className="w-full h-2 rounded-full bg-muted overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  isExpired ? "bg-red-500" :
                  authStatus.cookies_age_hours > 168 ? "bg-red-500" :
                  authStatus.cookies_age_hours > 72 ? "bg-amber-500" :
                  "bg-green-500"
                }`}
                style={{ width: `${Math.min(100, (authStatus.cookies_age_hours / 168) * 100)}%` }}
              />
            </div>
            {authStatus.last_successful_auth && (
              <p className="text-xs text-muted-foreground">
                Last successful download: {new Date(authStatus.last_successful_auth).toLocaleString()}
              </p>
            )}
          </div>
        )}

        <div className="flex gap-2 flex-wrap">
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
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
          >
            <Upload className="h-4 w-4" />
            Upload cookies.txt
          </button>
          {hasCookies && (
            <>
              <button
                onClick={() => validateMutation.mutate()}
                disabled={validateMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
              >
                {validateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                Validate
              </button>
              <button
                onClick={() => deleteCookiesMutation.mutate()}
                className="px-3 py-2 text-sm rounded-md border border-red-300 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
              >
                Remove
              </button>
            </>
          )}
        </div>
        {isExpired && (
          <p className="text-xs text-muted-foreground">
            Tip: After uploading fresh cookies, the queue will automatically resume.
          </p>
        )}
      </div>

      {/* Browser Cookie Sync (Tampermonkey) */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Globe className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">Browser Cookie Sync</h3>
          <HelpIcon text="Auto-sync cookies from your browser using Tampermonkey." anchor="cookie-authentication-optional" />
        </div>
        <p className="text-sm text-muted-foreground">
          Automatically sync your browser cookies to ChannelHoarder using a Tampermonkey userscript.
          Cookies are exported each time you load or refresh a YouTube page.
        </p>
        <div className="flex gap-2 flex-wrap items-center">
          <a
            href="/api/v1/settings/userscript.user.js"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90"
          >
            <ExternalLink className="h-4 w-4" />
            Install Tampermonkey Script
          </a>
        </div>
        <p className="text-xs text-muted-foreground">
          Requires the{" "}
          <a
            href="https://www.tampermonkey.net/"
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-foreground"
          >
            Tampermonkey
          </a>{" "}
          browser extension. The script comes pre-configured with this server's address.
        </p>
      </div>

      {/* YouTube API Key */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Key className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">YouTube Data API Key (Optional)</h3>
          <HelpIcon text="Free API key from Google Cloud Console. 10,000 units/day." anchor="youtube-data-api-key-optional" />
        </div>
        <p className="text-sm text-muted-foreground">
          Provides more reliable channel discovery, thumbnails, and video metadata. Falls back to yt-dlp if not set.
        </p>
        <div className="flex items-center gap-2">
          {authLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : authStatus?.api_key_configured ? (
            <>
              <CheckCircle2 className="h-4 w-4 text-green-500" />
              <span className="text-sm">API key configured</span>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">Not configured</span>
          )}
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Paste your API key here..."
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
    </div>
  )
}

function MediaManagementTab() {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [template, setTemplate] = useState(
    "{channel_name}/Season {season}/S{season}E{episode} - {title} - {upload_date} - [{video_id}]"
  )
  const [shortsEnabled, setShortsEnabled] = useState(false)
  const [livestreamsEnabled, setLivestreamsEnabled] = useState(false)
  const [subtitlesEnabled, setSubtitlesEnabled] = useState(false)
  const [chaptersEnabled, setChaptersEnabled] = useState(false)
  const [setPermissions, setSetPermissions] = useState(false)
  const [chmodFolder, setChmodFolder] = useState("755")
  const [chmodFile, setChmodFile] = useState("644")
  const [chownGroup, setChownGroup] = useState("")
  const [maxDuration, setMaxDuration] = useState(0)

  const { data: settings } = useQuery({
    queryKey: ["app-settings"],
    queryFn: api.getSettings,
  })

  useEffect(() => {
    if (settings) {
      if (settings.naming_template) setTemplate(String(settings.naming_template))
      if (settings.shorts_enabled != null) setShortsEnabled(settings.shorts_enabled === true || settings.shorts_enabled === "true")
      if (settings.livestreams_enabled != null) setLivestreamsEnabled(settings.livestreams_enabled === true || settings.livestreams_enabled === "true")
      if (settings.subtitles_enabled != null) setSubtitlesEnabled(settings.subtitles_enabled === true || settings.subtitles_enabled === "true")
      if (settings.chapters_enabled != null) setChaptersEnabled(settings.chapters_enabled === true || settings.chapters_enabled === "true")
      if (settings.set_permissions != null) setSetPermissions(settings.set_permissions === true || settings.set_permissions === "true")
      if (settings.chmod_folder) setChmodFolder(String(settings.chmod_folder))
      if (settings.chmod_file) setChmodFile(String(settings.chmod_file))
      if (settings.chown_group) setChownGroup(String(settings.chown_group))
      if (settings.max_video_duration != null) setMaxDuration(Math.round(Number(settings.max_video_duration) / 3600))
    }
  }, [settings])

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        naming_template: template,
        shorts_enabled: shortsEnabled,
        livestreams_enabled: livestreamsEnabled,
        subtitles_enabled: subtitlesEnabled,
        chapters_enabled: chaptersEnabled,
        set_permissions: setPermissions,
        chmod_folder: chmodFolder,
        chmod_file: chmodFile,
        chown_group: chownGroup || null,
        max_video_duration: maxDuration > 0 ? maxDuration * 3600 : 0,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      toast("Media settings saved")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const previewMutation = useMutation({
    mutationFn: (tmpl: string) => api.previewNaming({ template: tmpl }),
  })

  return (
    <div className="space-y-6">
      {/* Naming Template */}
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <div className="flex items-center gap-2">
          <h3 className="font-semibold">Default Naming Template</h3>
          <HelpIcon text="Controls how downloaded files are named and organized." anchor="file-organization" />
        </div>
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
        </div>
        {previewMutation.data && (
          <div className="rounded-md bg-muted p-3">
            <p className="text-sm font-mono">{previewMutation.data.full_path}</p>
          </div>
        )}
      </div>

      {/* YouTube Shorts */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="font-semibold">YouTube Shorts</h3>
        <p className="text-sm text-muted-foreground">
          By default, YouTube Shorts (videos under 30 seconds) are excluded from downloads. Enable this
          to allow channels to opt-in to downloading shorts. The threshold is configurable per channel via the min duration setting.
        </p>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={shortsEnabled}
            onChange={(e) => setShortsEnabled(e.target.checked)}
            className="rounded"
          />
          <span className="text-sm">Allow shorts downloading (per-channel opt-in)</span>
        </label>
        <p className="text-xs text-muted-foreground">
          {shortsEnabled
            ? "Channels can individually enable shorts downloading in their settings."
            : "Shorts are excluded from all channel downloads."}
        </p>
      </div>

      {/* Livestreams */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="font-semibold">Livestreams</h3>
        <p className="text-sm text-muted-foreground">
          By default, YouTube livestreams and premieres are excluded from downloads. Enable this to allow channels to opt-in to downloading livestreams. Scheduled livestreams that haven't started will be automatically skipped.
        </p>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={livestreamsEnabled}
            onChange={(e) => setLivestreamsEnabled(e.target.checked)}
            className="rounded"
          />
          <span className="text-sm">Allow livestream downloading (per-channel opt-in)</span>
        </label>
        <p className="text-xs text-muted-foreground">
          {livestreamsEnabled
            ? "Channels can individually enable livestream downloading in their settings."
            : "Livestreams are excluded from all channel downloads."}
        </p>
      </div>

      {/* Subtitles */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <h3 className="font-semibold">Subtitles / Captions</h3>
          <HelpIcon text="Downloads English subtitles and auto-generated captions alongside videos." anchor="episode-management" />
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={subtitlesEnabled}
            onChange={(e) => setSubtitlesEnabled(e.target.checked)}
            className="rounded"
          />
          <span className="text-sm">Download subtitles and auto-generated captions</span>
        </label>
        <p className="text-xs text-muted-foreground">
          Auto-generated captions may not be available for all videos depending on authentication method. PO token authentication has limited subtitle support.
        </p>
      </div>

      {/* Chapters */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <h3 className="font-semibold">Chapters</h3>
          <HelpIcon text="Embeds chapter markers into the MP4 file so players like Plex and VLC can display them." anchor="episode-management" />
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={chaptersEnabled}
            onChange={(e) => setChaptersEnabled(e.target.checked)}
            className="rounded"
          />
          <span className="text-sm">Embed chapter markers in downloaded videos</span>
        </label>
        <p className="text-xs text-muted-foreground">
          Only applies to videos that have chapter markers set by the uploader.
        </p>
      </div>

      {/* File Permissions */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <h3 className="font-semibold">File Permissions</h3>
          <HelpIcon text="Apply chmod/chown after downloading files." anchor="configuration" />
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={setPermissions}
            onChange={(e) => setSetPermissions(e.target.checked)}
            className="rounded"
          />
          <span className="text-sm">Set permissions on downloaded files</span>
        </label>
        {setPermissions && (
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <label className="block text-xs text-muted-foreground mb-1">Folder chmod</label>
              <input
                type="text"
                value={chmodFolder}
                onChange={(e) => setChmodFolder(e.target.value)}
                placeholder="755"
                className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">File chmod</label>
              <input
                type="text"
                value={chmodFile}
                onChange={(e) => setChmodFile(e.target.value)}
                placeholder="644"
                className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-muted-foreground mb-1">chown group</label>
              <input
                type="text"
                value={chownGroup}
                onChange={(e) => setChownGroup(e.target.value)}
                placeholder="Group name or GID"
                className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
              />
            </div>
          </div>
        )}
      </div>

      {/* Max Duration Filter */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="font-semibold">Max Video Duration Filter</h3>
        <p className="text-sm text-muted-foreground">
          Skip auto-downloading videos longer than this duration. They'll be flagged for manual review
          and you'll get a push notification (if configured). Set to 0 to disable.
        </p>
        <div className="max-w-xs">
          <label className="block text-sm mb-1">Max Duration (hours)</label>
          <input
            type="number"
            min={0}
            value={maxDuration}
            onChange={(e) => setMaxDuration(Number(e.target.value))}
            className="w-full px-3 py-2 rounded-md border bg-background"
          />
          <p className="text-xs text-muted-foreground mt-1">
            {maxDuration > 0
              ? `Videos over ${maxDuration}h will need manual approval`
              : "Disabled  - all videos auto-queue"}
          </p>
        </div>
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

function AntiDetectTab() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [minDelay, setMinDelay] = useState(10)
  const [maxDelay, setMaxDelay] = useState(30)
  const [uaRotation, setUaRotation] = useState(true)
  const [jitter, setJitter] = useState(true)
  const [scanJitterEnabled, setScanJitterEnabled] = useState(true)
  const [scanJitterMaxSeconds, setScanJitterMaxSeconds] = useState(300)
  const [scanWindowEnabled, setScanWindowEnabled] = useState(false)
  const [scanWindowStartHour, setScanWindowStartHour] = useState<number>(22)
  const [scanWindowEndHour, setScanWindowEndHour] = useState<number>(6)
  const [scanMinIntervalHours, setScanMinIntervalHours] = useState<number>(12)
  const [manualScanCooldownMinutes, setManualScanCooldownMinutes] = useState<number>(5)

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
      if (settings.scan_jitter_enabled != null) setScanJitterEnabled(settings.scan_jitter_enabled === true || settings.scan_jitter_enabled === "true")
      if (settings.scan_jitter_max_seconds != null) setScanJitterMaxSeconds(Number(settings.scan_jitter_max_seconds))
      if (settings.scan_window_start_hour != null) {
        setScanWindowStartHour(Number(settings.scan_window_start_hour))
        setScanWindowEnabled(true)
      }
      if (settings.scan_window_end_hour != null) {
        setScanWindowEndHour(Number(settings.scan_window_end_hour))
        setScanWindowEnabled(true)
      }
      if (settings.scan_min_interval_hours != null) setScanMinIntervalHours(Number(settings.scan_min_interval_hours))
      if (settings.manual_scan_cooldown_minutes != null) setManualScanCooldownMinutes(Number(settings.manual_scan_cooldown_minutes))
    }
  }, [settings])

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        download_delay_min: minDelay,
        download_delay_max: maxDelay,
        user_agent_rotation: uaRotation,
        jitter_enabled: jitter,
        scan_jitter_enabled: scanJitterEnabled,
        scan_jitter_max_seconds: scanJitterMaxSeconds,
        scan_window_start_hour: scanWindowEnabled ? scanWindowStartHour : null,
        scan_window_end_hour: scanWindowEnabled ? scanWindowEndHour : null,
        scan_min_interval_hours: scanMinIntervalHours,
        manual_scan_cooldown_minutes: manualScanCooldownMinutes,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      toast("Anti-detection settings saved")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  return (
    <div className="space-y-6">
      <div className="rounded-lg border bg-card p-4 space-y-4">
        <div className="flex items-center gap-2">
          <h3 className="font-semibold">Download Delays</h3>
          <HelpIcon text="Random delay between downloads to avoid rate limiting." anchor="anti-detection" />
        </div>
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

      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="font-semibold">Scan Schedule</h3>
        <p className="text-sm text-muted-foreground">
          Each channel is scanned once per day. By default scans spread across the full 24 hours for the most organic traffic pattern. You can optionally constrain them to a specific local-time window (e.g. 10 PM to 6 AM). Windows must be at least 8 hours wide to avoid clustering.
        </p>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={scanWindowEnabled}
            onChange={(e) => setScanWindowEnabled(e.target.checked)}
            className="rounded"
          />
          <span className="text-sm">Limit scans to a time window</span>
        </label>
        {scanWindowEnabled && (() => {
          const width = scanWindowEndHour > scanWindowStartHour
            ? scanWindowEndHour - scanWindowStartHour
            : (24 - scanWindowStartHour) + scanWindowEndHour
          const tooNarrow = scanWindowStartHour !== scanWindowEndHour && width < 8
          return (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">Start (local time)</label>
                  <select
                    value={scanWindowStartHour}
                    onChange={(e) => setScanWindowStartHour(Number(e.target.value))}
                    className="px-2 py-1.5 rounded-md border bg-background text-sm"
                  >
                    {Array.from({ length: 24 }, (_, i) => (
                      <option key={i} value={i}>{String(i).padStart(2, "0")}:00</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">End (local time)</label>
                  <select
                    value={scanWindowEndHour}
                    onChange={(e) => setScanWindowEndHour(Number(e.target.value))}
                    className="px-2 py-1.5 rounded-md border bg-background text-sm"
                  >
                    {Array.from({ length: 24 }, (_, i) => (
                      <option key={i} value={i}>{String(i).padStart(2, "0")}:00</option>
                    ))}
                  </select>
                </div>
              </div>
              <p className={`text-xs ${tooNarrow ? "text-red-500" : "text-muted-foreground"}`}>
                {tooNarrow
                  ? `Window is only ${width} hours. Must be at least 8 hours.`
                  : `Each channel is scanned once per 24 hours, between ${String(scanWindowStartHour).padStart(2, "0")}:00 and ${String(scanWindowEndHour).padStart(2, "0")}:00 (${width === 0 ? 24 : width} hour window).`}
              </p>
            </div>
          )
        })()}
        <div className="pt-3 border-t space-y-3">
          <div>
            <label className="flex items-center gap-1 text-sm font-medium mb-1">
              Minimum hours between auto-scans
              <HelpIcon text="Ensures random scheduling never re-scans a channel within this many hours. Default 12 means each channel is scanned roughly once per day." anchor="channel-management" />
            </label>
            <input
              type="number"
              value={scanMinIntervalHours}
              onChange={(e) => setScanMinIntervalHours(Math.max(1, Math.min(168, Number(e.target.value) || 12)))}
              min={1}
              max={168}
              className="w-32 px-2 py-1.5 rounded-md border bg-background text-sm"
            />
            <p className="text-xs text-muted-foreground mt-1">
              Hours. Random auto-scans are guaranteed to be at least this far apart.
            </p>
          </div>
          <div>
            <label className="flex items-center gap-1 text-sm font-medium mb-1">
              Manual "Scan Now" cooldown
              <HelpIcon text="Prevents spam-clicking the Scan Now button. Attempts within this window are rejected with a 429 response." anchor="channel-management" />
            </label>
            <input
              type="number"
              value={manualScanCooldownMinutes}
              onChange={(e) => setManualScanCooldownMinutes(Math.max(0, Math.min(1440, Number(e.target.value) || 0)))}
              min={0}
              max={1440}
              className="w-32 px-2 py-1.5 rounded-md border bg-background text-sm"
            />
            <p className="text-xs text-muted-foreground mt-1">
              Minutes. Set to 0 to disable the cooldown.
            </p>
          </div>
        </div>
      </div>

      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="font-semibold">Scan Jitter</h3>
        <p className="text-sm text-muted-foreground">
          When multiple channels become due at the same tick, this adds a random delay between each scan so they don't all fire at once.
        </p>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={scanJitterEnabled}
            onChange={(e) => setScanJitterEnabled(e.target.checked)}
            className="rounded"
          />
          <span className="text-sm">Enable scan jitter</span>
        </label>
        {scanJitterEnabled && (
          <div>
            <label className="block text-sm mb-1">Max jitter between channels (seconds)</label>
            <input
              type="number"
              value={scanJitterMaxSeconds}
              onChange={(e) => setScanJitterMaxSeconds(Math.max(0, Number(e.target.value) || 0))}
              min={0}
              max={3600}
              className="w-32 px-2 py-1.5 rounded-md border bg-background text-sm"
            />
            <p className="text-xs text-muted-foreground mt-1">
              Each channel waits a random 0 to {scanJitterMaxSeconds}s before its scan begins. Channel scan order is also randomized.
            </p>
          </div>
        )}
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

const WEBHOOK_EVENTS = [
  { key: "download_complete", label: "Download Complete" },
  { key: "download_failed", label: "Download Failed" },
  { key: "cookies_expired", label: "Cookies Expired" },
  { key: "cookies_refreshed", label: "Cookies Refreshed" },
  { key: "health_alert", label: "Health Alerts" },
  { key: "critical_alert", label: "Critical Alerts" },
  { key: "review_required", label: "Long Video Review Required" },
  { key: "move_complete", label: "File Move Complete" },
  { key: "scan_complete", label: "Channel Scan Complete" },
  { key: "quality_upgrade", label: "Quality Upgrade" },
]

function NotificationsTab() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [telegramToken, setTelegramToken] = useState("")
  const [telegramChatId, setTelegramChatId] = useState("")
  const [pushoverAppToken, setPushoverAppToken] = useState("")
  const [pushoverUserKey, setPushoverUserKey] = useState("")
  const [discordWebhookUrl, setDiscordWebhookUrl] = useState("")
  const [enabledEvents, setEnabledEvents] = useState<string[]>(WEBHOOK_EVENTS.map(e => e.key))

  const { data: settings } = useQuery({
    queryKey: ["app-settings"],
    queryFn: api.getSettings,
  })

  useEffect(() => {
    if (settings) {
      if (settings.telegram_bot_token) setTelegramToken(String(settings.telegram_bot_token))
      if (settings.telegram_chat_id) setTelegramChatId(String(settings.telegram_chat_id))
      if (settings.pushover_app_token) setPushoverAppToken(String(settings.pushover_app_token))
      if (settings.pushover_user_key) setPushoverUserKey(String(settings.pushover_user_key))
      if (settings.discord_webhook_url) setDiscordWebhookUrl(String(settings.discord_webhook_url))
      if (settings.webhook_events && Array.isArray(settings.webhook_events)) {
        setEnabledEvents(settings.webhook_events)
      }
    }
  }, [settings])

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        telegram_bot_token: telegramToken || null,
        telegram_chat_id: telegramChatId || null,
        pushover_app_token: pushoverAppToken || null,
        pushover_user_key: pushoverUserKey || null,
        discord_webhook_url: discordWebhookUrl || null,
        webhook_events: enabledEvents,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      toast("Notification settings saved")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const testTelegramMutation = useMutation({
    mutationFn: () => api.testWebhook("telegram"),
    onSuccess: (data: any) => toast(data.success ? data.message : data.error, data.success ? "success" : "error"),
    onError: (e: Error) => toast(e.message, "error"),
  })

  const testPushoverMutation = useMutation({
    mutationFn: () => api.testWebhook("pushover"),
    onSuccess: (data: any) => toast(data.success ? data.message : data.error, data.success ? "success" : "error"),
    onError: (e: Error) => toast(e.message, "error"),
  })

  const testDiscordMutation = useMutation({
    mutationFn: () => api.testWebhook("discord"),
    onSuccess: (data: any) => toast(data.success ? data.message : data.error, data.success ? "success" : "error"),
    onError: (e: Error) => toast(e.message, "error"),
  })

  const toggleEvent = (key: string) => {
    setEnabledEvents(prev =>
      prev.includes(key) ? prev.filter(e => e !== key) : [...prev, key]
    )
  }

  return (
    <div className="space-y-6">
      {/* Telegram */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Send className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">Telegram</h3>
          <HelpIcon text="Get notified via Telegram bot." anchor="notifications" />
        </div>
        <p className="text-sm text-muted-foreground">
          Create a bot via @BotFather, then use the bot token and your chat ID.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="block text-sm mb-1">Bot Token</label>
            <input
              type="password"
              placeholder="123456:ABC-DEF..."
              value={telegramToken}
              onChange={(e) => setTelegramToken(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
          <div>
            <label className="block text-sm mb-1">Chat ID</label>
            <input
              type="text"
              placeholder="-1001234567890"
              value={telegramChatId}
              onChange={(e) => setTelegramChatId(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
        </div>
        <button
          onClick={() => testTelegramMutation.mutate()}
          disabled={!telegramToken || !telegramChatId || testTelegramMutation.isPending}
          className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
        >
          {testTelegramMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          Test Telegram
        </button>
      </div>

      {/* Pushover */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Bell className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">Pushover</h3>
          <HelpIcon text="Push notifications to your phone." anchor="notifications" />
        </div>
        <p className="text-sm text-muted-foreground">
          Create an app at pushover.net to get your tokens.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="block text-sm mb-1">Application Token</label>
            <input
              type="password"
              placeholder="azGDORePK8gMaC0QOYAMyEEuzJnyUi"
              value={pushoverAppToken}
              onChange={(e) => setPushoverAppToken(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
          <div>
            <label className="block text-sm mb-1">User Key</label>
            <input
              type="password"
              placeholder="uQiRzpo4DXghDmr9QzzfQu27cmVRsG"
              value={pushoverUserKey}
              onChange={(e) => setPushoverUserKey(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </div>
        </div>
        <button
          onClick={() => testPushoverMutation.mutate()}
          disabled={!pushoverAppToken || !pushoverUserKey || testPushoverMutation.isPending}
          className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
        >
          {testPushoverMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          Test Pushover
        </button>
      </div>

      {/* Discord */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Globe className="h-5 w-5 text-primary" />
          <h3 className="font-semibold">Discord</h3>
          <HelpIcon text="Send notifications to a Discord channel via webhook. Uses embeds with color-coded status." anchor="notifications" />
        </div>
        <p className="text-sm text-muted-foreground">
          Create a webhook in your Discord channel settings under Integrations.
        </p>
        <div>
          <label className="block text-sm mb-1">Webhook URL</label>
          <input
            type="password"
            placeholder="https://discord.com/api/webhooks/..."
            value={discordWebhookUrl}
            onChange={(e) => setDiscordWebhookUrl(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm"
          />
        </div>
        <button
          onClick={() => testDiscordMutation.mutate()}
          disabled={!discordWebhookUrl || testDiscordMutation.isPending}
          className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent disabled:opacity-50"
        >
          {testDiscordMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          Test Discord
        </button>
      </div>

      {/* Event Selection */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <h3 className="font-semibold">Notification Events</h3>
        <p className="text-sm text-muted-foreground">
          Choose which events trigger push notifications.
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          {WEBHOOK_EVENTS.map((evt) => (
            <label key={evt.key} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={enabledEvents.includes(evt.key)}
                onChange={() => toggleEvent(evt.key)}
                className="rounded"
              />
              <span className="text-sm">{evt.label}</span>
            </label>
          ))}
        </div>
      </div>

      <button
        onClick={() => saveMutation.mutate()}
        disabled={saveMutation.isPending}
        className="flex items-center gap-1.5 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 disabled:opacity-50"
      >
        {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save Notification Settings
      </button>
    </div>
  )
}
