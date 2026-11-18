import { Menu, Moon, Sun } from "lucide-react"
import { useEffect, useState } from "react"
import { Button } from "@/components/ui/Button"
import { useWebSocket } from "@/hooks/useWebSocket"

export default function Header({ onMenuClick }: { onMenuClick: () => void }) {
  const [dark, setDark] = useState(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("theme")
      return saved ? saved === "dark" : true
    }
    return true
  })
  const { connected } = useWebSocket()

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    localStorage.setItem("theme", dark ? "dark" : "light")
  }, [dark])

  return (
    <header className="flex items-center gap-4 border-b bg-card px-4 py-3">
      <Button variant="ghost" size="icon" onClick={onMenuClick} className="lg:hidden" aria-label="Open menu">
        <Menu className="h-6 w-6" />
      </Button>

      <div className="flex-1" />

      {/* WebSocket status */}
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <div
          className={`h-2 w-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
        />
        {connected ? "Live" : "Disconnected"}
      </div>

      {/* Dark mode toggle */}
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setDark(!dark)}
        aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      >
        {dark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
      </Button>
    </header>
  )
}
