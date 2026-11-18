import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const VIDEO_URL_TEMPLATES: Record<string, string> = {
  youtube: "https://www.youtube.com/watch?v={video_id}",
  rumble: "https://rumble.com/{video_id}",
  twitch: "https://www.twitch.tv/videos/{video_id}",
  dailymotion: "https://www.dailymotion.com/video/{video_id}",
  vimeo: "https://vimeo.com/{video_id}",
  odysee: "https://odysee.com/{video_id}",
}

export function buildVideoUrl(platform: string, videoId: string): string {
  const template = VIDEO_URL_TEMPLATES[platform] || `https://www.youtube.com/watch?v=${videoId}`
  let vid = videoId
  if (platform === "twitch" && vid.startsWith("v") && /^\d+$/.test(vid.slice(1))) {
    vid = vid.slice(1)
  }
  return template.replace("{video_id}", vid)
}

export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return "0 B"
  const k = 1024
  const sizes = ["B", "KB", "MB", "GB", "TB"]
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i]
}

export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
  return `${m}:${s.toString().padStart(2, "0")}`
}

export function formatDate(date: string | null): string {
  if (!date) return "-"
  // A date-only value ("2025-01-01", e.g. an upload date) is a calendar day, not a
  // moment: parse it as local midnight, or west of UTC it shows as the day before.
  const ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  const value = ymd ? new Date(Number(ymd[1]), Number(ymd[2]) - 1, Number(ymd[3])) : new Date(date)
  return value.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

/** Relative time such as "5 minutes ago" or "in 2 hours". Returns "-" for empty/invalid input. */
export function formatRelative(date: string | Date | null | undefined): string {
  if (!date) return "-"
  const t = new Date(date).getTime()
  if (Number.isNaN(t)) return "-"
  const diffSec = Math.round((t - Date.now()) / 1000)
  const abs = Math.abs(diffSec)
  if (abs < 45) return "just now"
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" })
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["minute", 60],
    ["hour", 3600],
    ["day", 86400],
    ["week", 604800],
    ["month", 2592000],
    ["year", 31536000],
  ]
  let unit: Intl.RelativeTimeFormatUnit = "minute"
  let size = 60
  for (const [u, s] of units) {
    if (abs >= s) { unit = u; size = s }
  }
  return rtf.format(Math.round(diffSec / size), unit)
}

/** Full local date and time, e.g. "Nov 17, 2026, 03:04 PM". Returns "-" for empty/invalid input. */
export function formatFull(date: string | Date | null | undefined): string {
  if (!date) return "-"
  const d = new Date(date)
  if (Number.isNaN(d.getTime())) return "-"
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function formatSpeed(bytesPerSec: number): string {
  if (!bytesPerSec || bytesPerSec <= 0) return "0 B/s"
  const k = 1024
  const sizes = ["B/s", "KB/s", "MB/s", "GB/s"]
  const i = Math.min(Math.floor(Math.log(bytesPerSec) / Math.log(k)), sizes.length - 1)
  return parseFloat((bytesPerSec / Math.pow(k, i)).toFixed(1)) + " " + sizes[i]
}

export function formatEta(seconds: number): string {
  if (!seconds || seconds <= 0) return "--:--"
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
  return `${m}:${s.toString().padStart(2, "0")}`
}

export function formatElapsed(seconds: number): string {
  if (!seconds || seconds <= 0) return "0s"
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}
