import { Menu, Moon, Sun, RefreshCw } from "lucide-react"
import { useEffect, useState } from "react"
import { useWebSocket } from "@/hooks/useWebSocket"

export default function Header({ onMenuClick }: { onMenuClick: () => void }) {
  const [dark, setDark] = useState(() => {
    if (typeof window !== "undefined") {
      return document.documentElement.classList.contains("dark")
    }
    return false
  })
  const { connected } = useWebSocket()

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    localStorage.setItem("theme", dark ? "dark" : "light")
  }, [dark])

  useEffect(() => {
    const saved = localStorage.getItem("theme")
    if (saved === "dark") setDark(true)
  }, [])

  return (
    <header className="flex items-center gap-4 border-b bg-card px-4 py-3">
      <button onClick={onMenuClick} className="lg:hidden">
        <Menu className="h-6 w-6" />
      </button>

      <div className="flex-1" />

      {/* WebSocket status */}
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <div
          className={`h-2 w-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
        />
        {connected ? "Live" : "Disconnected"}
      </div>

      {/* Dark mode toggle */}
      <button
        onClick={() => setDark(!dark)}
        className="p-2 rounded-md hover:bg-accent transition-colors"
      >
        {dark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
      </button>
    </header>
  )
}
