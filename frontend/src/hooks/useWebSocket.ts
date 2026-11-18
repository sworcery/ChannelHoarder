import { useEffect, useState, useCallback } from "react"
import type { WSMessage } from "@/lib/types"

type MessageHandler = (msg: WSMessage) => void

// One shared connection for the whole app. It opens when the first hook mounts and
// closes when the last one unmounts; reconnects every 3 seconds after a drop.
const handlers = new Set<MessageHandler>()
const statusListeners = new Set<(connected: boolean) => void>()
let socket: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | undefined
let users = 0
let isConnected = false

function setConnected(value: boolean) {
  if (isConnected === value) return
  isConnected = value
  statusListeners.forEach((l) => l(value))
}

function connect() {
  if (users === 0) return
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
  const ws = new WebSocket(`${protocol}//${window.location.host}/ws/progress`)
  socket = ws

  ws.onopen = () => {
    if (socket === ws) setConnected(true)
  }

  ws.onmessage = (event) => {
    try {
      const msg: WSMessage = JSON.parse(event.data)
      handlers.forEach((handler) => {
        try { handler(msg) } catch (err) { console.error("WebSocket handler failed", err) }
      })
    } catch {
      // ignore parse errors
    }
  }

  ws.onclose = () => {
    if (socket !== ws) return
    socket = null
    setConnected(false)
    clearTimeout(reconnectTimer)
    if (users > 0) reconnectTimer = setTimeout(connect, 3000)
  }

  ws.onerror = () => {
    ws.close()
  }
}

function subscribe(handler: MessageHandler) {
  handlers.add(handler)
  return () => {
    handlers.delete(handler)
  }
}

export function useWebSocket() {
  const [connected, setConnectedState] = useState(isConnected)

  useEffect(() => {
    users += 1
    statusListeners.add(setConnectedState)
    setConnectedState(isConnected)
    if (users === 1) connect()
    return () => {
      statusListeners.delete(setConnectedState)
      users -= 1
      if (users === 0) {
        clearTimeout(reconnectTimer)
        const ws = socket
        socket = null
        setConnected(false)
        ws?.close()
      }
    }
  }, [])

  const stableSubscribe = useCallback(subscribe, [])

  return { connected, subscribe: stableSubscribe }
}
