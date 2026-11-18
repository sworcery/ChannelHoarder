import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "react-router-dom"
import { X } from "lucide-react"
import { api } from "@/lib/api"
import type { DashboardAlert } from "@/lib/types"
import { Banner } from "@/components/ui/Banner"
import { Button } from "@/components/ui/Button"
import type { Tone } from "@/components/ui/Badge"
import { useToast } from "@/components/ui/toaster"
import { useWebSocket } from "@/hooks/useWebSocket"

const TONE: Record<DashboardAlert["severity"], Tone> = {
  critical: "danger",
  warning: "warning",
  info: "info",
}

const DISMISS_KEY = "alerts_dismissed"

function readDismissed(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem(DISMISS_KEY) || "[]")
  } catch {
    return []
  }
}

/** Every alert shown anywhere: the server's list plus the shared-folders notice. */
function useAlerts() {
  const queryClient = useQueryClient()
  const { subscribe } = useWebSocket()
  const [dismissed, setDismissed] = useState<string[]>(readDismissed)

  const { data: alerts } = useQuery({
    queryKey: ["dashboard-alerts"],
    queryFn: api.getAlerts,
    refetchInterval: 30000,
    staleTime: 10000,
  })

  const { data: sharedData } = useQuery({
    queryKey: ["shared-folders"],
    queryFn: api.getSharedFolders,
    staleTime: 300000,
  })

  // A fresh cookies_expired event shows up at once instead of at the next poll
  useEffect(() => {
    return subscribe((msg) => {
      if (msg.type === "cookies_expired") queryClient.invalidateQueries({ queryKey: ["dashboard-alerts"] })
    })
  }, [subscribe, queryClient])

  const sharedKey = (sharedData?.groups ?? [])
    .flatMap((g) => g.channels.map((c) => c.id))
    .sort((a, b) => a - b)
    .join(",")

  const all: DashboardAlert[] = [...(alerts ?? [])]
  if (sharedKey) {
    all.push({
      // The key is in the id, so a different set of shared channels shows the notice again
      id: `shared_folders:${sharedKey}`,
      severity: "info",
      title: "Some channels share one folder on disk",
      message: "Separate them so each channel keeps its own folder.",
      action: { label: "Separate them", kind: "link", target: "/settings?tab=diagnostics&anchor=shared-folders" },
    })
  }

  const visible = all.filter((a) => a.severity !== "info" || !dismissed.includes(a.id))

  const dismiss = (id: string) => {
    const next = [...dismissed, id]
    setDismissed(next)
    try {
      sessionStorage.setItem(DISMISS_KEY, JSON.stringify(next))
    } catch {
      // storage unavailable: the dismissal lasts until the page reloads
    }
  }

  return { visible, dismiss }
}

function AlertAction({ alert, size }: { alert: DashboardAlert; size?: "sm" | "md" }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const resume = useMutation({
    mutationFn: () => api.resumeQueue(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["dashboard-alerts"] })
      queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue"] })
      queryClient.invalidateQueries({ queryKey: ["download-queue-dashboard"] })
      queryClient.invalidateQueries({ queryKey: ["download-paused"] })
      toast("Queue resumed")
    },
    onError: (e: Error) => toast(e.message, "error"),
  })

  const { action } = alert
  const go = () => {
    const target = action.target || "/"
    // Settings opens the tab named in ?tab=; Diagnostics should open on its overview.
    if (/^\/settings\?tab=diagnostics\b/.test(target)) {
      try { localStorage.setItem("diagnostics_tab", "overview") } catch { /* storage unavailable */ }
    }
    navigate(target)
  }

  return (
    <Button
      size={size}
      variant="outline"
      loading={action.kind === "resume_queue" && resume.isPending}
      onClick={() => (action.kind === "resume_queue" ? resume.mutate() : go())}
    >
      {action.label}
    </Button>
  )
}

function DismissButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="icon" onClick={onClick} title="Dismiss for this session" aria-label="Dismiss">
      <X className="h-4 w-4" />
    </Button>
  )
}

/** Full list on the Dashboard; one line (top alert plus "+N more") everywhere else. */
/** hide: alerts the current page already shows in its own banner. */
export function AlertStrip({ variant, hide }: { variant: "full" | "slim"; hide?: (a: DashboardAlert) => boolean }) {
  const { visible: all, dismiss } = useAlerts()
  const visible = all.filter((a) => !hide?.(a))
  if (visible.length === 0) return null

  // Standing conditions are role="status": announced once, not on every page
  if (variant === "full") {
    return (
      <div className="space-y-3">
        {visible.map((a) => (
          <Banner
            key={a.id}
            role="status"
            tone={TONE[a.severity]}
            title={a.title}
            action={
              <div className="flex items-center gap-1">
                <AlertAction alert={a} />
                {a.severity === "info" && <DismissButton onClick={() => dismiss(a.id)} />}
              </div>
            }
          >
            {a.message}
          </Banner>
        ))}
      </div>
    )
  }

  const top = visible[0]
  const more = visible.length - 1
  return (
    <Banner
      role="status"
      tone={TONE[top.severity]}
      className="items-center rounded-none border-x-0 border-t-0 px-4 py-2"
      action={
        <div className="flex items-center gap-1">
          <AlertAction alert={top} size="sm" />
          {top.severity === "info" && <DismissButton onClick={() => dismiss(top.id)} />}
        </div>
      }
    >
      <div className="flex items-center gap-2">
        <p className="min-w-0 truncate">
          <strong>{top.title}</strong>
          <span className="hidden md:inline"> &middot; {top.message}</span>
        </p>
        {more > 0 && (
          <Link to="/" className="shrink-0 whitespace-nowrap underline font-medium">
            +{more} more
          </Link>
        )}
      </div>
    </Banner>
  )
}
