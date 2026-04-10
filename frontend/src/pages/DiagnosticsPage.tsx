import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatDateTime } from "@/lib/utils"
import { HelpIcon } from "@/components/ui/HelpIcon"
import {
  Stethoscope,
  ClipboardCopy,
  Search,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Loader2,
  ChevronDown,
  ChevronUp,
} from "lucide-react"

export default function DiagnosticsPage() {
  const [tab, setTab] = useState<"overview" | "logs">("overview")
  const [logSearch, setLogSearch] = useState("")
  const [logPage, setLogPage] = useState(0)
  const [expandedLog, setExpandedLog] = useState<number | null>(null)
  const [copied, setCopied] = useState(false)

  const { data: diagnostics, isLoading } = useQuery({
    queryKey: ["diagnostics"],
    queryFn: api.getDiagnostics,
    enabled: tab === "overview",
  })

  const { data: logs } = useQuery({
    queryKey: ["system-logs", logPage, logSearch],
    queryFn: () => api.getLogs({ skip: logPage * 50, limit: 50, search: logSearch || undefined }),
    enabled: tab === "logs",
  })

  const copyReport = async () => {
    if (!diagnostics) return
    const report = [
      "=== ChannelHoarder - System Diagnostic Report ===",
      `Generated: ${new Date().toISOString()}`,
      `App Version: ${diagnostics.app_version}`,
      `yt-dlp Version: ${diagnostics.ytdlp_version}`,
      `PO Tokens: ${diagnostics.pot_status}`,
      `Cookies: ${diagnostics.cookies_status}`,
      `API Key: ${diagnostics.api_key_configured ? "configured" : "not configured"}`,
      `Disk Free: ${diagnostics.disk_free_formatted}`,
      `Channels: ${diagnostics.total_channels}`,
      `Downloads: ${diagnostics.total_downloads}`,
      `Failed: ${diagnostics.total_failed}`,
      "",
      "--- Recent Errors ---",
      ...diagnostics.recent_errors.map(
        (e: any) => `[${e.created_at}] ${e.error_code}: ${e.message}`
      ),
      "",
      "--- System Config ---",
      ...Object.entries(diagnostics.system_info).map(
        ([k, v]) => `${k}: ${v}`
      ),
      "=== End Report ===",
    ].join("\n")

    try {
      await navigator.clipboard.writeText(report)
    } catch {
      // Fallback for non-HTTPS contexts (e.g. local network IP)
      const textarea = document.createElement("textarea")
      textarea.value = report
      textarea.style.position = "fixed"
      textarea.style.left = "-9999px"
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand("copy")
      document.body.removeChild(textarea)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-bold">Diagnostics</h1>
          <HelpIcon text="System health, error history, and troubleshooting." anchor="troubleshooting" />
        </div>
        {tab === "overview" && diagnostics && (
          <button
            onClick={copyReport}
            className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border hover:bg-accent"
          >
            <ClipboardCopy className="h-4 w-4" />
            {copied ? "Copied!" : "Copy Diagnostic Report"}
          </button>
        )}
      </div>

      <div className="flex gap-1 border-b">
        <button
          onClick={() => setTab("overview")}
          className={`px-4 py-2 text-sm font-medium border-b-2 ${tab === "overview" ? "border-primary text-primary" : "border-transparent text-muted-foreground"}`}
        >
          System Overview
        </button>
        <button
          onClick={() => setTab("logs")}
          className={`px-4 py-2 text-sm font-medium border-b-2 ${tab === "logs" ? "border-primary text-primary" : "border-transparent text-muted-foreground"}`}
        >
          Error Logs
        </button>
      </div>

      {/* Overview Tab */}
      {tab === "overview" && (
        isLoading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : diagnostics ? (
          <div className="space-y-6">
            {/* System Status */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatusCard
                label="PO Tokens"
                status={diagnostics.pot_status === "healthy" ? "ok" : "error"}
                value={diagnostics.pot_status}
              />
              <StatusCard
                label="Cookies"
                status={diagnostics.cookies_status === "present" ? "ok" : "info"}
                value={diagnostics.cookies_status}
              />
              <StatusCard
                label="yt-dlp"
                status="ok"
                value={diagnostics.ytdlp_version}
              />
              <StatusCard
                label="Disk Space"
                status={
                  parseInt(diagnostics.disk_free_formatted) < 5 ? "warning" : "ok"
                }
                value={diagnostics.disk_free_formatted + " free"}
              />
            </div>

            {/* Stats */}
            <div className="rounded-lg border bg-card p-4">
              <h3 className="font-semibold mb-3">Statistics</h3>
              <div className="grid gap-2 text-sm sm:grid-cols-3">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Channels</span>
                  <span>{diagnostics.total_channels}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Downloaded</span>
                  <span>{diagnostics.total_downloads}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Failed</span>
                  <span className={diagnostics.total_failed > 0 ? "text-red-500" : ""}>
                    {diagnostics.total_failed}
                  </span>
                </div>
              </div>
            </div>

            {/* Recent Errors */}
            {diagnostics.recent_errors.length > 0 && (
              <div className="rounded-lg border bg-card p-4">
                <h3 className="font-semibold mb-3">Recent Errors</h3>
                <div className="space-y-2">
                  {diagnostics.recent_errors.map((error: any) => (
                    <div key={error.id} className="flex items-start gap-2 text-sm">
                      <XCircle className="h-4 w-4 text-red-500 flex-shrink-0 mt-0.5" />
                      <div>
                        <span className="font-mono text-xs text-red-500">{error.error_code}</span>
                        <span className="mx-1">-</span>
                        <span>{error.message}</span>
                        <span className="text-xs text-muted-foreground ml-2">
                          {formatDateTime(error.created_at)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : null
      )}

      {/* Logs Tab */}
      {tab === "logs" && (
        <div className="space-y-4">
          <div className="relative max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search logs..."
              value={logSearch}
              onChange={(e) => { setLogSearch(e.target.value); setLogPage(0) }}
              className="w-full pl-9 pr-3 py-2 rounded-md border bg-background"
            />
          </div>

          {logs && logs.items.length > 0 ? (
            <>
              <div className="space-y-2">
                {logs.items.map((log: any) => (
                  <div key={log.id} className="rounded-lg border bg-card p-3">
                    <div
                      className="flex items-center justify-between cursor-pointer"
                      onClick={() => setExpandedLog(expandedLog === log.id ? null : log.id)}
                    >
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        {log.error_code ? (
                          <XCircle className="h-4 w-4 text-red-500 flex-shrink-0" />
                        ) : (
                          <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0" />
                        )}
                        <span className="text-sm truncate">
                          {log.video_title || "Unknown"} - {log.event}
                        </span>
                        {log.error_code && (
                          <span className="text-xs font-mono text-red-500 flex-shrink-0">{log.error_code}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <span className="text-xs text-muted-foreground">{formatDateTime(log.created_at)}</span>
                        {expandedLog === log.id ? (
                          <ChevronUp className="h-4 w-4" />
                        ) : (
                          <ChevronDown className="h-4 w-4" />
                        )}
                      </div>
                    </div>
                    {expandedLog === log.id && (
                      <div className="mt-3 pt-3 border-t space-y-2">
                        {log.channel_name && (
                          <p className="text-sm"><span className="text-muted-foreground">Channel:</span> {log.channel_name}</p>
                        )}
                        {log.message && (
                          <p className="text-sm"><span className="text-muted-foreground">Message:</span> {log.message}</p>
                        )}
                        {log.details && (
                          <pre className="text-xs bg-muted rounded p-2 overflow-x-auto whitespace-pre-wrap">
                            {typeof log.details === "string"
                              ? log.details
                              : JSON.stringify(JSON.parse(log.details), null, 2)}
                          </pre>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Showing {logPage * 50 + 1}-{Math.min((logPage + 1) * 50, logs.total)} of {logs.total}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setLogPage(Math.max(0, logPage - 1))}
                    disabled={logPage === 0}
                    className="px-3 py-1 rounded-md border text-sm disabled:opacity-50"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setLogPage(logPage + 1)}
                    disabled={(logPage + 1) * 50 >= logs.total}
                    className="px-3 py-1 rounded-md border text-sm disabled:opacity-50"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          ) : (
            <p className="text-center py-8 text-muted-foreground">No logs found</p>
          )}
        </div>
      )}
    </div>
  )
}

function StatusCard({ label, status, value }: { label: string; status: "ok" | "warning" | "error" | "info"; value: string }) {
  const icons = {
    ok: <CheckCircle2 className="h-5 w-5 text-green-500" />,
    warning: <AlertTriangle className="h-5 w-5 text-yellow-500" />,
    error: <XCircle className="h-5 w-5 text-red-500" />,
    info: <Stethoscope className="h-5 w-5 text-blue-500" />,
  }

  return (
    <div className="rounded-lg border bg-card p-4 flex items-center gap-3">
      {icons[status]}
      <div>
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{value}</p>
      </div>
    </div>
  )
}
