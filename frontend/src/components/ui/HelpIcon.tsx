import * as PopoverPrimitive from "@radix-ui/react-popover"
import { CircleHelp, ExternalLink } from "lucide-react"

const REPO_URL = "https://github.com/sworcery/ChannelHoarder"

interface HelpIconProps {
  text: string
  anchor?: string
  side?: string // kept for backwards compat, ignored now
}

/** A small "?" button that opens a popover with the help text and an optional "Learn more" link.
 *  The popover is portaled, so it never sits inside a caller's <label>. */
export function HelpIcon({ text, anchor }: HelpIconProps) {
  return (
    <PopoverPrimitive.Root>
      <PopoverPrimitive.Trigger asChild>
        <button
          type="button"
          aria-label="Help"
          onClick={(e) => e.stopPropagation()}
          className="-m-[9px] inline-flex rounded-full p-[9px] align-middle max-sm:-m-[13px] max-sm:p-[13px] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <CircleHelp className="h-3.5 w-3.5" />
        </button>
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          side="bottom"
          align="start"
          sideOffset={8}
          collisionPadding={16}
          className="z-[90] w-64 max-w-[calc(100vw-2rem)] whitespace-normal rounded-md border bg-popover p-3 text-left text-xs font-normal leading-relaxed text-popover-foreground shadow-md focus:outline-none"
        >
          <span className="block">{text}</span>
          {anchor && (
            <a
              href={`${REPO_URL}#${anchor}`}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1.5 inline-flex items-center gap-0.5 whitespace-nowrap text-primary hover:underline"
            >
              Learn more <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  )
}
