import asyncio
import re
import logging
from datetime import datetime
import html # برای unescape اولیه اگر لازم باشد
import aiohttp
import feedparser
from telegram import Bot
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown # <--- برای MarkdownV2 escaping
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
    # (کد EventDetector بدون تغییر از پاسخ قبلی)
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
            # ... لیست فیدهای شما ...
            {'name': 'WinCell Co', 'url': 'https://rsshub.app/telegram/channel/wincellco', 'channel': 'wincellco'},
            {'name': 'Rayazistazma', 'url': 'https://rsshub.app/telegram/channel/Rayazistazma', 'channel': 'Rayazistazma'},
            {'name': 'SBU Bio Society', 'url': 'https://rsshub.app/telegram/channel/SBUBIOSOCIETY', 'channel': 'SBUBIOSOCIETY'},
            {'name': 'Test BioPy Channel', 'url': 'https://rsshub.app/telegram/channel/testbiopy', 'channel': 'testbiopy'}
        ]
        self.RLM = "\u200F"

    async def fetch_feed(self, session: aiohttp.ClientSession, feed_info: dict) -> List[EventInfo]:
        # (این متد بدون تغییر از پاسخ قبلی)
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

    def _escape_md_v2(self, text: str) -> str:
        """Wrapper for Telegram's MarkdownV2 escaper."""
        return escape_markdown(text, version=2)

    def _convert_node_to_markdown_v2_recursive(self, element, list_level=0) -> str:
        """به صورت بازگشتی گره BeautifulSoup را به رشته MarkdownV2 تبدیل می‌کند."""
        if isinstance(element, NavigableString):
            # متن‌ها باید escape شوند مگر اینکه داخل <pre> یا <code> باشند (که به صورت جداگانه مدیریت می‌شوند)
            parent_name = element.parent.name if element.parent else None
            if parent_name in ['pre', 'code']: # متن داخل code و pre نباید escape شود
                 return str(element) 
            return self._escape_md_v2(str(element))
        
        tag_name = element.name
        children_md = "".join(self._convert_node_to_markdown_v2_recursive(child, list_level + (1 if tag_name in ['ul', 'ol'] else 0)) for child in element.children)

        if tag_name in ['b', 'strong']: return f"*{children_md}*"
        if tag_name in ['i', 'em']: return f"_{children_md}_"
        if tag_name == 'u': return f"__{children_md}__" # تلگرام از __ برای underline استفاده می‌کند
        if tag_name in ['s', 'strike', 'del']: return f"~{children_md}~"
        
        if tag_name == 'code':
            # اگر code داخل pre بود، خود pre آن را مدیریت می‌کند
            if element.parent.name == 'pre': return children_md 
            return f"`{children_md}`" # برای کد inline (اطمینان از اینکه children_md حاوی ` نباشد سخت است)

        if tag_name == 'pre':
            # اگر داخل pre یک تگ code با کلاس زبان بود
            code_child = element.find('code', class_=lambda x: x and isinstance(x, list) and x[0].startswith('language-'))
            if code_child:
                lang = self._escape_md_v2(code_child['class'][0].split('-',1)[1])
                # محتوای کد (children_md اینجا محتوای code_child است) نباید escape شود
                # تابع بازگشتی برای محتوای code_child باید حالت خاصی داشته باشد.
                # برای سادگی، فرض می‌کنیم children_md از قبل متن خالص کد است.
                code_content = "".join(self._convert_node_to_markdown_v2_recursive(child) for child in code_child.children) # این باید متن خالص باشد
                return f"```{lang}\n{code_content.strip()}\n```" # متن کد escape نمی‌شود
            return f"```\n{children_md.strip()}\n```" # متن کد escape نمی‌شود

        if tag_name == 'a':
            href = element.get('href', '')
            # URL ها برای پرانتزهای Markdown نیازی به escape شدن ندارند مگر اینکه خودشان پرانتز داشته باشند.
            # متن لینک (children_md) قبلا escape شده است.
            if href and href.strip().lower().startswith(('http', 'tg://')):
                # برای جلوگیری از مشکلات با URLهایی که ) دارند، آنها را با %29 و %28 escape می‌کنیم
                safe_href = href.strip().replace('(', '%28').replace(')', '%29')
                return f"[{children_md}]({safe_href})"
            return children_md # اگر لینک معتبر نبود، فقط متن

        if tag_name == 'br': return '\n'
        
        # تگ‌های بلاک که نیاز به خط جدید قبل و بعد دارند
        if tag_name in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'table']:
            # اگر children_md با \n تمام نشده، اضافه کن. اگر با یک \n تمام شده، یکی دیگر اضافه کن.
            if not children_md.endswith("\n\n"):
                if children_md.endswith("\n"):
                    children_md += "\n"
                else:
                    children_md += "\n\n"
            return children_md
        
        if tag_name in ['ul', 'ol']: # برای لیست‌ها، هر فرزند li یک \n اضافه می‌کند
            return children_md # فقط محتوای فرزندان که آیتم‌های لیست هستند

        if tag_name == 'li':
            prefix = "• " # برای ul. برای ol باید شماره‌گذاری شود (پیچیده‌تر)
            # اگر داخل لیست تودرتو بودیم، کمی تورفتگی ایجاد کن (ساده‌سازی شده)
            indent = "  " * (list_level -1) if list_level > 0 else ""
            # اطمینان از اینکه با \n تمام می‌شود
            return f"{indent}{prefix}{children_md.strip()}\n"

        # اگر تگ ناشناخته بود، فقط محتوای فرزندان
        return children_md

    def _prepare_description_for_markdown_v2(self, html_content: str) -> str:
        if not html_content: return ""
        # ابتدا HTML entities را به کاراکترهای واقعی تبدیل کن (اگر لازم است)
        # html_content = html.unescape(html_content) # معمولا feedparser این کار را انجام می‌دهد
        soup = BeautifulSoup(html_content, "html.parser")

        first_p = soup.find('p', recursive=False)
        if first_p and first_p.get_text(strip=True).lower().startswith("forwarded from"):
            first_p.decompose()
        
        markdown_text = self._convert_node_to_markdown_v2_recursive(soup.body if soup.body else soup)
        
        # نرمال‌سازی نهایی خطوط جدید
        markdown_text = re.sub(r'\n\s*\n\s*\n+', '\n\n', markdown_text).strip()
        return markdown_text

    def format_event_message(self, event: EventInfo) -> str:
        # (منطق تشخیص و حذف عنوان تکراری مشابه قبل، اما روی متن ساده‌سازی شده از Markdown عمل می‌کند)
        # (تمام متن‌ها باید با _escape_md_v2 پردازش شوند قبل از قرارگیری در ساختار Markdown)
        
        display_title_text_unescaped = event.title.strip()
        display_title_md = self._escape_md_v2(display_title_text_unescaped)
        
        description_md = self._prepare_description_for_markdown_v2(event.description)
        
        # برای بررسی تکرار، از نسخه متنی ساده استفاده می‌کنیم
        temp_soup_for_plain_text_desc = BeautifulSoup(event.description, "html.parser") # از HTML اصلی
        first_p_temp = temp_soup_for_plain_text_desc.find('p', recursive=False)
        if first_p_temp and first_p_temp.get_text(strip=True).lower().startswith("forwarded from"):
            first_p_temp.decompose()
        description_plain_text_for_check = temp_soup_for_plain_text_desc.get_text(separator=' ', strip=True)

        show_separate_title = True
        if description_plain_text_for_check and display_title_text_unescaped:
            # ... (منطق نرمال‌سازی و مقایسه عنوان با خط اول توضیحات، مشابه قبل) ...
            leading_symbols_pattern = r"^[🔁🖼⚜️📝📢✔️✅🔆🗓️📍💳#٪♦️🔹🔸🟢♦️▪️▫️▪️•●🔘👁‍🗨\s]*(?=[^\s])"
            trailing_punctuation_pattern = r"[\s.:…]+$"
            def normalize_text(text):
                if not text: return ""
                text = re.sub(leading_symbols_pattern, "", text, flags=re.IGNORECASE).strip()
                text = re.sub(trailing_punctuation_pattern, "", text)
                return text.lower().strip()

            title_comp = normalize_text(display_title_text_unescaped)
            if title_comp:
                first_desc_line_plain = description_plain_text_for_check.split('\n', 1)[0].strip()
                first_desc_line_comp = normalize_text(first_desc_line_plain)
                if first_desc_line_comp and \
                   (title_comp == first_desc_line_comp or \
                   (len(title_comp) >= 8 and first_desc_line_comp.startswith(title_comp)) or \
                   (len(first_desc_line_comp) >= 8 and title_comp.startswith(first_desc_line_comp))):
                    logger.info(f"MDv2 - Title ('{display_title_text_unescaped}') considered part of description. Not showing separate title.")
                    show_separate_title = False


        # محدود کردن طول توضیحات (این کار روی Markdown پیچیده است، فعلا ساده انجام می‌دهیم)
        # اگر توضیحات Markdown خیلی طولانی شد، آن را به متن ساده تبدیل، کوتاه و دوباره escape می‌کنیم.
        # این کار فرمت‌بندی غنی را از دست می‌دهد.
        DESCRIPTION_MAX_LEN_PLAIN = 2800 
        if len(description_md) > DESCRIPTION_MAX_LEN_PLAIN + 500: # یک بافر برای کاراکترهای Markdown
            temp_soup = BeautifulSoup(event.description, "html.parser") # از HTML اصلی برای متن ساده استفاده کن
            # ... (منطق کوتاه کردن متن ساده مشابه قبل) ...
            plain_text_desc = temp_soup.get_text(separator=' ', strip=True) # یا از description_plain_text_for_check
            if len(plain_text_desc) > DESCRIPTION_MAX_LEN_PLAIN:
                # ... (منطق کوتاه کردن plain_text_desc) ...
                # description_md = self._escape_md_v2(کوتاه_شده_plain_text_desc)
                logger.warning(f"Description Markdown for '{display_title_text_unescaped}' might be too long. Truncation needed but complex.")
                # فعلا برای سادگی، اگر خیلی طولانی بود، به متن ساده کوتاه شده تبدیل می‌کنیم
                # این بهترین راه نیست.
                if len(description_plain_text_for_check) > DESCRIPTION_MAX_LEN_PLAIN:
                    cut_off_point = description_plain_text_for_check.rfind('.', 0, DESCRIPTION_MAX_LEN_PLAIN)
                    # ... (منطق کوتاه کردن مشابه قبل) ...
                    truncated_plain_text = description_plain_text_for_check[:DESCRIPTION_MAX_LEN_PLAIN] + "..."
                    description_md = self._escape_md_v2(truncated_plain_text)


        if not description_md.strip(): description_md = ""

        # مونتاژ پیام با MarkdownV2
        message_parts = []
        if show_separate_title and display_title_md:
             title_prefix = self.RLM if display_title_text_unescaped and not re.match(r"^\s*[\u0600-\u06FF]", display_title_text_unescaped) else ""
             message_parts.append(f"{title_prefix}*{display_title_md}*") # عنوان بولد

        if description_md:
            separator = "\n\n" if message_parts else ""
            message_parts.append(f"{separator}{description_md}") # توضیحات Markdown شده

        meta_info_parts = []
        if event.link:
            escaped_link_text = self._escape_md_v2("مشاهده کامل رویداد")
            # URL ها برای Markdown نیازی به escape شدن کاراکترهای خاص ندارند، مگر اینکه خودشان حاوی پرانتز باشند.
            safe_url = event.link.replace('(', '%28').replace(')', '%29')
            meta_info_parts.append(f"{self.RLM}🔗 [{escaped_link_text}]({safe_url})")
        
        source_text_escaped = self._escape_md_v2(event.source_channel)
        if event.source_channel_username:
            username_escaped = self._escape_md_v2(event.source_channel_username) # یوزرنیم نباید escape شود اگر در tg:// استفاده می‌شود
            tg_url = f"https://t.me/{event.source_channel_username}" # یوزرنیم برای URL نباید escape شود
            meta_info_parts.append(f"{self.RLM}📢 *{self._escape_md_v2('منبع:')}* [{source_text_escaped}]({tg_url})")
        else:
            meta_info_parts.append(f"{self.RLM}📢 *{self._escape_md_v2('منبع:')}* {source_text_escaped}")

        if event.published:
            # ... (منطق فرمت تاریخ مشابه قبل، اما متن نهایی باید escape شود) ...
            formatted_date_unescaped = event.published
            try:
                date_obj = datetime.strptime(event.published, "%a, %d %b %Y %H:%M:%S %Z")
                formatted_date_unescaped = date_obj.strftime("%d %b %Y - %H:%M (%Z)")
            except: pass
            if formatted_date_unescaped:
                 meta_info_parts.append(f"{self.RLM}📅 *{self._escape_md_v2('انتشار:')}* {self._escape_md_v2(formatted_date_unescaped)}")

        if meta_info_parts:
            separator_meta = "\n\n" if message_parts else "" 
            message_parts.append(separator_meta + "\n".join(meta_info_parts))

        final_message_md = "\n".join(message_parts).strip()
                                                          
        # محدودیت طول تلگرام
        # کوتاه کردن Markdown بدون شکستن فرمت بسیار سخت است
        if len(final_message_md) > 4096:
             logger.warning(f"MarkdownV2 Message for '{display_title_text_unescaped}' too long ({len(final_message_md)} chars).")
             # در اینجا باید استراتژی بهتری برای کوتاه کردن Markdown پیاده‌سازی شود.
             # فعلاً فقط هشدار می‌دهیم.

        return final_message_md

    async def publish_event(self, event: EventInfo):
        try:
            message_md = self.format_event_message(event)
            
            # بررسی اینکه آیا پیام واقعا خالی است
            # برای Markdown، نمی‌توان به سادگی با BeautifulSoup متن گرفت.
            # یک بررسی ساده‌تر: اگر فقط شامل کاراکترهای خاص Markdown یا RLM بود.
            # یا اگر متن اصلی (قبل از تبدیل به Markdown) خالی بود.
            is_message_effectively_empty = not message_md or \
                                          (not event.title and not self._prepare_description_for_markdown_v2(event.description).strip())

            if is_message_effectively_empty:
                logger.info(f"Skipping due to effectively empty MarkdownV2 message from {event.source_channel} (Title: {event.title[:30]}...).")
                return

            await self.bot.send_message(
                chat_id=self.target_channel, text=message_md,
                parse_mode=ParseMode.MARKDOWN_V2, # <--- تغییر به ParseMode.MARKDOWN_V2
                disable_web_page_preview=True 
            )
            logger.info(f"Published event (MarkdownV2): {event.title[:60]}... from {event.source_channel}")
        except Exception as e:
            logger.error(f"Failed to publish event ({event.title[:60]}...) using MarkdownV2 mode: {e}", exc_info=True)


    async def run_monitoring_loop(self):
        # (این متد بدون تغییر از پاسخ قبلی، با check_interval_seconds=180)
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
                    elif isinstance(result, Exception): logger.error(f"Feed task error: {result}", exc_info=True)
            
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
