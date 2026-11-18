import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { DateTime } from "@/components/ui/DateTime"
import { EmptyState } from "@/components/ui/EmptyState"
import { Tabs, readSavedTab, panelId, tabId, type TabItem } from "@/components/ui/Tabs"
import LibraryCheckCard from "./LibraryCheckCard"
import SharedFoldersCard from "./SharedFoldersCard"
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

type SubTab = "overview" | "logs" | "system-logs"

const SUB_TABS: readonly TabItem<SubTab>[] = [
  { key: "overview", label: "System Overview" },
  { key: "logs", label: "Error Logs" },
  { key: "system-logs", label: "System Logs" },
]
const SUB_TAB_KEYS = SUB_TABS.map((t) => t.key)

export default function DiagnosticsTab() {
  const [subTab, setSubTab] = useState<SubTab>(() =>
    readSavedTab("diagnostics_tab", SUB_TAB_KEYS, "overview"),
  )
  const [logSearch, setLogSearch] = useState("")
  const [logPage, setLogPage] = useState(0)
  const [expandedLog, setExpandedLog] = useState<number | null>(null)
  const [copied, setCopied] = useState(false)
  const [logLevel, setLogLevel] = useState("")

  const { data: diagnostics, isLoading } = useQuery({
    queryKey: ["diagnostics"],
    queryFn: api.getDiagnostics,
    enabled: subTab === "overview",
  })

  const { data: logs } = useQuery({
    queryKey: ["system-logs", logPage, logSearch],
    queryFn: () => api.getLogs({ skip: logPage * 50, limit: 50, search: logSearch || undefined }),
    enabled: subTab === "logs",
  })

  const { data: liveLogs } = useQuery({
    queryKey: ["live-logs", logLevel],
    queryFn: () => api.getLiveLogs({ level: logLevel || undefined, limit: 300 }),
    enabled: subTab === "system-logs",
    refetchInterval: 5000,
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
        <Tabs
          tabs={SUB_TABS}
          value={subTab}
          onChange={setSubTab}
          storageKey="diagnostics_tab"
          label="Diagnostics sections"
          idPrefix="diag"
          className="flex-1"
        />
        {subTab === "overview" && diagnostics && (
          <Button onClick={copyReport} className="ml-4 whitespace-nowrap">
            <ClipboardCopy className="h-4 w-4" />
            {copied ? "Copied!" : "Copy Diagnostic Report"}
          </Button>
        )}
      </div>

      {subTab === "overview" && (
        isLoading ? (
          <div role="tabpanel" id={panelId("diag", "overview")} aria-labelledby={tabId("diag", "overview")} className="flex justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : diagnostics ? (
          <div role="tabpanel" id={panelId("diag", "overview")} aria-labelledby={tabId("diag", "overview")} className="space-y-6">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatusCard label="PO Tokens" status={diagnostics.pot_status === "healthy" ? "ok" : "error"} value={diagnostics.pot_status} />
              <StatusCard label="Cookies" status={diagnostics.cookies_status === "present" ? "ok" : "info"} value={diagnostics.cookies_status} />
              <StatusCard label="yt-dlp" status="ok" value={diagnostics.ytdlp_version} />
              <StatusCard
                label="Disk Space"
                status={parseInt(diagnostics.disk_free_formatted) < 5 ? "warning" : "ok"}
                value={diagnostics.disk_free_formatted + " free"}
              />
            </div>

            <Card title="Statistics" className="space-y-3">
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
                  <span className={diagnostics.total_failed > 0 ? "text-danger" : ""}>
                    {diagnostics.total_failed}
                  </span>
                </div>
              </div>
            </Card>

            <LibraryCheckCard />

            <SharedFoldersCard />

            {diagnostics.recent_errors.length > 0 && (
              <Card title="Recent Errors" className="space-y-3">
                <div className="space-y-2">
                  {diagnostics.recent_errors.map((error: any) => (
                    <div key={error.id} className="flex items-start gap-2 text-sm">
                      <XCircle className="h-4 w-4 text-danger flex-shrink-0 mt-0.5" />
                      <div>
                        <span className="font-mono text-xs text-danger">{error.error_code}</span>
                        <span className="mx-1">-</span>
                        <span>{error.message}</span>
                        <DateTime value={error.created_at} className="text-xs text-muted-foreground ml-2" />
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>
        ) : null
      )}

      {subTab === "logs" && (
        <div role="tabpanel" id={panelId("diag", "logs")} aria-labelledby={tabId("diag", "logs")} className="space-y-4">
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
                  <Card key={log.id} className="p-3 space-y-0">
                    {/* Whole row is an unstyled click target that expands the entry */}
                    <div
                      className="flex items-center justify-between cursor-pointer"
                      onClick={() => setExpandedLog(expandedLog === log.id ? null : log.id)}
                    >
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        {log.error_code ? (
                          <XCircle className="h-4 w-4 text-danger flex-shrink-0" />
                        ) : (
                          <CheckCircle2 className="h-4 w-4 text-success flex-shrink-0" />
                        )}
                        <span className="text-sm truncate">
                          {log.video_title || "Unknown"} - {log.event}
                        </span>
                        {log.error_code && (
                          <span className="text-xs font-mono text-danger flex-shrink-0">{log.error_code}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <DateTime value={log.created_at} className="text-xs text-muted-foreground" />
                        {expandedLog === log.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
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
                  </Card>
                ))}
              </div>

              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Showing {logPage * 50 + 1}-{Math.min((logPage + 1) * 50, logs.total)} of {logs.total}
                </p>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    onClick={() => setLogPage(Math.max(0, logPage - 1))}
                    disabled={logPage === 0}
                  >
                    Previous
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => setLogPage(logPage + 1)}
                    disabled={(logPage + 1) * 50 >= logs.total}
                  >
                    Next
                  </Button>
                </div>
              </div>
            </>
          ) : (
            <EmptyState title="No logs found" />
          )}
        </div>
      )}

      {subTab === "system-logs" && (
        <div role="tabpanel" id={panelId("diag", "system-logs")} aria-labelledby={tabId("diag", "system-logs")} className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <select
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value)}
                className="px-2 py-1.5 rounded-md border bg-background text-sm"
              >
                <option value="">All Levels</option>
                <option value="DEBUG">DEBUG</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
              <span className="text-xs text-muted-foreground">
                {liveLogs?.total || 0} entries (auto-refreshing)
              </span>
            </div>
            <Button onClick={() => api.exportLogs()}>
              <ClipboardCopy className="h-4 w-4" />
              Export Debug Log
            </Button>
          </div>

          <Card className="overflow-hidden p-0 space-y-0">
            <div className="max-h-[600px] overflow-y-auto p-3 font-mono text-xs leading-relaxed">
              {liveLogs?.entries?.length ? (
                liveLogs.entries.map((entry: any, i: number) => {
                  const levelColor =
                    entry.level === "ERROR" || entry.level === "CRITICAL"
                      ? "text-danger"
                      : entry.level === "WARNING"
                        ? "text-warning"
                        : entry.level === "DEBUG"
                          ? "text-muted-foreground"
                          : ""
                  return (
                    <div key={i} className={`py-0.5 ${levelColor}`}>
                      <span className="text-muted-foreground">{entry.timestamp?.slice(11, 19)}</span>
                      {" "}
                      <span className="font-semibold">[{entry.level}]</span>
                      {" "}
                      <span className="text-muted-foreground">{entry.logger}:</span>
                      {" "}
                      {entry.message}
                    </div>
                  )
                })
              ) : (
                <EmptyState title="No log entries" />
              )}
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}

function StatusCard({ label, status, value }: { label: string; status: "ok" | "warning" | "error" | "info"; value: string }) {
  const icons = {
    ok: <CheckCircle2 className="h-5 w-5 text-success" />,
    warning: <AlertTriangle className="h-5 w-5 text-warning" />,
    error: <XCircle className="h-5 w-5 text-danger" />,
    info: <Stethoscope className="h-5 w-5 text-info" />,
  }

  return (
    <Card className="flex items-center gap-3 space-y-0">
      {icons[status]}
      <div>
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{value}</p>
      </div>
    </Card>
  )
}
