import type { DashboardAlert, Channel, Video, QueueEntry, DashboardStats, DownloadLog, MessageResponse, LibraryCheckState, LibraryCheckApplyResult, SharedFolderGroup, SharedFolderPreview } from "./types"

const BASE_URL = "/api/v1"

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    // Validation errors (422) arrive as a list of {loc, msg, type}; show their messages.
    const detail = Array.isArray(error.detail)
      ? error.detail.map((d: { msg?: string }) => d?.msg ?? String(d)).join("; ")
      : error.detail
    throw new Error(detail || `HTTP ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

interface PaginatedResponse<T> {
  items: T[]
  total: number
  skip?: number
  limit?: number
}

interface SettingsData {
  [key: string]: string | number | boolean | string[] | null
}

export interface DetectCleanPreview {
  threshold: number
  new_shorts_count: number
  new_shorts: { video_id: number; title: string; duration: number | null; status: string; has_file: boolean; file_size: number }[]
  existing_shorts: { video_id: number; title: string; duration: number | null; file_size: number; matches_rule: boolean; reason: string }[]
  existing_shorts_with_files: number
  files_to_delete: number
  disk_space_freed: number
  will_renumber: boolean
}

export const api = {
  // Channels
  getChannels: (search?: string) =>
    request<Channel[]>(`/channels/${search ? `?search=${encodeURIComponent(search)}` : ""}`),
  addChannel: (data: { url: string; quality?: string; download_dir?: string; auto_download?: boolean; download_from_date?: string; title_filter?: string; title_filter_is_regex?: boolean; title_filter_mode?: string }) =>
    request<Channel>("/channels/", { method: "POST", body: JSON.stringify(data) }),
  getChannel: (id: number) => request<Channel>(`/channels/${id}`),
  updateChannel: (id: number, data: Partial<Channel>) =>
    request<Channel>(`/channels/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  getRetentionPreview: (id: number, days: number) =>
    request<{ count: number; total_bytes: number }>(`/channels/${id}/retention-preview?days=${days}`),
  deleteChannel: (id: number, deleteFiles = false) =>
    request<void>(`/channels/${id}?delete_files=${deleteFiles}`, { method: "DELETE" }),
  getChannelVideos: (id: number, params?: { skip?: number; limit?: number; status?: string; monitored?: boolean; search?: string }) => {
    const qs = new URLSearchParams()
    if (params?.skip) qs.set("skip", String(params.skip))
    if (params?.limit) qs.set("limit", String(params.limit))
    if (params?.status) qs.set("status", params.status)
    if (params?.monitored !== undefined) qs.set("monitored", String(params.monitored))
    if (params?.search) qs.set("search", params.search)
    return request<PaginatedResponse<Video> & { skip: number; limit: number }>(`/channels/${id}/videos?${qs}`)
  },
  scanChannel: (id: number) => request<MessageResponse>(`/channels/${id}/scan`, { method: "POST" }),
  refreshChannelMetadata: (id: number) => request<MessageResponse>(`/channels/${id}/refresh-metadata`, { method: "POST" }),
  downloadAllChannel: (id: number) => request<MessageResponse>(`/channels/${id}/download-all`, { method: "POST" }),
  downloadAllMissing: () => request<MessageResponse>("/channels/download-all-missing", { method: "POST" }),
  importScan: (id: number, folderPath: string, threshold = 75) =>
    request<{ matches: { file_path: string; matched_video_id: number; video_title: string; score: number }[]; total: number }>(`/channels/${id}/import/scan`, {
      method: "POST",
      body: JSON.stringify({ folder_path: folderPath, threshold }),
    }),
  importConfirm: (id: number, matches: { file_path: string; matched_video_id: number }[]) =>
    request<{ imported: number; errors: string[] }>(`/channels/${id}/import/confirm`, {
      method: "POST",
      body: JSON.stringify({ matches }),
    }),

  // Shorts management
  getChannelShorts: (channelId: number, status?: string) => {
    const qs = status ? `?status=${status}` : ""
    return request<PaginatedResponse<Video>>(`/channels/${channelId}/shorts${qs}`)
  },
  deleteChannelShorts: (channelId: number) =>
    request<{ deleted: number }>(`/channels/${channelId}/shorts/delete`, { method: "POST" }),
  detectChannelShorts: (channelId: number) =>
    request<{ detected: number }>(`/channels/${channelId}/shorts/detect`, { method: "POST" }),
  detectCleanShortsPreview: (channelId: number) =>
    request<DetectCleanPreview>(`/channels/${channelId}/shorts/detect-clean/preview`, { method: "POST" }),
  detectCleanShortsConfirm: (channelId: number, videoIds: number[]) =>
    request<{ message: string; detected: number; deleted: number; renamed: number; threshold: number }>(`/channels/${channelId}/shorts/detect-clean/confirm`, { method: "POST", body: JSON.stringify({ video_ids: videoIds }) }),

  // Livestream management
  deleteChannelLivestreams: (channelId: number) =>
    request<{ deleted: number; message: string }>(`/channels/${channelId}/livestreams/delete`, { method: "POST" }),
  detectChannelLivestreams: (channelId: number) =>
    request<{ detected: number; message: string }>(`/channels/${channelId}/livestreams/detect`, { method: "POST" }),
  toggleVideoLivestream: (channelId: number, videoId: number, isLivestream: boolean) =>
    request<MessageResponse>(`/channels/${channelId}/videos/${videoId}/livestream`, { method: "PATCH", body: JSON.stringify({ is_livestream: isLivestream }) }),

  // Channel recovery
  forceRescan: (channelId: number) =>
    request<MessageResponse>(`/channels/${channelId}/force-rescan`, { method: "POST" }),

  // Bulk video actions
  bulkQueueVideos: (channelId: number, videoIds: number[]) =>
    request<{ queued: number }>(`/channels/${channelId}/videos/bulk-queue`, {
      method: "POST",
      body: JSON.stringify({ video_ids: videoIds }),
    }),
  bulkSkipVideos: (channelId: number, videoIds: number[]) =>
    request<{ skipped: number; message?: string; running?: number }>(`/channels/${channelId}/videos/bulk-skip`, {
      method: "POST",
      body: JSON.stringify({ video_ids: videoIds }),
    }),
  bulkUnskipVideos: (channelId: number, videoIds: number[]) =>
    request<{ unskipped: number }>(`/channels/${channelId}/videos/bulk-unskip`, {
      method: "POST",
      body: JSON.stringify({ video_ids: videoIds }),
    }),
  bulkDeleteVideos: (channelId: number, videoIds: number[], deleteFiles = false) =>
    request<{ deleted: number }>(`/channels/${channelId}/videos/bulk-delete`, {
      method: "POST",
      body: JSON.stringify({ video_ids: videoIds, delete_files: deleteFiles }),
    }),
  bulkReclassifyVideos: (channelId: number, videoIds: number[], setShort?: boolean, setLivestream?: boolean) =>
    request<{ count: number; message: string }>(`/channels/${channelId}/videos/bulk-reclassify`, {
      method: "POST",
      body: JSON.stringify({ video_ids: videoIds, set_short: setShort, set_livestream: setLivestream }),
    }),
  bulkRenameVideos: (channelId: number, videoIds: number[]) =>
    request<{ message: string; renamed: number; skipped: number; errors: number }>(`/channels/${channelId}/videos/bulk-rename`, {
      method: "POST",
      body: JSON.stringify({ video_ids: videoIds }),
    }),

  // Video management
  deleteVideo: (channelId: number, videoId: number, deleteFiles = false) =>
    request<MessageResponse>(`/channels/${channelId}/videos/${videoId}?delete_files=${deleteFiles}`, { method: "DELETE" }),

  // File management
  redownloadVideo: (channelId: number, videoId: number) =>
    request<MessageResponse>(`/channels/${channelId}/videos/${videoId}/redownload`, { method: "POST" }),
  deleteVideoFile: (channelId: number, videoId: number) =>
    request<MessageResponse>(`/channels/${channelId}/videos/${videoId}/file`, { method: "DELETE" }),
  renameVideoFile: (channelId: number, videoId: number) =>
    request<MessageResponse & { renamed: boolean; new_path?: string }>(`/channels/${channelId}/videos/${videoId}/rename`, { method: "POST" }),

  // Monitoring
  toggleVideoMonitored: (channelId: number, videoId: number, monitored: boolean) =>
    request<MessageResponse>(`/channels/${channelId}/videos/${videoId}/monitored`, { method: "PATCH", body: JSON.stringify({ monitored }) }),
  toggleVideoShort: (channelId: number, videoId: number, isShort: boolean) =>
    request<MessageResponse>(`/channels/${channelId}/videos/${videoId}/short`, { method: "PATCH", body: JSON.stringify({ is_short: isShort }) }),
  bulkMonitorVideos: (channelId: number, videoIds: number[], monitored: boolean) =>
    request<MessageResponse>(`/channels/${channelId}/videos/bulk-monitor`, { method: "POST", body: JSON.stringify({ video_ids: videoIds, monitored }) }),
  // File management
  moveFilesPreview: (channelId: number, newDownloadDir: string) =>
    request<{ channel_id: number; channel_name: string; source_dir: string; dest_dir: string; same_path: boolean; file_count: number; missing_count: number; total_size: number; db_records: number }>(`/channels/${channelId}/move-files/preview`, { method: "POST", body: JSON.stringify({ new_download_dir: newDownloadDir }) }),
  moveChannelFiles: (channelId: number, newDownloadDir: string) =>
    request<MessageResponse>(`/channels/${channelId}/move-files`, { method: "POST", body: JSON.stringify({ new_download_dir: newDownloadDir }) }),
  moveAllPreview: (newDownloadDir: string) =>
    request<{ channels: { channel_name: string; file_count: number; total_size: number }[]; channels_to_move: number; total_files: number; total_size: number }>("/channels/move-all/preview", { method: "POST", body: JSON.stringify({ new_download_dir: newDownloadDir }) }),
  moveAllChannels: (newDownloadDir: string) =>
    request<MessageResponse>("/channels/move-all", { method: "POST", body: JSON.stringify({ new_download_dir: newDownloadDir }) }),

  // Quality management
  upgradeQuality: (channelId: number) =>
    request<MessageResponse>(`/channels/${channelId}/upgrade-quality`, { method: "POST" }),

  // Subtitle management
  downloadChannelSubtitles: (channelId: number) =>
    request<MessageResponse>(`/channels/${channelId}/download-subtitles`, { method: "POST" }),
  downloadVideoSubtitles: (channelId: number, videoId: number) =>
    request<MessageResponse>(`/channels/${channelId}/videos/${videoId}/download-subtitles`, { method: "POST" }),

  // Season management
  monitorSeason: (channelId: number, season: number, monitored: boolean) =>
    request<MessageResponse>(`/channels/${channelId}/seasons/${season}/monitor`, { method: "POST", body: JSON.stringify({ monitored }) }),
  downloadMissingSeason: (channelId: number, season: number) =>
    request<MessageResponse>(`/channels/${channelId}/seasons/${season}/download-missing`, { method: "POST" }),

  // Episode renumbering
  renumberPreview: (channelId: number) =>
    request<{ changes: { video_id: string; title: string; old_label: string; new_label: string; has_file: boolean }[]; total_changes: number }>(`/channels/${channelId}/renumber/preview`, { method: "POST" }),
  renumberConfirm: (channelId: number) =>
    request<{ message: string; updated: number; renamed: number }>(`/channels/${channelId}/renumber/confirm`, { method: "POST" }),

  // Upload-date repair (preview then confirm)
  repairDatesPreview: (channelId: number) =>
    request<{ channel_name: string; checked: number; changes: { video_id: number; source_id: string; title: string; old_date: string; new_date: string; old_episode: string; has_file: boolean }[]; total_changes: number; stopped_early: boolean; message: string | null }>(`/channels/${channelId}/repair-dates/preview`, { method: "POST" }),
  repairDatesConfirm: (channelId: number, changes: { video_id: number; source_id: string; new_date: string }[]) =>
    request<{ message: string; dates_corrected: number; skipped: number; renamed: number }>(`/channels/${channelId}/repair-dates/confirm`, { method: "POST", body: JSON.stringify({ changes }) }),

  // Downloads
  getQueue: (params?: { skip?: number; limit?: number; search?: string }) => {
    const qs = new URLSearchParams()
    if (params?.skip) qs.set("skip", String(params.skip))
    if (params?.limit) qs.set("limit", String(params.limit))
    if (params?.search) qs.set("search", params.search)
    return request<PaginatedResponse<QueueEntry>>(`/downloads/queue?${qs}`)
  },
  bulkRemoveFromQueue: (queueIds: number[]) =>
    request<{ removed: number }>("/downloads/queue/bulk-remove", {
      method: "POST",
      body: JSON.stringify({ queue_ids: queueIds }),
    }),
  removeFromQueue: (queueId: number) =>
    request<void>(`/downloads/queue/${queueId}`, { method: "DELETE" }),
  getHistory: (params?: { skip?: number; limit?: number; status?: string; search?: string; error_code?: string; channel_id?: number }) => {
    const qs = new URLSearchParams()
    if (params?.skip) qs.set("skip", String(params.skip))
    if (params?.limit) qs.set("limit", String(params.limit))
    if (params?.status) qs.set("status", params.status)
    if (params?.search) qs.set("search", params.search)
    if (params?.error_code) qs.set("error_code", params.error_code)
    if (params?.channel_id) qs.set("channel_id", String(params.channel_id))
    return request<PaginatedResponse<Video>>(`/downloads/history?${qs}`)
  },
  retryDownload: (videoId: number) => request<MessageResponse>(`/downloads/retry/${videoId}`, { method: "POST" }),
  retryAllFailed: (opts?: { retryableOnly?: boolean; errorCode?: string }) => {
    const qs = new URLSearchParams()
    if (opts?.retryableOnly) qs.set("retryable_only", "true")
    if (opts?.errorCode) qs.set("error_code", opts.errorCode)
    const q = qs.toString()
    return request<MessageResponse & { count?: number }>(
      `/downloads/retry-all-failed${q ? `?${q}` : ""}`,
      { method: "POST" },
    )
  },
  getErrorCatalog: () =>
    request<Record<string, { label: string; tip: string; failed_tip: string; failed_fix: string; explanation: string; suggested_fix: string; severity: string; retryable: boolean; permanent: boolean }>>(
      "/downloads/error-catalog",
    ),
  getFailedCounts: () => request<Record<string, number>>("/downloads/failed-counts"),
  getHeldChannels: () =>
    request<{ id: number; name: string; reason: string }[]>("/downloads/held-channels"),
  getActiveDownloads: () => request<QueueEntry[]>("/downloads/active"),
  downloadAnyway: (videoId: number) => request<MessageResponse>(`/downloads/download-anyway/${videoId}`, { method: "POST" }),
  getPauseStatus: () => request<{ paused: boolean; reason: string | null }>("/downloads/paused"),
  pauseQueue: () => request<MessageResponse>("/downloads/pause", { method: "POST" }),
  resumeQueue: () => request<MessageResponse>("/downloads/resume", { method: "POST" }),
  clearQueue: () => request<MessageResponse>("/downloads/clear-queue", { method: "POST" }),
  moveQueueItemToTop: (queueId: number) =>
    request<MessageResponse>(`/downloads/queue/${queueId}/move-to-top`, { method: "POST" }),
  downloadNow: (queueId: number) =>
    request<MessageResponse>(`/downloads/queue/${queueId}/download-now`, { method: "POST" }),

  // Quick downloads
  startQuickDownload: (data: { url: string; quality?: string }) =>
    request<{ download_id: string; title: string; thumbnail: string | null; duration: number | null }>("/quick-download", { method: "POST", body: JSON.stringify(data) }),
  getQuickDownloadFiles: () =>
    request<{ filename: string; size_bytes: number; created_at: string; expires_at: string }[]>("/quick-download/files"),
  deleteQuickDownloadFile: (filename: string) =>
    request<MessageResponse>(`/quick-download/files/${encodeURIComponent(filename)}`, { method: "DELETE" }),

  // Dashboard
  getStats: () => request<DashboardStats>("/dashboard/stats"),
  getAlerts: () => request<DashboardAlert[]>("/dashboard/alerts"),
  getRecentDownloads: (limit = 20) => request<Video[]>(`/dashboard/recent?limit=${limit}`),

  // Auth
  uploadCookies: async (file: File) => {
    const formData = new FormData()
    formData.append("file", file)
    const res = await fetch(`${BASE_URL}/auth/cookies/upload`, { method: "POST", body: formData })
    if (!res.ok) throw new Error("Upload failed")
    return res.json()
  },
  validateCookies: () => request<{ status: string; message: string }>("/auth/cookies/validate", { method: "POST" }),
  deleteCookies: () => request<void>("/auth/cookies", { method: "DELETE" }),
  setApiKey: (apiKey: string) =>
    request<MessageResponse>(`/auth/api-key?api_key=${encodeURIComponent(apiKey)}`, { method: "PUT" }),
  getAuthStatus: () => request<{ pot_status: string; pot_message: string | null; cookies_status: string; cookies_message: string | null; cookies_age_hours: number | null; api_key_configured: boolean; api_key_valid: boolean | null; last_successful_auth?: string | null; player_client: string }>("/auth/status"),
  setPlayerClient: (strategy: string) =>
    request<MessageResponse>(`/auth/player-client?strategy=${encodeURIComponent(strategy)}`, { method: "PUT" }),

  // Settings
  getSettings: () => request<SettingsData>("/settings/"),
  updateSettings: (data: SettingsData) =>
    request<MessageResponse>("/settings/", { method: "PUT", body: JSON.stringify(data) }),
  previewNaming: (data: { template: string; channel_name?: string }) =>
    request<{ preview_path: string; full_path: string }>("/settings/naming/preview", { method: "POST", body: JSON.stringify(data) }),
  exportConfig: () => request<{ version: string; settings: SettingsData; channels: Partial<Channel>[] }>("/settings/export"),
  importConfig: async (file: File) => {
    const formData = new FormData()
    formData.append("file", file)
    const res = await fetch(`${BASE_URL}/settings/import`, { method: "POST", body: formData })
    if (!res.ok) {
      const body = await res.json().catch(() => null)
      throw new Error(typeof body?.detail === "string" ? body.detail : "Import failed")
    }
    return res.json()
  },

  // Webhooks
  testWebhook: (provider: string) =>
    request<MessageResponse>(`/settings/webhook/test?provider=${provider}`, { method: "POST" }),

  // System
  getHealth: () => request<{ status: string; version: string; download_dir?: string; temp_dir?: string }>("/system/health"),
  getLiveLogs: (params?: { level?: string; limit?: number }) => {
    const qs = new URLSearchParams()
    if (params?.level) qs.set("level", params.level)
    if (params?.limit) qs.set("limit", String(params.limit))
    return request<{ entries: { timestamp: string; level: string; message: string; logger: string }[]; total: number }>(`/system/live-logs?${qs}`)
  },
  exportLogs: async () => {
    const resp = await fetch("/api/v1/system/live-logs/export")
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `channelhoarder-debug-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-")}.txt`
    a.click()
    URL.revokeObjectURL(url)
  },
  startLibraryCheck: () => request<{ status: string }>("/system/library-check/start", { method: "POST" }),
  getLibraryCheck: () => request<LibraryCheckState>("/system/library-check"),
  applyLibraryCheck: (body: { item_ids?: string[]; all_certain?: boolean; actions?: Record<string, "redownload" | "skip"> }) =>
    request<LibraryCheckApplyResult>("/system/library-check/apply", { method: "POST", body: JSON.stringify(body) }),
  getSharedFolders: () => request<{ groups: SharedFolderGroup[] }>("/system/shared-folders"),
  previewSharedFolderMove: (channelId: number) =>
    request<SharedFolderPreview>(`/system/shared-folders/preview?channel_id=${channelId}`),
  moveSharedFolderChannel: (channelId: number) =>
    request<{ message: string }>("/system/shared-folders/move", { method: "POST", body: JSON.stringify({ channel_id: channelId }) }),
  getYtdlpVersion: () => request<{ version: string }>("/system/ytdlp/version"),
  getDiagnostics: () => request<{ generated_at: string; app_version: string; ytdlp_version: string; pot_status: string; cookies_status: string; api_key_configured: boolean; disk_free_bytes: number; disk_free_formatted: string; disk_status: string; disk_where: string; total_channels: number; total_downloads: number; total_failed: number; recent_errors: { id: number; error_code: string; message: string; created_at: string }[]; system_info: Record<string, string> }>("/system/diagnostics"),
  getLogs: (params?: { skip?: number; limit?: number; error_code?: string; event?: string; search?: string }) => {
    const qs = new URLSearchParams()
    if (params?.skip) qs.set("skip", String(params.skip))
    if (params?.limit) qs.set("limit", String(params.limit))
    if (params?.error_code) qs.set("error_code", params.error_code)
    if (params?.event) qs.set("event", params.event)
    if (params?.search) qs.set("search", params.search)
    return request<PaginatedResponse<DownloadLog>>(`/system/logs?${qs}`)
  },
  scanAll: () => request<MessageResponse>("/system/scan-all", { method: "POST" }),
}
