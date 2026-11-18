export type SettingsTab = "general" | "scanning" | "media" | "auth" | "antidetect" | "notifications" | "diagnostics"

export const TAB_LABELS: Record<SettingsTab, string> = {
  general: "General",
  scanning: "Scanning",
  media: "Media Management",
  auth: "Authentication",
  antidetect: "Anti-Detection",
  notifications: "Notifications",
  diagnostics: "Diagnostics",
}

export interface SettingEntry {
  label: string
  keywords: string
  tab: SettingsTab
  /** The id of the element that holds the setting on its tab. */
  anchorId: string
}

// One entry per setting a user might look for. Each anchorId matches an id on the tab's component.
export const SETTINGS_INDEX: readonly SettingEntry[] = [
  { label: "Default Quality", keywords: "resolution 1080p 4k best new channels", tab: "general", anchorId: "default-quality" },
  { label: "Max Concurrent Downloads", keywords: "simultaneous parallel speed", tab: "general", anchorId: "max-concurrent-downloads" },
  { label: "Max Retries", keywords: "retry failed attempts", tab: "general", anchorId: "max-retries" },
  { label: "Log Level", keywords: "debug logging verbose", tab: "general", anchorId: "log-level" },
  { label: "System Info", keywords: "storage used total channels", tab: "general", anchorId: "system-info" },
  { label: "yt-dlp Version", keywords: "update engine", tab: "general", anchorId: "ytdlp" },
  { label: "Backup & Restore", keywords: "export import config json", tab: "general", anchorId: "backup-restore" },

  { label: "Scan Schedule", keywords: "interval hours how often scan each channel", tab: "scanning", anchorId: "scan-schedule" },
  { label: "Scan All Channels Now", keywords: "manual scan everything", tab: "scanning", anchorId: "scan-all" },
  { label: "Feed Checks", keywords: "rss youtube feeds minutes new uploads", tab: "scanning", anchorId: "feed-checks" },
  { label: "Scan Window", keywords: "time hours night limit scans", tab: "scanning", anchorId: "scan-window" },
  { label: "Scan Now Cooldown", keywords: "manual spam click wait minutes", tab: "scanning", anchorId: "scan-cooldown" },

  { label: "Default Naming Template", keywords: "filename folder plex season episode", tab: "media", anchorId: "naming-template" },
  { label: "YouTube Shorts", keywords: "short videos vertical", tab: "media", anchorId: "shorts" },
  { label: "Livestreams", keywords: "live streams premieres", tab: "media", anchorId: "livestreams" },
  { label: "Subtitles / Captions", keywords: "subtitles captions cc english", tab: "media", anchorId: "subtitles" },
  { label: "Chapters", keywords: "chapter markers embed", tab: "media", anchorId: "chapters" },
  { label: "SponsorBlock", keywords: "sponsor skip segments ads", tab: "media", anchorId: "sponsorblock" },
  { label: "File Permissions", keywords: "chmod chown group PUID permissions", tab: "media", anchorId: "file-permissions" },
  { label: "Max Video Duration Filter", keywords: "long videos review hours skip", tab: "media", anchorId: "max-duration" },

  { label: "PO Tokens", keywords: "token server pot status", tab: "auth", anchorId: "po-tokens" },
  { label: "Cookie Authentication", keywords: "cookies upload cookies.txt sign in validate expired", tab: "auth", anchorId: "cookies" },
  { label: "Browser Cookie Sync", keywords: "tampermonkey userscript", tab: "auth", anchorId: "cookie-sync" },
  { label: "YouTube Data API Key", keywords: "google api key quota", tab: "auth", anchorId: "api-key" },
  { label: "yt-dlp Player Client", keywords: "ios web_safari tv format error", tab: "auth", anchorId: "player-client" },

  { label: "Download Delays", keywords: "delay seconds rate limit pause", tab: "antidetect", anchorId: "download-delays" },
  { label: "User-Agent Rotation", keywords: "browser user agent", tab: "antidetect", anchorId: "user-agent-rotation" },
  { label: "Random Jitter", keywords: "random delay download timing", tab: "antidetect", anchorId: "random-jitter" },
  { label: "Scan Jitter", keywords: "random delay between channels scan order", tab: "antidetect", anchorId: "scan-jitter" },

  { label: "Telegram", keywords: "bot token chat id notifications", tab: "notifications", anchorId: "telegram" },
  { label: "Pushover", keywords: "push phone app token user key notifications", tab: "notifications", anchorId: "pushover" },
  { label: "Discord", keywords: "webhook url notifications", tab: "notifications", anchorId: "discord" },
  { label: "Notification Events", keywords: "alerts which events trigger", tab: "notifications", anchorId: "notification-events" },

  { label: "Copy Diagnostic Report", keywords: "support bug report issue copy", tab: "diagnostics", anchorId: "diagnostic-report" },
  { label: "Statistics", keywords: "failed downloads totals", tab: "diagnostics", anchorId: "diagnostic-statistics" },
  { label: "Library Check", keywords: "wrong file renamed missing videos repair extra files", tab: "diagnostics", anchorId: "library-check" },
  { label: "Shared Folders", keywords: "same name channels folder move platform", tab: "diagnostics", anchorId: "shared-folders" },
  { label: "Recent Errors", keywords: "error codes failures", tab: "diagnostics", anchorId: "recent-errors" },
]

export function searchSettings(query: string): SettingEntry[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return []
  return SETTINGS_INDEX
    .map((e) => {
      const label = e.label.toLowerCase()
      const hay = `${label} ${e.keywords.toLowerCase()} ${TAB_LABELS[e.tab].toLowerCase()}`
      if (!words.every((w) => hay.includes(w))) return null
      // Matches in the label rank above matches in keywords.
      return { e, score: words.every((w) => label.includes(w)) ? 0 : 1 }
    })
    .filter((x): x is { e: SettingEntry; score: number } => x !== null)
    .sort((a, b) => a.score - b.score)
    .map((x) => x.e)
}
