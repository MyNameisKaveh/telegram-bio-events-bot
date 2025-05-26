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
from bs4 import BeautifulSoup # <--- کتابخانه برای تجزیه HTML
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
        # حذف اولیه بخش "Forwarded From" برای دقت بیشتر در تشخیص کلمات کلیدی
        first_p_tag = soup.find('p')
        if first_p_tag and first_p_tag.get_text(strip=True).lower().startswith("forwarded from"):
            first_p_tag.decompose()
        
        text_only_description = soup.get_text(separator=' ', strip=True)
        full_text = f"{title} {text_only_description}"
        text_lower = full_text.lower()

        matches = sum(1 for keyword in self.EVENT_KEYWORDS if keyword in text_lower)
        has_specific_pattern = any([
            'ثبت نام' in text_lower, 'شرکت در' in text_lower, 
            # 'برگزار می' in text_lower, # این ممکن است بیش از حد کلی باشد
            'register' in text_lower, 'join' in text_lower, 'وبینار' in text_lower,
            'کارگاه' in text_lower, 'دوره آنلاین' in text_lower, 'سمینار' in text_lower
        ])
        # اگر عنوان شامل کلمه کلیدی بود یا توضیحات شامل الگوی خاص بود یا تعداد کلمات کلیدی کافی بود
        title_has_keyword = any(keyword in title.lower() for keyword in ['وبینار', 'کارگاه', 'سمینار', 'دوره', 'کنفرانس', 'همایش'])
        
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
        # (این متد بدون تغییر نسبت به آخرین نسخه کامل ارائه شده باقی می‌ماند، مگر اینکه بخواهید تعداد feed.entries[:X] را تغییر دهید)
        # ... (کد fetch_feed از پاسخ قبلی را اینجا کپی کنید) ...
        # فقط برای اطمینان، بخش اصلی آن را اینجا می آورم:
        events = []
        feed_url, feed_name = feed_info['url'], feed_info['name']
        logger.info(f"Fetching feed: {feed_name} from {feed_url}")
        try:
            async with session.get(feed_url, timeout=45) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = feedparser.parse(content)
                    logger.info(f"Fetched {feed_name}. Entries: {len(feed.entries)}")
                    for entry in feed.entries[:10]: # یا :5 اگر هنوز می‌خواهید
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
        """
        HTML را با BeautifulSoup پاکسازی می‌کند تا متن ساده با فرمت خوب تولید شود.
        - بخش "Forwarded From" را حذف می‌کند.
        - تگ <br> را به خط جدید تبدیل می‌کند.
        - سعی در حفظ پاراگراف‌بندی دارد.
        """
        if not html_content:
            return ""

        soup = BeautifulSoup(html_content, "html.parser")

        # 1. حذف بخش "Forwarded From"
        # معمولاً اولین پاراگراف در خروجی RSSHub برای پیام‌های فوروارد شده است.
        first_p_tag = soup.find('p')
        if first_p_tag:
            first_p_text = first_p_tag.get_text(strip=True)
            if first_p_text.lower().startswith("forwarded from"):
                logger.debug(f"Removing 'Forwarded From' paragraph: {first_p_tag.get_text(strip=True)[:100]}")
                first_p_tag.decompose() # حذف کامل تگ p و محتویاتش

        # 2. تبدیل تگ‌های <br> به کاراکتر خط جدید (\n)
        for br_tag in soup.find_all("br"):
            br_tag.replace_with("\n")

        # 3. استخراج متن با حفظ ساختار بهتر
        # از get_text(separator='\n') استفاده می‌کنیم که بین بلاک‌های مختلف خط جدید می‌گذارد
        # و strip=True فضاهای خالی اضافی در ابتدا و انتهای هر بخش متنی را حذف می‌کند.
        text_content = soup.get_text(separator='\n', strip=True)
        
        # نرمال‌سازی شکستگی خطوط: حذف خطوط کاملاً خالی و فضاهای خالی ابتدا/انتهای هر خط
        lines = [line.strip() for line in text_content.splitlines()]
        cleaned_text = "\n".join(line for line in lines if line) # فقط خطوط غیرخالی را نگه دار

        return cleaned_text

    def format_event_message(self, event: EventInfo) -> str:
        RLM = "\u200F"

        display_title = event.title.strip()
        
        # پاکسازی توضیحات با استفاده از متد جدید
        description_cleaned_text = self._clean_html_and_extract_text(event.description)
        
        # آماده‌سازی عنوان و توضیحات برای بررسی تکرار
        # یک لیست از کاراکترها/ایموجی‌هایی که ممکن است در ابتدا باشند و برای مقایسه باید حذف شوند
        leading_chars_to_strip_pattern = r"^[🔁🖼⚜️📝📢✔️✅🔆🗓️📍💳#٪♦️🔹🔸🟢♦️▪️▫️▪️•●🔘👁‍🗨\s]*(?=[^\s])"
        # حذف این کاراکترها از ابتدای عنوان برای مقایسه
        normalized_title_for_comparison = re.sub(leading_chars_to_strip_pattern, "", display_title, flags=re.IGNORECASE).strip().lower()
        normalized_title_for_comparison = re.sub(r"[\s.:…]+$", "", normalized_title_for_comparison) # حذف نقطه‌گذاری انتهایی

        description_to_display = description_cleaned_text
        title_is_separate = True # به طور پیش‌فرض عنوان جداگانه نمایش داده می‌شود

        if description_cleaned_text and normalized_title_for_comparison:
            first_desc_line = description_cleaned_text.split('\n', 1)[0].strip()
            # نرمال‌سازی خط اول توضیحات برای مقایسه
            normalized_first_desc_line = re.sub(leading_chars_to_strip_pattern, "", first_desc_line, flags=re.IGNORECASE).strip().lower()
            normalized_first_desc_line = re.sub(r"[\s.:…]+$", "", normalized_first_desc_line)

            # اگر عنوان نرمال‌شده و خط اول توضیحات نرمال‌شده یکی بودند
            # یا اگر عنوان بخش قابل توجهی از ابتدای خط اول توضیحات بود (مثلاً عنوان کوتاه شده در RSS)
            if (normalized_title_for_comparison == normalized_first_desc_line) or \
               (len(normalized_title_for_comparison) > 8 and normalized_first_desc_line.startswith(normalized_title_for_comparison)) or \
               (len(normalized_first_desc_line) > 8 and normalized_title_for_comparison.startswith(normalized_first_desc_line)): # بررسی اینکه آیا یکی پیشوند دیگری است
                
                logger.info(f"Title considered redundant or part of description's first line for: '{display_title}'")
                # در این حالت، عنوان جداگانه نمایش داده نمی‌شود و توضیحات از ابتدا نمایش داده می‌شود
                # (چون خود توضیحات شامل خط اولی است که شبیه عنوان است)
                # یا اگر می‌خواهید حتما عنوان جدا باشد و خط اول از توضیحات حذف شود:
                if '\n' in description_cleaned_text:
                    description_to_display = description_cleaned_text.split('\n', 1)[1].strip()
                else: # توضیحات فقط همان یک خط بود
                    description_to_display = "" 
                # تمیزکاری مجدد اگر با حذف خط اول، خالی شده باشد
                description_to_display = "\n".join(filter(None, (line.strip() for line in description_to_display.splitlines())))
                
        # محدود کردن طول نهایی توضیحات
        DESCRIPTION_MAX_LEN = 2000  # افزایش بیشتر محدودیت، چون می‌خواهید چیزی از قلم نیفتد
                                     # اما مراقب محدودیت کلی تلگرام (۴۰۹۶ کاراکتر) باشید
        if len(description_to_display) > DESCRIPTION_MAX_LEN:
            # سعی کن در یک نقطه مناسب (مثل انتهای جمله) کوتاه کنی
            cut_off_point = description_to_display.rfind('.', 0, DESCRIPTION_MAX_LEN)
            if cut_off_point != -1 and cut_off_point > DESCRIPTION_MAX_LEN - 300: # اگر نقطه خیلی دور نبود
                 description_to_display = description_to_display[:cut_off_point+1] + f"{RLM} (...)"
            else:
                 description_to_display = description_to_display[:DESCRIPTION_MAX_LEN] + f"{RLM}..."
        
        if not description_to_display.strip(): # اگر توضیحات خالی شد
            description_to_display = ""

        # مونتاژ پیام
        message_parts = []
        if display_title: # عنوان همیشه نمایش داده می‌شود (مگر اینکه در آینده تصمیم دیگری بگیریم)
             message_parts.append(f"{RLM}📝 **{display_title}**")

        if description_to_display:
            # اگر عنوان و توضیحات هر دو وجود دارند، دو خط فاصله
            # اگر فقط توضیحات وجود دارد (و عنوان جداگانه نمایش داده نشده)، فاصله‌ای لازم نیست
            separator = "\n\n" if display_title else ""
            message_parts.append(f"{separator}{RLM}{description_to_display}")

        meta_info_parts = []
        if event.link:
            meta_info_parts.append(f"{RLM}🔗 [مشاهده کامل رویداد]({event.link})")
        
        if event.source_channel_username:
            meta_info_parts.append(f"{RLM}📢 **منبع:** [{event.source_channel}](https://t.me/{event.source_channel_username})")
        else:
            meta_info_parts.append(f"{RLM}📢 **منبع:** {event.source_channel}")

        if event.published:
            formatted_date = ""
            try:
                date_obj = datetime.strptime(event.published, "%a, %d %b %Y %H:%M:%S %Z") # Fri, 23 May 2025 22:41:11 GMT
                formatted_date = date_obj.strftime("%d %b %Y - %H:%M (%Z)") # 23 May 2025 - 22:41 (GMT)
            except ValueError:
                try: # تلاش برای فرمت ساده‌تر
                    main_date_part = event.published.split(',')[1].strip() if ',' in event.published else event.published
                    formatted_date = main_date_part.rsplit(':',2)[0] + " " + main_date_part.rsplit(' ',1)[-1] # 23 May 2025 22 (GMT)
                except: formatted_date = event.published # نمایش خام
            if formatted_date:
                 meta_info_parts.append(f"{RLM}📅 **انتشار:** {formatted_date}")

        if meta_info_parts:
            # اگر بخش اصلی پیام (عنوان یا توضیحات) وجود داشت، دو خط فاصله تا متا
            separator_meta = "\n\n" if message_parts else ""
            message_parts.append(separator_meta + "\n".join(meta_info_parts))

        final_message = "\n".join(message_parts).strip() # filter(None,..) حذف شد تا فاصله‌های عمدی حفظ شوند
                                                          # .strip() نهایی برای حذف فضاهای اضافی کل پیام
        
        TELEGRAM_MSG_MAX_LEN = 4096
        if len(final_message) > TELEGRAM_MSG_MAX_LEN:
            logger.warning(f"Message for '{display_title}' too long ({len(final_message)} chars), will be truncated by Telegram or cause error.")
            # اگر خیلی طولانی شد، باید استراتژی بهتری برای کوتاه کردن کل پیام داشته باشیم
            # فعلا فقط هشدار می‌دهیم. تلگرام خودش کوتاه می‌کند یا خطا می‌دهد.
            
        return final_message

    # ... (متدهای publish_event و run_monitoring_loop بدون تغییر از پاسخ قبلی) ...
    async def publish_event(self, event: EventInfo):
        try:
            message = self.format_event_message(event)
            if not message or (not event.title and not event.description): # اگر پیام یا محتوای اصلی خالی بود
                logger.info(f"Skipping empty or content-less message for an event from {event.source_channel}.")
                return

            await self.bot.send_message(
                chat_id=self.target_channel, text=message,
                parse_mode='Markdown', disable_web_page_preview=False # True برای جلوگیری از پیش‌نمایش لینک‌ها اگر شلوغ می‌شود
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
        if 'web_server_task' in locals() and web_server_task and not web_server_task.done(): #locals() اضافه شد
            web_server_task.cancel()
            try: await web_server_task
            except asyncio.CancelledError: logger.info("Web server task cancelled.")
        logger.info("Application fully stopped.")

if __name__ == "__main__":
    asyncio.run(main())
