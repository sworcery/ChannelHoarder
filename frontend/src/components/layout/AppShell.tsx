import { ReactNode, useState, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { AlertTriangle, X } from "lucide-react"
import { api } from "@/lib/api"
import { useWebSocket } from "@/hooks/useWebSocket"
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
    <div className="flex items-center gap-3 border-b border-red-300 bg-red-50 px-4 py-3 text-red-900 dark:border-red-800 dark:bg-red-950/50 dark:text-red-200">
      <AlertTriangle className="h-5 w-5 shrink-0 text-red-600 dark:text-red-400" />
      <p className="flex-1 text-sm">
        <strong>YouTube cookies have expired.</strong>{" "}
        Downloads will fail until you re-export cookies from your browser and upload them in{" "}
        <Link to="/settings" className="underline font-medium hover:text-red-700 dark:hover:text-red-100">
          Settings &rarr; Authentication
        </Link>.
      </p>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 rounded p-1 hover:bg-red-200 dark:hover:bg-red-900"
        title="Dismiss"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}

export default function AppShell({ children }: { children: ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div
        className={`fixed inset-y-0 left-0 z-50 w-64 transform transition-transform lg:relative lg:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
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
