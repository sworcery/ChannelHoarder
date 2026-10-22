// Pulls channel URLs and @handles out of free text (newline/comma lists,
// YouTube Takeout subscriptions.csv). Everything else is ignored.
const URL_RE = /(https?:\/\/[^\s,;"'<>]+)|(?:^|[\s,;"'])((?:www\.|m\.)?youtube\.com\/[^\s,;"'<>]+)/giu
const HANDLE_RE = /(?:^|[\s,;"'])(@[\p{L}\p{N}._-]+)(?=$|[\s,;"'])/gu

export function extractChannelUrls(text: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  const add = (url: string) => {
    if (!seen.has(url)) {
      seen.add(url)
      out.push(url)
    }
  }
  for (const line of text.split(/\r?\n/)) {
    const urls = [...line.matchAll(URL_RE)]
    if (urls.length > 0) {
      // A line with a URL is a CSV row or list entry; any @handle on it is part
      // of a title or description, not another channel.
      for (const m of urls) {
        const url = m[1] ?? `https://${m[2]}`
        add(url.replace(/[.)\]]+$/, ""))
      }
      continue
    }
    for (const m of line.matchAll(HANDLE_RE)) {
      const handle = m[1].replace(/\.+$/, "")
      if (handle.length > 1) add(`https://www.youtube.com/${handle}`)
    }
  }
  return out
}
