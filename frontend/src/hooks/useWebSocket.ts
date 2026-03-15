import { useEffect, useRef, useState, useCallback } from "react"
import type { WSMessage } from "@/lib/types"

type MessageHandler = (msg: WSMessage) => void

export function useWebSocket() {
  const ws = useRef<WebSocket | null>(null)
  const [connected, setConnected] = useState(false)
  const handlers = useRef<Set<MessageHandler>>(new Set())
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()

  const connect = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const url = `${protocol}//${window.location.host}/ws/progress`

    const socket = new WebSocket(url)

    socket.onopen = () => {
      setConnected(true)
      console.log("WebSocket connected")
    }

    socket.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data)
        handlers.current.forEach((handler) => handler(msg))
      } catch {
        // ignore parse errors
      }
    }

    socket.onclose = () => {
      setConnected(false)
      // Reconnect after 3 seconds
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    socket.onerror = () => {
      socket.close()
    }

    ws.current = socket
  }, [])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      ws.current?.close()
    }
  }, [connect])

  const subscribe = useCallback((handler: MessageHandler) => {
    handlers.current.add(handler)
    return () => {
      handlers.current.delete(handler)
    }
  }, [])

  return { connected, subscribe }
}
