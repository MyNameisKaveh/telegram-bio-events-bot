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
from bs4 import BeautifulSoup # کتابخانه برای تجزیه HTML
from aiohttp import web

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class EventInfo:
    title: str
    description: str  # HTML خام از فید
    link: str
    published: str
    source_channel: str
    source_channel_username: Optional[str] = None


class EventDetector:
    EVENT_KEYWORDS = [
        'وبینار', 'webinar', 'کارگاه', 'workshop', 'سمینار', 'seminar', 'کنفرانس', 'conference', 
        'همایش', 'congress', 'نشست', 'meeting', 'دوره آموزشی', 'course', 'کلاس', 'class', 
        'ایونت', 'event', 'برگزار', 'organize', 'شرکت', 'participate', 'ثبت نام', 'register',
        'رایگان', 'free', 'آنلاین', 'online', 'مجازی', 'virtual', 'آموزش', 'training', 
        'فراخوان', 'call', 'گواهی', 'certificate', 'مدرک', 'certification', 'لایو', 'live'
    ]

    def detect_event(self, title: str, description_html: str) -> bool:
        soup = BeautifulSoup(description_html, "html.parser")
        first_p_tag = soup.find('p')
        if first_p_tag and first_p_tag.get_text(strip=True).lower().startswith("forwarded from"):
            first_p_tag.decompose()
        
        text_only_description = soup.get_text(separator=' ', strip=True)
        full_text = f"{title} {text_only_description}"
        text_lower = full_text.lower()

        matches = sum(1 for keyword in self.EVENT_KEYWORDS if keyword in text_lower)
        has_specific_pattern = any([
            'ثبت نام' in text_lower, 'شرکت در' in text_lower, 
            'register' in text_lower, 'join' in text_lower, 'وبینار' in text_lower,
            'کارگاه' in text_lower, 'دوره آنلاین' in text_lower, 'سمینار' in text_lower
        ])
        title_has_keyword = any(keyword in title.lower() for keyword in ['وبینار', 'کارگاه', 'سمینار', 'دوره', 'کنفرانس', 'همایش', 'ایونت', 'نشست'])
        
        return title_has_keyword or has_specific_pattern or matches >= 1


class RSSTelegramBot:
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
            async with session.get(feed_url, timeout=45) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = feedparser.parse(content)
                    logger.info(f"Fetched {feed_name}. Entries: {len(feed.entries)}")
                    for entry in feed.entries[:10]: 
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
                        if len(self.processed_items) > 1500:
                            self.processed_items = set(list(self.processed_items)[500:])
                            logger.info(f"Cleaned up processed_items. New size: {len(self.processed_items)}")
                else:
                    logger.error(f"Error fetching {feed_name}: Status {response.status} - {await response.text(encoding='utf-8', errors='ignore')}")
        except Exception as e:
            logger.error(f"Exception in fetch_feed for {feed_name} ({feed_url}): {e}", exc_info=True)
        return events

    def _clean_html_and_extract_text(self, html_content: str) -> str:
        if not html_content:
            return ""
        soup = BeautifulSoup(html_content, "html.parser")

        # 1. حذف بخش "Forwarded From"
        first_p = soup.find('p')
        if first_p and first_p.get_text(strip=True).lower().startswith("forwarded from"):
            first_p.decompose()

        # 2. تبدیل تگ‌های <br> به یک placeholder خاص برای حفظ آن‌ها
        # و همچنین اضافه کردن placeholder بعد از تگ‌های پاراگراف برای جداسازی
        br_placeholder = "<<BR_TAG_PLACEHOLDER>>"
        for br_tag in soup.find_all("br"):
            br_tag.replace_with(br_placeholder)
        for p_tag in soup.find_all("p"): # برای جداسازی پاراگراف‌ها
             if p_tag.get_text(strip=True): # فقط اگر پاراگراف محتوا داشت
                p_tag.append(br_placeholder) # یک شکست خط بعد از هر پاراگراف اضافه کن

        # 3. استخراج متن، اجازه بده BeautifulSoup فاصله‌گذاری بین تگ‌های inline را مدیریت کند
        text_content = soup.get_text(separator=' ', strip=True) # separator=' ' برای جلوگیری از چسبیدن کلمات inline
        
        # 4. جایگزینی placeholder با \n واقعی
        text_with_breaks = text_content.replace(br_placeholder, "\n")
        
        # 5. نرمال‌سازی فضاهای خالی و خطوط جدید
        # تبدیل چندین خط جدید متوالی به یک خط جدید (برای حفظ پاراگراف‌بندی بصری)
        # و حذف فضاهای خالی ابتدا و انتهای هر خط
        lines = [line.strip() for line in text_with_breaks.splitlines()]
        
        # برای حفظ پاراگراف‌بندی که توسط دو \n ایجاد می‌شود، باید کمی متفاوت عمل کنیم
        # اما برای سادگی فعلی، تمام خطوط کاملاً خالی را حذف می‌کنیم
        # این کار باعث می‌شود پاراگراف‌ها با یک \n از هم جدا شوند
        cleaned_text = "\n".join(line for line in lines if line) # فقط خطوط غیرخالی

        return cleaned_text

    def format_event_message(self, event: EventInfo) -> str:
        RLM = "\u200F"
        display_title = event.title.strip()
        
        description_cleaned_text = self._clean_html_and_extract_text(event.description)
        
        description_to_display = description_cleaned_text
        title_is_displayed_separately = True # به طور پیش‌فرض عنوان جداگانه نمایش داده می‌شود

        # --- منطق جدید برای بررسی تکرار عنوان ---
        if description_cleaned_text and display_title:
            # رشته‌ای از ایموجی‌ها و کاراکترهای خاص که ممکن است در ابتدای عنوان باشند
            leading_symbols_pattern = r"^[🔁🖼⚜️📝📢✔️✅🔆🗓️📍💳#٪♦️🔹🔸🟢♦️▪️▫️▪️•●🔘👁‍🗨\s]*(?=[^\s])"
            trailing_punctuation_pattern = r"[\s.:…]+$"

            def normalize_text_for_comparison(text):
                if not text: return ""
                text = re.sub(leading_symbols_pattern, "", text, flags=re.IGNORECASE).strip()
                text = re.sub(trailing_punctuation_pattern, "", text)
                return text.lower()

            title_comp = normalize_text_for_comparison(display_title)
            
            if title_comp: # فقط اگر عنوان نرمال‌شده خالی نبود
                first_desc_line = description_cleaned_text.split('\n', 1)[0].strip()
                first_desc_line_comp = normalize_text_for_comparison(first_desc_line)

                # اگر عنوان نرمال‌شده با خط اول توضیحات نرمال‌شده یکی بود،
                # یا اگر یکی پیشوند معنادار دیگری بود
                if (title_comp == first_desc_line_comp) or \
                   (len(title_comp) > 7 and first_desc_line_comp.startswith(title_comp)) or \
                   (len(first_desc_line_comp) > 7 and title_comp.startswith(first_desc_line_comp)):
                    
                    logger.info(f"Title ('{display_title}') matches first line of desc ('{first_desc_line}'). Removing first line from description.")
                    if '\n' in description_cleaned_text:
                        description_to_display = description_cleaned_text.split('\n', 1)[1].strip()
                    else: # توضیحات فقط همان یک خط بود
                        description_to_display = "" 
                    
                    description_to_display = "\n".join(filter(None, (line.strip() for line in description_to_display.splitlines())))
        # --- پایان منطق بررسی تکرار عنوان ---
        
        DESCRIPTION_MAX_LEN = 2500 # افزایش بیشتر برای اینکه چیزی از قلم نیفتد
        if len(description_to_display) > DESCRIPTION_MAX_LEN:
            cut_off_point = description_to_display.rfind('.', 0, DESCRIPTION_MAX_LEN)
            if cut_off_point != -1 and cut_off_point > DESCRIPTION_MAX_LEN - 300:
                 description_to_display = description_to_display[:cut_off_point+1] + f"{RLM} (...)"
            else:
                 description_to_display = description_to_display[:DESCRIPTION_MAX_LEN] + f"{RLM}..."
        
        if not description_to_display.strip():
            description_to_display = ""

        message_parts = []
        if display_title:
             message_parts.append(f"{RLM}📝 **{display_title}**")

        if description_to_display:
            separator = "\n\n" if display_title else "" # اگر عنوان بود، دو خط فاصله
            message_parts.append(f"{separator}{RLM}{description_to_display}")

        meta_info_parts = []
        if event.link: meta_info_parts.append(f"{RLM}🔗 [مشاهده کامل رویداد]({event.link})")
        if event.source_channel_username:
            meta_info_parts.append(f"{RLM}📢 **منبع:** [{event.source_channel}](https://t.me/{event.source_channel_username})")
        else: meta_info_parts.append(f"{RLM}📢 **منبع:** {event.source_channel}")

        if event.published:
            formatted_date = ""
            try:
                date_obj = datetime.strptime(event.published, "%a, %d %b %Y %H:%M:%S %Z")
                formatted_date = date_obj.strftime("%d %b %Y - %H:%M (%Z)")
            except: formatted_date = event.published # نمایش خام در صورت خطا
            if formatted_date: meta_info_parts.append(f"{RLM}📅 **انتشار:** {formatted_date}")

        if meta_info_parts:
            separator_meta = "\n\n" if message_parts else "" # اگر متن اصلی بود، دو خط فاصله تا متا
            message_parts.append(separator_meta + "\n".join(meta_info_parts))

        final_message = "\n".join(message_parts).strip()
                                                          
        TELEGRAM_MSG_MAX_LEN = 4096
        if len(final_message) > TELEGRAM_MSG_MAX_LEN:
            logger.warning(f"Msg for '{display_title}' too long ({len(final_message)}), truncating.")
            # کوتاه کردن اضطراری اگر خیلی طولانی شد
            # این باید بهتر مدیریت شود، مثلا با تقسیم پیام
            excess_chars = len(final_message) - (TELEGRAM_MSG_MAX_LEN - 20) # برای " (...)" جا بگذار
            if description_to_display and len(description_to_display) > excess_chars:
                # سعی کن از توضیحات کم کنی
                description_to_display_truncated_further = description_to_display[:-excess_chars-len(f"{RLM} (...)")] + f"{RLM} (...)"
                # ... و پیام را دوباره بساز (این بخش برای سادگی حذف شد، اما یک راه حل کامل‌تر نیاز است)
                final_message = final_message[:TELEGRAM_MSG_MAX_LEN - 20] + f"{RLM} (...)"
            else: # اگر توضیحات کوتاه بود یا نبود، از انتهای پیام ببر
                final_message = final_message[:TELEGRAM_MSG_MAX_LEN - 20] + f"{RLM} (...)"

        return final_message

    # ... (متدهای publish_event و run_monitoring_loop بدون تغییر از پاسخ قبلی) ...
    async def publish_event(self, event: EventInfo):
        try:
            message = self.format_event_message(event)
            if not message or (not event.title and not event.description): 
                logger.info(f"Skipping empty or content-less message for event from {event.source_channel} (Title: {event.title[:30]}...).")
                return

            await self.bot.send_message(
                chat_id=self.target_channel, text=message,
                parse_mode='Markdown', disable_web_page_preview=True # پیش‌نمایش لینک را غیرفعال کردم تا پیام شلوغ نشود
            )
            logger.info(f"Published event: {event.title[:60]}... from {event.source_channel}")
        except Exception as e:
            logger.error(f"Failed to publish event ({event.title[:60]}...): {e}", exc_info=True)

    async def run_monitoring_loop(self):
        logger.info("Starting RSS monitoring...")
        await asyncio.sleep(10) 
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
                    await asyncio.sleep(5) 
            else:
                logger.info("No new events found.")
            
            check_interval_seconds = 600 
            logger.info(f"Next check in {check_interval_seconds // 60} minutes.")
            await asyncio.sleep(check_interval_seconds)


# ... (کلاس Config و توابع health_check, start_web_server, main بدون تغییر از پاسخ قبلی) ...
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
