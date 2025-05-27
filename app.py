import asyncio
import re
import logging
from datetime import datetime
import html # برای escape/unescape کردن HTML entities
import aiohttp
import feedparser
from telegram import Bot
from telegram.constants import ParseMode # ParseMode را اضافه می‌کنیم
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple
from bs4 import BeautifulSoup, NavigableString, Tag # BeautifulSoup و اجزایش را اضافه می‌کنیم
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
    # (کد EventDetector بدون تغییر از پاسخ قبلی که شامل BeautifulSoup برای استخراج متن بود، باقی می‌ماند)
    # ... (کد EventDetector از پاسخ قبلی را اینجا کپی کنید) ...
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
        # تگ‌های HTML مجاز در تلگرام و اتریبیوت‌های مجاز برای تگ <a>
        self.ALLOWED_TAGS = {
            'b': [], 'strong': [], 'i': [], 'em': [], 'u': [], 's': [], 
            'strike': [], 'del': [], 'code': [], 'pre': [], 
            'a': ['href'], 'span': ['class'] # span فقط برای tg-spoiler
        }
        self.RLM = "\u200F"


    async def fetch_feed(self, session: aiohttp.ClientSession, feed_info: dict) -> List[EventInfo]:
        # (این متد بدون تغییر نسبت به آخرین نسخه کامل ارائه شده باقی می‌ماند)
        # ... (کد fetch_feed از پاسخ قبلی را اینجا کپی کنید) ...
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

    def _sanitize_href(self, url: Optional[str]) -> str:
        if url:
            url = url.strip()
            # فقط URL های امن را اجازه بده
            if url.lower().startswith(('http://', 'https://', 'mailto:', 'tg://')):
                return html.escape(url, quote=True) # escape کردن برای مقدار اتریبیوت
        return ""

    def _convert_node_to_telegram_html(self, node) -> str:
        """به صورت بازگشتی یک گره BeautifulSoup را به رشته HTML امن برای تلگرام تبدیل می‌کند."""
        if isinstance(node, NavigableString):
            return html.escape(str(node)) # متن‌ها باید escape شوند

        if node.name == 'br':
            return '\n' # <br> به خط جدید تبدیل می‌شود

        # مدیریت تگ‌های بلاک اصلی برای ایجاد فاصله پاراگراف
        # تلگرام تگ <p> را به طور خاص برای فاصله نمی‌شناسد، باید از \n\n استفاده کنیم
        # این تابع فقط محتوای داخلی را برمی‌گرداند، فاصله‌گذاری بین بلاک‌ها باید در سطح بالاتر انجام شود
        # یا اینکه در اینجا بعد از هر بلاک \n\n اضافه کنیم و سپس نرمال‌سازی کنیم.

        # پردازش فرزندان
        children_html = "".join(self._convert_node_to_telegram_html(child) for child in node.children)

        tag_name = node.name
        if tag_name in self.ALLOWED_TAGS:
            attrs_str = ""
            if tag_name == 'a':
                href = self._sanitize_href(node.get('href'))
                if href:
                    attrs_str = f' href="{href}"'
                else: # اگر لینک معتبر نبود، تگ a را نادیده بگیر و فقط محتوایش را برگردان
                    return children_html 
            elif tag_name == 'span':
                # فقط اسپویلر تلگرام مجاز است
                if node.get('class') == ['tg-spoiler']:
                    attrs_str = ' class="tg-spoiler"'
                else: # سایر span ها نادیده گرفته می‌شوند
                    return children_html
            elif tag_name == 'pre':
                 # برای <pre>، اگر داخلش <code> با کلاس زبان بود، آن را هم در نظر بگیر
                code_child = node.find('code', class_=lambda x: x and isinstance(x, list) and x[0].startswith('language-'))
                if code_child:
                    lang = html.escape(code_child['class'][0].split('-',1)[1], quote=True)
                    # محتوای کد باید escape شود. children_html از قبل این کار را برای محتوای code_child انجام داده
                    return f'<pre><code class="language-{lang}">{children_html}</code></pre>'
                # اگر کد ساده بود یا بدون کلاس زبان
                return f"<pre>{children_html}</pre>"
            elif tag_name == 'code' and node.parent.name == 'pre':
                 # اگر code داخل pre بود، خود pre تگ را می‌سازد، اینجا فقط محتوا را برگردان
                 return children_html


            # برای سایر تگ‌های مجاز مانند b, i, u, s
            return f"<{tag_name}{attrs_str}>{children_html}</{tag_name}>"
        
        # اگر تگ مجاز نبود، فقط محتوای فرزندانش را برگردان (unwrap)
        return children_html

    def _prepare_description_telegram_html(self, html_content: str) -> str:
        if not html_content:
            return ""
        soup = BeautifulSoup(html_content, "html.parser")

        # 1. حذف بخش "Forwarded From"
        first_p = soup.find('p')
        if first_p and first_p.get_text(strip=True).lower().startswith("forwarded from"):
            first_p.decompose()
        
        # 2. تبدیل کل محتوای پاکسازی شده به HTML سازگار با تلگرام
        # این کار را با پیمایش فرزندان body یا خود soup انجام می‌دهیم
        # و برای هر بلاک اصلی (مثل <p> های سابق) یک \n\n اضافه می‌کنیم.
        
        parts = []
        # به جای پیمایش تگ‌های خاص، کل بدنه را به تابع بازگشتی می‌دهیم
        # و سپس \n ها را نرمال‌سازی می‌کنیم.
        # این روش به حفظ ساختار درختی و تودرتو کمک می‌کند.
        
        # پاکسازی اولیه برای تبدیل ساختارهای بلاک به همراه \n
        # این بخش نیاز به دقت زیادی دارد تا ساختار حفظ شود
        # برای مثال، بعد از هر تگ p یا div که در ریشه قرار دارد، دو خط جدید اضافه می‌کنیم.
        body_content_tags = soup.find_all(['p', 'div'], recursive=False) # فقط تگ‌های سطح اول
        if not body_content_tags and soup.body: # اگر تگ body بود ولی p, div مستقیم نداشت
            body_content_tags = soup.body.contents
        elif not body_content_tags: # اگر body هم نبود، از خود soup شروع کن
             body_content_tags = soup.contents

        for element in body_content_tags:
            converted_html_part = self._convert_node_to_telegram_html(element)
            if converted_html_part.strip(): # فقط اگر بخشی محتوا داشت
                parts.append(converted_html_part)

        # اتصال بخش‌های تبدیل شده با دو خط جدید (برای ایجاد پاراگراف)
        # این ممکن است بیش از حد \n\n ایجاد کند اگر خود _convert_node_to_telegram_html هم \n گذاشته باشد.
        # بهتر است _convert_node_to_telegram_html فقط تگ‌ها را تبدیل کند و \n ها را اینجا مدیریت کنیم.
        # فعلا با \n ساده join می‌کنیم و سپس نرمال‌سازی می‌کنیم.
        
        final_html = "\n".join(parts).strip()
        
        # نرمال‌سازی خطوط جدید: بیش از دو \n متوالی را به دو \n تبدیل کن
        final_html = re.sub(r'\n\s*\n\s*\n+', '\n\n', final_html)
        # حذف خطوط خالی که ممکن است فقط شامل RLM باشند
        final_html = "\n".join(line for line in final_html.splitlines() if line.strip() != self.RLM and line.strip())

        return final_html.strip()


    def format_event_message(self, event: EventInfo) -> str:
        display_title = event.title.strip()
        
        # تبدیل توضیحات به HTML سازگار با تلگرام
        description_telegram_html = self._prepare_description_telegram_html(event.description)
        
        # برای بررسی تکرار، یک نسخه متنی از توضیحات و عنوان نیاز داریم
        temp_soup_desc = BeautifulSoup(description_telegram_html, "html.parser") # از HTML تبدیل شده، متن بگیر
        description_plain_text_for_check = temp_soup_desc.get_text(separator=' ', strip=True)

        description_to_display_html = description_telegram_html
        
        if description_plain_text_for_check and display_title:
            leading_symbols_pattern = r"^[🔁🖼⚜️📝📢✔️✅🔆🗓️📍💳#٪♦️🔹🔸🟢♦️▪️▫️▪️•●🔘👁‍🗨\s]*(?=[^\s])"
            trailing_punctuation_pattern = r"[\s.:…]+$"
            def normalize_text(text):
                if not text: return ""
                text = re.sub(leading_symbols_pattern, "", text, flags=re.IGNORECASE).strip()
                text = re.sub(trailing_punctuation_pattern, "", text)
                return text.lower().strip()

            title_comp = normalize_text(display_title)
            if title_comp:
                first_desc_line_plain = description_plain_text_for_check.split('\n', 1)[0].strip()
                first_desc_line_comp = normalize_text(first_desc_line_plain)

                if first_desc_line_comp and \
                   (title_comp == first_desc_line_comp or \
                   (len(title_comp) >= 8 and first_desc_line_comp.startswith(title_comp)) or \
                   (len(first_desc_line_comp) >= 8 and title_comp.startswith(first_desc_line_comp))):
                    
                    logger.info(f"HTML MODE - Title ('{display_title}') considered redundant with first line of desc ('{first_desc_line_plain}'). Adjusting.")
                    # برای HTML، حذف خط اول کمی پیچیده‌تر است.
                    # ساده‌ترین کار این است که اگر تکرار بود، کل توضیحات را نمایش ندهیم اگر خیلی کوتاه بود،
                    # یا اگر طولانی بود، سعی کنیم بخش اول را حذف کنیم (که ساده نیست در HTML)
                    # فعلا اگر تکرار بود و توضیحات فقط همان خط بود، خالی‌اش می‌کنیم.
                    # این بخش نیاز به بهبود دارد: چگونه یک "خط" را از رشته HTML حذف کنیم.
                    # برای سادگی، اگر خط اول توضیحات (به صورت متنی) با عنوان یکی بود، کل توضیحات HTML را نگه می‌داریم
                    # و صرفا عنوان جداگانه را نمایش نمی‌دهیم یا باید راهی برای حذف بخش اول HTML پیدا کنیم.
                    # فعلا، اگر تکرار بود، همان منطق قبلی (حذف خط اول از نسخه متنی و سپس فرمت مجدد) را نمی‌توان به سادگی روی HTML اعمال کرد.
                    # پس، اگر تکرار بود، عنوان را نمایش می‌دهیم و توضیحات را هم کامل می‌آوریم، یا سعی می‌کنیم از توضیحات کم کنیم.
                    # این بخش نیاز به بازنگری اساسی دارد اگر بخواهیم بخش اول یک رشته HTML را حذف کنیم.
                    # برای این نسخه، اگر تکرار بود، فقط لاگ می‌گیریم و هر دو را نمایش می‌دهیم، چون حذف از HTML پیچیده است.
                    # یا اینکه، اگر عنوان تکراری بود، خود عنوان را حذف کنیم و بگذاریم توضیحات شامل آن باشد.
                    # **تصمیم فعلی: اگر تکرار بود، خط اول را از description_telegram_html (اگر با <br> جدا شده بود) حذف می‌کنیم.**
                    # این کار بسیار تقریبی است.
                    if title_comp == first_desc_line_comp: # فقط اگر دقیقا یکی بودند
                        if '\n' in description_telegram_html: # فرض می‌کنیم \n جداکننده خط اول است
                            description_to_display_html = description_telegram_html.split('\n', 1)[1].strip()
                        else:
                            description_to_display_html = "" # کل توضیحات همان عنوان بود


        DESCRIPTION_MAX_LEN_HTML = 3800 # برای HTML محدودیت کاراکتر کمتر است چون خود تگ‌ها هم فضا می‌گیرند
        if len(description_to_display_html) > DESCRIPTION_MAX_LEN_HTML:
            # کوتاه کردن HTML بدون شکستن تگ‌ها پیچیده است. فعلا یک کوتاه کردن ساده متنی انجام می‌دهیم.
            # این ممکن است HTML را نامعتبر کند. راه بهتر، کوتاه کردن قبل از تبدیل به HTML است یا استفاده از کتابخانه.
            temp_soup = BeautifulSoup(description_to_display_html, "html.parser")
            plain_text_for_truncate = temp_soup.get_text(separator=' ', strip=True)
            if len(plain_text_for_truncate) > DESCRIPTION_MAX_LEN_HTML: # اگر متن خالص هم طولانی بود
                cut_off_point = plain_text_for_truncate.rfind('.', 0, DESCRIPTION_MAX_LEN_HTML - 20) # -20 برای " (...)"
                if cut_off_point != -1:
                    truncated_plain_text = plain_text_for_truncate[:cut_off_point+1] + f"{self.RLM} (...)"
                else:
                    truncated_plain_text = plain_text_for_truncate[:DESCRIPTION_MAX_LEN_HTML - 20] + f"{self.RLM}..."
                # تبدیل مجدد متن کوتاه شده به HTML ساده (فقط escape کردن)
                description_to_display_html = html.escape(truncated_plain_text) # این فرمت غنی را از دست می‌دهد
                logger.warning("Description was too long and has been truncated to plain text.")


        if not description_to_display_html.strip():
            description_to_display_html = ""

        # مونتاژ پیام با HTML
        message_parts = []
        if display_title:
             message_parts.append(f"{self.RLM}<b>{html.escape(display_title)}</b>")

        if description_to_display_html:
            separator = "\n\n" if display_title else ""
            # RLM برای خود توضیحات لازم نیست اگر کل بلوک HTML جهت درستی داشته باشد
            # اما اگر متن فارسی با کاراکتر انگلیسی شروع شود، می‌تواند مفید باشد.
            # فعلا RLM را برای کل بلوک توضیحات اضافه نمی‌کنیم چون خود HTML باید جهت را مدیریت کند.
            message_parts.append(f"{separator}{description_to_display_html}")


        meta_info_parts = []
        if event.link:
            meta_info_parts.append(f"{self.RLM}🔗 <a href=\"{self._sanitize_href(event.link)}\">مشاهده کامل رویداد</a>")
        
        if event.source_channel_username:
            meta_info_parts.append(f"{self.RLM}📢 <b>منبع:</b> <a href=\"https://t.me/{html.escape(event.source_channel_username)}\">{html.escape(event.source_channel)}</a>")
        else:
            meta_info_parts.append(f"{self.RLM}📢 <b>منبع:</b> {html.escape(event.source_channel)}")

        if event.published:
            formatted_date = event.published
            try:
                date_obj = datetime.strptime(event.published, "%a, %d %b %Y %H:%M:%S %Z")
                formatted_date = date_obj.strftime("%d %b %Y - %H:%M (%Z)")
            except: pass
            if formatted_date:
                 meta_info_parts.append(f"{self.RLM}📅 <b>انتشار:</b> {html.escape(formatted_date)}")

        if meta_info_parts:
            separator_meta = "\n\n" if message_parts else "" 
            message_parts.append(separator_meta + "\n".join(meta_info_parts)) # \n بین آیتم‌های متا کافی است

        final_message_html = "\n".join(message_parts).strip()
                                                          
        TELEGRAM_MSG_MAX_LEN = 4096
        if len(final_message_html) > TELEGRAM_MSG_MAX_LEN:
            logger.warning(f"HTML Message for '{display_title}' too long ({len(final_message_html)} chars). Telegram might truncate or reject.")
            # کوتاه کردن HTML بدون شکستن تگ‌ها بسیار پیچیده است.
            # این بخش نیاز به یک کتابخانه یا منطق قوی برای کوتاه کردن HTML دارد.
            # فعلا فقط هشدار می‌دهیم.
            
        return final_message_html

    async def publish_event(self, event: EventInfo):
        try:
            message_html = self.format_event_message(event) # متد فرمت‌بندی اکنون HTML برمی‌گرداند
            
            is_message_effectively_empty = not message_html or \
                                          (not event.title and not self._prepare_description_telegram_html(event.description).strip()) # بررسی محتوای واقعی
            
            if is_message_effectively_empty:
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
