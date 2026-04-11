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

export const api = {
  // Channels
  getChannels: (search?: string) =>
    request<any[]>(`/channels/${search ? `?search=${encodeURIComponent(search)}` : ""}`),
  addChannel: (data: { url: string; quality?: string; download_dir?: string }) =>
    request<any>("/channels/", { method: "POST", body: JSON.stringify(data) }),
  getChannel: (id: number) => request<any>(`/channels/${id}`),
  updateChannel: (id: number, data: any) =>
    request<any>(`/channels/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteChannel: (id: number, deleteFiles = false) =>
    request<void>(`/channels/${id}?delete_files=${deleteFiles}`, { method: "DELETE" }),
  getChannelVideos: (id: number, params?: { skip?: number; limit?: number; status?: string; monitored?: boolean; search?: string }) => {
    const qs = new URLSearchParams()
    if (params?.skip) qs.set("skip", String(params.skip))
    if (params?.limit) qs.set("limit", String(params.limit))
    if (params?.status) qs.set("status", params.status)
    if (params?.monitored !== undefined) qs.set("monitored", String(params.monitored))
    if (params?.search) qs.set("search", params.search)
    return request<{ items: any[]; total: number; skip: number; limit: number }>(`/channels/${id}/videos?${qs}`)
  },
  scanChannel: (id: number) => request<any>(`/channels/${id}/scan`, { method: "POST" }),
  refreshChannelMetadata: (id: number) => request<any>(`/channels/${id}/refresh-metadata`, { method: "POST" }),
  downloadAllChannel: (id: number) => request<any>(`/channels/${id}/download-all`, { method: "POST" }),
  downloadAllMissing: () => request<any>("/channels/download-all-missing", { method: "POST" }),
  importScan: (id: number, folderPath: string, threshold = 75) =>
    request<{ matches: any[]; total: number }>(`/channels/${id}/import/scan`, {
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
    return request<{ items: any[]; total: number }>(`/channels/${channelId}/shorts${qs}`)
  },
  deleteChannelShorts: (channelId: number) =>
    request<{ deleted: number }>(`/channels/${channelId}/shorts/delete`, { method: "POST" }),
  detectChannelShorts: (channelId: number) =>
    request<{ detected: number }>(`/channels/${channelId}/shorts/detect`, { method: "POST" }),

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

  // Video management
  deleteVideo: (channelId: number, videoId: number, deleteFiles = false) =>
    request<any>(`/channels/${channelId}/videos/${videoId}?delete_files=${deleteFiles}`, { method: "DELETE" }),

  // File management
  redownloadVideo: (channelId: number, videoId: number) =>
    request<any>(`/channels/${channelId}/videos/${videoId}/redownload`, { method: "POST" }),
  deleteVideoFile: (channelId: number, videoId: number) =>
    request<any>(`/channels/${channelId}/videos/${videoId}/file`, { method: "DELETE" }),
  renameVideoFile: (channelId: number, videoId: number) =>
    request<any>(`/channels/${channelId}/videos/${videoId}/rename`, { method: "POST" }),

  // Monitoring
  toggleVideoMonitored: (channelId: number, videoId: number, monitored: boolean) =>
    request<any>(`/channels/${channelId}/videos/${videoId}/monitored`, { method: "PATCH", body: JSON.stringify({ monitored }) }),
  bulkMonitorVideos: (channelId: number, videoIds: number[], monitored: boolean) =>
    request<any>(`/channels/${channelId}/videos/bulk-monitor`, { method: "POST", body: JSON.stringify({ video_ids: videoIds, monitored }) }),
  monitorAllVideos: (channelId: number, monitored: boolean) =>
    request<any>(`/channels/${channelId}/monitor-all`, { method: "POST", body: JSON.stringify({ monitored }) }),

  // Quality management
  upgradeQuality: (channelId: number) =>
    request<any>(`/channels/${channelId}/upgrade-quality`, { method: "POST" }),

  // Season management
  monitorSeason: (channelId: number, season: number, monitored: boolean) =>
    request<any>(`/channels/${channelId}/seasons/${season}/monitor`, { method: "POST", body: JSON.stringify({ monitored }) }),
  downloadMissingSeason: (channelId: number, season: number) =>
    request<any>(`/channels/${channelId}/seasons/${season}/download-missing`, { method: "POST" }),

  // Episode renumbering
  renumberPreview: (channelId: number) =>
    request<any>(`/channels/${channelId}/renumber/preview`, { method: "POST" }),
  renumberConfirm: (channelId: number) =>
    request<any>(`/channels/${channelId}/renumber/confirm`, { method: "POST" }),

  // Downloads
  getQueue: (params?: { skip?: number; limit?: number; search?: string }) => {
    const qs = new URLSearchParams()
    if (params?.skip) qs.set("skip", String(params.skip))
    if (params?.limit) qs.set("limit", String(params.limit))
    if (params?.search) qs.set("search", params.search)
    return request<{ items: any[]; total: number }>(`/downloads/queue?${qs}`)
  },
  bulkRemoveFromQueue: (queueIds: number[]) =>
    request<{ removed: number }>("/downloads/queue/bulk-remove", {
      method: "POST",
      body: JSON.stringify({ queue_ids: queueIds }),
    }),
  addToQueue: (videoId: number, priority = 0) =>
    request<any>("/downloads/queue", { method: "POST", body: JSON.stringify({ video_id: videoId, priority }) }),
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
    return request<{ items: any[]; total: number }>(`/downloads/history?${qs}`)
  },
  retryDownload: (videoId: number) => request<any>(`/downloads/retry/${videoId}`, { method: "POST" }),
  retryAllFailed: () => request<any>("/downloads/retry-all-failed", { method: "POST" }),
  getActiveDownloads: () => request<any[]>("/downloads/active"),
  getPauseStatus: () => request<{ paused: boolean }>("/downloads/paused"),
  pauseQueue: () => request<any>("/downloads/pause", { method: "POST" }),
  resumeQueue: () => request<any>("/downloads/resume", { method: "POST" }),
  clearQueue: () => request<any>("/downloads/clear-queue", { method: "POST" }),
  setQueuePriority: (queueId: number, priority: number) =>
    request<any>(`/downloads/queue/${queueId}/priority`, { method: "POST", body: JSON.stringify({ priority }) }),
  downloadNow: (queueId: number) =>
    request<any>(`/downloads/queue/${queueId}/download-now`, { method: "POST" }),

  // Standalone downloads
  downloadStandalone: (data: { url: string; quality?: string; download_dir?: string }) =>
    request<any>("/downloads/standalone", { method: "POST", body: JSON.stringify(data) }),
  getStandaloneSettings: () => request<any>("/downloads/standalone/settings"),
  updateStandaloneSettings: (download_dir: string) =>
    request<any>("/downloads/standalone/settings", { method: "PUT", body: JSON.stringify({ download_dir }) }),

  // Dashboard
  getStats: () => request<any>("/dashboard/stats"),
  getRecentDownloads: (limit = 20) => request<any[]>(`/dashboard/recent?limit=${limit}`),
  getStorage: () => request<any>("/dashboard/storage"),

  // Auth
  uploadCookies: async (file: File) => {
    const formData = new FormData()
    formData.append("file", file)
    const res = await fetch(`${BASE_URL}/auth/cookies/upload`, { method: "POST", body: formData })
    if (!res.ok) throw new Error("Upload failed")
    return res.json()
  },
  getCookieStatus: () => request<any>("/auth/cookies/status"),
  validateCookies: () => request<any>("/auth/cookies/validate", { method: "POST" }),
  deleteCookies: () => request<void>("/auth/cookies", { method: "DELETE" }),
  setApiKey: (apiKey: string) =>
    request<any>(`/auth/api-key?api_key=${encodeURIComponent(apiKey)}`, { method: "PUT" }),
  getAuthStatus: () => request<any>("/auth/status"),

  // Settings
  getSettings: () => request<any>("/settings/"),
  updateSettings: (data: any) =>
    request<any>("/settings/", { method: "PUT", body: JSON.stringify(data) }),
  previewNaming: (data: any) =>
    request<any>("/settings/naming/preview", { method: "POST", body: JSON.stringify(data) }),
  exportConfig: () => request<any>("/settings/export"),
  importConfig: async (file: File) => {
    const formData = new FormData()
    formData.append("file", file)
    const res = await fetch(`${BASE_URL}/settings/import`, { method: "POST", body: formData })
    if (!res.ok) throw new Error("Import failed")
    return res.json()
  },

  // Webhooks
  testWebhook: (provider: string) =>
    request<any>(`/settings/webhook/test?provider=${provider}`, { method: "POST" }),

  // System
  getHealth: () => request<any>("/system/health"),
  getLiveLogs: (params?: { level?: string; limit?: number }) => {
    const qs = new URLSearchParams()
    if (params?.level) qs.set("level", params.level)
    if (params?.limit) qs.set("limit", String(params.limit))
    return request<{ entries: any[]; total: number }>(`/system/live-logs?${qs}`)
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
  getYtdlpVersion: () => request<any>("/system/ytdlp/version"),
  updateYtdlp: () => request<any>("/system/ytdlp/update", { method: "POST" }),
  getDiagnostics: () => request<any>("/system/diagnostics"),
  getVideoDiagnostics: (videoId: number) => request<any>(`/system/diagnostics/${videoId}`),
  getLogs: (params?: { skip?: number; limit?: number; error_code?: string; event?: string; search?: string }) => {
    const qs = new URLSearchParams()
    if (params?.skip) qs.set("skip", String(params.skip))
    if (params?.limit) qs.set("limit", String(params.limit))
    if (params?.error_code) qs.set("error_code", params.error_code)
    if (params?.event) qs.set("event", params.event)
    if (params?.search) qs.set("search", params.search)
    return request<{ items: any[]; total: number }>(`/system/logs?${qs}`)
  },
  scanAll: () => request<any>("/system/scan-all", { method: "POST" }),
}
