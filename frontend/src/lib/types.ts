export interface Channel {
  id: number
  channel_id: string
  channel_name: string
  channel_url: string
  platform: string
  thumbnail_url: string | null
  banner_url: string | null
  description: string | null
  quality: string
  naming_template: string | null
  download_dir: string | null
  enabled: boolean
  include_shorts: boolean
  include_livestreams: boolean
  auto_download: boolean
  prioritize_downloads: boolean
  quality_cutoff: string | null
  min_quality: string | null
  preferred_codec: string | null
  min_video_duration: number | null
  download_from_date: string | null
  retention_days: number | null
  title_filter: string | null
  title_filter_is_regex: boolean
  title_filter_mode: "include" | "exclude"
  last_scanned_at: string | null
  next_scan_at: string | null
  total_videos: number
  downloaded_count: number
  health_status: string
  last_error_code: string | null
  created_at: string
  updated_at: string
}

export interface Video {
  id: number
  video_id: string
  channel_id: number
  channel_name: string | null
  platform: string
  title: string
  upload_date: string
  duration: number | null
  thumbnail_url: string | null
  season: number
  episode: number
  status: string
  is_short: boolean
  is_livestream: boolean
  monitored: boolean
  file_path: string | null
  file_size: number | null
  quality_downloaded: string | null
  error_code: string | null
  error_message: string | null
  error_details: string | null
  retry_count: number
  discovered_at: string
  downloaded_at: string | null
}

export interface QueueEntry {
  id: number
  video_id: number
  priority: number
  queued_at: string
  started_at: string | null
  progress_percent: number
  speed_bps: number | null
  eta_seconds: number | null
  target_quality: string | null
  estimated_size: number | null
  retry_after: string | null
  quality_attempts: number
  running: boolean
  stuck: boolean
  video: VideoSummary
}

/** Lightweight video returned inside queue entries (backend VideoSummary). */
export interface VideoSummary {
  id: number
  video_id: string
  channel_id: number
  channel_name: string | null
  platform: string
  title: string
  upload_date: string
  duration: number | null
  thumbnail_url: string | null
  status: string
  monitored: boolean
  error_code: string | null
  error_message: string | null
  retry_count: number
}

export interface DashboardStats {
  total_channels: number
  active_channels: number
  total_videos_known: number
  total_downloaded: number
  total_failed: number
  total_pending: number
  queue_length: number
  storage_used_bytes: number
  storage_used_formatted: string
  pot_status: string
  cookies_status: string
  api_key_configured: boolean
  ytdlp_version: string
  last_scan_at: string | null
  active_downloads: number
  cookies_expired: boolean
}

export interface DownloadLog {
  id: number
  video_id: number
  event: string
  error_code: string | null
  message: string | null
  details?: string | null
  video_title?: string | null
  channel_name?: string | null
  created_at: string
}

export interface MessageResponse {
  message: string
}

export interface WSMessage {
  type: string
  payload: Record<string, any>
}

export type VideoStatus = "pending" | "pending_review" | "queued" | "downloading" | "completed" | "failed" | "skipped"

export const STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  pending_review: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
  queued: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
  downloading: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
  completed: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
  failed: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  skipped: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
}

export const HEALTH_COLORS: Record<string, string> = {
  healthy: "text-green-500",
  warning: "text-yellow-500",
  unhealthy: "text-red-500",
  unknown: "text-gray-400",
}

export type LibraryCheckKind = "wrong_file" | "likely" | "lost" | "manual"

export interface LibraryCheckItem {
  id: string
  kind: LibraryCheckKind
  certain: boolean
  video_id: number
  title: string
  channel_name: string
  file_path: string | null
  real_video_id: number | null
  real_title: string | null
  detail: string
  state: "open" | "fixed" | "skipped" | "error"
  result: string | null
}

export interface LibraryCheckState {
  status: "idle" | "running" | "done" | "error"
  processed: number
  total: number
  started_at: string | null
  finished_at: string | null
  error: string | null
  notes: string[]
  items: LibraryCheckItem[]
}

export interface SharedFolderChannel {
  id: number
  channel_name: string
  platform: string
  created_at: string | null
  file_count: number
}

export interface SharedFolderGroup {
  folder: string
  path: string
  channels: SharedFolderChannel[]
  default_channel_id: number
}

export interface SharedFolderPreview {
  channel_id: number
  channel_name: string
  old_folder: string
  new_folder: string
  new_path: string
  video_files: number
  sidecar_files: number
  missing_files: number
  staying_files: string[]
  shared_sidecar_files: string[]
  skipped_files: string[]
  untracked_move: string[]
  untracked_stay: string[]
}

export interface LibraryCheckApplyResult {
  results: { id: string; state: string; message: string }[]
}
