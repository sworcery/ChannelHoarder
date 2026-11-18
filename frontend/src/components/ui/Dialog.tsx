import { useRef, type ReactNode } from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"

export type DialogSize = "sm" | "md" | "lg" | "xl"

const SIZES: Record<DialogSize, string> = {
  sm: "max-w-md",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
}

export interface DialogProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  /** Shown under the title in muted text. */
  description?: ReactNode
  size?: DialogSize
  /** "left" makes a full-height drawer pinned to the left edge instead of a centred modal. */
  side?: "left"
  /** Pinned bottom bar (buttons). Stays in view while the body scrolls. */
  footer?: ReactNode
  /** Esc closes the dialog (default true). Turn off during destructive in-progress work. */
  closeOnEsc?: boolean
  /** Clicking the backdrop closes the dialog (default true). */
  closeOnBackdrop?: boolean
  /** Show the X button (default true). */
  showClose?: boolean
  className?: string
  children?: ReactNode
}

/** The one modal. Radix handles the focus trap, focus return, Esc and aria wiring. */
export function Dialog({
  open,
  onClose,
  title,
  description,
  size = "md",
  side,
  footer,
  closeOnEsc = true,
  closeOnBackdrop = true,
  showClose = true,
  className,
  children,
}: DialogProps) {
  const openerRef = useRef<HTMLElement | null>(null)
  // Record the opener in the render that opens the dialog, before the content mounts and moves focus.
  // (An effect would run after a child's autoFocus; Radix also skips onOpenAutoFocus then.)
  if (open && !openerRef.current && typeof document !== "undefined") {
    openerRef.current = document.activeElement as HTMLElement | null
  }
  // Toasts sit above the dialog; a pointer-down on one must not count as an outside click.
  const inToastRegion = (e: { target: EventTarget | null }) =>
    e.target instanceof Element && !!e.target.closest("[data-toast-region]")
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <DialogPrimitive.Content
          aria-modal="true"
          {...(description ? {} : { "aria-describedby": undefined })}
          onOpenAutoFocus={() => { if (!openerRef.current) openerRef.current = document.activeElement as HTMLElement | null }}
          onCloseAutoFocus={(e) => {
            e.preventDefault()
            openerRef.current?.focus?.()
            openerRef.current = null
          }}
          onEscapeKeyDown={(e) => { if (!closeOnEsc) e.preventDefault() }}
          onPointerDownOutside={(e) => { if (!closeOnBackdrop || inToastRegion(e)) e.preventDefault() }}
          onInteractOutside={(e) => { if (!closeOnBackdrop || inToastRegion(e)) e.preventDefault() }}
          className={cn(
            side === "left"
              ? "fixed left-0 top-0 z-50 flex h-full w-64 max-w-[85vw] flex-col bg-card text-card-foreground shadow-lg focus:outline-none"
              : [
                  "fixed left-1/2 top-1/2 z-50 flex w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col",
                  "max-h-[85vh] max-h-[85dvh] rounded-lg border bg-card text-card-foreground shadow-lg focus:outline-none",
                  SIZES[size],
                ],
            className,
          )}
        >
          {side === "left" ? (
            <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
          ) : (
            <div className="flex items-start justify-between gap-3 border-b p-4">
              <div className="min-w-0">
                <DialogPrimitive.Title className="text-lg font-semibold">{title}</DialogPrimitive.Title>
                {description && (
                  <DialogPrimitive.Description className="mt-1 text-sm text-muted-foreground">
                    {description}
                  </DialogPrimitive.Description>
                )}
              </div>
              {showClose && (
                <button
                  type="button"
                  onClick={onClose}
                  aria-label="Close"
                  className="rounded p-1 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>
          )}
          <div className={cn("flex-1 overflow-y-auto", side !== "left" && "p-4")}>{children}</div>
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t p-4">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
