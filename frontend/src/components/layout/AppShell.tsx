import { ReactNode, useState, useEffect } from "react"
import { useLocation } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { AlertStrip } from "@/components/AlertStrip"
import { Dialog } from "@/components/ui/Dialog"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useToast } from "@/components/ui/toaster"
import Sidebar from "./Sidebar"
import Header from "./Header"

/** True while Settings has its Authentication tab open. Settings keeps the open tab in
 *  localStorage and announces changes with a "settings-tab" window event. */
function useAuthTabOpen(onSettings: boolean) {
  const read = () => {
    try { return localStorage.getItem("settings_tab") === "auth" } catch { return false }
  }
  const [open, setOpen] = useState(read)
  useEffect(() => {
    if (!onSettings) return
    setOpen(read())
    const onTab = (e: Event) => setOpen((e as CustomEvent<string>).detail === "auth")
    window.addEventListener("settings-tab", onTab)
    return () => window.removeEventListener("settings-tab", onTab)
  }, [onSettings])
  return onSettings && open
}

/** The one alert slot: hidden on the Dashboard, which shows the full list itself.
 *  The Downloads page explains a paused queue in its own banner, so skip those there.
 *  A queue the user paused is not a fault: it shows on the Dashboard and Downloads only.
 *  Cookie expiry is skipped where the page already shows its own banner. */
function SlimAlerts() {
  const { pathname } = useLocation()
  const onDownloads = pathname.startsWith("/downloads")
  const onAuthTab = useAuthTabOpen(pathname.startsWith("/settings"))
  // Same key as the Downloads page, so the answer is shared
  const { data: pause } = useQuery({
    queryKey: ["download-paused"],
    queryFn: api.getPauseStatus,
    refetchInterval: 30000,
    staleTime: 10000,
  })
  const pausedFor = pause?.paused ? pause.reason : null
  if (pathname === "/") return null
  return (
    <AlertStrip
      variant="slim"
      hide={(a) =>
        a.id === "queue_paused" ||
        // The Downloads banner covers a disk-full or cookie pause; a "disk almost full"
        // or expired cookies without that pause have no banner there, so they still show
        (onDownloads && a.id === "disk_full" && pausedFor === "disk_full") ||
        (onDownloads && a.id === "cookies_expired" && pausedFor === "cookies_expired") ||
        (a.id === "cookies_expired" && onAuthTab)
      }
    />
  )
}

function GlobalNotifications() {
  const { subscribe } = useWebSocket()
  const { toast } = useToast()
  useEffect(() => {
    return subscribe((msg) => {
      if (msg.type === "critical_alert") {
        toast(msg.payload.message || "Critical alert", "error")
      } else if (msg.type === "move_complete") {
        if (msg.payload.failed) {
          toast(msg.payload.message || "File move failed", "error")
        } else {
          toast(msg.payload.message || "File move complete")
        }
      }
    })
  }, [subscribe, toast])
  return null
}

export default function AppShell({ children }: { children: ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // Close the drawer when the window widens to lg, so its overlay can't keep dimming the page
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)")
    const onChange = (e: MediaQueryListEvent) => { if (e.matches) setSidebarOpen(false) }
    mq.addEventListener("change", onChange)
    return () => mq.removeEventListener("change", onChange)
  }, [])

  return (
    <div className="flex h-screen supports-[height:100dvh]:h-dvh overflow-hidden bg-background">
      <GlobalNotifications />
      {/* Mobile menu: the sidebar as a left drawer Dialog */}
      <Dialog
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        title="Menu"
        side="left"
        className="lg:hidden"
      >
        <Sidebar onClose={() => setSidebarOpen(false)} />
      </Dialog>

      {/* Sidebar (desktop) */}
      <div className="hidden w-64 lg:block">
        <Sidebar onClose={() => setSidebarOpen(false)} />
      </div>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header onMenuClick={() => setSidebarOpen(true)} />
        <SlimAlerts />
        <main className="flex-1 overflow-x-hidden overflow-y-auto p-4 sm:p-6">{children}</main>
      </div>
    </div>
  )
}
