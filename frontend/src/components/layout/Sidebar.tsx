import { NavLink } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import {
  LayoutDashboard,
  Tv,
  Download,
  Settings,
  Stethoscope,
  X,
} from "lucide-react"
import { cn } from "@/lib/utils"

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/channels", icon: Tv, label: "Channels" },
  { to: "/downloads", icon: Download, label: "Downloads" },
  { to: "/settings", icon: Settings, label: "Settings" },
  { to: "/diagnostics", icon: Stethoscope, label: "Diagnostics" },
]

export default function Sidebar({ onClose }: { onClose: () => void }) {
  const { data: health } = useQuery({
    queryKey: ["system-health"],
    queryFn: api.getHealth,
    staleTime: 300000,
  })

  return (
    <div className="flex h-full flex-col bg-card border-r">
      {/* Logo */}
      <div className="flex flex-col items-center px-4 py-5 border-b relative">
        <button onClick={onClose} className="absolute top-3 right-3 lg:hidden">
          <X className="h-5 w-5" />
        </button>
        <img src="/logo.svg" alt="ChannelHoarder" className="h-32 w-32" />
        <span className="text-xl font-bold mt-2">ChannelHoarder</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            onClick={onClose}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 px-3 py-2.5 rounded-md text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              )
            }
          >
            <item.icon className="h-5 w-5" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t p-4">
        <p className="text-xs text-muted-foreground">ChannelHoarder</p>
        <p className="text-xs text-muted-foreground">v{health?.version || "..."}</p>
      </div>
    </div>
  )
}
