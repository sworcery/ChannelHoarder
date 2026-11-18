import type { ReactNode } from "react"
import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import type { Tone } from "@/components/ui/Badge"

const TONES: Record<Tone, string> = {
  neutral: "border-border bg-muted/50 text-foreground",
  success: "border-success-border bg-success-soft/30 text-success",
  warning: "border-warning-border bg-warning-soft/30 text-warning",
  danger: "border-danger-border bg-danger-soft/30 text-danger",
  info: "border-info-border bg-info-soft/30 text-info",
}

const ICONS: Record<Tone, typeof Info> = {
  neutral: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: XCircle,
  info: Info,
}

interface BannerProps {
  tone?: Tone
  title?: ReactNode
  /** Right-side action (a Button). */
  action?: ReactNode
  className?: string
  /** Override the live-region role; use "status" for a condition that stays on screen. */
  role?: "alert" | "status"
  children?: ReactNode
}

export function Banner({ tone = "info", title, action, className, role, children }: BannerProps) {
  const Icon = ICONS[tone]
  return (
    <div
      role={role ?? (tone === "danger" || tone === "warning" ? "alert" : "status")}
      className={cn("flex flex-wrap items-start gap-3 rounded-lg border p-4", TONES[tone], className)}
    >
      <Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
      <div className="min-w-0 flex-1 text-sm">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={cn(title && "mt-0.5", "text-foreground/80")}>{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  )
}
