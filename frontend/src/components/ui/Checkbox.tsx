import { forwardRef, useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react"
import { cn } from "@/lib/utils"

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  /** Visible label; clicking it toggles the box, which gives a large hit area. */
  label?: ReactNode
}

/** Native checkbox, 20px. With `label`, the whole row is the click target (min 40px tall on phones). */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, className, id, ...props },
  ref,
) {
  const autoId = useId()
  const inputId = id ?? autoId
  const box = (
    <input
      ref={ref}
      id={inputId}
      type="checkbox"
      className={cn(
        "h-5 w-5 shrink-0 cursor-pointer rounded border border-input accent-primary",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        "disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-danger",
        className,
      )}
      {...props}
    />
  )
  if (!label) return box
  return (
    <label htmlFor={inputId} className="inline-flex min-h-10 cursor-pointer items-center gap-2 text-sm sm:min-h-0">
      {box}
      <span>{label}</span>
    </label>
  )
})

export interface SwitchProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange" | "role"> {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  /** Visible label next to the switch; clicking it toggles too. Otherwise pass aria-label or use Field. */
  label?: ReactNode
}

/** Real button role="switch", about 44x24. */
export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(function Switch(
  { checked, onCheckedChange, label, className, disabled, id, ...props },
  ref,
) {
  const autoId = useId()
  const btnId = id ?? autoId
  const sw = (
    <button
      ref={ref}
      id={btnId}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-transparent transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        "disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-danger",
        checked ? "bg-primary" : "bg-muted-foreground",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className={cn(
          "pointer-events-none block h-5 w-5 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-[22px]" : "translate-x-0.5",
        )}
      />
    </button>
  )
  if (!label) return sw
  return (
    <span className="inline-flex min-h-10 items-center gap-3 text-sm sm:min-h-0">
      {sw}
      <label htmlFor={btnId} className="cursor-pointer">{label}</label>
    </span>
  )
})
