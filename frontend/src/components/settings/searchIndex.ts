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

  { label: "Telegram", keywords: "bot token chat id notifications connect",tab: "notifications", anchorId: "telegram" },
  { label: "Pushover", keywords: "push phone app token user key notifications connect",tab: "notifications", anchorId: "pushover" },
  { label: "Discord", keywords: "webhook url notifications connect",tab: "notifications", anchorId: "discord" },
  { label: "Notification Events", keywords: "alerts which events trigger connect",tab: "notifications", anchorId: "notification-events" },

  { label: "Copy Diagnostic Report", keywords: "support bug report issue copy", tab: "diagnostics", anchorId: "diagnostic-report" },
  { label: "Statistics", keywords: "failed downloads totals", tab: "diagnostics", anchorId: "diagnostic-statistics" },
  { label: "Library Check", keywords: "wrong file renamed missing videos repair extra files", tab: "diagnostics", anchorId: "library-check" },
  { label: "Shared Folders", keywords: "same name channels folder move platform", tab: "diagnostics", anchorId: "shared-folders" },
  { label: "Recent Errors", keywords: "error codes failures", tab: "diagnostics", anchorId: "recent-errors" },
]

/** Things people search for that are not on this page. Choosing one shows the text instead of jumping. */
export interface PointerEntry {
  label: string
  keywords: string
  text: string
}

export const POINTER_INDEX: readonly PointerEntry[] = [
  { label: "Download folder", keywords: "path directory location output where files saved", text: "Set per channel (in its settings), or with DOWNLOAD_DIR in the container settings." },
  { label: "Minimum quality", keywords: "min quality cutoff resolution upgrade", text: "Set per channel: open the channel's page and change its Quality settings." },
  { label: "Retention", keywords: "delete old videos days keep cleanup", text: "Set per channel: open the channel's page and use its \"Delete videos after (days)\" field." },
  { label: "Shorter than (minimum duration)", keywords: "shorter min minimum duration length short videos skip", text: "Set per channel: open a channel, click Edit, and use \"Skip videos shorter than (seconds)\"." },
  { label: "Auto-download", keywords: "auto download automatic new videos", text: "Set per channel: open a channel, click Edit, and use the Auto-download switch under Monitoring." },
  { label: "Theme", keywords: "dark light mode appearance colour color", text: "Use the theme button in the page header." },
  { label: "Timezone", keywords: "time zone clock tz", text: "Set with the TZ variable in the container settings." },
  { label: "Proxy", keywords: "http socks vpn", text: "ChannelHoarder has no proxy setting." },
  { label: "Download order", keywords: "queue priority newest oldest", text: "Set on the Downloads page." },
]

type Scored<T> = { e: T; score: number }

function matches(words: string[], label: string, rest: string): number | null {
  const l = label.toLowerCase()
  // A word matches only at the start of a word, so "nfo" does not find "Info".
  const tokens = `${l} ${rest.toLowerCase()}`.split(/[^a-z0-9.]+/).filter(Boolean)
  if (!words.every((w) => tokens.some((t) => t.startsWith(w)))) return null
  // Matches in the label rank above matches in keywords.
  const labelTokens = l.split(/[^a-z0-9.]+/).filter(Boolean)
  return words.every((w) => labelTokens.some((t) => t.startsWith(w))) ? 0 : 1
}

function rank<T>(items: readonly T[], score: (e: T) => number | null): T[] {
  return items
    .map((e) => ({ e, score: score(e) }))
    .filter((x): x is Scored<T> => x.score !== null)
    .sort((a, b) => a.score - b.score)
    .map((x) => x.e)
}

const toWords = (query: string) => query.toLowerCase().split(/[^a-z0-9.]+/).filter(Boolean)

export function searchSettings(query: string): SettingEntry[] {
  const words = toWords(query)
  if (words.length === 0) return []
  return rank(SETTINGS_INDEX, (e) => matches(words, e.label, `${e.keywords} ${TAB_LABELS[e.tab]}`))
}

export function searchPointers(query: string): PointerEntry[] {
  const words = toWords(query)
  if (words.length === 0) return []
  return rank(POINTER_INDEX, (e) => matches(words, e.label, e.keywords))
}
