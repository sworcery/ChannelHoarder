# ChannelHoarder — Project History and Current State

A working reference for anyone picking up this codebase. It covers what the project is, how it
got here, the bug classes that keep recurring, and what is currently outstanding.

Last updated: 2026-08-16 (at v1.9.40, commit `b76b7ad`)

---

## 1. What this project is

ChannelHoarder is a self-hosted video channel archiver with a web UI. It monitors channels across
YouTube, Rumble, Twitch, Dailymotion, Vimeo, and Odysee, and downloads new videos into a
Plex-compatible layout.

- **Shipping form:** a single Docker container (FastAPI backend + React frontend + yt-dlp + PO token server)
- **Distribution:** GitHub → ghcr.io, plus the Unraid Community Applications store
- **License:** MIT
- **Status:** public, with real users actively installing and filing issues. Treat regressions as
  user-facing incidents, not internal breakage.

The user base is predominantly Unraid home-server operators. Most bug reports arrive with Docker
path mappings, container templates, and Plex libraries in the picture — assume that context.

---

## 2. Current state (as of v1.9.40)

| | |
|---|---|
| Version | 1.9.40 |
| Branch | `main` (only long-lived branch) |
| HEAD | `b76b7ad` — "Honor the global naming template everywhere, not just on download" |
| Working tree | clean, nothing unpushed |
| CI | GitHub Actions → ghcr.io, green; also rebuilds on a schedule |
| Total commits | 200, starting 2026-04-05 |
| Releases | none cut — no git tags, no GitHub Releases. Shipping is via `:latest` on every push. |

There is no test suite. Verification is lint + typecheck + manual/ad-hoc scripts. See §8.

---

## 3. Architecture

```
backend/app/
  config.py          Settings; APP_VERSION lives here
  database.py        Async SQLAlchemy engine, session, PRAGMA tuning
  models.py          Channel, Video, DownloadQueue, AppSetting, DownloadLog
  schemas.py         Pydantic request/response models
  routers/           channels, downloads, dashboard, settings, auth, system,
                     websocket, quick_download
  services/          channel_service, download_service, ytdlp_service,
                     naming_service, import_service, metadata_service,
                     settings_service, storage_service, youtube_api_service,
                     notification_service, webhook_service, scheduler_service,
                     diagnostics_service
  tasks/             scan_channels, process_queue, quality_upgrade,
                     nfo_maintenance, cookie_watcher, cookie_recovery,
                     pot_watchdog, health_check, temp_cleanup,
                     quick_download_cleanup, cleanup_unavailable
  utils/             platform_utils, file_utils, renumber, quality_utils,
                     cookie_utils, permissions, rate_limiter, scan_window,
                     error_codes, log_buffer, user_agents
frontend/src/
  pages/             Dashboard, Channels, ChannelDetail, Downloads, Settings,
                     StandaloneDownload
  lib/               api.ts (all API calls), types.ts, utils.ts
docker/              Dockerfile, entrypoint.sh, unraid-template.xml
tools/               cookie_exporter.py, tampermonkey exporter, install_task.bat
docs/                API.md
```

### Conventions that matter

- **Version must be bumped in three files every commit:** `backend/app/config.py`,
  `backend/pyproject.toml`, `frontend/package.json`. CHANGELOG.md updated too.
- All SQLAlchemy relationships use `lazy="noload"`; use explicit `joinedload()` when needed.
- `Channel.total_videos` / `downloaded_count` are cached ints and go stale — the channels router
  computes live counts from the Video table instead.
- Settings are key/value rows in `AppSetting`, JSON-encoded values.
- SQLite in WAL mode, async via aiosqlite. No Alembic — schema changes go through model edits, and
  any migration in `database.py` must be guarded with `PRAGMA table_info()` and tested on both a
  fresh install and an upgrade.
- yt-dlp format strings use `bestvideo*` (with the asterisk) so they match muxed streams too.
- Prefer `release_date` over `upload_date` from yt-dlp; prefer `contentDetails.videoPublishedAt`
  over `snippet.publishedAt` from the YouTube API.
- Frontend uses TanStack Query with `placeholderData: keepPreviousData`, which means `isLoading`
  stays false — use `isFetching && !data` for loading states.

---

## 4. How the project evolved

**v1.4.x (Apr 5–6)** — initial public release, Unraid CA submission, fresh-install DB creation fix.

**v1.5.x (Apr 6–8)** — security/perf pass, ruff configured, multi-arch attempted then dropped
(QEMU crashed on native Node modules). PO token server watchdog. Cookies became primary auth with
PO tokens as fallback.

**v1.6.x (Apr 8–10)** — `EXTRA_DOWNLOAD_DIRS`, episode numbering fixes, "Fix Episode Numbers"
with preview/confirm.

**v1.7.x (Apr 10 – May 2)** — the big feature era. Playlists, season posters, per-episode
monitored flags, Sonarr-style status icons, collapsible season groups, quality cutoff and upgrade
detection, per-episode file management, min-duration filter, subtitles, 4K, in-app debug log
viewer, Plex setup guidance, file move, notifications (Telegram/Pushover/Discord), inline help
links, per-channel scan scheduling with jitter and rate limits. Also three audit passes fixing
CRITICAL/HIGH findings.

**v1.8.x (May 2–14)** — Quick Download replaced standalone downloads. Rumble metadata fixes,
per-channel minimum quality, per-channel title filters (keyword + regex), progress-based stall
detection replacing the hard 15-minute download timeout.

**v1.9.x (May 25 – Aug 5)** — stabilization and hardening. curl_cffi impersonation fixes, NFO
regeneration + daily maintenance, Sonarr-style episode management overhaul, SponsorBlock, bulk
rename, Rumble channel-page scraping, Deno for yt-dlp's n-signature challenge, cookie CSRF
hardening, Docker image slimming, per-attempt temp dirs, storage-walk caching, Gitea removal.

---

## 5. Recurring bug classes (read this before debugging)

These are the themes that have produced the most user-visible breakage. New bugs usually rhyme
with one of them.

### 5.1 Path/template resolution divergence — the most damaging class

**The pattern:** more than one code path computes where a video file belongs, and they disagree.
Whichever path runs last moves the file, so files migrate on their own and users see churn.

**The definitive case (#32, fixed in v1.9.40):** the global "Default Naming Template" setting was
resolved in exactly one place — `download_service`. Every other path that built or moved a file
read `channel.naming_template` directly. For anyone who set only the *global* template, that field
is empty, so `build_output_path` fell back to `DEFAULT_TEMPLATE`
(`{channel_name}/Season {season}/S{season}E{episode} - ...`).

The damage came from `_rename_existing_files` in `channel_service.py`, which runs **at the end of
every scan**. It computed an "expected path" with the wrong template, saw a mismatch, and moved
correctly-named downloads into a `Season YYYY` folder — roughly every 10 minutes. Renumbering had
the same bug, so the obvious remedy made it worse. Removing and re-adding channels never helped,
because the per-channel field stayed empty.

**The fix:** template resolution now lives in one shared helper,
`naming_service.resolve_naming_template(db, channel_template)` — per-channel override, then the
global setting, then the built-in default. Every path uses it. **If you add a new code path that
builds or moves a file, it must call this helper.** That is the single most important invariant in
this codebase.

Three related defects fixed in the same pass:
- `renumber._regenerate_nfo` wrote NFOs for videos with no file on disk; with no file to sit
  beside, the NFO path fell back to the default template and recreated `Season YYYY` folders even
  for flat layouts. Now skipped (matching what `nfo_maintenance` already did).
- Renumber, single rename, and bulk rename all appended a hardcoded `.mp4`, so imported
  `.mkv`/`.webm` files were renamed to `.mp4` without transcoding and became mislabeled. The real
  extension is now preserved. (`quick_download.py` and `download_service.py` still append `.mp4`
  legitimately — those are genuinely new mp4 downloads.)
- An invalid naming template could raise mid-scan and mark healthy channels unhealthy. Templates
  are now validated on save, and an invalid stored template degrades to the default instead of
  raising. `validate_template` also does a trial `str.format()`, because the field scan only sees
  balanced `{...}` pairs and a stray brace like `{title` used to slip through and fail later.

Earlier related issue: **#18** (v1.7.39) was an earlier, narrower version of the same global-template
bug. **#32** also had a first, incomplete fix in v1.9.37 (the season *poster* was creating empty
`Season` folders regardless of template) — that was real but was not the cause the reporter was
describing.

### 5.2 Rumble — extractor unreliability and anti-bot

Rumble has consumed more effort than every other platform combined (#19, #23, #34).

- yt-dlp's Rumble channel extractor is unreliable: depending on page layout it returns 0 videos, or
  a partial list with no IDs that the scan then drops. **ChannelHoarder now scrapes Rumble channel
  pages directly as the primary path** (`ytdlp_service._scrape_rumble_channel`), falling back to
  yt-dlp only if that fails.
- Rumble rejects Python's default TLS fingerprint with HTTP 403. Fixed with `curl_cffi` browser
  impersonation. Note: **curl_cffi 0.15.0 is incompatible with yt-dlp** (it supports 0.10–0.14);
  the module imports fine, which made the version check pass while yt-dlp silently refused to use
  it. Pin accordingly.
- The scraper parses the embedded JSON `relative_url` field. **#34** was caused by matching slugs
  with `[a-z0-9-]+`: Rumble derives slugs from titles, so any title containing a period yields a
  dotted slug (`/v7d2q7k-i-was-not-prepared-for-this...html`) that never matched. On a real channel
  this dropped ~80% of videos (as few as 1 of 25 per page). Now matched as "anything up to `.html`";
  the `/v<id>-` prefix still excludes non-video links like `/c/<channel>`.
- Pagination was capped at 50 pages (~1250 videos); large channels run past 100. Backstop is now 200.
- curl_cffi could double-free and crash the process on network failure during scans; there is now a
  cooldown guard (`_curlcffi_cooling_down`) that skips curl_cffi paths after a network error.
- **Cloudflare interactive challenges are not solvable in-app.** If a user's IP is challenged in a
  browser, impersonation cannot help. The supported workaround is exporting `cf_clearance` cookies
  from a browser on the same public IP; the scraper deliberately replays the exact User-Agent
  captured at export time, because `cf_clearance` is bound to IP *and* browser version.
  `tools/cookie_exporter.py` ships `.rumble.com` in its defaults for this.

**#19 ended as environmental**, not a code bug: the reporter's IP was Cloudflare-challenged and they
moved to a separate instance on an unrestricted IP.

### 5.3 YouTube authentication (PO tokens, cookies, JS runtime)

- The container runs a **bgutil PO token HTTP server**; the yt-dlp plugin reaches it via `base_url`.
- **Deno is required** — yt-dlp's challenge solver needs it to solve YouTube's n-signature
  challenge. Node is no longer an accepted runtime; without Deno, cookie sessions fail with "No
  video formats found."
- A leftover `~/bgutil-ytdlp-pot-provider` symlink also activated the redundant bgutil *script*
  provider, which spawned Deno and spammed a jsdom `--allow-read` `NotCapable` error on every
  download (#33). The symlink is now deliberately removed in `entrypoint.sh` — **do not re-add it.**
- yt-dlp is shipped current via image builds (a `CACHEBUST` arg forces a fresh `pip install
  --upgrade yt-dlp` layer on every CI run, including scheduled rebuilds). Runtime auto-update was
  removed on purpose — stale yt-dlp used to cause format errors, and in-place updating was worse.
- Do not force a single `player_client`. YouTube runs per-client experiments (DRM on tv, SABR on
  web/mweb) that break any hardcoded choice; let yt-dlp pick.

### 5.4 Docker path mapping and the allowed-download-roots allowlist

Users repeatedly hit "Path X is not under any allowed download directory" (#6, and again on the
Unraid forum). Mapping a Docker volume is **not** sufficient — the container path must also appear
in `EXTRA_DOWNLOAD_DIRS`, which backs `settings.allowed_download_roots`.

Fixed in v1.9.39: the Unraid template now exposes an "Extra Download Dirs" variable (defaulting to
`/media`, matching the template's "Extra Media Path" volume), and the error message now explains
the fix instead of dead-ending. **Guard worth preserving:** extra roots are only honored if the
directory actually exists, so an allowlisted-but-unmapped path cannot silently accept downloads
into the container's ephemeral layer where they vanish on recreation.

### 5.5 Session and concurrency hazards

Recurring themes: sessions held across long downloads, detached instances, and races.
`download_service` deliberately snapshots channel/video data into plain dataclasses and releases
the DB session before downloading (Phase 1 / Phase 2 / Phase 3 structure). Downloads run in
per-attempt isolated temp directories because a stalled yt-dlp thread cannot be killed
(`asyncio.to_thread` cancellation does not stop the OS thread) and orphans may keep writing.

---

## 6. Issue history

All issues #1–#35 are closed. Reporters are an engaged, repeat group: **Lyky35**, **MarioMan632**,
**Patriot2407**, **Denimbeard**, **alicecantsleep**, **Shad0wWulf**, **Lajci**, **blitzmikyu**,
**Nevarro**, **xhermanson**. Several have tested builds across many iterations — Patriot2407 in
particular stayed through a 22-comment Rumble thread.

Closed, grouped by theme:

- **Install/config:** #1 fresh-install DB creation, #2 settings not changeable on Unraid,
  #6 alternate download location + 360p cap, #9 debug log export
- **Playlists:** #4 playlist support, #10 playlist collections, #28 private/deleted playlist titles
- **Cookies/auth:** #5 cookie de-auth loop, #8 downloads without cookies, #11 per-agent cookies,
  #17 cookie invalid date, #24 fresh cookies failing, #29 challenge solving failed
- **Platform-specific:** #16 Twitch VOD `v` prefix, #23 Odysee downloads, #19 Rumble (long-running),
  #34 Rumble partial video lists
- **Naming/metadata:** #18 filename template ignored, #26 metadata lost after renumber,
  #32 Season folders on flat templates
- **Detection/filters:** #15 quality/length thresholds, #21 keyword/tag filtering, #25 shorts
  detection, #31 requested format unavailable
- **Scale/UI:** #12 and #13 the 50-channel display limit, #14 force-refresh duplicates,
  #27 mass update/rename
- **Features delivered:** #3 subtitles, #7 chapters + Discord, #30 SponsorBlock
- **Timeouts:** #20 PO token failure on long downloads, #22 15-minute hard timeout
- **Cosmetic:** #33 PO provider log spam
- **Docs/UX:** #35 per-channel livestream toggle — turned out to be a real bug: the toggle was
  gated on `channel.platform === "youtube"` while the backend gated livestreams for *all*
  platforms, so non-YouTube channels were permanently opted out with no way to change it.

### Currently open

**#36 — Transcoding options** (Nevarro, 2026-08-07, no reply yet)
Apple TV cannot play AV1; asking for transcoding. Substantial feature — would mean invoking ffmpeg
re-encode as a post-processing step, with format/codec settings and significant CPU cost. Worth a
considered answer either way; a cheaper partial answer may be format-selection preferences that
avoid AV1 at download time rather than transcoding after the fact.

**#37 — Sort options** (xhermanson, 2026-08-16, no reply yet)
Wants channel-view sorting by percentage complete, or incomplete channels floated to the top. Also
notes the "health" indicator is unclear. Small, well-scoped, and the health-label confusion is
worth addressing separately.

**#38 — Unable to scan for new videos and unable to import existing** (Denimbeard, 2026-08-16, no
reply yet) — **the priority.** Debug log attached and analyzed:

- Reporter is on **v1.9.28**, twelve versions behind current. Not caused by recent work; some of
  it may already be fixed. Confirm their version before deep investigation.
- Two distinct failures in the log:
  1. `Scan failed for channel Board AF: Instance <Video at 0x...> is not present in this Session`
     — a SQLAlchemy detached-instance error during scan. This is the §5.5 class.
  2. `Failed to fetch RSS feed for channel PLeImKFecYFCwHG65VNoyy_Y5KI3l2s5FJ: 404 Not Found` —
     the RSS fast-path is being handed a **playlist** ID where a channel ID is expected. Playlists
     have no channel RSS feed, so this 404s. Their reported playlist is
     `https://www.youtube.com/playlist?list=PLeImKFecYFCwHG65VNoyy_Y5KI3l2s5FJ`.
- Environment otherwise healthy: PO tokens healthy, cookies present, API key configured,
  26 channels, 852 downloads, 246 GB free.

### Unraid forum requests (not filed as GitHub issues)

From the support thread; users were told these are noted:
- **"No-Plex mode"** — a toggle to skip `.nfo` files and posters entirely for users who only want
  video files in a flat layout. Partially addressed (v1.9.37 stopped forcing Season folders and
  posters on flat templates), but `.nfo` generation is still unconditional.
- **"Monitor from now"** — add a channel but only monitor videos going forward instead of the whole
  back catalog.
- **Multiple profiles / per-user cookies** — deferred as a large architectural change; running
  separate instances is the current answer. Per-channel cookie assignment would be a lighter option.
- Quick Download files land in `/tmp/quick-downloads` *inside* the container (7-day auto-clean, not
  a mapped volume), which surprises users looking for them on the host.

---

## 7. Known tech debt and deferred items

- **No releases or tags.** The release checklist calls for `gh release create vX.Y.Z`, but none
  exist. Users cannot pin a version or read release notes; everything ships as `:latest`.
- **No test suite.** `pytest` with `asyncio_mode="auto"` is the intended setup and does not exist yet.
- **Unused import** at `backend/app/routers/channels.py` in `detect_clean_shorts_confirm` —
  `build_output_path` is imported but not used. Pre-existing, harmless, left in place.
- **Dead fallback** in `metadata_service.write_episode_nfo`: the branch that constructs a path when
  `video_file_path` is None ignores the naming template and base dir. All four callers currently
  pass a path, so it is unreachable — but it is a landmine if a future caller does not.
- **Debug export is low-signal.** Exports are dominated by apscheduler and aiosqlite DEBUG lines;
  a two-minute window can be 500 lines with almost no application events. This has twice made user
  logs nearly useless for diagnosis. Filtering noisy loggers out of the export would pay for itself.
- `Channel.total_videos` / `downloaded_count` remain stale cached values, worked around at the
  router layer rather than fixed at the source.

---

## 8. Working conventions

**Before every commit:**
```bash
ruff check backend/                 # backend lint
cd frontend && npx tsc --noEmit     # frontend typecheck
docker build -f docker/Dockerfile . # only if Docker files changed
```
Bump the version in all three files, update CHANGELOG.md, and test fresh-install behavior when
touching database or startup logic.

**Versioning:** local commits may use a 4th decimal (`1.2.3.1`); pushes bump the 3rd
(`1.2.3` → `1.2.4`) and drop the 4th.

**Git:** local commits are routine; **pushing requires explicit approval**, as does force-pushing
or deleting remote branches. `main` is the only long-lived branch.

**Issues:** never close without the reporter confirming — post the fix, let them verify, close after
a few days of silence. Never use `Fixes #N` / `Closes #N` in commit messages (it auto-closes on
push); use `(#N)` or `Re #N`. Label and assign new issues on review.

**CI:** GitHub Actions builds and pushes to ghcr.io on every push to `main`, plus a schedule that
keeps yt-dlp current. Gitea mirroring was removed entirely in `a99061d`; GitHub is the only remote.

**Unraid template:** if Docker config changes (Dockerfile, entrypoint, env vars, volumes), update
`docker/unraid-template.xml`. Icons must be full public URLs. Never copy repo XML templates into
Unraid's `templates-user/` directory — it overrides users' saved container settings on every edit.

---

## 9. Suggested next steps

1. **Reply to #38** — the only open bug, unanswered, with a usable log. Confirm whether the
   reporter still reproduces on 1.9.40 before chasing the v1.9.28 traces. The playlist-ID-to-RSS
   404 looks like a genuine logic bug worth fixing regardless of version.
2. **Reply to #36 and #37** — both unanswered feature requests. #37 is small enough to just build.
3. **Consider cutting a release for 1.9.40.** Several substantial fixes have shipped with no
   release notes, and the naming-template fix in particular affected anyone using a global template.
4. **Reduce debug-export noise** — cheap, and it makes every future bug report more useful.
