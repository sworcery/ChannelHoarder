import type { HTMLAttributes, ReactNode } from "react"
import { cn } from "@/lib/utils"

interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  /** Lucide icon element, shown in the primary colour (give it `h-5 w-5`). */
  icon?: ReactNode
  title?: ReactNode
  /** Heading level of the title (default h3). */
  as?: "h2" | "h3"
  /** Small slot right after the title (e.g. a HelpIcon). */
  help?: ReactNode
  /** Right-aligned slot in the header (buttons, badges). */
  actions?: ReactNode
}

export function Card({ icon, title, as: Heading = "h3", help, actions, className, children, ...props }: CardProps) {
  const hasHeader = icon || title || actions
  return (
    <div className={cn("rounded-lg border bg-card p-4 space-y-4", className)} {...props}>
      {hasHeader && (
        <div className="flex flex-wrap items-center gap-2">
          {icon && <span className="inline-flex text-primary [&>svg]:h-5 [&>svg]:w-5">{icon}</span>}
          {title && <Heading className="font-semibold">{title}</Heading>}
          {help}
          {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  )
}
