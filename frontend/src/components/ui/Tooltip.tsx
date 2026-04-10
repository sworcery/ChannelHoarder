import { useState, useRef, type ReactNode } from "react"

interface TooltipProps {
  content: ReactNode
  side?: "top" | "bottom" | "left" | "right"
  children: ReactNode
}

export function Tooltip({ content, side = "top", children }: TooltipProps) {
  const [visible, setVisible] = useState(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>()

  const show = () => {
    clearTimeout(timeoutRef.current)
    timeoutRef.current = setTimeout(() => setVisible(true), 200)
  }

  const hide = () => {
    clearTimeout(timeoutRef.current)
    setVisible(false)
  }

  const positionClasses: Record<string, string> = {
    top: "bottom-full left-1/2 -translate-x-1/2 mb-2",
    bottom: "top-full left-1/2 -translate-x-1/2 mt-2",
    left: "right-full top-1/2 -translate-y-1/2 mr-2",
    right: "left-full top-1/2 -translate-y-1/2 ml-2",
  }

  return (
    <span className="relative inline-flex" onMouseEnter={show} onMouseLeave={hide}>
      {children}
      {visible && (
        <span
          className={`absolute z-50 px-2.5 py-1.5 text-xs leading-relaxed rounded-md border bg-popover text-popover-foreground shadow-md max-w-xs whitespace-normal pointer-events-none ${positionClasses[side]}`}
        >
          {content}
        </span>
      )}
    </span>
  )
}
