import { ExternalLink } from "lucide-react"

const REPO_URL = "https://github.com/sworcery/ChannelHoarder"

interface HelpIconProps {
  text: string
  anchor?: string
  side?: string // kept for backwards compat, ignored now
}

export function HelpIcon({ text, anchor }: HelpIconProps) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <span>{text}</span>
      {anchor && (
        <a
          href={`${REPO_URL}#${anchor}`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-0.5 text-primary hover:underline whitespace-nowrap"
        >
          Learn more <ExternalLink className="h-3 w-3" />
        </a>
      )}
    </span>
  )
}
