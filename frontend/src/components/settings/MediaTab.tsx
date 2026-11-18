import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useToast } from "@/components/ui/toaster"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Checkbox } from "@/components/ui/Checkbox"
import { Field } from "@/components/ui/Field"
import { Input, Select, Textarea } from "@/components/ui/Input"
import { useSettingsDraft, useReportForm, asBool, type ServerSettings } from "./settingsForm"

const DEFAULT_TEMPLATE = "{channel_name}/Season {season}/S{season}E{episode} - {title} - {upload_date} - [{video_id}]"

const fromServer = (s: ServerSettings) => ({
  template: s.naming_template ? String(s.naming_template) : DEFAULT_TEMPLATE,
  shortsEnabled: asBool(s.shorts_enabled, false),
  livestreamsEnabled: asBool(s.livestreams_enabled, false),
  subtitlesEnabled: asBool(s.subtitles_enabled, false),
  chaptersEnabled: asBool(s.chapters_enabled, false),
  sponsorblockMode: s.sponsorblock_mode != null ? String(s.sponsorblock_mode) : "off",
  setPermissions: asBool(s.set_permissions, false),
  chmodFolder: s.chmod_folder ? String(s.chmod_folder) : "755",
  chmodFile: s.chmod_file ? String(s.chmod_file) : "644",
  chownGroup: s.chown_group ? String(s.chown_group) : "",
  maxDuration: s.max_video_duration != null ? Math.round(Number(s.max_video_duration) / 3600) : 0,
})

export default function MediaTab() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { data: settings } = useQuery({ queryKey: ["app-settings"], queryFn: api.getSettings })
  const { values, set, dirty, reset } = useSettingsDraft(settings, fromServer)

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        naming_template: values.template,
        shorts_enabled: values.shortsEnabled,
        livestreams_enabled: values.livestreamsEnabled,
        subtitles_enabled: values.subtitlesEnabled,
        chapters_enabled: values.chaptersEnabled,
        sponsorblock_mode: values.sponsorblockMode,
        set_permissions: values.setPermissions,
        chmod_folder: values.chmodFolder,
        chmod_file: values.chmodFile,
        chown_group: values.chownGroup || null,
        max_video_duration: values.maxDuration > 0 ? values.maxDuration * 3600 : 0,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["app-settings"] })
      toast("Media settings saved")
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

  // Preview only reads the template; it is not a setting.
  const previewMutation = useMutation({
    mutationFn: (tmpl: string) => api.previewNaming({ template: tmpl }),
  })

  return (
    <div className="space-y-6">
      <Card
        title="Default Naming Template"
        as="h2"
        id="naming-template"
        help={<HelpIcon text="Controls how downloaded files are named and organized." anchor="file-organization" />}
      >
        <p className="text-sm text-muted-foreground">
          Available variables: {"{channel_name}"}, {"{season}"}, {"{episode}"}, {"{title}"}, {"{upload_date}"}, {"{video_id}"}
        </p>
        <Textarea
          aria-label="Default naming template"
          value={values.template}
          onChange={(e) => set("template", e.target.value)}
          rows={2}
          className="font-mono"
        />
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => previewMutation.mutate(values.template)}>
            Preview
          </Button>
        </div>
        {previewMutation.data && (
          <div className="rounded-md bg-muted p-3">
            <p className="text-sm font-mono">{previewMutation.data.full_path}</p>
          </div>
        )}
      </Card>

      <Card
        title="YouTube Shorts"
        as="h2"
        className="space-y-3"
        id="shorts"
        help={<HelpIcon text="Shorts are skipped unless a channel opts in; this switch allows the opt-in." anchor="episode-management" />}
      >
        <p className="text-sm text-muted-foreground">
          Videos of 60 seconds or less, or on the channel's Shorts tab, are excluded from downloads by default.
        </p>
        <Checkbox
          label="Allow shorts downloading (per-channel opt-in)"
          checked={values.shortsEnabled}
          onChange={(e) => set("shortsEnabled", e.target.checked)}
        />
        <p className="text-xs text-muted-foreground">
          {values.shortsEnabled
            ? "Channels can individually enable shorts downloading in their settings."
            : "Shorts are excluded from all channel downloads."}
        </p>
      </Card>

      <Card
        title="Livestreams"
        as="h2"
        className="space-y-3"
        id="livestreams"
        help={<HelpIcon text="Livestreams and premieres are skipped unless a channel opts in; this switch allows the opt-in." anchor="episode-management" />}
      >
        <p className="text-sm text-muted-foreground">
          Livestreams and premieres are excluded from downloads by default. Scheduled livestreams that haven't started are skipped.
        </p>
        <Checkbox
          label="Allow livestream downloading (per-channel opt-in)"
          checked={values.livestreamsEnabled}
          onChange={(e) => set("livestreamsEnabled", e.target.checked)}
        />
        <p className="text-xs text-muted-foreground">
          {values.livestreamsEnabled
            ? "Now open a channel's page and turn on \"Include livestreams when downloading\" in its Livestreams section. Livestreams stay excluded until you opt in per channel."
            : "Livestreams are excluded from all channel downloads."}
        </p>
      </Card>

      <Card
        title="Subtitles / Captions"
        as="h2"
        className="space-y-3"
        id="subtitles"
        help={<HelpIcon text="Downloads English subtitles and auto-generated captions alongside videos." anchor="episode-management" />}
      >
        <Checkbox
          label="Download subtitles and auto-generated captions"
          checked={values.subtitlesEnabled}
          onChange={(e) => set("subtitlesEnabled", e.target.checked)}
        />
        <p className="text-xs text-muted-foreground">
          Auto-generated captions may not be available for all videos depending on authentication method. PO token authentication has limited subtitle support.
        </p>
      </Card>

      <Card
        title="Chapters"
        as="h2"
        className="space-y-3"
        id="chapters"
        help={<HelpIcon text="Embeds chapter markers so players like Plex and VLC can show them." anchor="episode-management" />}
      >
        <Checkbox
          label="Embed chapter markers in downloaded videos"
          checked={values.chaptersEnabled}
          onChange={(e) => set("chaptersEnabled", e.target.checked)}
        />
        <p className="text-xs text-muted-foreground">Only applies to videos that have chapter markers set by the uploader.</p>
      </Card>

      <Card
        title="SponsorBlock"
        as="h2"
        className="space-y-3"
        id="sponsorblock"
        help={<HelpIcon text="Marks or cuts sponsor and self-promotion segments, using the SponsorBlock database. YouTube only." anchor="episode-management" />}
      >
        <div className="max-w-xs">
          <Select
            aria-label="SponsorBlock mode"
            value={values.sponsorblockMode}
            onChange={(e) => set("sponsorblockMode", e.target.value)}
          >
            <option value="off">Off</option>
            <option value="mark">Mark segments as chapters</option>
            <option value="remove">Remove segments from video</option>
          </Select>
        </div>
        <p className="text-xs text-muted-foreground">
          {values.sponsorblockMode === "remove"
            ? "Sponsor, self-promo, and interaction-reminder segments will be cut out of downloaded YouTube videos."
            : values.sponsorblockMode === "mark"
            ? "Segments are added as skippable chapters; the video is left intact."
            : "SponsorBlock is disabled."}
        </p>
      </Card>

      <Card
        title="File Permissions"
        as="h2"
        className="space-y-3"
        id="file-permissions"
        help={<HelpIcon text="Applies chmod and chown to files after they download." anchor="configuration" />}
      >
        <Checkbox
          label="Set permissions on downloaded files"
          checked={values.setPermissions}
          onChange={(e) => set("setPermissions", e.target.checked)}
        />
        {values.setPermissions && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Folder chmod">
              <Input type="text" value={values.chmodFolder} onChange={(e) => set("chmodFolder", e.target.value)} placeholder="755" />
            </Field>
            <Field label="File chmod">
              <Input type="text" value={values.chmodFile} onChange={(e) => set("chmodFile", e.target.value)} placeholder="644" />
            </Field>
            <Field label="chown group">
              <Input type="text" value={values.chownGroup} onChange={(e) => set("chownGroup", e.target.value)} placeholder="Group name or GID" />
            </Field>
          </div>
        )}
      </Card>

      <Card title="Max Video Duration Filter" as="h2" className="space-y-3" id="max-duration">
        <p className="text-sm text-muted-foreground">
          Skip auto-downloading videos longer than this. They are flagged for manual review and you get a push notification
          (if configured). Set to 0 to disable.
        </p>
        <Field
          label="Max Duration (hours)"
          hint={values.maxDuration > 0 ? `Videos over ${values.maxDuration}h will need manual approval` : "Disabled. All videos auto-queue."}
          className="max-w-xs"
        >
          <Input
            type="number"
            min={0}
            value={values.maxDuration}
            onChange={(e) => set("maxDuration", Number(e.target.value))}
          />
        </Field>
      </Card>
    </div>
  )
}
