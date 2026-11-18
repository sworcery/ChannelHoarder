import { useState, useRef, useEffect } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { Banner } from "@/components/ui/Banner"
import { Button, buttonClasses } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { DateTime } from "@/components/ui/DateTime"
import { Field } from "@/components/ui/Field"
import { PageHeader } from "@/components/ui/PageHeader"
import { Tabs, readSavedTab, panelId, tabId, type TabItem } from "@/components/ui/Tabs"
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
  const tabs: readonly TabItem<SettingsTab>[] = [
    { key: "general", label: "General" },
    { key: "media", label: "Media Management" },
    { key: "auth", label: "Authentication" },
    { key: "antidetect", label: "Anti-Detection" },
    { key: "connect", label: "Connect" },
    { key: "diagnostics", label: "Diagnostics" },
  ]

  const [tab, setTab] = useState<SettingsTab>(() =>
    // Migrate old tab keys to new ones
    readSavedTab(
      "settings_tab",
      tabs.map((t) => t.key),
      "general",
      { naming: "media", ytdlp: "general", notifications: "connect" },
    ),
  )

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" />

      <Tabs tabs={tabs} value={tab} onChange={setTab} storageKey="settings_tab" label="Settings sections" idPrefix="settings" />

      <div role="tabpanel" id={panelId("settings", tab)} aria-labelledby={tabId("settings", tab)}>
        {tab === "general" && <GeneralTab />}
        {tab === "media" && <MediaManagementTab />}
        {tab === "auth" && <AuthTab />}
        {tab === "antidetect" && <AntiDetectTab />}
        {tab === "connect" && <NotificationsTab />}
        {tab === "diagnostics" && <DiagnosticsTab />}
      </div>
    </div>
  )
}

function GeneralTab() {
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const importRef = useRef<HTMLInputElement>(null)
  const { data: stats } = useQuery({ queryKey: ["dashboard-stats"], queryFn: api.getStats })
  const { data: ytdlpVersion } = useQuery({
    queryKey: ["ytdlp-version"],
    queryFn: api.getYtdlpVersion,
  })

  // Settings state
  const [scanMinIntervalHours, setScanMinIntervalHours] = useState<number>(12)
  const [rssCheckMinutes, setRssCheckMinutes] = useState<number>(0)
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
      if (settings.scan_min_interval_hours != null) setScanMinIntervalHours(Number(settings.scan_min_interval_hours))
      if (settings.rss_check_minutes != null) setRssCheckMinutes(Number(settings.rss_check_minutes))
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
        scan_min_interval_hours: scanMinIntervalHours,
        rss_check_minutes: rssCheckMinutes,
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
      const skippedSettings: { key: string; reason: string }[] = result.skipped_settings ?? []
      const skippedChannels: { channel_id: string; channel_name?: string; reason: string }[] = result.skipped_channels ?? []
      const existing = skippedChannels.filter((c) => c.reason === "already exists").length
      const notImported = skippedChannels.filter((c) => c.reason !== "already exists")
      toast(`Imported ${result.imported_settings} settings, ${result.imported_channels} channels (${existing} already existed)`)
      if (skippedSettings.length || notImported.length) {
        // Group channels by reason so a restore onto an install missing a volume
        // reads as one line per reason, not one long line per channel.
        const byReason = new Map<string, string[]>()
        for (const c of notImported) {
          const names = byReason.get(c.reason) ?? []
          names.push(c.channel_name || c.channel_id)
          byReason.set(c.reason, names)
        }
        const parts = [
          ...skippedSettings.map((s) => `Setting ${s.key}: ${s.reason}`),
          ...[...byReason].map(([reason, names]) => {
            const shown = names.slice(0, 3).join(", ")
            const more = names.length > 3 ? ` and ${names.length - 3} more` : ""
            return `${names.length} channel${names.length === 1 ? "" : "s"} (${shown}${more}): ${reason}`
          }),
        ]
        toast(`Skipped: ${parts.join(" | ")}`, "error")
      }
    } catch (e: any) {
      toast(e.message, "error")
    }
  }

  return (
    <div className="space-y-6">
      {/* Scan Controls */}
      <Card
        icon={<Clock />}
        title="Scan Schedule"
        help={<HelpIcon text="How often each channel is checked for new uploads." anchor="automatic-scanning" />}
      >
        <div>
          <label htmlFor="scan-min-interval-hours" className="block text-sm font-medium mb-1">Scan each channel every</label>
          <input
            id="scan-min-interval-hours"
            type="number"
            value={scanMinIntervalHours}
            onChange={(e) => setScanMinIntervalHours(Math.max(1, Math.min(168, Number(e.target.value) || 12)))}
            min={1}
            max={168}
            className="w-32 px-2 py-1.5 rounded-md border bg-background text-sm"
          />
          <span className="text-sm text-muted-foreground ml-2">hours</span>
          <p className="text-xs text-muted-foreground mt-1">
            Each channel gets its own random scan time, between this many hours and twice that (never more than 12 hours extra) after its last scan, so channels aren't all scanned at once. With a scan window set, a scan that comes due outside it waits for the window to open. A new interval applies from each channel's next scan; Scan All Channels Now applies it right away. To keep scans to certain hours of the day, set a scan window under Anti-Detection.
          </p>
        </div>
        <div>
          <label htmlFor="rss-check-minutes" className="block text-sm font-medium mb-1">Check YouTube feeds every</label>
          <input
            id="rss-check-minutes"
            type="number"
            value={rssCheckMinutes}
            onChange={(e) => {
              const n = Math.round(Number(e.target.value) || 0)
              setRssCheckMinutes(Math.max(0, Math.min(1440, n)))
            }}
            onBlur={() => {
              if (rssCheckMinutes > 0 && rssCheckMinutes < 15) setRssCheckMinutes(15)
            }}
            min={0}
            max={1440}
            className="w-32 px-2 py-1.5 rounded-md border bg-background text-sm"
          />
          <span className="text-sm text-muted-foreground ml-2">minutes (0 = off, otherwise 15 to 1440)</span>
          <p className="text-xs text-muted-foreground mt-1">
            Checks each YouTube channel's feed and scans only when a new video appears, so new uploads are found sooner without extra scans. Interval scans still run as the backup. With many channels it checks less often to stay under YouTube's daily feed limit, and with a scan window set it only checks inside the window.
          </p>
        </div>
        <Button
          variant="primary"
          onClick={() => scanAllMutation.mutate()}
          disabled={scanAllMutation.isPending}
        >
          {scanAllMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Scan All Channels Now
        </Button>
      </Card>

      {/* Download & System Settings */}
      <Card icon={<Settings />} title="Download Settings">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Default Quality" hint="Default quality for new channels">
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
          </Field>
          <Field label="Max Concurrent Downloads" hint="Simultaneous downloads (1-5)">
            <input
              type="number"
              min={1}
              max={5}
              value={maxConcurrent}
              onChange={(e) => setMaxConcurrent(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </Field>
          <Field label="Max Retries" hint="Retry attempts for failed downloads">
            <input
              type="number"
              min={1}
              max={10}
              value={maxRetries}
              onChange={(e) => setMaxRetries(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </Field>
          <Field label="Log Level" hint="Set to debug for troubleshooting">
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
          </Field>
        </div>
      </Card>

      {/* Save button */}
      <Button
        variant="primary"
        onClick={() => saveSettingsMutation.mutate()}
        disabled={saveSettingsMutation.isPending}
      >
        {saveSettingsMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save Settings
      </Button>

      {/* System Info */}
      <Card title="System Info" className="space-y-2">
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
      </Card>

      {/* yt-dlp */}
      <Card
        title="yt-dlp"
        help={<HelpIcon text="Video download engine. Keep updated for YouTube compatibility." anchor="tech-stack" />}
      >
        <div>
          <p className="text-sm">Current version: <span className="font-mono">{ytdlpVersion?.version || "unknown"}</span></p>
          <p className="text-xs text-muted-foreground">
            yt-dlp is bundled with the app and updated with each release
          </p>
        </div>
      </Card>

      {/* Backup & Restore */}
      <Card title="Backup & Restore" className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Export all settings and channel subscriptions as JSON. Import to restore on a new instance.
        </p>
        <div className="flex gap-2">
          <Button variant="primary" onClick={handleExport}>
            <Download className="h-4 w-4" />
            Export Config
          </Button>
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
          <Button onClick={() => importRef.current?.click()}>
            <Upload className="h-4 w-4" />
            Import Config
          </Button>
        </div>
      </Card>
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

  const playerClientMutation = useMutation({
    mutationFn: (strategy: string) => api.setPlayerClient(strategy),
    onSuccess: (data: any) => { refetchAuth(); toast(data.message || "Player client updated") },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const isExpired = authStatus?.cookies_status === "expired"
  const hasCookies = authStatus?.cookies_status === "present" || authStatus?.cookies_status === "warning" || isExpired

  return (
    <div className="space-y-6">
      {/* Error banner */}
      {authError && (
        <Banner
          tone="danger"
          title="Failed to load auth status"
          action={<Button variant="link" size="sm" onClick={() => refetchAuth()}>Retry</Button>}
        >
          {(authError as Error).message}
        </Banner>
      )}

      {/* Cookie expired warning */}
      {isExpired && (
        <Banner tone="warning" title="Cookies Expired">
          Your YouTube cookies have expired and the download queue has been auto-paused.
          Export fresh cookies from your browser and upload them below to resume downloads.
        </Banner>
      )}

      {/* PO Tokens */}
      <Card
        icon={<Shield />}
        title="PO Tokens"
        help={<HelpIcon text="Auto-generated tokens for YouTube auth. Cookies take priority when available." anchor="authentication" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">
          PO tokens are generated automatically by the built-in server. When cookies are available, they take priority.
        </p>
        <div className="flex items-center gap-2">
          {authLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : authStatus?.pot_status === "enabled" ? (
            <CheckCircle2 className="h-4 w-4 text-success" />
          ) : (
            <XCircle className="h-4 w-4 text-danger" />
          )}
          <span className="text-sm">{authLoading ? "Checking..." : authStatus?.pot_message || "Unknown"}</span>
        </div>
      </Card>

      {/* Cookie Upload */}
      <Card
        icon={<FileText />}
        title="Cookie Authentication"
        help={<HelpIcon text="Upload cookies from your browser for YouTube auth." anchor="cookie-authentication-optional" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">
          Export cookies from your browser using a browser extension (e.g. "Get cookies.txt LOCALLY") and upload the file here.
        </p>
        <div className="flex items-center gap-2">
          {authLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : isExpired ? (
            <>
              <XCircle className="h-4 w-4 text-danger" />
              <span className="text-sm text-danger">{authStatus?.cookies_message}</span>
            </>
          ) : hasCookies ? (
            <>
              <CheckCircle2 className="h-4 w-4 text-success" />
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
                Last successful download: <DateTime value={authStatus.last_successful_auth} />
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
          <Button variant="primary" onClick={() => fileRef.current?.click()}>
            <Upload className="h-4 w-4" />
            Upload cookies.txt
          </Button>
          {hasCookies && (
            <>
              <Button onClick={() => validateMutation.mutate()} disabled={validateMutation.isPending}>
                {validateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                Validate
              </Button>
              <Button variant="danger-outline" onClick={() => deleteCookiesMutation.mutate()}>
                Remove
              </Button>
            </>
          )}
        </div>
        {isExpired && (
          <p className="text-xs text-muted-foreground">
            Tip: After uploading fresh cookies, the queue will automatically resume.
          </p>
        )}
      </Card>

      {/* Browser Cookie Sync (Tampermonkey) */}
      <Card
        icon={<Globe />}
        title="Browser Cookie Sync"
        help={<HelpIcon text="Auto-sync cookies from your browser using Tampermonkey." anchor="cookie-authentication-optional" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">
          Automatically sync your browser cookies to ChannelHoarder using a Tampermonkey userscript.
          Cookies are exported each time you load or refresh a YouTube page.
        </p>
        <div className="flex gap-2 flex-wrap items-center">
          <a
            href="/api/v1/settings/userscript.user.js"
            target="_blank"
            rel="noopener noreferrer"
            className={buttonClasses("primary", "md")}
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
      </Card>

      {/* YouTube API Key */}
      <Card
        icon={<Key />}
        title="YouTube Data API Key (Optional)"
        help={<HelpIcon text="Free API key from Google Cloud Console. 10,000 units/day." anchor="youtube-data-api-key-optional" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">
          Provides more reliable channel discovery, thumbnails, and video metadata. Falls back to yt-dlp if not set.
        </p>
        <div className="flex items-center gap-2">
          {authLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : authStatus?.api_key_configured ? (
            <>
              <CheckCircle2 className="h-4 w-4 text-success" />
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
          <Button
            variant="primary"
            onClick={() => apiKeyMutation.mutate(apiKey)}
            disabled={!apiKey || apiKeyMutation.isPending}
          >
            Save
          </Button>
        </div>
        {apiKeyMutation.error && (
          <p className="text-sm text-danger">{(apiKeyMutation.error as Error).message}</p>
        )}
      </Card>

      {/* yt-dlp Player Client */}
      <Card
        icon={<Key />}
        title="yt-dlp Player Client"
        help={<HelpIcon text="The YouTube client yt-dlp uses when fetching formats. Leave on Default (automatic) - yt-dlp picks a working client and adapts to YouTube's changes. Only override if a specific video fails; 'ios' and 'web_safari' are good fallbacks." anchor="player-client" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">
          Controls which YouTube player API yt-dlp uses to list downloadable formats. Change this if downloads fail with a format error.
        </p>
        <div className="flex gap-2">
          <select
            value={authStatus?.player_client || "default"}
            onChange={(e) => playerClientMutation.mutate(e.target.value)}
            disabled={authLoading || playerClientMutation.isPending}
            className="flex-1 px-3 py-2 rounded-md border bg-background text-sm disabled:opacity-50"
          >
            <option value="default">Default (automatic - recommended)</option>
            <option value="ios">ios</option>
            <option value="web_safari">web_safari</option>
            <option value="tv">tv</option>
            <option value="web">web</option>
            <option value="web_creator">web_creator</option>
            <option value="mweb">mweb</option>
            <option value="android">android</option>
            <option value="android_vr">android_vr</option>
            <option value="mediaconnect">mediaconnect</option>
          </select>
        </div>
      </Card>
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
  const [sponsorblockMode, setSponsorblockMode] = useState("off")
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
      if (settings.sponsorblock_mode != null) setSponsorblockMode(String(settings.sponsorblock_mode))
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
        sponsorblock_mode: sponsorblockMode,
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
      <Card
        title="Default Naming Template"
        help={<HelpIcon text="Controls how downloaded files are named and organized." anchor="file-organization" />}
      >
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
          <Button onClick={() => previewMutation.mutate(template)}>
            Preview
          </Button>
        </div>
        {previewMutation.data && (
          <div className="rounded-md bg-muted p-3">
            <p className="text-sm font-mono">{previewMutation.data.full_path}</p>
          </div>
        )}
      </Card>

      {/* YouTube Shorts */}
      <Card title="YouTube Shorts" className="space-y-3">
        <p className="text-sm text-muted-foreground">
          By default, YouTube Shorts (videos of 60 seconds or less, or on the channel's Shorts tab) are excluded from downloads. Enable this
          to allow channels to opt-in to downloading shorts.
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
      </Card>

      {/* Livestreams */}
      <Card title="Livestreams" className="space-y-3">
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
            ? "Now open a channel's page and turn on \"Include livestreams when downloading\" in its Livestreams section - livestreams stay excluded until you opt in per channel."
            : "Livestreams are excluded from all channel downloads."}
        </p>
      </Card>

      {/* Subtitles */}
      <Card
        title="Subtitles / Captions"
        help={<HelpIcon text="Downloads English subtitles and auto-generated captions alongside videos." anchor="episode-management" />}
        className="space-y-3"
      >
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
      </Card>

      {/* Chapters */}
      <Card
        title="Chapters"
        help={<HelpIcon text="Embeds chapter markers into the MP4 file so players like Plex and VLC can display them." anchor="episode-management" />}
        className="space-y-3"
      >
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
      </Card>

      {/* SponsorBlock */}
      <Card
        title="SponsorBlock"
        help={<HelpIcon text="Uses the crowdsourced SponsorBlock database to handle sponsor, self-promotion, and interaction-reminder segments. 'Mark' adds them as chapters you can skip; 'Remove' cuts them out of the file. YouTube only." anchor="episode-management" />}
        className="space-y-3"
      >
        <div className="max-w-xs">
          <select
            value={sponsorblockMode}
            onChange={(e) => setSponsorblockMode(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm"
          >
            <option value="off">Off</option>
            <option value="mark">Mark segments as chapters</option>
            <option value="remove">Remove segments from video</option>
          </select>
        </div>
        <p className="text-xs text-muted-foreground">
          {sponsorblockMode === "remove"
            ? "Sponsor, self-promo, and interaction-reminder segments will be cut out of downloaded YouTube videos."
            : sponsorblockMode === "mark"
            ? "Segments are added as skippable chapters; the video is left intact."
            : "SponsorBlock is disabled."}
        </p>
      </Card>

      {/* File Permissions */}
      <Card
        title="File Permissions"
        help={<HelpIcon text="Apply chmod/chown after downloading files." anchor="configuration" />}
        className="space-y-3"
      >
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
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Folder chmod">
              <input
                type="text"
                value={chmodFolder}
                onChange={(e) => setChmodFolder(e.target.value)}
                placeholder="755"
                className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
              />
            </Field>
            <Field label="File chmod">
              <input
                type="text"
                value={chmodFile}
                onChange={(e) => setChmodFile(e.target.value)}
                placeholder="644"
                className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
              />
            </Field>
            <Field label="chown group">
              <input
                type="text"
                value={chownGroup}
                onChange={(e) => setChownGroup(e.target.value)}
                placeholder="Group name or GID"
                className="w-full px-2 py-1.5 rounded-md border bg-background text-sm"
              />
            </Field>
          </div>
        )}
      </Card>

      {/* Max Duration Filter */}
      <Card title="Max Video Duration Filter" className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Skip auto-downloading videos longer than this duration. They'll be flagged for manual review
          and you'll get a push notification (if configured). Set to 0 to disable.
        </p>
        <Field
          label="Max Duration (hours)"
          hint={maxDuration > 0
            ? `Videos over ${maxDuration}h will need manual approval`
            : "Disabled  - all videos auto-queue"}
          className="max-w-xs"
        >
          <input
            type="number"
            min={0}
            value={maxDuration}
            onChange={(e) => setMaxDuration(Number(e.target.value))}
            className="w-full px-3 py-2 rounded-md border bg-background"
          />
        </Field>
      </Card>

      <Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
        {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save Settings
      </Button>
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
      <Card
        title="Download Delays"
        help={<HelpIcon text="Random delay between downloads to avoid rate limiting." anchor="anti-detection" />}
      >
        <p className="text-sm text-muted-foreground">
          Add delays between downloads to avoid YouTube rate limiting.
        </p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Min Delay (seconds)">
            <input
              type="number"
              value={minDelay}
              onChange={(e) => setMinDelay(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background"
            />
          </Field>
          <Field label="Max Delay (seconds)">
            <input
              type="number"
              value={maxDelay}
              onChange={(e) => setMaxDelay(Number(e.target.value))}
              className="w-full px-3 py-2 rounded-md border bg-background"
            />
          </Field>
        </div>
      </Card>

      <Card title="User-Agent Rotation" className="space-y-3">
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
      </Card>

      <Card title="Random Jitter" className="space-y-3">
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
      </Card>

      <Card title="Scan Schedule" className="space-y-3">
        <p className="text-sm text-muted-foreground">
          How often each channel is scanned is set under General. By default scans spread across the full 24 hours for the most organic traffic pattern. You can optionally constrain them to a specific local-time window (e.g. 10 PM to 6 AM); a scan that comes due outside the window waits for the next one. Windows must be at least 8 hours wide to avoid clustering.
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
              <div className="flex flex-wrap items-center gap-2">
                <Field label="Start (local time)">
                  <select
                    value={scanWindowStartHour}
                    onChange={(e) => setScanWindowStartHour(Number(e.target.value))}
                    className="px-2 py-1.5 rounded-md border bg-background text-sm"
                  >
                    {Array.from({ length: 24 }, (_, i) => (
                      <option key={i} value={i}>{String(i).padStart(2, "0")}:00</option>
                    ))}
                  </select>
                </Field>
                <Field label="End (local time)">
                  <select
                    value={scanWindowEndHour}
                    onChange={(e) => setScanWindowEndHour(Number(e.target.value))}
                    className="px-2 py-1.5 rounded-md border bg-background text-sm"
                  >
                    {Array.from({ length: 24 }, (_, i) => (
                      <option key={i} value={i}>{String(i).padStart(2, "0")}:00</option>
                    ))}
                  </select>
                </Field>
              </div>
              <p className={`text-xs ${tooNarrow ? "text-danger" : "text-muted-foreground"}`}>
                {tooNarrow
                  ? `Window is only ${width} hours. Must be at least 8 hours.`
                  : `Scans only run between ${String(scanWindowStartHour).padStart(2, "0")}:00 and ${String(scanWindowEndHour).padStart(2, "0")}:00 (${width === 0 ? 24 : width} hour window).`}
              </p>
            </div>
          )
        })()}
        <div className="pt-3 border-t space-y-3">
          <Field
            label={<>Manual "Scan Now" cooldown</>}
            help={<HelpIcon text="Prevents spam-clicking the Scan Now button. Attempts within this window are rejected with a 429 response." anchor="channel-management" />}
            hint="Minutes. Set to 0 to disable the cooldown."
          >
            <input
              type="number"
              value={manualScanCooldownMinutes}
              onChange={(e) => setManualScanCooldownMinutes(Math.max(0, Math.min(1440, Number(e.target.value) || 0)))}
              min={0}
              max={1440}
              className="w-32 px-2 py-1.5 rounded-md border bg-background text-sm"
            />
          </Field>
        </div>
      </Card>

      <Card title="Scan Jitter" className="space-y-3">
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
          <Field
            label="Max jitter between channels (seconds)"
            hint={`Each channel waits a random 0 to ${scanJitterMaxSeconds}s before its scan begins. Channel scan order is also randomized.`}
          >
            <input
              type="number"
              value={scanJitterMaxSeconds}
              onChange={(e) => setScanJitterMaxSeconds(Math.max(0, Number(e.target.value) || 0))}
              min={0}
              max={3600}
              className="w-32 px-2 py-1.5 rounded-md border bg-background text-sm"
            />
          </Field>
        )}
      </Card>

      <Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
        {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save Settings
      </Button>
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
  { key: "scan_failed", label: "Channel Scan Failed" },
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
      <Card
        icon={<Send />}
        title="Telegram"
        help={<HelpIcon text="Get notified via Telegram bot." anchor="notifications" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">
          Create a bot via @BotFather, then use the bot token and your chat ID.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Bot Token">
            <input
              type="password"
              placeholder="123456:ABC-DEF..."
              value={telegramToken}
              onChange={(e) => setTelegramToken(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </Field>
          <Field label="Chat ID">
            <input
              type="text"
              placeholder="-1001234567890"
              value={telegramChatId}
              onChange={(e) => setTelegramChatId(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </Field>
        </div>
        <Button
          onClick={() => testTelegramMutation.mutate()}
          disabled={!telegramToken || !telegramChatId || testTelegramMutation.isPending}
        >
          {testTelegramMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          Test Telegram
        </Button>
      </Card>

      {/* Pushover */}
      <Card
        icon={<Bell />}
        title="Pushover"
        help={<HelpIcon text="Push notifications to your phone." anchor="notifications" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">
          Create an app at pushover.net to get your tokens.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Application Token">
            <input
              type="password"
              placeholder="azGDORePK8gMaC0QOYAMyEEuzJnyUi"
              value={pushoverAppToken}
              onChange={(e) => setPushoverAppToken(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </Field>
          <Field label="User Key">
            <input
              type="password"
              placeholder="uQiRzpo4DXghDmr9QzzfQu27cmVRsG"
              value={pushoverUserKey}
              onChange={(e) => setPushoverUserKey(e.target.value)}
              className="w-full px-3 py-2 rounded-md border bg-background text-sm"
            />
          </Field>
        </div>
        <Button
          onClick={() => testPushoverMutation.mutate()}
          disabled={!pushoverAppToken || !pushoverUserKey || testPushoverMutation.isPending}
        >
          {testPushoverMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          Test Pushover
        </Button>
      </Card>

      {/* Discord */}
      <Card
        icon={<Globe />}
        title="Discord"
        help={<HelpIcon text="Send notifications to a Discord channel via webhook. Uses embeds with color-coded status." anchor="notifications" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">
          Create a webhook in your Discord channel settings under Integrations.
        </p>
        <Field label="Webhook URL">
          <input
            type="password"
            placeholder="https://discord.com/api/webhooks/..."
            value={discordWebhookUrl}
            onChange={(e) => setDiscordWebhookUrl(e.target.value)}
            className="w-full px-3 py-2 rounded-md border bg-background text-sm"
          />
        </Field>
        <Button
          onClick={() => testDiscordMutation.mutate()}
          disabled={!discordWebhookUrl || testDiscordMutation.isPending}
        >
          {testDiscordMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          Test Discord
        </Button>
      </Card>

      {/* Event Selection */}
      <Card title="Notification Events" className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Choose which events trigger push notifications.
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
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
      </Card>

      <Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
        {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
        Save Notification Settings
      </Button>
    </div>
  )
}
