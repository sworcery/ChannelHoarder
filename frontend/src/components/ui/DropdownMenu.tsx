import { useState, useRef, useEffect, useLayoutEffect, type ReactNode } from "react"
import { createPortal } from "react-dom"

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
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; right: number }>({ top: 0, right: 0 })

  // Position the menu just below the trigger, right-aligned to it. Using fixed
  // positioning in a portal keeps the menu above everything and free of any
  // overflow-hidden ancestor that would otherwise clip it.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    setPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right })
  }, [open])

  useEffect(() => {
    if (!open) return
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node
      if (
        !triggerRef.current?.contains(target) &&
        !menuRef.current?.contains(target)
      ) {
        setOpen(false)
      }
    }
    const close = () => setOpen(false)
    document.addEventListener("mousedown", handleClickOutside)
    window.addEventListener("scroll", close, true)
    window.addEventListener("resize", close)
    return () => {
      document.removeEventListener("mousedown", handleClickOutside)
      window.removeEventListener("scroll", close, true)
      window.removeEventListener("resize", close)
    }
  }, [open])

  return (
    <>
      <button
        ref={triggerRef}
        onClick={() => setOpen(!open)}
        className="p-1 hover:bg-accent rounded"
      >
        {trigger}
      </button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            style={{ position: "fixed", top: pos.top, right: pos.right }}
            className="z-[9999] min-w-[160px] rounded-md border bg-popover shadow-md py-1"
          >
            <div onClick={() => setOpen(false)}>{children}</div>
          </div>,
          document.body
        )}
    </>
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
