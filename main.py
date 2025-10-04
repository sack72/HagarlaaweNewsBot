async def fetch_and_post_headlines(bot: Bot):
    last_link = load_last_posted_link()
    all_new_entries = []

    for url in RSS_URLS:
        logging.info("Fetching %s", url)
        feed = feedparser.parse(url)

        new_entries = []
        for entry in feed.entries:
            if hasattr(entry, "link") and entry.link == last_link:
                break
            new_entries.append(entry)
        new_entries.reverse()
        all_new_entries.extend(new_entries)

    all_new_entries.sort(key=lambda e: e.get("published_parsed") or time.gmtime())

    if not all_new_entries:
        logging.info("No new headlines.")
        return

    for entry in all_new_entries:
        title_raw = entry.title
        link = entry.link if hasattr(entry, "link") else None

        # Clean title from emojis or prefixes
        title = re.sub(r'[\U0001F1E6-\U0001F1FF]{2}:?\s*', '', title_raw, flags=re.UNICODE).strip()
        title = re.sub(r'^[^:]+:\s*', '', title).strip()

        somali_text = await translate_to_somali(title)

        # Build final message without country filtering
        message_to_send = f"{title}\n\n🇸🇴 {somali_text}\n🔗 {link}"

        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_to_send,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logging.error("Telegram send failed: %s", e)

        if link:
            save_last_posted_link(link)

        await asyncio.sleep(1)

