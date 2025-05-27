import asyncio
import re
import logging
from datetime import datetime
import html # برای escape/unescape کردن HTML entities
import aiohttp
import feedparser
from telegram import Bot
from telegram.constants import ParseMode
import os
from dataclasses import dataclass
from typing import List, Optional
from bs4 import BeautifulSoup, NavigableString, Tag
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
    # (کد EventDetector بدون تغییر از پاسخ قبلی باقی می‌ماند)
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
        
        title_lower = title.lower()
        strong_title_keywords = ['وبینار', 'کارگاه', 'سمینار', 'دوره', 'کنفرانس', 'همایش', 'نشست آموزشی', 'کلاس آنلاین']
        if any(keyword in title_lower for keyword in strong_title_keywords):
            return True

        full_text_desc_lower = text_only_description.lower()
        combined_text_lower = f"{title_lower} {full_text_desc_lower}"
        matches = sum(1 for keyword in self.EVENT_KEYWORDS if keyword in combined_text_lower)
        
        has_specific_pattern = any([
            'ثبت نام' in combined_text_lower, 'شرکت در' in combined_text_lower,
            'لینک ثبت نام' in combined_text_lower, 'جهت ثبت نام' in combined_text_lower,
            'هزینه دوره' in full_text_desc_lower, 'سرفصل های دوره' in full_text_desc_lower,
            'مدرس دوره' in full_text_desc_lower, 'اطلاعات بیشتر و ثبت نام' in full_text_desc_lower,
            'register for' in combined_text_lower, 'join this' in combined_text_lower
        ])
        
        title_has_keyword = any(keyword in title_lower for keyword in ['آموزش', 'فراخوان', 'لایو'])
        return title_has_keyword or has_specific_pattern or matches >= 2

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
        self.ALLOWED_TAGS_TELEGRAM = { # تگ‌های HTML مجاز تلگرام
            'b': [], 'strong': [], 'i': [], 'em': [], 'u': [], 's': [], 
            'strike': [], 'del': [], 'code': [], 'pre': [], 
            'a': ['href'], 'span': ['class'] # span فقط برای tg-spoiler
        }
        self.RLM = "\u200F" # Right-to-Left Mark

    async def fetch_feed(self, session: aiohttp.ClientSession, feed_info: dict) -> List[EventInfo]:
        # (این متد بدون تغییر از پاسخ قبلی باقی می‌ماند)
        events = []
        feed_url, feed_name = feed_info['url'], feed_info['name']
        logger.info(f"Fetching feed: {feed_name} from {feed_url}")
        try:
            headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
            async with session.get(feed_url, timeout=45, headers=headers) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = feedparser.parse(content)
                    logger.info(f"Fetched {feed_name}. Entries: {len(feed.entries)}. LastBuildDate: {feed.feed.get('updated', feed.feed.get('published', 'N/A'))}")
                    for entry in feed.entries[:7]: 
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

    def _sanitize_href(self, url: Optional[str]) -> Optional[str]:
        if url:
            url = url.strip()
            if url.lower().startswith(('http://', 'https://', 'mailto:', 'tg://')):
                return html.escape(url, quote=True)
        return None

    def _recursive_html_to_telegram_html(self, element) -> str:
        """ گره BeautifulSoup را به رشته HTML امن برای تلگرام (با حفظ تگ‌های مجاز) تبدیل می‌کند. """
        if isinstance(element, NavigableString):
            return html.escape(str(element))
        
        if element.name == 'br':
            return '\n'

        # پردازش فرزندان ابتدا
        children_html = "".join(self._recursive_html_to_telegram_html(child) for child in element.children)

        tag_name = element.name
        if tag_name in self.ALLOWED_TAGS_TELEGRAM:
            attrs = {}
            if tag_name == 'a':
                href = self._sanitize_href(element.get('href'))
                if not href: return children_html # اگر لینک معتبر نبود، فقط محتوا
                attrs['href'] = href
            elif tag_name == 'span':
                if element.get('class') == ['tg-spoiler']:
                    attrs['class'] = 'tg-spoiler'
                else: return children_html # اسپویلر نبود، فقط محتوا
            elif tag_name == 'pre': # برای pre، اگر code با کلاس زبان داشت
                code_child = element.find('code', class_=lambda x: x and isinstance(x, list) and x[0].startswith('language-'))
                if code_child:
                    lang = html.escape(code_child['class'][0].split('-',1)[1], quote=True)
                    # محتوای کد از قبل توسط فراخوانی بازگشتی escape شده است
                    # این بخش نیاز به دقت دارد تا مطمئن شویم محتوای code_child فقط متن escape شده است
                    # children_html در اینجا باید محتوای داخل <code> باشد.
                    return f'<pre><code class="language-{lang}">{children_html}</code></pre>'
                return f"<pre>{children_html}</pre>" # pre ساده
            elif tag_name == 'code' and element.parent.name == 'pre':
                return children_html # محتوای code داخل pre، خود pre تگ را می‌سازد

            # ساخت رشته اتریبیوت‌ها
            attrs_str = "".join([f' {k}="{v}"' for k, v in attrs.items()])
            return f"<{tag_name}{attrs_str}>{children_html}</{tag_name}>"
        
        # اگر تگ در لیست مجاز نبود، فقط محتوای فرزندان (حذف تگ)
        return children_html

    def _prepare_description_telegram_html(self, html_content: str) -> str:
        if not html_content: return ""
        soup = BeautifulSoup(html_content, "html.parser")

        # 1. حذف بخش "Forwarded From"
        first_p = soup.find('p', recursive=False) # فقط پاراگراف سطح اول
        if first_p and first_p.get_text(strip=True).lower().startswith("forwarded from"):
            first_p.decompose()
        
        # 2. تبدیل محتوای اصلی به HTML سازگار با تلگرام
        # ما به جای پیمایش دستی تگ‌های بلاک، کل محتوای soup (پس از حذف احتمالی Forwarded From)
        # را به تابع بازگشتی می‌دهیم. تابع بازگشتی <br> را به \n تبدیل می‌کند.
        # سپس نرمال‌سازی خطوط جدید برای ایجاد فاصله‌های پاراگرافی انجام می‌شود.
        
        processed_html_parts = []
        # اگر soup.body وجود داشت و فرزند داشت، از آن استفاده کن، در غیر اینصورت از خود soup
        container_to_process = soup.body if soup.body and soup.body.contents else soup
        
        for element in container_to_process.children: # پیمایش فرزندان سطح اول کانتینر
            html_part = self._recursive_html_to_telegram_html(element)
            # اگر خود element یک بلاک اصلی بود (مثل p, div)، بعدش یک \n\n اضافه می‌کنیم
            # این برای حفظ فاصله‌های پاراگرافی است، چون تلگرام <p> را برای فاصله نمی‌شناسد.
            if element.name in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'blockquote', 'hr', 'table']:
                # اگر html_part با \n تمام نشده بود، یک \n اضافه کن (برای جداسازی از بلاک بعدی)
                # و چون بلاک است، یک \n دیگر برای ایجاد پاراگراف
                stripped_part = html_part.rstrip('\n')
                if stripped_part: # فقط اگر محتوا داشت
                    processed_html_parts.append(stripped_part + "\n\n")
            elif html_part.strip(): # اگر inline بود یا فقط متن
                processed_html_parts.append(html_part)
        
        final_html = "".join(processed_html_parts)
        
        # نرمال‌سازی خطوط جدید: بیش از دو \n متوالی را به دو \n تبدیل کن
        final_html = re.sub(r'\n\s*\n\s*\n+', '\n\n', final_html).strip()
        return final_html

    def format_event_message(self, event: EventInfo) -> str:
        display_title_text = event.title.strip() # عنوان به صورت متن ساده
        
        # توضیحات به فرمت HTML سازگار با تلگرام تبدیل می‌شود
        description_telegram_html = self._prepare_description_telegram_html(event.description)
        
        # برای بررسی تکرار، یک نسخه متنی ساده از توضیحات HTML شده لازم داریم
        temp_soup_for_plain_text = BeautifulSoup(description_telegram_html, "html.parser")
        description_plain_text_for_check = temp_soup_for_plain_text.get_text(separator=' ', strip=True)

        show_separate_title = True # به طور پیش‌فرض عنوان نمایش داده می‌شود

        if description_plain_text_for_check and display_title_text:
            leading_symbols_pattern = r"^[🔁🖼⚜️📝📢✔️✅🔆🗓️📍💳#٪♦️🔹🔸🟢♦️▪️▫️▪️•●🔘👁‍🗨\s]*(?=[^\s])"
            trailing_punctuation_pattern = r"[\s.:…]+$"
            def normalize_text(text):
                if not text: return ""
                text = re.sub(leading_symbols_pattern, "", text, flags=re.IGNORECASE).strip()
                text = re.sub(trailing_punctuation_pattern, "", text)
                return text.lower().strip()

            title_comp = normalize_text(display_title_text)
            if title_comp:
                first_desc_line_plain = description_plain_text_for_check.split('\n', 1)[0].strip()
                first_desc_line_comp = normalize_text(first_desc_line_plain)

                if first_desc_line_comp:
                    # اگر عنوان نرمال‌شده با خط اول توضیحات نرمال‌شده یکی بود، یا شباهت زیادی داشت
                    if (title_comp == first_desc_line_comp) or \
                       (len(title_comp) > 10 and first_desc_line_comp.startswith(title_comp)) or \
                       (len(first_desc_line_comp) > 10 and title_comp.startswith(first_desc_line_comp)):
                        logger.info(f"Title ('{display_title_text}') considered part of description. Not showing separate title.")
                        show_separate_title = False # عنوان جداگانه نمایش داده نشود

        # محدود کردن طول توضیحات HTML (بسیار سخت است که بدون شکستن HTML انجام شود)
        # فعلا این بخش را ساده نگه می‌داریم و فقط هشدار می‌دهیم اگر خیلی طولانی بود
        # DESCRIPTION_MAX_LEN_HTML = 3800 (در پاسخ قبلی بود، فعلا حذف شد تا تست کنیم)
        
        if not description_telegram_html.strip(): # اگر توضیحات خالی شد
            description_telegram_html = ""

        # مونتاژ پیام با HTML
        message_parts = []
        if show_separate_title and display_title_text:
             # RLM برای عنوان اگر با کاراکتر LTR شروع شود
             title_prefix = self.RLM if display_title_text and not re.match(r"^\s*[\u0600-\u06FF]", display_title_text) else ""
             message_parts.append(f"{title_prefix}<b>{html.escape(display_title_text)}</b>")

        if description_telegram_html:
            separator = "\n\n" if message_parts else "" # اگر عنوان نمایش داده شده بود، دو خط فاصله
            # خود description_telegram_html باید شامل RLM های لازم باشد اگر از _recursive_html_to_telegram_html می‌آید
            # یا اینکه در ابتدای هر پاراگراف اصلی (که با \n\n جدا شده) RLM بگذاریم
            # فعلا RLM کلی برای توضیحات نمی‌گذاریم، به HTML تولید شده اتکا می‌کنیم
            message_parts.append(f"{separator}{description_telegram_html}")

        meta_info_parts = []
        escaped_link_text = html.escape("مشاهده کامل رویداد")
        if event.link:
            meta_info_parts.append(f"{self.RLM}🔗 <a href=\"{self._sanitize_href(event.link)}\">{escaped_link_text}</a>")
        
        escaped_source_label = html.escape("منبع:")
        escaped_source_name = html.escape(event.source_channel)
        if event.source_channel_username:
            escaped_username = html.escape(event.source_channel_username)
            meta_info_parts.append(f"{self.RLM}📢 <b>{escaped_source_label}</b> <a href=\"https://t.me/{escaped_username}\">{escaped_source_name}</a>")
        else:
            meta_info_parts.append(f"{self.RLM}📢 <b>{escaped_source_label}</b> {escaped_source_name}")

        if event.published:
            formatted_date = event.published
            try:
                date_obj = datetime.strptime(event.published, "%a, %d %b %Y %H:%M:%S %Z")
                formatted_date = date_obj.strftime("%d %b %Y - %H:%M (%Z)")
            except: pass 
            if formatted_date:
                 meta_info_parts.append(f"{self.RLM}📅 <b>{html.escape('انتشار:')}</b> {html.escape(formatted_date)}")

        if meta_info_parts:
            separator_meta = "\n\n" if message_parts else "" 
            message_parts.append(separator_meta + "\n".join(meta_info_parts))

        final_message_html = "\n".join(message_parts).strip()
                                                          
        TELEGRAM_MSG_MAX_LEN = 4096
        if len(final_message_html) > TELEGRAM_MSG_MAX_LEN:
            logger.warning(f"HTML Message for '{display_title_text}' too long ({len(final_message_html)} chars). Telegram might truncate or reject.")
            # راه حل ساده: کوتاه کردن از انتها (ممکن است HTML را بشکند)
            # final_message_html = final_message_html[:TELEGRAM_MSG_MAX_LEN - 20] + "..." # این خطرناک است برای HTML
            # بهتر است از ابتدا توضیحات را کوتاه کنیم اگر لازم شد.
            
        return final_message_html

    async def publish_event(self, event: EventInfo):
        # (این متد همانند آخرین نسخه کامل ارائه شده باقی می‌ماند، فقط parse_mode='HTML' می‌شود)
        try:
            message_html = self.format_event_message(event)
            
            # بررسی اینکه آیا پیام واقعا خالی است (حتی اگر فقط شامل تگ‌های خالی یا RLM باشد)
            temp_soup = BeautifulSoup(message_html, "html.parser")
            is_message_effectively_empty = not temp_soup.get_text(strip=True)

            if is_message_effectively_empty :
                logger.info(f"Skipping due to effectively empty HTML message for event from {event.source_channel} (Title: {event.title[:30]}...).")
                return

            await self.bot.send_message(
                chat_id=self.target_channel, text=message_html,
                parse_mode=ParseMode.HTML, # <--- تغییر به ParseMode.HTML
                disable_web_page_preview=True 
            )
            logger.info(f"Published event (HTML): {event.title[:60]}... from {event.source_channel}")
        except Exception as e:
            logger.error(f"Failed to publish event ({event.title[:60]}...) using HTML mode: {e}", exc_info=True)

    async def run_monitoring_loop(self):
        # (این متد همانند آخرین نسخه کامل ارائه شده باقی می‌ماند، با check_interval_seconds=180)
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
            
            check_interval_seconds = 180 
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
