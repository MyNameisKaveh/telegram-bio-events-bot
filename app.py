import asyncio
import re
import logging
from datetime import datetime
import aiohttp
import feedparser
from telegram import Bot
# from telegram.ext import Application # اگر Application استفاده نمی‌شود، می‌توان حذف کرد
import os
from dataclasses import dataclass, field # field را اضافه کنید
from typing import List, Optional
# import json # اگر فایل processed_items.json استفاده نشود، این دیگر لازم نیست
from aiohttp import web

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class EventInfo:
    """Class to store event information"""
    title: str
    description: str  # این همچنان HTML خام خواهد بود
    link: str
    published: str
    source_channel: str  # نام نمایشی کانال
    source_channel_username: Optional[str] = None # نام کاربری برای لینک


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

    def detect_event(self, title: str, description_html: str) -> bool: # امضای متد تغییر کرد
        """Detect if content contains event information based on title and HTML description."""
        # یک نسخه فقط متنی برای جستجوی کلمات کلیدی ایجاد کنید
        text_only_description = re.sub(r'<[^>]+>', '', description_html).strip()
        full_text = f"{title} {text_only_description}" # از متن خالص برای تشخیص استفاده کنید
        text_lower = full_text.lower()

        # شمارش کلمات کلیدی مطابق
        matches = sum(1 for keyword in self.EVENT_KEYWORDS if keyword in text_lower)

        # اگر حداقل ۲ کلمه کلیدی یا الگوهای خاصی داشتیم، True برگردان
        # به نظر می‌رسد با توجه به محتوای XML که فرستادید، حتی یک کلمه کلیدی مثل "وبینار" یا "ثبت نام" کافی باشد.
        # می‌توانید این منطق را بر اساس نیاز خودتان دقیق‌تر کنید.
        # برای مثال، اگر فقط حضور "ثبت نام" یا "وبینار" کافی است:
        has_specific_pattern = any([
            'ثبت نام' in text_lower,
            'شرکت در' in text_lower,
            'برگزار می' in text_lower, # مراقب باشید این ممکن است False Positive زیاد داشته باشد
            'register' in text_lower,
            'join' in text_lower,
            'وبینار' in text_lower, # اضافه شد
            'کارگاه' in text_lower, # اضافه شد
            'دوره آنلاین' in text_lower # اضافه شد
        ])

        # اگر تعداد کلمات کلیدی به حد نصاب رسید یا یکی از الگوهای خیلی خاص پیدا شد
        return matches >= 2 or has_specific_pattern


class RSSTelegramBot:
    """RSS-based Telegram Event Bot"""

    def __init__(self, bot_token: str, target_channel: str):
        self.bot_token = bot_token
        self.target_channel = target_channel
        self.bot = Bot(token=bot_token)
        self.detector = EventDetector()
        self.processed_items = set()  # To avoid duplicates (in-memory for now)

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
            # فیدهای دیگر خود را اینجا اضافه کنید
            # کانال جدید شما برای تست:
            {
                'name': 'کانال تست', # می‌توانید یک نام دلخواه برای نمایش انتخاب کنید
                'url': 'https://rsshub.app/telegram/channel/testbiopy', # آدرس RSS فید کانال
                'channel': 'testbiopy' # نام کاربری کانال تلگرام
            }
        ]

    async def fetch_feed(self, session: aiohttp.ClientSession, feed_info: dict) -> List[EventInfo]:
        """Fetch and parse RSS feed"""
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

                    for entry in feed.entries[:10]:  # Check last 10 entries
                        entry_id = f"{feed_info.get('channel', feed_name)}_{entry.get('id', entry.get('link', ''))}"

                        if entry_id not in self.processed_items:
                            raw_title = entry.get('title', '').strip()
                            raw_description_html = entry.get('description', entry.get('summary', ''))

                            # رویداد را با استفاده از عنوان و توضیحات HTML خام تشخیص دهید
                            if self.detector.detect_event(raw_title, raw_description_html):
                                event = EventInfo(
                                    title=raw_title,
                                    description=raw_description_html, # HTML خام اینجا ذخیره می‌شود
                                    link=entry.get('link', ''),
                                    published=entry.get('published', ''),
                                    source_channel=feed_name,
                                    source_channel_username=feed_info.get('channel') # نام کاربری کانال منبع
                                )
                                events.append(event)
                                self.processed_items.add(entry_id)
                                # عدم ذخیره processed_items در فایل طبق درخواست فعلی کاربر

                        # مدیریت حجم processed_items برای جلوگیری از مصرف زیاد حافظه
                        if len(self.processed_items) > 1000: # این عدد را می‌توانید تنظیم کنید
                            items_list = list(self.processed_items)
                            # حذف ۲۰۰ آیتم قدیمی‌تر برای آزاد کردن حافظه
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
        """Format event for Telegram with RTL, source linking, and better structure."""
        RLM = "\u200F"  # Right-to-Left Mark

        # 1. آماده‌سازی عنوان
        display_title = event.title.strip()
        # اگر می‌خواهید ایموجی‌های خاصی را از ابتدای عنوان حذف کنید، اینجا می‌توانید اضافه کنید
        # مثال: display_title = re.sub(r"^[🔁🖼⚜️\s]+", "", display_title).strip()

        # 2. آماده‌سازی توضیحات
        raw_description_html = event.description
        
        # تلاش اولیه برای حفظ برخی شکستگی‌های خط از HTML قبل از حذف کامل تگ‌ها
        # این یک روش تقریبی است. برای نتایج بهتر، استفاده از کتابخانه HTML parser توصیه می‌شود.
        temp_html = raw_description_html.replace('<br/>', '\n').replace('<br />', '\n').replace('<br>', '\n')
        temp_html = re.sub(r'</p>\s*<p>', '</p>\n<p>', temp_html, flags=re.IGNORECASE) # برای ایجاد فاصله بین پاراگراف‌ها
        
        # حذف تمام تگ‌های HTML برای گرفتن متن خالص
        text_content_from_html = re.sub(r'<[^>]+>', '', temp_html).strip()
        
        final_description = text_content_from_html

        # حذف دقیق‌تر خط "Forwarded From" اگر در ابتدای متن باشد
        # این الگو سعی می‌کند "Forwarded From" و هر چیزی بعد از آن تا انتهای خط اول را پیدا کند
        match = re.match(r"^\s*Forwarded From[^\n]*(?:\n|$)", final_description, re.IGNORECASE)
        if match:
            # حذف بخش پیدا شده (هدر "Forwarded From") و گرفتن بقیه متن
            final_description = final_description[match.end():].strip()

        # حذف فضاهای خالی اضافی و محدود کردن طول توضیحات
        final_description = "\n".join([line.strip() for line in final_description.splitlines() if line.strip()]) # حذف خطوط خالی و strip کردن هر خط
        
        DESCRIPTION_MAX_LEN = 1000  # افزایش محدودیت طول توضیحات
        if len(final_description) > DESCRIPTION_MAX_LEN:
            final_description = final_description[:DESCRIPTION_MAX_LEN] + "..."
        elif not final_description: # اگر توضیحات پس از پاکسازی خالی شد
            final_description = ""


        # 3. مونتاژ پیام در بخش‌های مختلف
        message_parts = []

        # بخش عنوان
        if display_title: # اطمینان از اینکه عنوان خالی نیست
             message_parts.append(f"{RLM}📝 **{display_title}**")

        # بخش توضیحات
        if final_description:
            message_parts.append(f"\n\n{RLM}{final_description}") # دو خط جدید قبل از توضیحات

        # بخش اطلاعات متا (لینک، منبع، تاریخ) در یک بلوک جدا
        meta_info_parts = []
        if event.link:
            meta_info_parts.append(f"{RLM}🔗 [مشاهده کامل]({event.link})")
        
        if event.source_channel_username:
            meta_info_parts.append(f"{RLM}📢 **منبع:** [{event.source_channel}](https://t.me/{event.source_channel_username})")
        else:
            meta_info_parts.append(f"{RLM}📢 **منبع:** {event.source_channel}")

        if event.published:
            try:
                date_obj = datetime.strptime(event.published, "%a, %d %b %Y %H:%M:%S %Z")
                formatted_date = date_obj.strftime("%d %b %Y - %H:%M %Z")
                meta_info_parts.append(f"{RLM}📅 **انتشار:** {formatted_date}")
            except ValueError:
                # اگر فرمت تاریخ ورودی متفاوت بود، بخش ساده‌تری از آن را نمایش بده
                try:
                    # تلاش برای گرفتن بخش اصلی تاریخ بدون روز هفته و ثانیه اگر فرمت پیچیده بود
                    main_date_part = event.published.split(',')[1].strip() if ',' in event.published else event.published
                    meta_info_parts.append(f"{RLM}📅 **انتشار:** {main_date_part.rsplit(':',1)[0]} GMT")
                except:
                    meta_info_parts.append(f"{RLM}📅 **انتشار:** {event.published}") # نمایش به صورت خام در صورت بروز خطا


        if meta_info_parts:
            message_parts.append("\n\n" + "\n".join(meta_info_parts)) # دو خط جدید قبل از بلوک متا، و یک خط بین آیتم‌های متا

        # پیام نهایی با حذف فضاهای خالی احتمالی در ابتدا و انتها
        return "\n".join(filter(None,message_parts)).strip()

    async def publish_event(self, event: EventInfo):
        """Publish event to Telegram channel"""
        try:
            message = self.format_event_message(event)
            if not message: # اگر پیام خالی بود (مثلا توضیحات و عنوان مناسبی نداشت)
                logger.info(f"Skipping empty message for an event from {event.source_channel}.")
                return

            await self.bot.send_message(
                chat_id=self.target_channel,
                text=message,
                parse_mode='Markdown', # MarkdownV2 برای کنترل بیشتر بهتر است اما پیچیدگی بیشتری دارد
                disable_web_page_preview=False # اجازه به پیش‌نمایش لینک‌ها
            )
            logger.info(f"Published event: {event.title[:50]}... from {event.source_channel}")
        except Exception as e:
            logger.error(f"Failed to publish event ({event.title[:50]}...): {e}", exc_info=True)

    async def run_monitoring_loop(self):
        """Main monitoring loop"""
        logger.info("Starting RSS monitoring...")
        
        # اولین اجرا: کمی صبر کنید تا شبکه پایدار شود (اختیاری)
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
                # رویدادها را به ترتیب تاریخ انتشار مرتب کنید (اگر لازم است و تاریخ قابل اتکاست)
                # all_new_events.sort(key=lambda x: x.published_parsed_object_or_default, reverse=False)
                
                for event_to_publish in all_new_events:
                    await self.publish_event(event_to_publish)
                    await asyncio.sleep(3)  # فاصله بین ارسال پیام‌ها برای جلوگیری از محدودیت تلگرام
            else:
                logger.info("No new events found in this cycle.")

            # انتظار برای بررسی بعدی
            check_interval_seconds = 600 # 10 دقیقه
            logger.info(f"Next check in {check_interval_seconds // 60} minutes.")
            await asyncio.sleep(check_interval_seconds)


class Config:
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TARGET_CHANNEL = os.getenv('TARGET_CHANNEL') # مقدار پیش‌فرض را حذف کردم تا حتما از متغیر محیطی خوانده شود

async def health_check(request):
    """Health check endpoint for Hugging Face Spaces"""
    # می‌توانید اطلاعات بیشتری اینجا برگردانید، مثلا تعداد آیتم‌های پردازش شده
    return web.Response(text=f"Bot is running! Processed items in memory: {len(rss_bot.processed_items if 'rss_bot' in globals() else 0)}", status=200)

async def start_web_server():
    """Start web server for health checks"""
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check) # روت اصلی را هم به health check وصل می‌کنیم

    runner = web.AppRunner(app)
    await runner.setup()
    # پورت را از متغیر محیطی PORT بخوانید که توسط هاگینگ فیس اسپیس تنظیم می‌شود
    # اگر تنظیم نشده بود، از 7860 استفاده کن
    port = int(os.environ.get("PORT", "7860"))
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        logger.info(f"Web server started on port {port}")
    except OSError as e:
        logger.error(f"Failed to start web server on port {port}: {e}. Ensure the port is free or try another.")
        # اگر وب سرور حیاتی نیست، می‌توانید بدون آن ادامه دهید یا برنامه را متوقف کنید
        # raise # برای متوقف کردن برنامه

# گلوبال کردن ربات برای دسترسی در health_check
rss_bot: Optional[RSSTelegramBot] = None

async def main():
    global rss_bot # برای دسترسی به نمونه ربات در health_check

    if not Config.BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is required!")
        return
    if not Config.TARGET_CHANNEL:
        logger.error("TARGET_CHANNEL environment variable is required!")
        return
    
    logger.info("Application starting...")

    # Create bot instance
    rss_bot = RSSTelegramBot(
        bot_token=Config.BOT_TOKEN,
        target_channel=Config.TARGET_CHANNEL
    )

    try:
        # Test bot connection
        bot_info = await rss_bot.bot.get_me()
        logger.info(f"Bot started: @{bot_info.username}")

        # Start web server for health checks (همزمان با ربات)
        # وب سرور را در یک تسک جداگانه اجرا کنید تا حلقه اصلی ربات بلاک نشود
        web_server_task = asyncio.create_task(start_web_server())
        
        # Start monitoring
        await rss_bot.run_monitoring_loop()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt)")
    except Exception as e:
        logger.error(f"Critical bot error: {e}", exc_info=True)
    finally:
        logger.info("Bot shutting down...")
        if 'web_server_task' in locals() and not web_server_task.done():
            web_server_task.cancel() # در صورت اتمام کار، وب سرور را هم متوقف کنید
            try:
                await web_server_task
            except asyncio.CancelledError:
                logger.info("Web server task cancelled.")


if __name__ == "__main__":
    # برای مدیریت بهتر Ctrl+C در ویندوز و پایتون‌های جدیدتر
    # loop = asyncio.get_event_loop()
    # try:
    #     loop.run_until_complete(main())
    # except KeyboardInterrupt:
    #     logger.info("Application shutting down (KeyboardInterrupt caught in __main__)...")
    # finally:
    #     # می‌توانید اینجا کارهای پاکسازی نهایی را انجام دهید
    #     # tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    #     # [task.cancel() for task in tasks]
    #     # loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
    #     # loop.close()
    #     logger.info("Application fully stopped.")
    asyncio.run(main())
