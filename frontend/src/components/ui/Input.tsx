import { forwardRef, type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

// One field look: 40px tall, 16px text below sm (stops iOS zoom-on-focus), 14px from sm up.
export const FIELD_BASE =
  "w-full rounded-md border border-input bg-background px-3 text-base text-foreground sm:text-sm " +
  "placeholder:text-muted-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background " +
  "disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-danger"

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, type = "text", ...props },
  ref,
) {
  return <input ref={ref} type={type} className={cn(FIELD_BASE, "h-10", className)} {...props} />
})

/** Native <select>, styled to match Input. */
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...props },
  ref,
) {
  return (
    <div className="relative">
      <select ref={ref} className={cn(FIELD_BASE, "h-10 appearance-none pr-9", className)} {...props}>
        {children}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
    </div>
  )
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, rows = 3, ...props },
  ref,
) {
  return <textarea ref={ref} rows={rows} className={cn(FIELD_BASE, "min-h-20 py-2", className)} {...props} />
})
