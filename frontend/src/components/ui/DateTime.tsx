import { formatFull, formatRelative } from "@/lib/utils"

interface DateTimeProps {
  value: string | Date | null | undefined
  className?: string
}

/** Relative time ("5 minutes ago") with the full date and time in the hover title. */
export function DateTime({ value, className }: DateTimeProps) {
  const d = value ? new Date(value) : null
  if (!d || Number.isNaN(d.getTime())) return <span className={className}>-</span>
  return (
    <time dateTime={d.toISOString()} title={formatFull(value)} className={className}>
      {formatRelative(value)}
    </time>
  )
}
