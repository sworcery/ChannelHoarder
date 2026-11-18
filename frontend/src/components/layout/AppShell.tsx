import { ReactNode, useState, useEffect } from "react"
import { useLocation } from "react-router-dom"
import { AlertStrip } from "@/components/AlertStrip"
import { Dialog } from "@/components/ui/Dialog"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useToast } from "@/components/ui/toaster"
import Sidebar from "./Sidebar"
import Header from "./Header"

/** The one alert slot: hidden on the Dashboard, which shows the full list itself.
 *  The Downloads page explains a paused queue in its own banner, so skip those there. */
function SlimAlerts() {
  const { pathname } = useLocation()
  if (pathname === "/") return null
  // Only the paused kinds: a "disk almost full" without a pause must still show there
  const onDownloads = pathname.startsWith("/downloads")
  return (
    <AlertStrip
      variant="slim"
      hide={(a) => onDownloads && a.action.kind === "resume_queue"}
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
