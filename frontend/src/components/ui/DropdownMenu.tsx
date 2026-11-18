import { useRef, useState, type ReactNode } from "react"
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu"
import { cn } from "@/lib/utils"

interface DropdownMenuProps {
  trigger: ReactNode
  /** Accessible name for the trigger button (needed when the trigger is just an icon). */
  label?: string
  children: ReactNode
}

interface DropdownItemProps {
  onClick: () => void
  children: ReactNode
  variant?: "default" | "danger"
  disabled?: boolean
}

/** Radix menu: portaled, flips/shifts to stay on screen, Esc / outside-click close, arrow-key navigation. */
export function DropdownMenu({ trigger, label, children }: DropdownMenuProps) {
  const [open, setOpen] = useState(false)
  const touchRef = useRef(false)
  return (
    <DropdownPrimitive.Root open={open} onOpenChange={setOpen}>
      <DropdownPrimitive.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          // Radix opens on pointerdown; for touch, open on click instead so a swipe that starts here still scrolls.
          onPointerDown={(e) => {
            touchRef.current = e.pointerType === "touch"
            if (touchRef.current) e.preventDefault()
          }}
          onClick={() => {
            if (touchRef.current) { touchRef.current = false; setOpen((o) => !o) }
          }}
          className="inline-flex min-h-10 min-w-10 items-center justify-center rounded p-1 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:min-h-0 sm:min-w-0"
        >
          {trigger}
        </button>
      </DropdownPrimitive.Trigger>
      <DropdownPrimitive.Portal>
        <DropdownPrimitive.Content
          align="end"
          side="bottom"
          sideOffset={4}
          collisionPadding={8}
          className="z-[9999] max-h-[var(--radix-dropdown-menu-content-available-height)] min-w-[160px] overflow-y-auto max-w-[calc(100vw-1rem)] rounded-md border bg-popover py-1 text-popover-foreground shadow-md"
        >
          {children}
        </DropdownPrimitive.Content>
      </DropdownPrimitive.Portal>
    </DropdownPrimitive.Root>
  )
}

export function DropdownItem({ onClick, children, variant = "default", disabled = false }: DropdownItemProps) {
  return (
    <DropdownPrimitive.Item
      onSelect={() => onClick()}
      disabled={disabled}
      className={cn(
        "flex min-h-10 w-full cursor-pointer select-none items-center gap-2 px-3 py-1.5 text-left text-sm outline-none sm:min-h-0",
        "hover:bg-accent focus:bg-accent data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        variant === "danger" && "text-danger",
      )}
    >
      {children}
    </DropdownPrimitive.Item>
  )
}

export function DropdownSeparator() {
  return <DropdownPrimitive.Separator className="my-1 h-px bg-border" />
}
