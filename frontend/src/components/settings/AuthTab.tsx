import { useState, useRef } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { Banner } from "@/components/ui/Banner"
import { Button, buttonClasses } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { DateTime } from "@/components/ui/DateTime"
import { Input, Select } from "@/components/ui/Input"
import {
  Upload,
  Key,
  Shield,
  FileText,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  ExternalLink,
  Globe,
} from "lucide-react"

// Everything on this tab acts at once (upload, validate, remove, API key, player client),
// so it has no part in the save bar.
export default function AuthTab() {
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
    onSuccess: (data: any) => {
      refetchAuth()
      setApiKey("")
      toast(data.valid ? "API key saved and validated" : "API key saved but validation failed - check the key")
    },
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
      {authError && (
        <Banner
          tone="danger"
          title="Failed to load auth status"
          action={<Button variant="link" size="sm" onClick={() => refetchAuth()}>Retry</Button>}
        >
          {(authError as Error).message}
        </Banner>
      )}

      {isExpired && (
        <Banner tone="warning" title="Cookies Expired">
          Your YouTube cookies have expired and the download queue has been auto-paused.
          Export fresh cookies from your browser and upload them below to resume downloads.
        </Banner>
      )}

      <Card
        icon={<Shield />}
        title="PO Tokens"
        as="h2"
        id="po-tokens"
        help={<HelpIcon text="Tokens the built-in server makes for YouTube; cookies take priority when you have them." anchor="po-tokens" />}
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

      <Card
        icon={<FileText />}
        title="Cookie Authentication"
        as="h2"
        id="cookies"
        help={<HelpIcon text="Cookies from your browser sign yt-dlp in as you." anchor="cookie-authentication" />}
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
            aria-label="cookies.txt file"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) uploadMutation.mutate(file)
              e.target.value = ""
            }}
            className="hidden"
          />
          <Button variant="outline" onClick={() => fileRef.current?.click()}>
            <Upload className="h-4 w-4" />
            Upload cookies.txt
          </Button>
          {hasCookies && (
            <>
              <Button variant="outline" onClick={() => validateMutation.mutate()} disabled={validateMutation.isPending}>
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

      <Card
        icon={<Globe />}
        title="Browser Cookie Sync"
        as="h2"
        id="cookie-sync"
        help={<HelpIcon text="A Tampermonkey script sends your browser's cookies to this server on its own." anchor="cookie-authentication" />}
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
            className={buttonClasses("outline", "md")}
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

      <Card
        icon={<Key />}
        title="YouTube Data API Key (Optional)"
        as="h2"
        id="api-key"
        help={<HelpIcon text="A free Google key for more reliable channel discovery (10,000 units a day)." anchor="youtube-data-api-key-optional" />}
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
          <Input
            type="text"
            aria-label="YouTube Data API key"
            placeholder="Paste your API key here..."
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="flex-1"
          />
          <Button
            variant="outline"
            onClick={() => apiKeyMutation.mutate(apiKey)}
            disabled={!apiKey || apiKeyMutation.isPending}
          >
            Save key
          </Button>
        </div>
        {apiKeyMutation.error && (
          <p className="text-sm text-danger">{(apiKeyMutation.error as Error).message}</p>
        )}
      </Card>

      <Card
        icon={<Key />}
        title="yt-dlp Player Client"
        as="h2"
        id="player-client"
        help={<HelpIcon text="Which YouTube client yt-dlp uses for formats; leave it on Default unless one video fails." anchor="player-client" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">
          Controls which YouTube player API yt-dlp uses to list downloadable formats. Change this if downloads fail with a format error.
          The new client applies at once.
        </p>
        <Select
          aria-label="Player client"
          value={authStatus?.player_client || "default"}
          onChange={(e) => playerClientMutation.mutate(e.target.value)}
          disabled={authLoading || playerClientMutation.isPending}
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
        </Select>
      </Card>
    </div>
  )
}
