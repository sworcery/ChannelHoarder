import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react"
import { cn } from "@/lib/utils"

interface FieldProps {
  label: ReactNode
  /** Small slot after the label (e.g. a HelpIcon). */
  help?: ReactNode
  /** Muted text under the control. */
  hint?: ReactNode
  /** Error text under the control; also sets aria-invalid on the control. */
  error?: ReactNode
  className?: string
  /** A single control element (input/select/textarea); it receives id, aria-describedby and aria-invalid. */
  children: ReactElement<Record<string, unknown>> | ReactNode
}

export function Field({ label, help, hint, error, className, children }: FieldProps) {
  const id = useId()
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ") || undefined

  const control = isValidElement<Record<string, unknown>>(children)
    ? cloneElement(children, {
        id: (children.props.id as string | undefined) ?? id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
      })
    : children
  const controlId = isValidElement<Record<string, unknown>>(children) ? ((children.props.id as string | undefined) ?? id) : id

  return (
    <div className={className}>
      <div className="mb-1 flex items-center gap-1 text-sm font-medium">
        <label htmlFor={controlId}>{label}</label>
        {help}
      </div>
      {control}
      {hint && <p id={hintId} className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      {error && <p id={errorId} role="alert" className={cn("mt-1 text-xs text-danger")}>{error}</p>}
    </div>
  )
}
