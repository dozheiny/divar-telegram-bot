# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-file Python script (`main.py`, ~870 lines, stdlib + `requests` only) that polls Divar
(Iranian classifieds) for new real-estate listings matching a saved search and posts them with
photo galleries to one or more Telegram chats/channels. There is no test suite, no linter config,
no `requirements.txt`, and no package layout — `requests` is installed directly in the Dockerfile.

## Running

```bash
docker compose up -d --build         # normal deployment (cron inside the container, every minute)
docker compose logs -f               # cron writes to PID 1's stdout/stderr, so logs land here

python3 main.py                      # one-shot local run; needs env vars exported (see .env.sample)
set -x BOT_TOKEN ... ; python3 main.py   # fish shell — `export` is not available
```

`SEARCH_CONDITIONS` and `BOT_TOKEN` are read with `os.environ[...]` and crash the process if
missing; everything else has a default. Copy `.env.sample` to `.env` for compose.

Manual testing tips:
- `SKIP_EXISTING_ON_FIRST_RUN=true` with an empty token file seeds state without spamming a chat.
- To replay everything: write `[]` into `data/tokens.json` (compose) and rerun.
- Point `BOT_CHATID` at a throwaway private chat when exercising the send path.

## Architecture

`main()` is one cron tick, and the whole design assumes it may be invoked every minute:

1. **Run lock** — `acquire_run_lock()` takes a non-blocking `fcntl.flock` on `data/bot.lock`.
   If another tick is still running (the first full dump can take many minutes), this tick exits
   immediately. Any long-running change must keep this property.
2. **State** — `data/tokens.json` is a flat JSON list of already-sent Divar tokens, trimmed to the
   newest `MAX_TOKENS` (5000). `tokens_path()` prefers `./data/tokens.json` when that directory
   exists (the compose volume) and falls back to `./tokens.json` next to the script. An empty list
   means "first run": post everything unless `SKIP_EXISTING_ON_FIRST_RUN`. `save_tokens()` tries
   atomic `os.replace` and falls back to in-place write because Podman/Docker *file* bind-mounts
   reject replace with EBUSY — hence the compose volume mounts the **directory**.
   `process_data()` appends and saves a token *before* sending, so a crash mid-dump never restarts
   the dump from scratch (at the cost of possibly losing one listing).
3. **Discovery (two-stage, undocumented Divar internals)** — `fetch_search_context()` GETs
   `https://divar.ir/s/<SEARCH_CONDITIONS>` as a browser and scrapes
   `window.__PRELOADED_STATE__` to recover the city ids and the filter `formData` that the URL
   encodes. Those are then POSTed to `https://api.divar.ir/v8/postlist/w/search` (two pages max).
   If the API call fails, it falls back to the server-rendered `nb.listWidgets` posts from the same
   page state. Listings are deduped by token and reversed so Telegram receives oldest-first.
4. **Enrichment** — `fetch_post_details(token)` GETs `https://api.divar.ir/v8/posts-v2/web/<token>`
   and walks `sections[].widgets[]`, keyed on `section_name` + `widget_type` + `data["@type"]`, to
   pull images, description, publish/update times, area/year/rooms, floor, and the "photos are
   decorative" warning. Floor comes from the `UNEXPANDABLE_ROW` whose title is **exactly** `طبقه`
   (`"۳"` or `"۳ از ۴"`) — the match must stay exact, because `تعداد کل طبقات ساختمان` and
   `تعداد واحد در طبقه` also contain that word. Amenities come from the `SELECTOR_ROW` titled
   `سایر ویژگی‌ها و امکانات`: its `action.payload.modal_page.widget_list` is already embedded in the
   same response (no extra request), and `parse_features_modal()` splits it into `(specs, amenities)`
   — `UNEXPANDABLE_ROW` key/values plus `DESCRIPTION_ROW` labels paired with the chips in the
   following `WRAPPER_ROW`, and `FEATURE_ROW` titles. Divar already words negatives in the title
   (`آسانسور` vs `آسانسور ندارد`), so titles are passed through verbatim rather than re-derived into
   booleans. When a listing has no modal, the inline `GROUP_FEATURE_ROW` titles are the fallback.
5. **Sending (three-tier fallback)** — per chat id: download every image and upload as
   `sendMediaGroup`/`sendPhoto` (the normal path, since Telegram usually cannot fetch Divar's CDN);
   then a single remote-URL `sendPhoto`; then text-only `sendMessage`. The module-level
   `_remote_photos_work` latch stops retrying URL photos for the rest of the process once Telegram
   rejects one. `telegram_request()` centralises retries and 429 `retry_after` handling.

## Conventions and gotchas

- Captions are HTML `parse_mode`; every interpolated field goes through `html.escape`.
  `build_caption()` respects Telegram's 1024-char photo-caption limit (4096 for text) by trimming
  the description body first, then dropping the `سایر ویژگی‌ها و امکانات` block if the structured
  part still leaves no room. It must never slice the assembled caption, since that cuts the closing
  `</a>` and Telegram rejects the HTML — keep the `allowed = max_len - len(base) - 2` budget
  (newline + ellipsis) and the title/facts/features/link ordering.
- Persian strings are matched as substrings against live Divar data (`متراژ`, `آسانسور`, `تزئینی`,
  `ندارد`, …). They are load-bearing; don't "clean them up".
- Divar's API shapes are unstable and undocumented. Parsing is defensive throughout
  (`(x or {}).get(...)`, broad `except` around per-listing work) so one bad listing or schema
  change degrades instead of killing the run. New parsing code should follow suit.
- Image URLs are upgraded from thumbnails via `prefer_full_image_url()`
  (`/webp_thumbnail/` → `/webp_post/`).
- Proxies apply to **Telegram only** (`proxies=proxy_config` in `telegram_request`); Divar requests
  intentionally go direct.
- `run.sh` exists because cron does not inherit container env: it dumps `printenv` into
  `/app/cron-env.sh`, which `crontab` sources before each run. New env vars work automatically, but
  anything that changes how env reaches the script must keep this shim working.
- `EXCLUDE_TITLE` matches are recorded in `tokens.json` (marked as seen) rather than re-checked
  every tick.
- The shell here is fish; `export VAR=...` and other bash-isms will fail.
