import datetime
import html
import json
import logging
import os
import random
import time
from io import BytesIO

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

SEARCH_CONDITIONS = os.environ["SEARCH_CONDITIONS"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
# Comma-separated: private chat id, @channelusername, or -100... channel id
CHAT_IDS = [
    c.strip()
    for c in os.environ.get("BOT_CHATID", "").split(",")
    if c.strip()
]
EXCLUDE_TITLE = [
    w.strip()
    for w in os.environ.get("EXCLUDE_TITLE", "").split(",")
    if w.strip()
]
MAX_IMAGES = max(1, min(10, int(os.environ.get("MAX_IMAGES", "4"))))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "5000"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))

DIVAR_SEARCH_PAGE = f"https://divar.ir/s/{SEARCH_CONDITIONS}"
DIVAR_POSTLIST_API = "https://api.divar.ir/v8/postlist/w/search"
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

proxy_config = {}
if os.environ.get("HTTP_PROXY"):
    proxy_config["http"] = os.environ["HTTP_PROXY"]
if os.environ.get("HTTPS_PROXY"):
    proxy_config["https"] = os.environ["HTTPS_PROXY"]

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
}
API_HEADERS = {
    "User-Agent": BROWSER_HEADERS["User-Agent"],
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://divar.ir",
    "Referer": DIVAR_SEARCH_PAGE,
    "X-Standard-Divar-Error": "true",
}

session = requests.Session()
session.headers.update(BROWSER_HEADERS)


def tokens_path():
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "tokens.json")


def load_tokens():
    path = tokens_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return []
            data = json.loads(content)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not load tokens.json: %s", exc)
        return []


def save_tokens(tokens):
    if len(tokens) > MAX_TOKENS:
        tokens = tokens[-MAX_TOKENS:]
    with open(tokens_path(), "w", encoding="utf-8") as outfile:
        json.dump(tokens, outfile, ensure_ascii=False)


def parse_preloaded_state(html_text):
    for marker in ("window.__PRELOADED_STATE__ = ", "window.__PRELOADED_STATE__="):
        start = html_text.find(marker)
        if start >= 0:
            payload_start = start + len(marker)
            state, _ = json.JSONDecoder().raw_decode(html_text[payload_start:])
            return state
    raise ValueError("Divar preloaded state not found")


def fetch_search_context():
    """
    Load the Divar search page once to recover city ids + filter form_data
    that match SEARCH_CONDITIONS (works even when SSR post list is empty).
    """
    response = session.get(DIVAR_SEARCH_PAGE, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    state = parse_preloaded_state(response.text)

    cities = ((state.get("multiCity") or {}).get("selectedCities")) or []
    city_ids = [str(city["id"]) for city in cities if city.get("id") is not None]
    if not city_ids:
        raise ValueError("No city selected in Divar search page state")

    search_data = (state.get("search") or {}).get("searchData") or {}
    form_data = search_data.get("formData")
    if not form_data:
        raise ValueError("No search formData in Divar page state")

    ssr_posts = extract_posts_from_widgets(
        ((state.get("nb") or {}).get("listWidgets")) or [],
        nested=True,
    )

    return {
        "city_ids": city_ids,
        "form_data": form_data,
        "server_payload": search_data.get("serverPayload"),
        "ssr_posts": ssr_posts,
    }


def extract_posts_from_widgets(widgets, nested=False):
    posts = []
    for widget in widgets:
        if nested:
            data = (((widget.get("data") or {}).get("dto") or {}).get("data"))
        else:
            data = widget.get("data")
            if widget.get("widget_type") and widget.get("widget_type") != "POST_ROW":
                continue
        if isinstance(data, dict) and data.get("token") and "title" in data:
            posts.append(data)
    return posts


def postlist_search(context, pagination_data=None):
    body = {
        "city_ids": context["city_ids"],
        "search_data": {
            "form_data": {"data": context["form_data"]},
        },
    }
    if context.get("server_payload"):
        body["search_data"]["server_payload"] = context["server_payload"]
    if pagination_data:
        body["pagination_data"] = pagination_data

    response = session.post(
        DIVAR_POSTLIST_API,
        headers=API_HEADERS,
        json=body,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def get_houses_pages():
    context = fetch_search_context()
    houses = []

    # Prefer live postlist API (reliable); fall back to SSR widgets if present
    try:
        first = postlist_search(context)
        page_posts = extract_posts_from_widgets(first.get("list_widgets") or [])
        houses.extend(page_posts)
        logging.info("Postlist page 1: %s listings", len(page_posts))

        pagination = first.get("pagination") or {}
        if pagination.get("has_next_page") and pagination.get("data"):
            second = postlist_search(context, pagination_data=pagination["data"])
            page_posts = extract_posts_from_widgets(second.get("list_widgets") or [])
            houses.extend(page_posts)
            logging.info("Postlist page 2: %s listings", len(page_posts))
    except requests.RequestException as exc:
        logging.warning("Postlist API failed (%s); using SSR widgets if any", exc)
        houses = list(context.get("ssr_posts") or [])

    if not houses and context.get("ssr_posts"):
        houses = list(context["ssr_posts"])

    # Dedupe by token, keep API order, then oldest-first for Telegram
    seen = set()
    unique = []
    for house in houses:
        token = house.get("token")
        if token and token not in seen:
            seen.add(token)
            unique.append(house)
    return unique[::-1]


def prefer_full_image_url(url):
    if not url:
        return url
    return url.replace("/webp_thumbnail/", "/webp_post/")


def _feature_status(items, *, title_keys, icon_names):
    """Return True/False/None from Divar GroupFeatureRow items."""
    for item in items:
        title = item.get("title") or ""
        icon_name = ((item.get("icon") or {}).get("icon_name") or "").upper()
        matched = any(key in title for key in title_keys) or icon_name in icon_names
        if not matched:
            continue
        if item.get("available") is True:
            return True
        if "ندارد" in title:
            return False
        return True
    return None


def _bool_label(value):
    if value is True:
        return "✅"
    if value is False:
        return "❌"
    return "—"


def fetch_post_details(token):
    """Images, full description, and amenities from the post detail API."""
    details = {
        "images": [],
        "body": "",
        "has_parking": None,
        "has_elevator": None,
        "has_storage": None,
    }
    try:
        response = session.get(
            f"https://api.divar.ir/v8/posts-v2/web/{token}",
            headers=API_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logging.warning("Detail fetch failed for %s: %s", token, exc)
        return details

    feature_items = []
    for section in payload.get("sections") or []:
        name = section.get("section_name")
        for widget in section.get("widgets") or []:
            data = widget.get("data") or {}
            widget_type = widget.get("widget_type")

            if name == "DESCRIPTION" and widget_type == "DESCRIPTION_ROW":
                text = (data.get("text") or "").strip()
                if text:
                    details["body"] = text

            if name == "IMAGE":
                for item in data.get("items") or []:
                    image = item.get("image") or {}
                    url = image.get("url")
                    if not url:
                        continue
                    if item.get("video_url") and details["images"]:
                        continue
                    details["images"].append(url)

            if name == "LIST_DATA" and "GroupFeatureRow" in (data.get("@type") or ""):
                feature_items.extend(data.get("items") or [])

    details["images"] = details["images"][:MAX_IMAGES]
    details["has_elevator"] = _feature_status(
        feature_items, title_keys=("آسانسور",), icon_names={"ELEVATOR"}
    )
    details["has_parking"] = _feature_status(
        feature_items, title_keys=("پارکینگ",), icon_names={"PARKING"}
    )
    details["has_storage"] = _feature_status(
        feature_items,
        title_keys=("انبار",),
        icon_names={"CABINET", "WAREHOUSE", "STORAGE"},
    )
    return details


def extract_house_data(house):
    web_info = (
        ((house.get("action") or {}).get("payload") or {}).get("web_info") or {}
    )
    image_url = prefer_full_image_url(house.get("image_url") or "")
    token = house["token"]
    return {
        "title": house.get("title") or "",
        "price_info": "\n".join(
            part
            for part in (
                house.get("top_description_text") or "",
                house.get("middle_description_text") or "",
            )
            if part
        ),
        "body": "",
        "district": web_info.get("district_persian")
        or house.get("bottom_description_text")
        or "",
        "has_parking": None,
        "has_elevator": None,
        "has_storage": None,
        "has_image": int(house.get("image_count") or 0) > 0 or bool(image_url),
        "image_url": image_url or None,
        "token": token,
        "url": f"https://divar.ir/v/{token}",
    }


def enrich_house_details(house):
    details = fetch_post_details(house["token"])
    house["body"] = details.get("body") or house.get("body") or ""
    house["has_parking"] = details.get("has_parking")
    house["has_elevator"] = details.get("has_elevator")
    house["has_storage"] = details.get("has_storage")
    house["_detail_images"] = details.get("images") or []
    return house


def build_caption(house, max_len=1024):
    title = html.escape(house["title"])
    district = html.escape(house.get("district") or "")
    price_info = html.escape(house.get("price_info") or "")
    body = html.escape(house.get("body") or "")
    link = html.escape(house["url"])

    amenities = (
        f"پارکینگ: {_bool_label(house.get('has_parking'))}\n"
        f"آسانسور: {_bool_label(house.get('has_elevator'))}\n"
        f"انباری: {_bool_label(house.get('has_storage'))}"
    )

    def assemble(body_text):
        parts = [f"<b>{title}</b>"]
        if district:
            parts.append(f"<i>{district}</i>")
        if price_info:
            parts.append(price_info)
        parts.append(amenities)
        if body_text:
            parts.append(body_text)
        parts.append(f'<a href="{link}">مشاهده در دیوار</a>')
        return "\n".join(parts)

    caption = assemble(body)
    if len(caption) <= max_len:
        return caption

    # Keep title/price/amenities/link; trim the long description body.
    base_without_body = assemble("")
    allowed = max_len - len(base_without_body) - 1
    if allowed < 32:
        return base_without_body[:max_len]

    trimmed = body[:allowed].rstrip() + "…"
    return assemble(trimmed)[:max_len]

def inline_keyboard(house):
    return {
        "inline_keyboard": [
            [{"text": "مشاهده آگهی در دیوار", "url": house["url"]}]
        ]
    }


def download_image(url):
    try:
        response = session.get(
            url,
            headers={
                "User-Agent": BROWSER_HEADERS["User-Agent"],
                "Referer": "https://divar.ir/",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type and not url.lower().endswith(
            (".webp", ".jpg", ".jpeg", ".png")
        ):
            return None, None
        name = "photo.webp" if "webp" in (content_type + url).lower() else "photo.jpg"
        return response.content, name
    except requests.RequestException as exc:
        logging.warning("Image download failed (%s): %s", url[:80], exc)
        return None, None


def telegram_request(method, *, data=None, files=None, retries=3):
    url = f"{TG_API}/{method}"
    for attempt in range(retries):
        try:
            response = session.post(
                url,
                data=data,
                files=files,
                proxies=proxy_config or None,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logging.warning("Telegram %s network error: %s", method, exc)
            time.sleep(2 + attempt)
            continue

        if response.status_code == 429:
            retry_after = random.randint(3, 7)
            try:
                retry_after = int(
                    response.json().get("parameters", {}).get("retry_after", retry_after)
                )
            except Exception:
                pass
            logging.info("Rate limited; sleeping %ss", retry_after)
            time.sleep(retry_after)
            continue

        if not response.ok:
            logging.warning(
                "Telegram %s failed: %s %s",
                method,
                response.status_code,
                response.text[:300],
            )
            return None
        return response
    return None


def send_text(chat_id, house):
    return telegram_request(
        "sendMessage",
        data={
            "chat_id": chat_id,
            "parse_mode": "HTML",
            "text": build_caption(house, max_len=4096),
            "reply_markup": json.dumps(inline_keyboard(house)),
        },
    )


def send_single_photo(chat_id, house, image_url):
    caption = build_caption(house)
    reply_markup = json.dumps(inline_keyboard(house))
    content, filename = download_image(image_url)

    if content:
        result = telegram_request(
            "sendPhoto",
            data={
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": reply_markup,
            },
            files={"photo": (filename, BytesIO(content))},
        )
        if result is not None:
            return result

    return telegram_request(
        "sendPhoto",
        data={
            "chat_id": chat_id,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        },
    )


def send_media_group(chat_id, house, image_urls):
    caption = build_caption(house)
    media = []
    files = {}

    for index, image_url in enumerate(image_urls):
        content, filename = download_image(image_url)
        item = {"type": "photo", "parse_mode": "HTML"}
        if index == 0:
            item["caption"] = caption
        if content:
            field = f"photo{index}"
            files[field] = (filename or f"photo{index}.jpg", content)
            item["media"] = f"attach://{field}"
        else:
            item["media"] = image_url
        media.append(item)

    prepared = {key: (name, BytesIO(blob)) for key, (name, blob) in files.items()}
    return telegram_request(
        "sendMediaGroup",
        data={"chat_id": chat_id, "media": json.dumps(media)},
        files=prepared or None,
    )


def collect_image_urls(house):
    urls = list(house.get("_detail_images") or [])
    if not urls and house.get("image_url"):
        urls = [house["image_url"]]
    seen = set()
    unique = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            unique.append(url)
    return unique[:MAX_IMAGES]


def send_telegram_message(house):
    if not CHAT_IDS:
        logging.error("BOT_CHATID is empty")
        return

    enrich_house_details(house)
    image_urls = collect_image_urls(house)
    for chat_id in CHAT_IDS:
        ok = None
        if len(image_urls) > 1:
            ok = send_media_group(chat_id, house, image_urls)
        elif len(image_urls) == 1:
            ok = send_single_photo(chat_id, house, image_urls[0])
        if ok is None:
            send_text(chat_id, house)


def process_data(houses, tokens, *, seed_only=False):
    for house in houses:
        try:
            house_data = extract_house_data(house)
        except Exception as exc:
            logging.warning("Skip malformed listing: %s", exc)
            continue

        token = house_data["token"]
        if token in tokens:
            continue
        if any(word in house_data["title"] for word in EXCLUDE_TITLE):
            tokens.append(token)
            continue

        tokens.append(token)
        if seed_only:
            continue

        try:
            send_telegram_message(house_data)
            time.sleep(1)
        except Exception as exc:
            logging.exception("Failed to send listing %s: %s", token, exc)
    return tokens


def main():
    if not CHAT_IDS:
        raise SystemExit(
            "BOT_CHATID is required (private id, @channelusername, or -100...)"
        )

    logging.info("Run started at %s", datetime.datetime.now().isoformat())
    tokens = load_tokens()
    first_run = len(tokens) == 0
    seed_only = first_run and SKIP_EXISTING_ON_FIRST_RUN
    if seed_only:
        logging.info(
            "Empty tokens.json — seeding without sending "
            "(SKIP_EXISTING_ON_FIRST_RUN=true)"
        )
    elif first_run:
        logging.info(
            "Empty tokens.json — posting all current listings, "
            "then only new ads afterwards"
        )

    try:
        houses = get_houses_pages()
        logging.info("Fetched %s unique listings", len(houses))
        tokens = process_data(houses, tokens, seed_only=seed_only)
    except Exception:
        logging.exception("Failed to fetch Divar listings")

    save_tokens(tokens)
    logging.info("Done. Known tokens: %s", len(tokens))


if __name__ == "__main__":
    main()
