import { useState, useEffect } from "react"

/**
 * Debounce a value  - delays updating until the value stops changing for `delay` ms.
 * Useful for search inputs to avoid firing a query on every keystroke.
 */
export function useDebounce<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])

  return debounced
}
