import { useRef } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { formatBytes } from "@/lib/utils"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Field } from "@/components/ui/Field"
import { Input, Select } from "@/components/ui/Input"
import { Upload, Download, Settings } from "lucide-react"
import { useSettingsDraft, useReportForm, type ServerSettings } from "./settingsForm"

const fromServer = (s: ServerSettings) => ({
  defaultQuality: s.default_quality ? String(s.default_quality) : "best",
  maxConcurrent: s.max_concurrent_downloads != null ? Number(s.max_concurrent_downloads) : 1,
  maxRetries: s.max_retries != null ? Number(s.max_retries) : 3,
  logLevel: s.log_level ? String(s.log_level) : "info",
})

export default function GeneralTab() {
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const importRef = useRef<HTMLInputElement>(null)
  const { data: stats } = useQuery({ queryKey: ["dashboard-stats"], queryFn: api.getStats })
  const { data: ytdlpVersion } = useQuery({ queryKey: ["ytdlp-version"], queryFn: api.getYtdlpVersion })
  const { data: settings } = useQuery({ queryKey: ["app-settings"], queryFn: api.getSettings })

  const { values, set, dirty, reset } = useSettingsDraft(settings, fromServer)

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        default_quality: values.defaultQuality,
        max_concurrent_downloads: values.maxConcurrent,
        max_retries: values.maxRetries,
        log_level: values.logLevel,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      toast("Settings saved. Changes take effect immediately.")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })
  const save = async () => {
    try {
      await saveMutation.mutateAsync()
      return true
    } catch {
      return false
    }
  }
  useReportForm(dirty, saveMutation.isPending, save, reset)

  // Export and import act at once; they are not part of the save bar.
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
      queryClient.invalidateQueries({ queryKey: ["app-settings"] })
    } catch (e: any) {
      toast(e.message, "error")
    }
  }

  return (
    <div className="space-y-6">
      <Card icon={<Settings />} title="Download Settings" as="h2" id="download-settings">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div id="default-quality">
            <Field label="Default Quality" hint="Default quality for new channels">
              <Select value={values.defaultQuality} onChange={(e) => set("defaultQuality", e.target.value)}>
                <option value="best">Best Available</option>
                <option value="2160p">4K (2160p)</option>
                <option value="1080p">1080p</option>
                <option value="720p">720p</option>
                <option value="480p">480p</option>
              </Select>
            </Field>
          </div>
          <div id="max-concurrent-downloads">
            <Field label="Max Concurrent Downloads" hint="Simultaneous downloads (1-5)">
              <Input
                type="number"
                min={1}
                max={5}
                value={values.maxConcurrent}
                onChange={(e) => set("maxConcurrent", Number(e.target.value))}
              />
            </Field>
          </div>
          <div id="max-retries">
            <Field label="Max Retries" hint="Retry attempts for failed downloads">
              <Input
                type="number"
                min={1}
                max={10}
                value={values.maxRetries}
                onChange={(e) => set("maxRetries", Number(e.target.value))}
              />
            </Field>
          </div>
          <div id="log-level">
            <Field label="Log Level" hint="Set to debug for troubleshooting">
              <Select value={values.logLevel} onChange={(e) => set("logLevel", e.target.value)}>
                <option value="debug">Debug</option>
                <option value="info">Info</option>
                <option value="warning">Warning</option>
                <option value="error">Error</option>
              </Select>
            </Field>
          </div>
        </div>
      </Card>

      <Card title="System Info" as="h2" className="space-y-2" id="system-info">
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
            <span>{formatBytes(stats?.storage_used_bytes ?? 0)}</span>
          </div>
        </div>
      </Card>

      <Card
        title="yt-dlp"
        as="h2"
        id="ytdlp"
        help={<HelpIcon text="The download engine. It is bundled with the app and updated with each release." anchor="tech-stack" />}
      >
        <p className="text-sm">Current version: <span className="font-mono">{ytdlpVersion?.version || "unknown"}</span></p>
      </Card>

      <Card title="Backup & Restore" as="h2" className="space-y-3" id="backup-restore">
        <p className="text-sm text-muted-foreground">
          Export all settings and channel subscriptions as JSON. Import to restore on a new instance.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={handleExport}>
            <Download className="h-4 w-4" />
            Export Config
          </Button>
          <input
            ref={importRef}
            type="file"
            accept=".json"
            aria-label="Config file to import"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleImport(file)
              e.target.value = ""
            }}
            className="hidden"
          />
          <Button variant="outline" onClick={() => importRef.current?.click()}>
            <Upload className="h-4 w-4" />
            Import Config
          </Button>
        </div>
      </Card>
    </div>
  )
}
