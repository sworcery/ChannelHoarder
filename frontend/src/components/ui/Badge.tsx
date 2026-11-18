import type { HTMLAttributes } from "react"
import { cn } from "@/lib/utils"

export type Tone = "neutral" | "success" | "warning" | "danger" | "info"

export const BADGE_TONES: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  info: "bg-info-soft text-info",
}

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone
}

export function Badge({ tone = "neutral", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn("inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium", BADGE_TONES[tone], className)}
      {...props}
    />
  )
}
