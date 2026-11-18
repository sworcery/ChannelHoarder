import { ReactNode, useState, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { X } from "lucide-react"
import { api } from "@/lib/api"
import { Banner } from "@/components/ui/Banner"
import { Button } from "@/components/ui/Button"
import { Dialog } from "@/components/ui/Dialog"
import { useWebSocket } from "@/hooks/useWebSocket"
import { useToast } from "@/components/ui/toaster"
import Sidebar from "./Sidebar"
import Header from "./Header"

function CookieBanner() {
  const [dismissed, setDismissed] = useState(false)
  const [wsTriggered, setWsTriggered] = useState(false)

  const { data: stats } = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: api.getStats,
    refetchInterval: 30000,
  })

  // Listen for real-time cookies_expired WebSocket events
  const { subscribe } = useWebSocket()
  useEffect(() => {
    return subscribe((msg) => {
      if (msg.type === "cookies_expired") {
        setWsTriggered(true)
        setDismissed(false) // Re-show if previously dismissed
      }
    })
  }, [subscribe])

  const expired = wsTriggered || stats?.cookies_expired
  if (!expired || dismissed) return null

  return (
    <Banner
      tone="danger"
      className="rounded-none border-x-0 border-t-0 px-4 py-3"
      action={
        <Button variant="ghost" size="icon" onClick={() => setDismissed(true)} title="Dismiss" aria-label="Dismiss">
          <X className="h-4 w-4" />
        </Button>
      }
    >
      <strong>YouTube cookies have expired.</strong>{" "}
      Downloads will fail until you re-export cookies from your browser and upload them in{" "}
      <Link to="/settings" className="underline font-medium">
        Settings &rarr; Authentication
      </Link>.
    </Banner>
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
    <div className="flex h-screen overflow-hidden bg-background">
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
        <CookieBanner />
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  )
}
