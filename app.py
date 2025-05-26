import asyncio
import re
import logging
from datetime import datetime
import aiohttp
import feedparser
from telegram import Bot
import os
from dataclasses import dataclass, field
from typing import List, Optional
from aiohttp import web

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class EventInfo:
    """Class to store event information"""
    title: str
    description: str  # This will store the raw HTML description
    link: str
    published: str
    source_channel: str  # Display name of the source channel
    source_channel_username: Optional[str] = None # Username for the link


class EventDetector:
    """Class to detect events in RSS feed content"""

    EVENT_KEYWORDS = [
        'وبینار', 'webinar', 'کارگاه', 'workshop', 'سمینار', 'seminar',
        'کنفرانس', 'conference', 'همایش', 'congress', 'نشست', 'meeting',
        'دوره آموزشی', 'course', 'کلاس', 'class', 'ایونت', 'event',
        'برگزار', 'organize', 'شرکت', 'participate', 'ثبت نام', 'register',
        'رایگان', 'free', 'آنلاین', 'online', 'مجازی', 'virtual',
        'آموزش', 'training', 'فراخوان', 'call', 'گواهی', 'certificate',
        'مدرک', 'certification', 'لایو', 'live'
    ]

    def detect_event(self, title: str, description_html: str) -> bool:
        """Detect if content contains event information based on title and HTML description."""
        text_only_description = re.sub(r'<[^>]+>', '', description_html).strip()
        full_text = f"{title} {text_only_description}"
        text_lower = full_text.lower()

        matches = sum(1 for keyword in self.EVENT_KEYWORDS if keyword in text_lower)

        has_specific_pattern = any([
            'ثبت نام' in text_lower,
            'شرکت در' in text_lower,
            'برگزار می' in text_lower,
            'register' in text_lower,
            'join' in text_lower,
            'وبینار' in text_lower,
            'کارگاه' in text_lower,
            'دوره آنلاین' in text_lower
        ])
        return matches >= 2 or has_specific_pattern


class RSSTelegramBot:
    """RSS-based Telegram Event Bot"""

    def __init__(self, bot_token: str, target_channel: str):
        self.bot_token = bot_token
        self.target_channel = target_channel
        self.bot = Bot(token=bot_token)
        self.detector = EventDetector()
        self.processed_items = set()  # In-memory set

        self.rss_feeds = [
            {
                'name': 'WinCell Co',
                'url': 'https://rsshub.app/telegram/channel/wincellco',
                'channel': 'wincellco'
            },
            {
                'name': 'Rayazistazma',
                'url': 'https://rsshub.app/telegram/channel/Rayazistazma',
                'channel': 'Rayazistazma'
            },
            {
                'name': 'SBU Bio Society',
                'url': 'https://rsshub.app/telegram/channel/SBUBIOSOCIETY',
                'channel': 'SBUBIOSOCIETY'
            },
            {
                'name': 'Test BioPy Channel',
                'url': 'https://rsshub.app/telegram/channel/testbiopy',
                'channel': 'testbiopy'
            }
        ]

    async def fetch_feed(self, session: aiohttp.ClientSession, feed_info: dict) -> List[EventInfo]:
        events = []
        feed_url = feed_info['url']
        feed_name = feed_info['name']
        logger.info(f"Fetching feed: {feed_name} from {feed_url}")

        try:
            async with session.get(feed_url, timeout=30) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = feedparser.parse(content)
                    logger.info(f"Successfully fetched {feed_name}. Entries: {len(feed.entries)}")

                    for entry in feed.entries[:10]: # یا feed.entries[:5] اگر می‌خواهید ۵ تای آخر باشد
                        entry_id = f"{feed_info.get('channel', feed_name)}_{entry.get('id', entry.get('link', ''))}"

                        if entry_id not in self.processed_items:
                            raw_title = entry.get('title', '').strip()
                            raw_description_html = entry.get('description', entry.get('summary', ''))

                            if self.detector.detect_event(raw_title, raw_description_html):
                                event = EventInfo(
                                    title=raw_title,
                                    description=raw_description_html,
                                    link=entry.get('link', ''),
                                    published=entry.get('published', ''),
                                    source_channel=feed_name,
                                    source_channel_username=feed_info.get('channel')
                                )
                                events.append(event)
                                self.processed_items.add(entry_id)

                        if len(self.processed_items) > 1000:
                            items_list = list(self.processed_items)
                            self.processed_items = set(items_list[200:])
                            logger.info(f"Cleaned up processed_items. New size: {len(self.processed_items)}")
                else:
                    logger.error(f"Error fetching feed {feed_name}: Status {response.status} - {await response.text()}")
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Connection error for {feed_name} ({feed_url}): {e}")
        except aiohttp.ClientTimeout as e:
            logger.error(f"Timeout error for {feed_name} ({feed_url}): {e}")
        except Exception as e:
            logger.error(f"Error fetching or parsing feed {feed_name} ({feed_url}): {e}", exc_info=True)
        return events

    def format_event_message(self, event: EventInfo) -> str:
        """Format event for Telegram with improved structure, RTL, and redundancy check."""
        RLM = "\u200F"  # Right-to-Left Mark

        # 1. آماده‌سازی عنوان نمایشی
        display_title = event.title.strip()
        normalized_title_for_comparison = re.sub(r"^[🔁🖼⚜️📝📢✔️✅🔆🗓️📍💳#٪♦️🔹🔸🟢♦️▪️▫️▪️•●🔘👁‍🗨\s]+(?=[^\s])", "", display_title, flags=re.IGNORECASE).strip().lower()
        normalized_title_for_comparison = re.sub(r"[\s.:]*$", "", normalized_title_for_comparison)

        # 2. آماده‌سازی و پاکسازی توضیحات
        raw_description_html = event.description
        temp_html = raw_description_html.replace('<br/>', '\n').replace('<br />', '\n').replace('<br>', '\n')
        temp_html = re.sub(r'</p>\s*<p>', '</p>\n<p>', temp_html, flags=re.IGNORECASE)
        temp_html = re.sub(r'</li>\s*<li>', '</li>\n<li>', temp_html, flags=re.IGNORECASE)
        
        text_content_from_html = re.sub(r'<[^>]+>', '', temp_html).strip()
        description_after_html_strip = text_content_from_html

        match = re.match(r"^\s*Forwarded From[^\n]*(?:\n|$)", description_after_html_strip, re.IGNORECASE)
        if match:
            description_after_html_strip = description_after_html_strip[match.end():].strip()

        lines = [line.strip() for line in description_after_html_strip.splitlines()]
        description_processed = "\n".join(filter(None, lines))

        description_to_display = description_processed
        if description_processed and normalized_title_for_comparison: # فقط اگر عنوان برای مقایسه وجود داشت
            first_desc_line = description_processed.split('\n', 1)[0].strip()
            normalized_first_desc_line = re.sub(r"^[🔁🖼⚜️📝📢✔️✅🔆🗓️📍💳#٪♦️🔹🔸🟢♦️▪️▫️▪️•●🔘👁‍🗨\s]+(?=[^\s])", "", first_desc_line, flags=re.IGNORECASE).strip().lower()
            normalized_first_desc_line = re.sub(r"[\s.:]*$", "", normalized_first_desc_line)

            if normalized_title_for_comparison == normalized_first_desc_line or \
               (normalized_first_desc_line.startswith(normalized_title_for_comparison) and len(normalized_title_for_comparison) > 10):
                if '\n' in description_processed:
                    description_to_display = description_processed.split('\n', 1)[1].strip()
                else:
                    description_to_display = ""
                
                temp_lines = [line.strip() for line in description_to_display.splitlines()]
                description_to_display = "\n".join(filter(None, temp_lines))

        DESCRIPTION_MAX_LEN = 1200
        if len(description_to_display) > DESCRIPTION_MAX_LEN:
            cut_off_point = description_to_display.rfind('.', 0, DESCRIPTION_MAX_LEN)
            if cut_off_point != -1 and cut_off_point > DESCRIPTION_MAX_LEN - 200:
                 description_to_display = description_to_display[:cut_off_point+1] + " (...)"
            else:
                 description_to_display = description_to_display[:DESCRIPTION_MAX_LEN] + "..."
        
        if not description_to_display.strip():
            description_to_display = ""

        # 3. مونتاژ پیام نهایی
        message_parts = []
        if display_title:
             message_parts.append(f"{RLM}📝 **{display_title}**")

        if description_to_display:
            message_parts.append(f"\n\n{RLM}{description_to_display}")

        meta_info_parts = []
        if event.link:
            meta_info_parts.append(f"{RLM}🔗 [مشاهده کامل]({event.link})")
        
        if event.source_channel_username:
            meta_info_parts.append(f"{RLM}📢 **منبع:** [{event.source_channel}](https://t.me/{event.source_channel_username})")
        else:
            meta_info_parts.append(f"{RLM}📢 **منبع:** {event.source_channel}")

        if event.published:
            formatted_date = ""
            try:
                date_obj = datetime.strptime(event.published, "%a, %d %b %Y %H:%M:%S %Z")
                formatted_date = date_obj.strftime("%d %b %Y - %H:%M %Z")
            except ValueError:
                try:
                    main_date_part = event.published.split(',')[1].strip() if ',' in event.published else event.published
                    formatted_date = main_date_part.rsplit(':',1)[0] + " GMT"
                except:
                    formatted_date = event.published
            if formatted_date:
                 meta_info_parts.append(f"{RLM}📅 **انتشار:** {formatted_date}")

        if meta_info_parts:
            # اگر توضیحات نبود، فاصله تا متا کمتر باشد
            separator = "\n\n" if description_to_display else "\n"
            message_parts.append(separator + "\n".join(meta_info_parts))

        final_message = "\n".join(filter(None, message_parts)).strip()
        
        if len(final_message) > 4000:
            logger.warning(f"Generated message for '{display_title}' is very long: {len(final_message)} chars. May be truncated by Telegram.")
            # در اینجا می‌توانید پیام را بیشتر کوتاه کنید یا به چند پیام تقسیم کنید (پیچیده‌تر)
            # final_message = final_message[:4000] + "..." # یک کوتاه کردن ساده
            
        return final_message

    async def publish_event(self, event: EventInfo):
        try:
            message = self.format_event_message(event)
            if not message or not event.title : # اگر پیام یا عنوان اصلی خالی بود، ارسال نکن
                logger.info(f"Skipping empty or title-less message for an event from {event.source_channel}.")
                return

            await self.bot.send_message(
                chat_id=self.target_channel,
                text=message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
            logger.info(f"Published event: {event.title[:50]}... from {event.source_channel}")
        except Exception as e:
            logger.error(f"Failed to publish event ({event.title[:50]}...): {e}", exc_info=True)

    async def run_monitoring_loop(self):
        logger.info("Starting RSS monitoring...")
        await asyncio.sleep(5)

        while True:
            logger.info("Checking all feeds...")
            all_new_events = []
            async with aiohttp.ClientSession() as session:
                tasks = [self.fetch_feed(session, feed_info) for feed_info in self.rss_feeds]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, list):
                        all_new_events.extend(result)
                    elif isinstance(result, Exception):
                        logger.error(f"Feed processing task error: {result}", exc_info=result)
            
            if all_new_events:
                logger.info(f"Found {len(all_new_events)} new event(s) to publish.")
                for event_to_publish in all_new_events:
                    await self.publish_event(event_to_publish)
                    await asyncio.sleep(3)
            else:
                logger.info("No new events found in this cycle.")

            check_interval_seconds = 600 # 10 دقیقه
            logger.info(f"Next check in {check_interval_seconds // 60} minutes.")
            await asyncio.sleep(check_interval_seconds)


class Config:
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TARGET_CHANNEL = os.getenv('TARGET_CHANNEL')

rss_bot_instance: Optional[RSSTelegramBot] = None # نام متغیر را تغییر دادم که با کلاس همنام نباشد

async def health_check(request):
    global rss_bot_instance
    if rss_bot_instance:
        return web.Response(text=f"Bot is running! Processed items in memory: {len(rss_bot_instance.processed_items)}", status=200)
    return web.Response(text="Bot instance not initialized yet.", status=200) # یا 503

async def start_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "7860"))
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        logger.info(f"Web server started on port {port}")
    except OSError as e:
        logger.error(f"Failed to start web server on port {port}: {e}. Ensure the port is free or try another.")

async def main():
    global rss_bot_instance

    if not Config.BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is required!")
        return
    if not Config.TARGET_CHANNEL:
        logger.error("TARGET_CHANNEL environment variable is required!")
        return
    
    logger.info("Application starting...")

    rss_bot_instance = RSSTelegramBot(
        bot_token=Config.BOT_TOKEN,
        target_channel=Config.TARGET_CHANNEL
    )

    try:
        bot_info = await rss_bot_instance.bot.get_me()
        logger.info(f"Bot started: @{bot_info.username}")

        web_server_task = asyncio.create_task(start_web_server())
        await rss_bot_instance.run_monitoring_loop()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt)")
    except Exception as e:
        logger.error(f"Critical bot error: {e}", exc_info=True)
    finally:
        logger.info("Bot shutting down...")
        if 'web_server_task' in locals() and web_server_task and not web_server_task.done():
            web_server_task.cancel()
            try:
                await web_server_task
            except asyncio.CancelledError:
                logger.info("Web server task cancelled.")
        logger.info("Application fully stopped.")

if __name__ == "__main__":
    asyncio.run(main())
