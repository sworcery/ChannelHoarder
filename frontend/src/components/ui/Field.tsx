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
  /** Use a plain caption (aria-labelledby) instead of a <label>, so clicking it doesn't activate the control (toggle buttons). */
  caption?: boolean
  /** A single control element (input/select/textarea); it receives id, aria-describedby and aria-invalid. */
  children: ReactElement<Record<string, unknown>> | ReactNode
}

export function Field({ label, help, hint, error, className, caption, children }: FieldProps) {
  const id = useId()
  const captionId = `${id}-caption`
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ") || undefined

  const control = isValidElement<Record<string, unknown>>(children)
    ? cloneElement(children, {
        id: (children.props.id as string | undefined) ?? id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
        ...(caption ? { "aria-labelledby": captionId } : {}),
      })
    : children
  const controlId = isValidElement<Record<string, unknown>>(children) ? ((children.props.id as string | undefined) ?? id) : id

  return (
    <div className={className}>
      <div className="mb-1 flex items-center gap-1 text-sm font-medium">
        {caption ? <span id={captionId}>{label}</span> : <label htmlFor={controlId}>{label}</label>}
        {help}
      </div>
      {control}
      {hint && <p id={hintId} className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      {error && <p id={errorId} role="alert" className={cn("mt-1 text-xs text-danger")}>{error}</p>}
    </div>
  )
}
