import { forwardRef, type ButtonHTMLAttributes } from "react"
import { Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

// danger: the final confirm of a destructive action (usually inside a dialog).
// danger-outline: a page button that starts one (opens the confirm), so it doesn't
// outshout the page's primary action.
export type ButtonVariant = "primary" | "outline" | "ghost" | "success" | "warning" | "danger" | "danger-outline" | "link"
export type ButtonSize = "sm" | "md" | "icon"

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-primary text-primary-foreground hover:bg-primary/90",
  outline: "border hover:bg-accent",
  ghost: "hover:bg-accent",
  success: "bg-green-600 text-white hover:bg-green-700",
  warning: "bg-orange-600 text-white hover:bg-orange-700",
  danger: "bg-red-600 text-white hover:bg-red-700",
  "danger-outline": "border border-red-500/30 text-red-500 hover:bg-red-500/10",
  link: "text-primary hover:underline !px-0 !py-0",
}

const SIZES: Record<ButtonSize, string> = {
  sm: "px-3 py-1 text-xs",
  md: "px-4 py-2 text-sm",
  icon: "p-1.5",
}

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background " +
  "disabled:opacity-50 disabled:pointer-events-none"

/** Button look for non-button elements, e.g. an <a> download link. */
export function buttonClasses(variant: ButtonVariant = "outline", size: ButtonSize = "md"): string {
  return cn(BASE, VARIANTS[variant], SIZES[size])
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "outline", size = "md", loading = false, disabled, className, children, type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(buttonClasses(variant, size), className)}
      {...props}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  )
})
