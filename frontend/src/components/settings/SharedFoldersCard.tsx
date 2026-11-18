import { useEffect, useState } from "react"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { SharedFolderChannel, SharedFolderGroup, SharedFolderPreview } from "@/lib/types"
import { HelpIcon } from "@/components/ui/HelpIcon"
import { ConfirmDialog } from "@/components/ui/ConfirmDialog"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { EmptyState } from "@/components/ui/EmptyState"
import { Select } from "@/components/ui/Input"
import { useToast } from "@/components/ui/toaster"
import { Loader2, Folders } from "lucide-react"

const PLATFORM_LABELS: Record<string, string> = {
  youtube: "YouTube",
  rumble: "Rumble",
  twitch: "Twitch",
  dailymotion: "Dailymotion",
  vimeo: "Vimeo",
  odysee: "Odysee",
}

/** The folder the move creates for this channel: "Name (Platform)". */
const newFolderName = (c: SharedFolderChannel) =>
  `${c.channel_name} (${PLATFORM_LABELS[c.platform] ?? c.platform})`

export default function SharedFoldersCard() {
  const qc = useQueryClient()
  const { toast } = useToast()
  const [picked, setPicked] = useState<Record<string, number>>({})
  const [preview, setPreview] = useState<SharedFolderPreview | null>(null)
  const [loadingKey, setLoadingKey] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [moving, setMoving] = useState<number[]>([])

  const { subscribe } = useWebSocket()
  useEffect(() => {
    // The move result (and any error toast) comes from the global move_complete handler.
    return subscribe((msg) => {
      if (msg.type === "move_complete") {
        setMoving([])
        qc.invalidateQueries({ queryKey: ["shared-folders"] })
      }
    })
  }, [subscribe, qc])

  const { data } = useQuery({
    queryKey: ["shared-folders"],
    queryFn: api.getSharedFolders,
  })
  const groups = data?.groups ?? []

  const chosen = (g: SharedFolderGroup) => picked[g.path] ?? g.default_channel_id

  const showPreview = async (g: SharedFolderGroup) => {
    setLoadingKey(g.path)
    setPreview(null)
    try {
      setPreview(await api.previewSharedFolderMove(chosen(g)))
    } catch (e: any) {
      toast(e.message || "Could not preview the move", "error")
    } finally {
      setLoadingKey(null)
    }
  }

  const doMove = async () => {
    if (!preview) return
    const id = preview.channel_id
    setConfirming(false)
    try {
      const res = await api.moveSharedFolderChannel(id)
      setMoving((m) => [...m, id])
      toast(res.message)
      setPreview(null)
    } catch (e: any) {
      toast(e.message || "Could not start the move", "error")
    }
  }

  return (
    <Card
      icon={<Folders />}
      title="Shared Folders"
      as="h2"
      id="shared-folders"
      help={<HelpIcon text="Channels with the same name added before 1.9.50 share one folder; this moves one into its own." anchor="library-repair" />}
    >
      <p className="text-sm text-muted-foreground">
        Pick the channel to move into its own "Name (Platform)" folder. Only that channel's own videos and extra files
        (thumbnails, subtitles, NFO) move. Files another channel uses, and shared artwork, stay where they are.
      </p>

      {groups.length === 0 && <EmptyState title="No channels share a folder." />}

      {groups.map((g) => (
        <div key={g.path} className="rounded border p-3 space-y-2 text-sm">
          <p className="font-medium">{g.folder}</p>
          <p className="text-xs font-mono break-all text-muted-foreground">{g.path}</p>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground" aria-hidden="true">Move:</span>
            <div className="min-w-0 flex-1 sm:max-w-sm">
              <Select
                aria-label={`Channel to move out of ${g.folder}`}
                value={chosen(g)}
                onChange={(e) => { setPicked((p) => ({ ...p, [g.path]: Number(e.target.value) })); setPreview(null) }}
              >
                {g.channels.map((c) => (
                  <option key={c.id} value={c.id}>
                    {newFolderName(c)}: {c.file_count} files
                  </option>
                ))}
              </Select>
            </div>
            <Button
              size="sm"
              onClick={() => showPreview(g)}
              disabled={loadingKey === g.path || moving.includes(chosen(g))}
            >
              {loadingKey === g.path ? "Loading..." : "Preview"}
            </Button>
            {moving.includes(chosen(g)) && (
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Loader2 className="h-3 w-3 animate-spin" /> Moving...
              </span>
            )}
          </div>

          {!preview && (
            <p className="text-xs text-muted-foreground">
              The chosen channel gets a folder named like "{newFolderName(g.channels.find((c) => c.id === chosen(g)) ?? g.channels[0])}".
            </p>
          )}

          {preview && g.channels.some((c) => c.id === preview.channel_id) && (
            <div className="rounded bg-muted/50 p-2 space-y-1">
              <p>
                New folder: <span className="font-mono">{preview.new_folder}</span>
              </p>
              <p className="text-xs">
                {preview.video_files} video file(s) and {preview.sidecar_files} extra file(s) will move.
                {preview.missing_files > 0 && ` ${preview.missing_files} recorded file(s) are missing on disk.`}
              </p>
              {preview.shared_sidecar_files.length > 0 && (
                <div className="text-xs">
                  <p>{preview.shared_sidecar_files.length} video(s) use a file name another channel's video also uses, so their extra files stay:</p>
                  {preview.shared_sidecar_files.map((f) => (
                    <p key={f} className="font-mono break-all text-muted-foreground">{f}</p>
                  ))}
                </div>
              )}
              {preview.skipped_files.length > 0 && (
                <div className="text-xs">
                  <p>{preview.skipped_files.length} recorded file(s) are outside this folder or already in place and will not move:</p>
                  {preview.skipped_files.map((f) => (
                    <p key={f} className="font-mono break-all text-muted-foreground">{f}</p>
                  ))}
                </div>
              )}
              {preview.untracked_move.length > 0 && (
                <div className="text-xs">
                  <p>{preview.untracked_move.length} file(s) the app has no record of, but whose names carry this channel's video ids, will move too:</p>
                  {preview.untracked_move.map((f) => (
                    <p key={f} className="font-mono break-all text-muted-foreground">{f}</p>
                  ))}
                </div>
              )}
              {preview.untracked_stay.length > 0 && (
                <div className="text-xs">
                  <p>{preview.untracked_stay.length} video file(s) the app has no record of cannot be matched to this channel and will stay:</p>
                  {preview.untracked_stay.map((f) => (
                    <p key={f} className="font-mono break-all text-muted-foreground">{f}</p>
                  ))}
                </div>
              )}
              {preview.staying_files.length > 0 && (
                <div className="text-xs">
                  <p>{preview.staying_files.length} file(s) another channel uses will stay:</p>
                  {preview.staying_files.map((f) => (
                    <p key={f} className="font-mono break-all text-muted-foreground">{f}</p>
                  ))}
                </div>
              )}
              <Button variant="primary" size="sm" onClick={() => setConfirming(true)}>
                Move
              </Button>
            </div>
          )}
        </div>
      ))}

      <ConfirmDialog
        open={confirming}
        title="Move this channel to its own folder?"
        message={preview
          ? `'${preview.channel_name}' moves to '${preview.new_folder}' (${preview.video_files} videos). No file is overwritten or deleted; a file that cannot be moved safely is left where it is. If it stops half-way, run it again to finish.`
          : ""}
        confirmLabel="Move"
        onConfirm={doMove}
        onCancel={() => setConfirming(false)}
      />
    </Card>
  )
}
