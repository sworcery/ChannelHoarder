import { useEffect, useState } from "react"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { SharedFolderGroup, SharedFolderPreview } from "@/lib/types"
import { ConfirmDialog } from "@/components/ui/ConfirmDialog"
import { useToast } from "@/components/ui/toaster"
import { Loader2, Folders } from "lucide-react"

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
    <div className="rounded-lg border bg-card p-4 space-y-4">
      <h3 className="font-semibold flex items-center gap-2">
        <Folders className="h-4 w-4" /> Shared folders
      </h3>
      <p className="text-sm text-muted-foreground">
        Channels added before 1.9.50 with the same name share one folder. Pick the channel to move into its own
        "Name (Platform)" folder. Only that channel's own videos and their sidecar files move; a file another
        channel's video records stays put. Shared artwork stays with the channel that keeps the folder.
      </p>

      {groups.length === 0 && <p className="text-sm text-muted-foreground">No channels share a folder.</p>}

      {groups.map((g) => (
        <div key={g.path} className="rounded border p-3 space-y-2 text-sm">
          <p className="font-medium">{g.folder}</p>
          <p className="text-xs font-mono break-all text-muted-foreground">{g.path}</p>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-xs text-muted-foreground">Move:</label>
            <select
              value={chosen(g)}
              onChange={(e) => { setPicked((p) => ({ ...p, [g.path]: Number(e.target.value) })); setPreview(null) }}
              className="px-2 py-1 rounded-md border bg-background text-xs"
            >
              {g.channels.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.channel_name} ({c.platform}, {c.file_count} files)
                </option>
              ))}
            </select>
            <button
              onClick={() => showPreview(g)}
              disabled={loadingKey === g.path || moving.includes(chosen(g))}
              className="px-2 py-1 text-xs rounded-md border hover:bg-accent disabled:opacity-50"
            >
              {loadingKey === g.path ? "Loading..." : "Preview"}
            </button>
            {moving.includes(chosen(g)) && (
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Loader2 className="h-3 w-3 animate-spin" /> Moving...
              </span>
            )}
          </div>

          {preview && g.channels.some((c) => c.id === preview.channel_id) && (
            <div className="rounded bg-muted/50 p-2 space-y-1">
              <p>
                New folder: <span className="font-mono">{preview.new_folder}</span>
              </p>
              <p className="text-xs">
                {preview.video_files} video file(s) and {preview.sidecar_files} sidecar file(s) will move.
                {preview.missing_files > 0 && ` ${preview.missing_files} recorded file(s) are missing on disk.`}
              </p>
              {preview.shared_sidecar_files.length > 0 && (
                <div className="text-xs">
                  <p>{preview.shared_sidecar_files.length} video(s) share a file name with another channel's video, so their sidecars stay (shared name):</p>
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
                  <p>{preview.untracked_move.length} unrecorded file(s) tagged with this channel's video ids will move too:</p>
                  {preview.untracked_move.map((f) => (
                    <p key={f} className="font-mono break-all text-muted-foreground">{f}</p>
                  ))}
                </div>
              )}
              {preview.untracked_stay.length > 0 && (
                <div className="text-xs">
                  <p>{preview.untracked_stay.length} unrecorded video file(s) cannot be matched to this channel and will stay:</p>
                  {preview.untracked_stay.map((f) => (
                    <p key={f} className="font-mono break-all text-muted-foreground">{f}</p>
                  ))}
                </div>
              )}
              {preview.staying_files.length > 0 && (
                <div className="text-xs">
                  <p>{preview.staying_files.length} file(s) another channel records will stay:</p>
                  {preview.staying_files.map((f) => (
                    <p key={f} className="font-mono break-all text-muted-foreground">{f}</p>
                  ))}
                </div>
              )}
              <button
                onClick={() => setConfirming(true)}
                className="px-3 py-1 text-xs rounded-md bg-primary text-primary-foreground"
              >
                Move
              </button>
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
    </div>
  )
}
