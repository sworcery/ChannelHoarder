import { HelpCircle } from "lucide-react"
import { Tooltip } from "./Tooltip"
import type { ReactNode } from "react"

interface HelpIconProps {
  text: ReactNode
  side?: "top" | "bottom" | "left" | "right"
}

export function HelpIcon({ text, side = "top" }: HelpIconProps) {
  return (
    <Tooltip content={text} side={side}>
      <HelpCircle className="h-3.5 w-3.5 text-muted-foreground hover:text-foreground cursor-help flex-shrink-0" />
    </Tooltip>
  )
}
