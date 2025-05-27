import asyncio
import re
import logging
from datetime import datetime
import html # برای unescape اولیه اگر لازم باشد
import aiohttp
import feedparser
from telegram import Bot
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown # برای MarkdownV2 escaping
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
    title: str # همچنان برای تشخیص رویداد و لاگ‌ها لازم است
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
        # (منطق تشخیص رویداد از پاسخ قبلی، که به نظر شما خوب کار می‌کرد)
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
                else:
                    logger.error(f"Error fetching {feed_name}: Status {response.status} - {await response.text(encoding='utf-8', errors='ignore')}")
        except Exception as e:
            logger.error(f"Exception in fetch_feed for {feed_name} ({feed_url}): {e}", exc_info=True)
        return events

    def _escape_md_v2(self, text: Optional[str]) -> str:
        if text is None: return ""
        return escape_markdown(str(text), version=2)

    def _convert_node_to_markdown_v2_recursive(self, element, list_level=0, inside_pre=False) -> str:
        if isinstance(element, NavigableString):
            # متن‌ها باید escape شوند مگر اینکه داخل <pre> باشند
            if inside_pre:
                return str(element) # محتوای داخل <pre> نباید escape شود
            return self._escape_md_v2(str(element))
        
        tag_name = element.name
        
        # تعیین اینکه آیا فرزندان این تگ هم باید در حالت inside_pre پردازش شوند
        children_inside_pre = inside_pre or (tag_name == 'pre')

        children_md_parts = [self._convert_node_to_markdown_v2_recursive(child, list_level + (1 if tag_name in ['ul', 'ol'] else 0), children_inside_pre) for child in element.children]
        children_md = "".join(children_md_parts)

        if tag_name in ['b', 'strong']: return f"*{children_md}*"
        if tag_name in ['i', 'em']: return f"_{children_md}_"
        if tag_name == 'u': return f"__{children_md}__"
        if tag_name in ['s', 'strike', 'del']: return f"~{children_md}~"
        
        if tag_name == 'code':
            if element.parent.name == 'pre': # اگر code داخل pre بود
                return children_md # خود pre فرمت ``` را می‌سازد، اینجا فقط محتوای خام
            # برای کد inline، محتوا نباید شامل ` باشد یا باید مدیریت شود
            # تلگرام از دو بک‌تیک برای escape کردن بک‌تیک داخل کد inline پشتیبانی نمی‌کند.
            # پس اگر بک‌تیک داشت، نمایش آن در کد inline دشوار است. فعلا ساده نگه می‌داریم.
            return f"`{children_md}`" 

        if tag_name == 'pre':
            code_child = element.find('code', class_=lambda x: x and isinstance(x, list) and x[0].startswith('language-'))
            # children_md در اینجا محتوای خام (escape نشده) داخل <pre> است
            if code_child:
                lang = self._escape_md_v2(code_child['class'][0].split('-',1)[1])
                # محتوای کد را از خود code_child بگیریم چون children_md شامل تگ <code> هم هست
                # و باید متن خام داخل <code> را داشته باشیم
                pre_content = "".join(self._convert_node_to_markdown_v2_recursive(c, 0, True) for c in code_child.children)
                return f"```{lang}\n{pre_content.strip()}\n```"
            return f"```\n{children_md.strip()}\n```"

        if tag_name == 'a':
            href = element.get('href', '')
            if href and href.strip().lower().startswith(('http', 'tg://')):
                safe_href = href.strip().replace('(', '%28').replace(')', '%29')
                # children_md (متن لینک) از قبل escape شده است
                return f"[{children_md}]({safe_href})"
            return children_md 

        if tag_name == 'br': return '\n'
        
        # تگ‌های بلاک که نیاز به خط جدید قبل و بعد دارند
        if tag_name in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'table', 'figure']:
            # اگر children_md با \n تمام نشده، دو تا اضافه کن. اگر با یک \n تمام شده، یکی دیگر اضافه کن.
            processed_content = children_md.strip('\n') # \n های اضافی از انتهای محتوای داخلی حذف شوند
            if processed_content: # فقط اگر محتوا داشت
                return f"{processed_content}\n\n"
            return "\n\n" # اگر بلاک خالی بود هم یک فاصله پاراگراف ایجاد کند (یا می‌توان این را حذف کرد)
        
        if tag_name == 'ul': return children_md 
        if tag_name == 'ol': return children_md # شماره‌گذاری اتوماتیک Markdown برای ol پیچیده است، فعلا شبیه ul

        if tag_name == 'li':
            prefix = f"{self.RLM}• " 
            # اطمینان از اینکه با \n تمام می‌شود و فضاهای خالی اضافی ندارد
            return f"{prefix}{children_md.strip()}\n"

        return children_md # برای تگ‌های ناشناخته، فقط محتوای فرزندان

    def _prepare_description_for_markdown_v2(self, html_content: str) -> str:
        if not html_content: return ""
        soup = BeautifulSoup(html_content, "html.parser")

        first_p = soup.find('p', recursive=False)
        if first_p and first_p.get_text(strip=True).lower().startswith("forwarded from"):
            first_p.decompose()
        
        markdown_text = self._convert_node_to_markdown_v2_recursive(soup.body if soup.body else soup)
        
        # نرمال‌سازی نهایی خطوط جدید
        markdown_text = markdown_text.replace('\r\n', '\n').replace('\r', '\n')
        markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text) # بیش از دو \n متوالی به دو \n
        markdown_text = markdown_text.strip() # حذف فضاهای خالی و \n از ابتدا و انتهای کل متن
        
        # برای اطمینان از اینکه خطوطی که فقط شامل RLM هستند حذف شوند
        lines = markdown_text.splitlines()
        cleaned_lines = []
        for line in lines:
            stripped_line = line.strip()
            if stripped_line and stripped_line != self.RLM:
                cleaned_lines.append(stripped_line)
            elif not stripped_line and cleaned_lines and cleaned_lines[-1]: # برای حفظ یک خط خالی بین پاراگراف‌ها
                 cleaned_lines.append("")


        final_text = "\n".join(cleaned_lines)
        # ممکن است هنوز \n\n در انتهای متن باشد اگر آخرین بلاک \n\n اضافه کرده باشد
        return final_text.strip()


    def format_event_message(self, event: EventInfo) -> str:
        description_md = self._prepare_description_for_markdown_v2(event.description)
        
        if not description_md.strip(): # اگر توضیحات (که حالا شامل عنوان هم هست) خالی بود
            logger.info(f"MarkdownV2 description is empty for event (Original title: {event.title[:30]}...). Skipping.")
            return ""

        # RLM در ابتدای کل پیام اگر با کاراکتر LTR شروع شود
        # خود description_md باید شامل RLM های لازم برای خطوط داخلی باشد (مثلا برای آیتم های لیست)
        message_prefix = self.RLM if description_md and not re.match(r"^\s*[\u0600-\u06FF*_[~`#]", description_md) else ""
        message_parts = [f"{message_prefix}{description_md}"]

        meta_info_parts = []
        if event.link:
            escaped_link_text = self._escape_md_v2("مشاهده کامل رویداد")
            safe_url = event.link.replace('(', '%28').replace(')', '%29')
            meta_info_parts.append(f"{self.RLM}🔗 [{escaped_link_text}]({safe_url})")
        
        source_text_escaped = self._escape_md_v2(event.source_channel)
        if event.source_channel_username:
            tg_url = f"[https://t.me/](https://t.me/){event.source_channel_username}"
            meta_info_parts.append(f"{self.RLM}📢 *{self._escape_md_v2('منبع:')}* [{source_text_escaped}]({tg_url})")
        else:
            meta_info_parts.append(f"{self.RLM}📢 *{self._escape_md_v2('منبع:')}* {source_text_escaped}")

        if event.published:
            formatted_date_unescaped = event.published
            try:
                date_obj = datetime.strptime(event.published, "%a, %d %b %Y %H:%M:%S %Z")
                formatted_date_unescaped = date_obj.strftime("%d %b %Y - %H:%M (%Z)")
            except: pass
            if formatted_date_unescaped:
                 meta_info_parts.append(f"{self.RLM}📅 *{self._escape_md_v2('انتشار:')}* {self._escape_md_v2(formatted_date_unescaped)}")

        if meta_info_parts:
            message_parts.append("\n\n" + "\n".join(meta_info_parts)) # همیشه دو خط جدید قبل از بخش متا

        final_message_md = "\n".join(message_parts).strip()
                                                          
        if len(final_message_md) > 4096:
             logger.warning(f"MarkdownV2 Message (Original title: '{event.title[:30]}') too long ({len(final_message_md)} chars).")
        return final_message_md

    async def publish_event(self, event: EventInfo):
        # (این متد بدون تغییر از پاسخ قبلی)
        try:
            message_md = self.format_event_message(event)
            if not message_md:
                logger.info(f"Skipping due to formatted message being empty for event from {event.source_channel} (Original Title: {event.title[:30]}...).")
                return

            await self.bot.send_message(
                chat_id=self.target_channel, text=message_md,
                parse_mode=ParseMode.MARKDOWN_V2,
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
