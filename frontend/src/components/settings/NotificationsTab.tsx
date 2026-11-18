import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Checkbox } from "@/components/ui/Checkbox"
import { Field } from "@/components/ui/Field"
import { Input } from "@/components/ui/Input"
import { Bell, Globe, Loader2, Send } from "lucide-react"
import { useSettingsDraft, useReportForm, type ServerSettings } from "./settingsForm"

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

const fromServer = (s: ServerSettings) => ({
  telegramToken: s.telegram_bot_token ? String(s.telegram_bot_token) : "",
  telegramChatId: s.telegram_chat_id ? String(s.telegram_chat_id) : "",
  pushoverAppToken: s.pushover_app_token ? String(s.pushover_app_token) : "",
  pushoverUserKey: s.pushover_user_key ? String(s.pushover_user_key) : "",
  discordWebhookUrl: s.discord_webhook_url ? String(s.discord_webhook_url) : "",
  enabledEvents: Array.isArray(s.webhook_events)
    ? WEBHOOK_EVENTS.map((e) => e.key).filter((k) => (s.webhook_events as string[]).includes(k))
    : WEBHOOK_EVENTS.map((e) => e.key),
})

export default function NotificationsTab() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { data: settings } = useQuery({ queryKey: ["app-settings"], queryFn: api.getSettings })
  const { values, set, dirty, reset } = useSettingsDraft(settings, fromServer)

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        telegram_bot_token: values.telegramToken || null,
        telegram_chat_id: values.telegramChatId || null,
        pushover_app_token: values.pushoverAppToken || null,
        pushover_user_key: values.pushoverUserKey || null,
        discord_webhook_url: values.discordWebhookUrl || null,
        webhook_events: values.enabledEvents,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      toast("Notification settings saved")
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

  // The test buttons send through the saved settings, so they act at once and are not part of the save bar.
  const onTest = {
    onSuccess: (data: any) => toast(data.success ? data.message : data.error, data.success ? ("success" as const) : ("error" as const)),
    onError: (e: Error) => toast(e.message, "error"),
  }
  const testTelegramMutation = useMutation({ mutationFn: () => api.testWebhook("telegram"), ...onTest })
  const testPushoverMutation = useMutation({ mutationFn: () => api.testWebhook("pushover"), ...onTest })
  const testDiscordMutation = useMutation({ mutationFn: () => api.testWebhook("discord"), ...onTest })

  const toggleEvent = (key: string) => {
    const next = values.enabledEvents.includes(key)
      ? values.enabledEvents.filter((e) => e !== key)
      : [...values.enabledEvents, key]
    set("enabledEvents", WEBHOOK_EVENTS.map((e) => e.key).filter((k) => next.includes(k)))
  }

  return (
    <div className="space-y-6">
      {dirty && (
        <p className="text-sm text-muted-foreground" role="status">
          The Test buttons send with your saved settings, so save your changes before testing.
        </p>
      )}
      <Card
        icon={<Send />}
        title="Telegram"
        as="h2"
        id="telegram"
        help={<HelpIcon text="Get notified through a Telegram bot." anchor="notifications" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">Create a bot via @BotFather, then use the bot token and your chat ID.</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Bot Token">
            <Input
              type="password"
              placeholder="123456:ABC-DEF..."
              value={values.telegramToken}
              onChange={(e) => set("telegramToken", e.target.value)}
            />
          </Field>
          <Field label="Chat ID">
            <Input
              type="text"
              placeholder="-1001234567890"
              value={values.telegramChatId}
              onChange={(e) => set("telegramChatId", e.target.value)}
            />
          </Field>
        </div>
        <Button
          variant="outline"
          onClick={() => testTelegramMutation.mutate()}
          disabled={!values.telegramToken || !values.telegramChatId || dirty || testTelegramMutation.isPending}
        >
          {testTelegramMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          Test Telegram
        </Button>
      </Card>

      <Card
        icon={<Bell />}
        title="Pushover"
        as="h2"
        id="pushover"
        help={<HelpIcon text="Push notifications to your phone through Pushover." anchor="notifications" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">Create an app at pushover.net to get your tokens.</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Application Token">
            <Input
              type="password"
              placeholder="Your application token"
              value={values.pushoverAppToken}
              onChange={(e) => set("pushoverAppToken", e.target.value)}
            />
          </Field>
          <Field label="User Key">
            <Input
              type="password"
              placeholder="Your user key"
              value={values.pushoverUserKey}
              onChange={(e) => set("pushoverUserKey", e.target.value)}
            />
          </Field>
        </div>
        <Button
          variant="outline"
          onClick={() => testPushoverMutation.mutate()}
          disabled={!values.pushoverAppToken || !values.pushoverUserKey || dirty || testPushoverMutation.isPending}
        >
          {testPushoverMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          Test Pushover
        </Button>
      </Card>

      <Card
        icon={<Globe />}
        title="Discord"
        as="h2"
        id="discord"
        help={<HelpIcon text="Send notifications to a Discord channel through a webhook." anchor="notifications" />}
        className="space-y-3"
      >
        <p className="text-sm text-muted-foreground">Create a webhook in your Discord channel settings under Integrations.</p>
        <Field label="Webhook URL">
          <Input
            type="password"
            placeholder="https://discord.com/api/webhooks/..."
            value={values.discordWebhookUrl}
            onChange={(e) => set("discordWebhookUrl", e.target.value)}
          />
        </Field>
        <Button
          variant="outline"
          onClick={() => testDiscordMutation.mutate()}
          disabled={!values.discordWebhookUrl || dirty || testDiscordMutation.isPending}
        >
          {testDiscordMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}
          Test Discord
        </Button>
      </Card>

      <Card title="Notification Events" as="h2" className="space-y-3" id="notification-events">
        <p className="text-sm text-muted-foreground">Choose which events trigger push notifications.</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {WEBHOOK_EVENTS.map((evt) => (
            <Checkbox
              key={evt.key}
              label={evt.label}
              checked={values.enabledEvents.includes(evt.key)}
              onChange={() => toggleEvent(evt.key)}
            />
          ))}
        </div>
      </Card>
    </div>
  )
}
