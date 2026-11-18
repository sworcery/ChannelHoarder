import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { Card } from "@/components/ui/Card"
import { Checkbox } from "@/components/ui/Checkbox"
import { Field } from "@/components/ui/Field"
import { Input } from "@/components/ui/Input"
import { useSettingsDraft, useReportForm, asBool, type ServerSettings } from "./settingsForm"

const fromServer = (s: ServerSettings) => ({
  minDelay: s.download_delay_min != null ? Number(s.download_delay_min) : 10,
  maxDelay: s.download_delay_max != null ? Number(s.download_delay_max) : 30,
  uaRotation: asBool(s.user_agent_rotation, true),
  jitter: asBool(s.jitter_enabled, true),
  scanJitterEnabled: asBool(s.scan_jitter_enabled, true),
  scanJitterMaxSeconds: s.scan_jitter_max_seconds != null ? Number(s.scan_jitter_max_seconds) : 300,
})

export default function AntiDetectTab() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { data: settings } = useQuery({ queryKey: ["app-settings"], queryFn: api.getSettings })
  const { values, set, dirty, reset } = useSettingsDraft(settings, fromServer)

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        download_delay_min: values.minDelay,
        download_delay_max: values.maxDelay,
        user_agent_rotation: values.uaRotation,
        jitter_enabled: values.jitter,
        scan_jitter_enabled: values.scanJitterEnabled,
        scan_jitter_max_seconds: values.scanJitterMaxSeconds,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      toast("Anti-detection settings saved")
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

  return (
    <div className="space-y-6">
      <Card
        title="Download Delays"
        as="h2"
        id="download-delays"
        help={<HelpIcon text="A random pause between downloads keeps YouTube from rate limiting you." anchor="anti-detection" />}
      >
        <p className="text-sm text-muted-foreground">Add delays between downloads to avoid YouTube rate limiting.</p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Min Delay (seconds)">
            <Input type="number" value={values.minDelay} onChange={(e) => set("minDelay", Number(e.target.value))} />
          </Field>
          <Field label="Max Delay (seconds)">
            <Input type="number" value={values.maxDelay} onChange={(e) => set("maxDelay", Number(e.target.value))} />
          </Field>
        </div>
      </Card>

      <Card title="User-Agent Rotation" as="h2" className="space-y-3" id="user-agent-rotation">
        <p className="text-sm text-muted-foreground">
          Rotate user-agent strings between downloads to appear as different browsers.
        </p>
        <Checkbox
          label="Enable user-agent rotation"
          checked={values.uaRotation}
          onChange={(e) => set("uaRotation", e.target.checked)}
        />
      </Card>

      <Card title="Random Jitter" as="h2" className="space-y-3" id="random-jitter">
        <p className="text-sm text-muted-foreground">
          Add random extra delay (0-10s) to make download timing less predictable.
        </p>
        <Checkbox label="Enable jitter" checked={values.jitter} onChange={(e) => set("jitter", e.target.checked)} />
      </Card>

      <Card title="Scan Jitter" as="h2" className="space-y-3" id="scan-jitter">
        <p className="text-sm text-muted-foreground">
          When several channels become due at the same moment, this adds a random delay between each scan so they don't all fire at once.
        </p>
        <Checkbox
          label="Enable scan jitter"
          checked={values.scanJitterEnabled}
          onChange={(e) => set("scanJitterEnabled", e.target.checked)}
        />
        {values.scanJitterEnabled && (
          <Field
            label="Max jitter between channels (seconds)"
            hint={`Each channel waits a random 0 to ${values.scanJitterMaxSeconds}s before its scan begins. Channel scan order is also randomized.`}
          >
            <Input
              type="number"
              value={values.scanJitterMaxSeconds}
              onChange={(e) => set("scanJitterMaxSeconds", Math.max(0, Number(e.target.value) || 0))}
              min={0}
              max={3600}
              className="w-32"
            />
          </Field>
        )}
      </Card>
    </div>
  )
}
