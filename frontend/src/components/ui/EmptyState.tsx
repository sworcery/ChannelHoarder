import type { ReactNode } from "react"

interface EmptyStateProps {
  /** Lucide icon element, e.g. <Inbox />. */
  icon?: ReactNode
  title: ReactNode
  text?: ReactNode
  /** Optional action (a Button). */
  action?: ReactNode
}

export function EmptyState({ icon, title, text, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center py-8 text-center text-muted-foreground">
      {icon && <div className="mb-3 [&>svg]:h-10 [&>svg]:w-10">{icon}</div>}
      <p className="font-medium text-foreground">{title}</p>
      {text && <p className="mt-1 max-w-md text-sm">{text}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
