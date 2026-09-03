# Divar Telegram Bot

Sends new Divar listings (with photos) to a Telegram chat or **channel**.

## Setup

1. Open `@BotFather` in Telegram, create a bot, copy the token.

2. Choose where posts should go:

### Personal chat
- Message `@getidsbot`, copy your numeric `id`
- Open your bot and press **Start**

### Telegram channel (recommended)
- Create a public or private channel
- Add the bot as an **administrator** with permission to **Post Messages**
- Set `BOT_CHATID` to `@yourchannel` (public) or the numeric `-100...` id  
  (forward a channel post to `@getidsbot` to see the id)

```env
BOT_TOKEN=<BOT-TOKEN-HERE>
BOT_CHATID=@yourchannel
```

You can send to more than one destination:

```env
BOT_CHATID=@yourchannel,123456789
```

3. Copy `.env.sample` to `.env` and fill it in.

4. On [divar.ir](https://divar.ir/), pick city + filters. From the browser URL, copy everything after `https://divar.ir/s/`:

```text
https://divar.ir/s/mashhad/rent-residential/janbaz?districts=1124%2C442&credit=-100000000&rent=-3000000&size=-90
```

```env
SEARCH_CONDITIONS=mashhad/rent-residential/janbaz?districts=1124%2C442&credit=-100000000&rent=-3000000&size=-90
```

5. Optional filters / tuning:

```env
EXCLUDE_TITLE=زمین,کلنگی
MAX_IMAGES=4
```

6. Run with Docker:

```bash
docker compose up -d --build
```

Or cron on the host:

```cron
*/2 * * * * cd /path/to/divar-telegram-bot && /usr/bin/python3 main.py >> /var/log/divar-bot.log 2>&1
```

## Notes

- First run only seeds `tokens.json` and does **not** flood the channel with existing ads.
- New listings are sent with photos (gallery when available) plus a Divar button/link.
- If Divar CDN blocks downloads from your host, the bot falls back to URL-based Telegram photos, then text-only.
