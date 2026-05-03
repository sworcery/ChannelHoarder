import type { Channel, Video, QueueEntry, DashboardStats, DownloadLog, MessageResponse } from "./types"

const BASE_URL = "/api/v1"

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(error.detail || `HTTP ${res.status}`)
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

interface StorageInfo {
  total_bytes: number
  used_bytes: number
  free_bytes: number
  total_formatted: string
  used_formatted: string
  free_formatted: string
  paths: { path: string; free_bytes: number; free_formatted: string }[]
}

interface SettingsData {
  [key: string]: string | number | boolean | string[] | null
}

interface DetectCleanPreview {
  reclassified: number
  files_to_delete: number
  renumber_changes: number
  details: { video_id: string; title: string; has_file: boolean; file_path?: string }[]
}

export const api = {
  // Channels
  getChannels: (search?: string) =>
    request<Channel[]>(`/channels/${search ? `?search=${encodeURIComponent(search)}` : ""}`),
  addChannel: (data: { url: string; quality?: string; download_dir?: string }) =>
    request<Channel>("/channels/", { method: "POST", body: JSON.stringify(data) }),
  getChannel: (id: number) => request<Channel>(`/channels/${id}`),
  updateChannel: (id: number, data: Partial<Channel>) =>
    request<Channel>(`/channels/${id}`, { method: "PUT", body: JSON.stringify(data) }),
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
  detectCleanShortsConfirm: (channelId: number) =>
    request<{ message: string; detected: number; deleted: number; renamed: number; threshold: number }>(`/channels/${channelId}/shorts/detect-clean/confirm`, { method: "POST" }),

  // Livestream management
  getChannelLivestreams: (channelId: number, status?: string) => {
    const qs = status ? `?status=${status}` : ""
    return request<PaginatedResponse<Video>>(`/channels/${channelId}/livestreams${qs}`)
  },
  deleteChannelLivestreams: (channelId: number) =>
    request<{ deleted: number; message: string }>(`/channels/${channelId}/livestreams/delete`, { method: "POST" }),
  detectChannelLivestreams: (channelId: number) =>
    request<{ detected: number; message: string }>(`/channels/${channelId}/livestreams/detect`, { method: "POST" }),
  detectCleanLivestreamsPreview: (channelId: number) =>
    request<DetectCleanPreview>(`/channels/${channelId}/livestreams/detect-clean/preview`, { method: "POST" }),
  detectCleanLivestreamsConfirm: (channelId: number) =>
    request<{ message: string; detected: number; deleted: number; renamed: number }>(`/channels/${channelId}/livestreams/detect-clean/confirm`, { method: "POST" }),
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
    request<{ skipped: number }>(`/channels/${channelId}/videos/bulk-skip`, {
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
  monitorAllVideos: (channelId: number, monitored: boolean) =>
    request<MessageResponse>(`/channels/${channelId}/monitor-all`, { method: "POST", body: JSON.stringify({ monitored }) }),

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
  addToQueue: (videoId: number, priority = 0) =>
    request<MessageResponse>("/downloads/queue", { method: "POST", body: JSON.stringify({ video_id: videoId, priority }) }),
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
  retryAllFailed: () => request<MessageResponse>("/downloads/retry-all-failed", { method: "POST" }),
  getActiveDownloads: () => request<QueueEntry[]>("/downloads/active"),
  getPauseStatus: () => request<{ paused: boolean }>("/downloads/paused"),
  pauseQueue: () => request<MessageResponse>("/downloads/pause", { method: "POST" }),
  resumeQueue: () => request<MessageResponse>("/downloads/resume", { method: "POST" }),
  clearQueue: () => request<MessageResponse>("/downloads/clear-queue", { method: "POST" }),
  setQueuePriority: (queueId: number, priority: number) =>
    request<MessageResponse>(`/downloads/queue/${queueId}/priority`, { method: "POST", body: JSON.stringify({ priority }) }),
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
  getRecentDownloads: (limit = 20) => request<Video[]>(`/dashboard/recent?limit=${limit}`),
  getStorage: () => request<StorageInfo>("/dashboard/storage"),

  // Auth
  uploadCookies: async (file: File) => {
    const formData = new FormData()
    formData.append("file", file)
    const res = await fetch(`${BASE_URL}/auth/cookies/upload`, { method: "POST", body: formData })
    if (!res.ok) throw new Error("Upload failed")
    return res.json()
  },
  getCookieStatus: () => request<{ exists: boolean; path: string; size: number | null }>("/auth/cookies/status"),
  validateCookies: () => request<{ valid: boolean; message: string }>("/auth/cookies/validate", { method: "POST" }),
  deleteCookies: () => request<void>("/auth/cookies", { method: "DELETE" }),
  setApiKey: (apiKey: string) =>
    request<MessageResponse>(`/auth/api-key?api_key=${encodeURIComponent(apiKey)}`, { method: "PUT" }),
  getAuthStatus: () => request<{ pot_status: string; pot_message: string | null; cookies_status: string; cookies_message: string | null; cookies_age_hours: number | null; api_key_configured: boolean; api_key_valid: boolean | null; last_successful_auth: string | null }>("/auth/status"),

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
    if (!res.ok) throw new Error("Import failed")
    return res.json()
  },

  // Webhooks
  testWebhook: (provider: string) =>
    request<MessageResponse>(`/settings/webhook/test?provider=${provider}`, { method: "POST" }),

  // System
  getHealth: () => request<{ status: string; version: string }>("/system/health"),
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
  getYtdlpVersion: () => request<{ version: string }>("/system/ytdlp/version"),
  updateYtdlp: () => request<MessageResponse>("/system/ytdlp/update", { method: "POST" }),
  getDiagnostics: () => request<{ generated_at: string; app_version: string; ytdlp_version: string; pot_status: string; cookies_status: string; api_key_configured: boolean; disk_free_bytes: number; disk_free_formatted: string; total_channels: number; total_downloads: number; total_failed: number; recent_errors: { id: number; error_code: string; message: string; created_at: string }[]; system_info: Record<string, string> }>("/system/diagnostics"),
  getVideoDiagnostics: (videoId: number) => request<Record<string, unknown>>(`/system/diagnostics/${videoId}`),
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
