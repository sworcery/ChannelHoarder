import { useState, useRef, useEffect, type ReactNode } from "react"

interface DropdownMenuProps {
  trigger: ReactNode
  children: ReactNode
}

interface DropdownItemProps {
  onClick: () => void
  children: ReactNode
  variant?: "default" | "danger"
  disabled?: boolean
}

export function DropdownMenu({ trigger, children }: DropdownMenuProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [open])

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)} className="p-1 hover:bg-accent rounded">
        {trigger}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 min-w-[160px] rounded-md border bg-popover shadow-md py-1">
          <div onClick={() => setOpen(false)}>
            {children}
          </div>
        </div>
      )}
    </div>
  )
}

export function DropdownItem({ onClick, children, variant = "default", disabled = false }: DropdownItemProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`w-full text-left px-3 py-1.5 text-sm hover:bg-accent disabled:opacity-50 flex items-center gap-2 ${
        variant === "danger" ? "text-red-500 hover:text-red-600" : ""
      }`}
    >
      {children}
    </button>
  )
}

export function DropdownSeparator() {
  return <div className="h-px bg-border my-1" />
}
