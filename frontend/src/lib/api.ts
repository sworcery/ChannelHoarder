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
  addChannel: (data: { url: string; quality?: string }) =>
    request<any>("/channels/", { method: "POST", body: JSON.stringify(data) }),
  getChannel: (id: number) => request<any>(`/channels/${id}`),
  updateChannel: (id: number, data: any) =>
    request<any>(`/channels/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteChannel: (id: number, deleteFiles = false) =>
    request<void>(`/channels/${id}?delete_files=${deleteFiles}`, { method: "DELETE" }),
  getChannelVideos: (id: number, params?: { skip?: number; limit?: number; status?: string }) => {
    const qs = new URLSearchParams()
    if (params?.skip) qs.set("skip", String(params.skip))
    if (params?.limit) qs.set("limit", String(params.limit))
    if (params?.status) qs.set("status", params.status)
    return request<any[]>(`/channels/${id}/videos?${qs}`)
  },
  scanChannel: (id: number) => request<any>(`/channels/${id}/scan`, { method: "POST" }),
  downloadAllChannel: (id: number) => request<any>(`/channels/${id}/download-all`, { method: "POST" }),

  // Downloads
  getQueue: () => request<any[]>("/downloads/queue"),
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

  // System
  getHealth: () => request<any>("/system/health"),
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
