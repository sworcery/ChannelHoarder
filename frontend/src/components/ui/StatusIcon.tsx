import {
  CheckCircle,
  Circle,
  Clock,
  AlertTriangle,
  XCircle,
  Loader2,
  Ban,
  ArrowUpCircle,
  Eye,
} from "lucide-react"
import { Tooltip } from "./Tooltip"

interface StatusIconProps {
  status: string
  monitored: boolean
  qualityDownloaded?: string | null
  targetQuality?: string
  errorCode?: string | null
  errorMessage?: string | null
}

const QUALITY_RANK: Record<string, number> = {
  "480p": 1,
  "720p": 2,
  "1080p": 3,
  "best": 4,
}

function isQualityMet(downloaded: string | null | undefined, target: string | undefined): boolean {
  if (!downloaded || !target || target === "best") return true
  return (QUALITY_RANK[downloaded] || 0) >= (QUALITY_RANK[target] || 0)
}

export function StatusIcon({ status, monitored, qualityDownloaded, targetQuality, errorCode, errorMessage }: StatusIconProps) {
  // Downloading - animated spinner
  if (status === "downloading") {
    return (
      <Tooltip content="Downloading..." side="left">
        <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />
      </Tooltip>
    )
  }

  // Queued - waiting
  if (status === "queued") {
    return (
      <Tooltip content="Queued for download" side="left">
        <Clock className="h-4 w-4 text-blue-400" />
      </Tooltip>
    )
  }

  // Completed - check quality
  if (status === "completed") {
    if (!isQualityMet(qualityDownloaded, targetQuality)) {
      return (
        <Tooltip content={`Downloaded (${qualityDownloaded || "?"}) - upgrade available to ${targetQuality}`} side="left">
          <ArrowUpCircle className="h-4 w-4 text-yellow-500" />
        </Tooltip>
      )
    }
    return (
      <Tooltip content={`Downloaded${qualityDownloaded ? ` - ${qualityDownloaded}` : ""}`} side="left">
        <CheckCircle className="h-4 w-4 text-green-500" />
      </Tooltip>
    )
  }

  // Failed
  if (status === "failed") {
    const tip = errorMessage ? `Failed: ${errorMessage}` : `Failed${errorCode ? ` - ${errorCode}` : ""}`
    return (
      <Tooltip content={tip} side="left">
        <AlertTriangle className="h-4 w-4 text-red-500" />
      </Tooltip>
    )
  }

  // Skipped
  if (status === "skipped") {
    return (
      <Tooltip content="Skipped" side="left">
        <Ban className="h-4 w-4 text-gray-400" />
      </Tooltip>
    )
  }

  // Pending review
  if (status === "pending_review") {
    return (
      <Tooltip content="Needs review (long video)" side="left">
        <Eye className="h-4 w-4 text-purple-400" />
      </Tooltip>
    )
  }

  // Pending - depends on monitored
  if (status === "pending") {
    if (monitored) {
      return (
        <Tooltip content="Missing - monitored" side="left">
          <Circle className="h-4 w-4 text-orange-400" />
        </Tooltip>
      )
    }
    return (
      <Tooltip content="Unmonitored" side="left">
        <Circle className="h-4 w-4 text-gray-400" />
      </Tooltip>
    )
  }

  // Fallback
  return (
    <Tooltip content={status} side="left">
      <XCircle className="h-4 w-4 text-gray-400" />
    </Tooltip>
  )
}
