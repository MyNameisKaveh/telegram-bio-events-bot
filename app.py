import asyncio
import re
import logging
from datetime import datetime
import aiohttp
import feedparser
from telegram import Bot
import os
from dataclasses import dataclass
from typing import List, Optional
from bs4 import BeautifulSoup # <--- اضافه شد
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
    source_channel: str
    source_channel_username: Optional[str] = None


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
        # استفاده از BeautifulSoup برای استخراج متن تمیزتر برای تشخیص
        soup = BeautifulSoup(description_html, "html.parser")
        # حذف بخش "Forwarded From" قبل از استخراج متن برای تشخیص بهتر
        # این کار کمک می‌کند کلمات کلیدی موجود در بخش فوروارد شده، به اشتباه باعث تشخیص رویداد نشوند
        # اگرچه منطق اصلی حذف "Forwarded From" در format_event_message است.
        # اینجا یک پاکسازی اولیه انجام می‌دهیم.
        possible_forward_header = soup.find('p') # اولین پاراگراف
        if possible_forward_header and possible_forward_header.get_text(strip=True).lower().startswith("forwarded from"):
            possible_forward_header.extract()

        text_only_description = soup.get_text(separator=' ', strip=True) # separator=' ' برای جلوگیری از چسبیدن کلمات
        full_text = f"{title} {text_only_description}"
        text_lower = full_text.lower()

        matches = sum(1 for keyword in self.EVENT_KEYWORDS if keyword in text_lower)
        has_specific_pattern = any([
            'ثبت نام' in text_lower, 'شرکت در' in text_lower, 'برگزار می' in text_lower,
            'register' in text_lower, 'join' in text_lower, 'وبینار' in text_lower,
            'کارگاه' in text_lower, 'دوره آنلاین' in text_lower
        ])
        return matches >= 1 or has_specific_pattern # آستانه را به ۱ کاهش دادم، چون با پاکسازی بهتر، یک کلمه کلیدی هم می‌تواند کافی باشد.


class RSSTelegramBot:
    """RSS-based Telegram Event Bot"""

    def __init__(self, bot_token: str, target_channel: str):
        self.bot_token = bot_token
        self.target_channel = target_channel
        self.bot = Bot(token=bot_token)
        self.detector = EventDetector()
        self.processed_items = set()

        self.rss_feeds = [
            {'name': 'WinCell Co', 'url': 'https://rsshub.app/telegram/channel/wincellco', 'channel': 'wincellco'},
            {'name': 'Rayazistazma', 'url': 'https://rsshub.app/telegram/channel/Rayazistazma', 'channel': 'Rayazistazma'},
            {'name': 'SBU Bio Society', 'url': 'https://rsshub.app/telegram/channel/SBUBIOSOCIETY', 'channel': 'SBUBIOSOCIETY'},
            {'name': 'Test BioPy Channel', 'url': 'https://rsshub.app/telegram/channel/testbiopy', 'channel': 'testbiopy'}
        ]

    async def fetch_feed(self, session: aiohttp.ClientSession, feed_info: dict) -> List[EventInfo]:
        events = []
        feed_url, feed_name = feed_info['url'], feed_info['name']
        logger.info(f"Fetching feed: {feed_name} from {feed_url}")
        try:
            async with session.get(feed_url, timeout=45) as response: # افزایش تایم‌اوت
                if response.status == 200:
                    content = await response.text()
                    feed = feedparser.parse(content)
                    logger.info(f"Fetched {feed_name}. Entries: {len(feed.entries)}")
                    for entry in feed.entries[:10]: # یا هر تعداد که برای تست اولیه مناسب می‌دانید، مثلا ۵
                        entry_id = f"{feed_info.get('channel', feed_name)}_{entry.get('id', entry.get('link', ''))}"
                        if entry_id not in self.processed_items:
                            raw_title = entry.get('title', '').strip()
                            raw_description_html = entry.get('description', entry.get('summary', ''))
                            if self.detector.detect_event(raw_title, raw_description_html):
                                event = EventInfo(
                                    title=raw_title, description=raw_description_html,
                                    link=entry.get('link', ''), published=entry.get('published', ''),
                                    source_channel=feed_name, source_channel_username=feed_info.get('channel')
                                )
                                events.append(event)
                                self.processed_items.add(entry_id)
                        if len(self.processed_items) > 1500: # کمی افزایش ظرفیت حافظه
                            self.processed_items = set(list(self.processed_items)[500:])
                            logger.info(f"Cleaned up processed_items. New size: {len(self.processed_items)}")
                else:
                    logger.error(f"Error fetching {feed_name}: Status {response.status} - {await response.text(encoding='utf-8', errors='ignore')}")
        except Exception as e:
            logger.error(f"Exception in fetch_feed for {feed_name} ({feed_url}): {e}", exc_info=True)
        return events

    def _clean_html_and_extract_text(self, html_content: str) -> str:
        """
        Cleans HTML content using BeautifulSoup to produce well-formatted plain text.
        - Removes "Forwarded From" sections.
        - Converts <br> to newlines.
        - Handles <p> and list elements for better paragraph separation.
        - Strips other HTML tags.
        """
        if not html_content:
            return ""

        soup = BeautifulSoup(html_content, "html.parser") # یا 'lxml' اگر نصب کرده‌اید

        # 1. حذف بخش "Forwarded From" (معمولا اولین پاراگراف در خروجی RSSHub است)
        first_p = soup.find('p')
        if first_p and first_p.get_text(strip=True).lower().startswith("forwarded from"):
            logger.debug(f"Removing 'Forwarded From' paragraph: {first_p.prettify()}")
            first_p.decompose() # حذف کامل تگ p و محتویاتش

        # 2. تبدیل تگ‌های <br> به خط جدید قبل از استخراج متن
        for br in soup.find_all("br"):
            br.replace_with("\n")

        # 3. استخراج متن با حفظ ساختار بهتر برای پاراگراف‌ها و لیست‌ها
        # get_text با separator='\n' برای هر بلاک تگ یک خط جدید می‌گذارد
        # و strip=True فضاهای خالی اضافی را حذف می‌کند.
        text_blocks = []
        for element in soup.find_all(['p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            # برای هر بلاک، متن را گرفته و strip می‌کنیم. خطوط خالی را بعدا حذف می‌کنیم.
            block_text = element.get_text(separator=' ', strip=True) # separator=' ' برای کلمات داخل یک بلاک
            if block_text:
                text_blocks.append(block_text)
        
        if not text_blocks: # اگر ساختار p, div و غیره نبود، کل متن را بگیر
             text_blocks.append(soup.get_text(separator='\n', strip=True))


        # به هم چسباندن بلاک‌های متنی با یک خط جدید بین آن‌ها
        full_text = "\n".join(text_blocks)

        # حذف خطوطی که فقط شامل فضاهای خالی هستند و نرمال‌سازی شکستگی خطوط
        lines = [line.strip() for line in full_text.splitlines()]
        cleaned_text = "\n".join(filter(None, lines)) # حذف خطوط کاملا خالی

        return cleaned_text

    def format_event_message(self, event: EventInfo) -> str:
        RLM = "\u200F"

        display_title = event.title.strip()
        # نرمال‌سازی عنوان برای مقایسه (این بخش از کد قبلی حفظ شده و خوب است)
        normalized_title_for_comparison = re.sub(r"^[🔁🖼⚜️📝📢✔️✅🔆🗓️📍💳#٪♦️🔹🔸🟢♦️▪️▫️▪️•●🔘👁‍🗨\s]+(?=[^\s])", "", display_title, flags=re.IGNORECASE).strip().lower()
        normalized_title_for_comparison = re.sub(r"[\s.:]*$", "", normalized_title_for_comparison)

        # استفاده از متد جدید برای پاکسازی توضیحات با BeautifulSoup
        description_cleaned_text = self._clean_html_and_extract_text(event.description)
        
        description_to_display = description_cleaned_text
        if description_cleaned_text and normalized_title_for_comparison:
            first_desc_line = description_cleaned_text.split('\n', 1)[0].strip()
            normalized_first_desc_line = re.sub(r"^[🔁🖼⚜️📝📢✔️✅🔆🗓️📍💳#٪♦️🔹🔸🟢♦️▪️▫️▪️•●🔘👁‍🗨\s]+(?=[^\s])", "", first_desc_line, flags=re.IGNORECASE).strip().lower()
            normalized_first_desc_line = re.sub(r"[\s.:]*$", "", normalized_first_desc_line)

            # اگر عنوان و خط اول توضیحات یکی بودند یا عنوان پیشوندی از آن بود
            if normalized_title_for_comparison == normalized_first_desc_line or \
               (len(normalized_title_for_comparison) > 15 and normalized_first_desc_line.startswith(normalized_title_for_comparison)): # شرط طول برای پیشوندهای معنادار
                
                if '\n' in description_cleaned_text:
                    description_to_display = description_cleaned_text.split('\n', 1)[1].strip()
                else:
                    description_to_display = ""
                
                temp_lines = [line.strip() for line in description_to_display.splitlines()]
                description_to_display = "\n".join(filter(None, temp_lines))

        DESCRIPTION_MAX_LEN = 1500 # افزایش بیشتر محدودیت
        if len(description_to_display) > DESCRIPTION_MAX_LEN:
            cut_off_point = description_to_display.rfind('.', 0, DESCRIPTION_MAX_LEN)
            if cut_off_point != -1 and cut_off_point > DESCRIPTION_MAX_LEN - 300:
                 description_to_display = description_to_display[:cut_off_point+1] + " (...)"
            else:
                 description_to_display = description_to_display[:DESCRIPTION_MAX_LEN] + "..."
        
        if not description_to_display.strip():
            description_to_display = ""

        message_parts = []
        if display_title:
             message_parts.append(f"{RLM}📝 **{display_title}**")

        if description_to_display:
            message_parts.append(f"\n\n{RLM}{description_to_display}")

        meta_info_parts = []
        if event.link:
            meta_info_parts.append(f"{RLM}🔗 [مشاهده کامل رویداد]({event.link})") # متن لینک را کمی تغییر دادم
        
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
                except: formatted_date = event.published
            if formatted_date:
                 meta_info_parts.append(f"{RLM}📅 **انتشار:** {formatted_date}")

        if meta_info_parts:
            separator = "\n\n" if (display_title and description_to_display) or (display_title and not description_to_display and len(message_parts) > 0) else "\n"
            if not description_to_display and display_title: separator = "\n\n" # اگر فقط عنوان بود، دو خط فاصله تا متا

            message_parts.append(separator + "\n".join(meta_info_parts))

        final_message = "\n".join(filter(None, message_parts)).strip()
        
        TELEGRAM_MSG_MAX_LEN = 4096
        if len(final_message) > TELEGRAM_MSG_MAX_LEN:
            logger.warning(f"Message for '{display_title}' too long ({len(final_message)} chars), truncating.")
            # کوتاه کردن هوشمندانه‌تر پیام کلی اگر لازم باشد
            # برای سادگی، فعلا از انتهای پیام کلی می‌بریم
            final_message = final_message[:TELE_GRAM_MSG_MAX_LEN - 20] + "\n(... ادامه دارد ...)"
            
        return final_message

    async def publish_event(self, event: EventInfo):
        try:
            message = self.format_event_message(event)
            if not message or not event.title:
                logger.info(f"Skipping empty or title-less message for an event from {event.source_channel}.")
                return

            await self.bot.send_message(
                chat_id=self.target_channel, text=message,
                parse_mode='Markdown', disable_web_page_preview=False
            )
            logger.info(f"Published event: {event.title[:60]}... from {event.source_channel}")
        except Exception as e:
            logger.error(f"Failed to publish event ({event.title[:60]}...): {e}", exc_info=True)

    async def run_monitoring_loop(self):
        logger.info("Starting RSS monitoring...")
        await asyncio.sleep(10) # افزایش زمان انتظار اولیه
        while True:
            logger.info("Checking all feeds...")
            all_new_events = []
            async with aiohttp.ClientSession() as session:
                tasks = [self.fetch_feed(session, feed_info) for feed_info in self.rss_feeds]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, list): all_new_events.extend(result)
                    elif isinstance(result, Exception): logger.error(f"Feed task error: {result}", exc_info=result)
            
            if all_new_events:
                logger.info(f"Found {len(all_new_events)} new event(s).")
                # می‌توانید رویدادها را بر اساس تاریخ انتشارشان مرتب کنید (اگر معتبر باشد)
                # all_new_events.sort(key=lambda ev: ev.published_parsed_time_object_if_available, reverse=True)
                for event_to_publish in all_new_events:
                    await self.publish_event(event_to_publish)
                    await asyncio.sleep(5) # افزایش فاصله بین پیام‌ها
            else:
                logger.info("No new events found.")
            
            check_interval_seconds = 600
            logger.info(f"Next check in {check_interval_seconds // 60} minutes.")
            await asyncio.sleep(check_interval_seconds)

class Config:
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TARGET_CHANNEL = os.getenv('TARGET_CHANNEL')

rss_bot_instance: Optional[RSSTelegramBot] = None

async def health_check(request):
    global rss_bot_instance
    if rss_bot_instance:
        return web.Response(text=f"Bot is running! Processed items: {len(rss_bot_instance.processed_items)}", status=200)
    return web.Response(text="Bot not fully initialized.", status=200)

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
        logger.error(f"Failed to start web server on port {port}: {e}.")

async def main():
    global rss_bot_instance
    if not Config.BOT_TOKEN or not Config.TARGET_CHANNEL:
        logger.critical("Missing TELEGRAM_BOT_TOKEN or TARGET_CHANNEL environment variables!")
        return
    
    logger.info("Application starting...")
    rss_bot_instance = RSSTelegramBot(bot_token=Config.BOT_TOKEN, target_channel=Config.TARGET_CHANNEL)
    try:
        bot_info = await rss_bot_instance.bot.get_me()
        logger.info(f"Bot started: @{bot_info.username}")
        web_server_task = asyncio.create_task(start_web_server())
        await rss_bot_instance.run_monitoring_loop()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Critical bot error in main: {e}", exc_info=True)
    finally:
        logger.info("Bot shutting down...")
        if 'web_server_task' in locals() and web_server_task and not web_server_task.done():
            web_server_task.cancel()
            try: await web_server_task
            except asyncio.CancelledError: logger.info("Web server task cancelled.")
        logger.info("Application fully stopped.")

if __name__ == "__main__":
    asyncio.run(main())
