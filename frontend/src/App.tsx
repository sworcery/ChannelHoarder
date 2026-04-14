import { Routes, Route } from "react-router-dom"
import AppShell from "@/components/layout/AppShell"
import { ErrorBoundary } from "@/components/ErrorBoundary"
import DashboardPage from "@/pages/DashboardPage"
import ChannelsPage from "@/pages/ChannelsPage"
import ChannelDetailPage from "@/pages/ChannelDetailPage"
import DownloadsPage from "@/pages/DownloadsPage"
import StandaloneDownloadPage from "@/pages/StandaloneDownloadPage"
import SettingsPage from "@/pages/SettingsPage"

export default function App() {
  return (
    <ErrorBoundary>
      <AppShell>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/channels" element={<ChannelsPage />} />
          <Route path="/channels/:id" element={<ChannelDetailPage />} />
          <Route path="/downloads" element={<DownloadsPage />} />
          <Route path="/download-video" element={<StandaloneDownloadPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </AppShell>
    </ErrorBoundary>
  )
}
