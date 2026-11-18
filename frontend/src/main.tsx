import React from "react"
import ReactDOM from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createBrowserRouter, RouterProvider } from "react-router-dom"
import App from "./App"
import { ToastProvider } from "./components/ui/toaster"
import "./index.css"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30000,
    },
  },
})

// A data router (one catch-all route; App keeps its own <Routes>) so pages can use
// useBlocker, e.g. Settings asking before you leave with unsaved changes.
const router = createBrowserRouter([
  {
    path: "*",
    element: (
      <ToastProvider>
        <App />
      </ToastProvider>
    ),
  },
])

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
)
