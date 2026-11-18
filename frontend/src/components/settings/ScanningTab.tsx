import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Checkbox } from "@/components/ui/Checkbox"
import { Field } from "@/components/ui/Field"
import { Input, Select } from "@/components/ui/Input"
import { Clock, Loader2, RefreshCw, Rss } from "lucide-react"
import { useSettingsDraft, useReportForm, type ServerSettings } from "./settingsForm"

const fromServer = (s: ServerSettings) => ({
  scanMinIntervalHours: s.scan_min_interval_hours != null ? Number(s.scan_min_interval_hours) : 12,
  rssCheckMinutes: s.rss_check_minutes != null ? Number(s.rss_check_minutes) : 0,
  scanWindowEnabled: s.scan_window_start_hour != null || s.scan_window_end_hour != null,
  scanWindowStartHour: s.scan_window_start_hour != null ? Number(s.scan_window_start_hour) : 22,
  scanWindowEndHour: s.scan_window_end_hour != null ? Number(s.scan_window_end_hour) : 6,
  manualScanCooldownMinutes: s.manual_scan_cooldown_minutes != null ? Number(s.manual_scan_cooldown_minutes) : 5,
})

const hourOptions = Array.from({ length: 24 }, (_, i) => (
  <option key={i} value={i}>{String(i).padStart(2, "0")}:00</option>
))

export default function ScanningTab() {
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const { data: settings } = useQuery({ queryKey: ["app-settings"], queryFn: api.getSettings })
  const { values, set, dirty, reset } = useSettingsDraft(settings, fromServer)

  const scanAllMutation = useMutation({
    mutationFn: api.scanAll,
    onSuccess: (data: any) => toast(data.message || "Scan started"),
    onError: (e: Error) => toast(e.message, "error"),
  })

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        scan_min_interval_hours: values.scanMinIntervalHours,
        rss_check_minutes: values.rssCheckMinutes,
        scan_window_start_hour: values.scanWindowEnabled ? values.scanWindowStartHour : null,
        scan_window_end_hour: values.scanWindowEnabled ? values.scanWindowEndHour : null,
        manual_scan_cooldown_minutes: values.manualScanCooldownMinutes,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      toast("Scanning settings saved")
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

  const start = values.scanWindowStartHour
  const end = values.scanWindowEndHour
  const width = end > start ? end - start : 24 - start + end
  const tooNarrow = start !== end && width < 8
  const pad = (h: number) => String(h).padStart(2, "0")

  return (
    <div className="space-y-6">
      <Card
        icon={<Clock />}
        title="Scan Schedule"
        as="h2"
        id="scan-schedule"
        help={<HelpIcon text="How often each channel is checked for new uploads." anchor="automatic-scanning" />}
      >
        <div id="scan-interval">
          <Field
            label="Scan each channel every"
            hint="Each channel gets its own random scan time, between this many hours and twice that (never more than 12 hours extra) after its last scan. A new interval applies from each channel's next scan; Scan All Channels Now applies it right away."
          >
            <div className="flex items-center gap-2">
              <Input
                type="number"
                value={values.scanMinIntervalHours}
                onChange={(e) => set("scanMinIntervalHours", Math.max(1, Math.min(168, Number(e.target.value) || 12)))}
                min={1}
                max={168}
                className="w-32"
              />
              <span className="text-sm text-muted-foreground">hours</span>
            </div>
          </Field>
        </div>
        <Button variant="outline" onClick={() => scanAllMutation.mutate()} disabled={scanAllMutation.isPending} id="scan-all">
          {scanAllMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Scan All Channels Now
        </Button>
      </Card>

      <Card
        icon={<Rss />}
        title="Feed Checks"
        as="h2"
        id="feed-checks"
        help={<HelpIcon text="Checks each YouTube channel's feed and scans as soon as a new video appears." anchor="automatic-scanning" />}
      >
        <Field
          label="Check YouTube feeds every"
          hint="New uploads are found sooner without extra scans, and interval scans still run as the backup. With many channels it checks less often to stay under YouTube's daily feed limit, and with a scan window set it only checks inside the window."
        >
          <div className="flex items-center gap-2">
            <Input
              type="number"
              value={values.rssCheckMinutes}
              onChange={(e) => {
                const n = Math.round(Number(e.target.value) || 0)
                set("rssCheckMinutes", Math.max(0, Math.min(1440, n)))
              }}
              onBlur={() => {
                if (values.rssCheckMinutes > 0 && values.rssCheckMinutes < 15) set("rssCheckMinutes", 15)
              }}
              min={0}
              max={1440}
              className="w-32"
            />
            <span className="text-sm text-muted-foreground">minutes (0 = off, otherwise 15 to 1440)</span>
          </div>
        </Field>
      </Card>

      <Card
        title="Scan Window"
        as="h2"
        className="space-y-3"
        id="scan-window"
        help={<HelpIcon text="Keeps scans to certain hours of the day." anchor="automatic-scanning" />}
      >
        <p className="text-sm text-muted-foreground">
          By default scans spread across the full 24 hours. You can limit them to a local-time window (for example 10 PM to 6 AM);
          a scan that comes due outside it waits for the next one. Windows must be at least 8 hours wide.
        </p>
        <Checkbox
          label="Limit scans to a time window"
          checked={values.scanWindowEnabled}
          onChange={(e) => set("scanWindowEnabled", e.target.checked)}
        />
        {values.scanWindowEnabled && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Field label="Start (local time)">
                <Select value={start} onChange={(e) => set("scanWindowStartHour", Number(e.target.value))} className="w-32">
                  {hourOptions}
                </Select>
              </Field>
              <Field label="End (local time)">
                <Select value={end} onChange={(e) => set("scanWindowEndHour", Number(e.target.value))} className="w-32">
                  {hourOptions}
                </Select>
              </Field>
            </div>
            <p className={`text-xs ${tooNarrow ? "text-danger" : "text-muted-foreground"}`}>
              {tooNarrow
                ? `Window is only ${width} hours. Must be at least 8 hours.`
                : `Scans only run between ${pad(start)}:00 and ${pad(end)}:00 (${width === 0 ? 24 : width} hour window).`}
            </p>
          </div>
        )}
      </Card>

      <Card title="Scan Now Cooldown" as="h2" id="scan-cooldown">
        <Field
          label={<>Manual "Scan Now" cooldown</>}
          help={<HelpIcon text="Stops repeated clicks on Scan Now; attempts inside this window are rejected." anchor="channel-management" />}
          hint="Minutes. Set to 0 to disable the cooldown."
        >
          <Input
            type="number"
            value={values.manualScanCooldownMinutes}
            onChange={(e) => set("manualScanCooldownMinutes", Math.max(0, Math.min(1440, Number(e.target.value) || 0)))}
            min={0}
            max={1440}
            className="w-32"
          />
        </Field>
      </Card>
    </div>
  )
}
