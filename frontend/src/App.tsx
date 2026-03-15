import { Routes, Route } from "react-router-dom"
import AppShell from "@/components/layout/AppShell"
import DashboardPage from "@/pages/DashboardPage"
import ChannelsPage from "@/pages/ChannelsPage"
import ChannelDetailPage from "@/pages/ChannelDetailPage"
import DownloadsPage from "@/pages/DownloadsPage"
import SettingsPage from "@/pages/SettingsPage"
import DiagnosticsPage from "@/pages/DiagnosticsPage"

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/channels" element={<ChannelsPage />} />
        <Route path="/channels/:id" element={<ChannelDetailPage />} />
        <Route path="/downloads" element={<DownloadsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/diagnostics" element={<DiagnosticsPage />} />
      </Routes>
    </AppShell>
  )
}
