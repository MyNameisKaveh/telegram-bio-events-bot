import asyncio
import re
import logging
from datetime import datetime
import html
import aiohttp
import feedparser
from telegram import Bot
from telegram.constants import ParseMode
# از تابع escape_markdown خود کتابخانه استفاده نمی‌کنیم و یک نسخه سفارشی می‌نویسیم
# from telegram.helpers import escape_markdown 
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
    description: str
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
            {'name': 'WinCell Co', 'url': 'https://rsshub.app/telegram/channel/wincellco', 'channel': 'wincellco'},
            {'name': 'Rayazistazma', 'url': 'https://rsshub.app/telegram/channel/Rayazistazma', 'channel': 'Rayazistazma'},
            {'name': 'SBU Bio Society', 'url': 'https://rsshub.app/telegram/channel/SBUBIOSOCIETY', 'channel': 'SBUBIOSOCIETY'},
            {'name': 'Test BioPy Channel', 'url': 'https://rsshub.app/telegram/channel/testbiopy', 'channel': 'testbiopy'}
        ]
        self.RLM = "\u200F"
        # کاراکترهایی که در MarkdownV2 باید escape شوند طبق مستندات تلگرام
        self.MDV2_ESCAPE_CHARS = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']


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
                elif response.status == 429: # به طور خاص خطای Rate Limit را لاگ کن
                    logger.warning(f"Rate limited (429) while fetching {feed_name}. Will retry later. Content: {await response.text(encoding='utf-8', errors='ignore')[:200]}")
                else:
                    logger.error(f"Error fetching {feed_name}: Status {response.status} - {await response.text(encoding='utf-8', errors='ignore')}")
        except Exception as e:
            logger.error(f"Exception in fetch_feed for {feed_name} ({feed_url}): {e}", exc_info=True)
        return events

    def _escape_md_v2(self, text: Optional[str]) -> str:
        """تابع سفارشی برای escape کردن متن برای MarkdownV2 تلگرام."""
        if text is None: return ""
        text = str(text)
        # ابتدا خود بک‌اسلش باید escape شود
        text = text.replace('\\', '\\\\')
        for char_to_escape in self.MDV2_ESCAPE_CHARS:
            text = text.replace(char_to_escape, f'\\{char_to_escape}')
        return text

    def _convert_node_to_markdown_v2_recursive(self, element, list_level=0, inside_pre=False) -> str:
        # (این متد از پاسخ قبلی بدون تغییر کپی شده، چون مشکل اصلی در escape کردن بود)
        if isinstance(element, NavigableString):
            if inside_pre: return str(element) 
            return self._escape_md_v2(str(element))
        
        tag_name = element.name
        current_list_level = list_level
        if tag_name in ['ul', 'ol']: current_list_level += 1
        
        children_md_parts = [self._convert_node_to_markdown_v2_recursive(child, current_list_level, inside_pre or (tag_name == 'pre')) for child in element.children]
        children_md = "".join(children_md_parts)

        if tag_name in ['b', 'strong']: return f"*{children_md}*"
        if tag_name in ['i', 'em']: return f"_{children_md}_"
        if tag_name == 'u': return f"__{children_md}__"
        if tag_name in ['s', 'strike', 'del']: return f"~{children_md}~"
        
        if tag_name == 'code':
            if element.parent.name == 'pre': return children_md 
            return f"`{children_md}`" 

        if tag_name == 'pre':
            code_child = element.find('code', class_=lambda x: x and isinstance(x, list) and x[0].startswith('language-'))
            if code_child:
                lang = self._escape_md_v2(code_child['class'][0].split('-',1)[1])
                pre_content = "".join(self._convert_node_to_markdown_v2_recursive(c, 0, True) for c in code_child.children)
                return f"```{lang}\n{pre_content.strip()}\n```"
            pre_content = "".join(self._convert_node_to_markdown_v2_recursive(c, 0, True) for c in element.children)
            return f"```\n{pre_content.strip()}\n```"

        if tag_name == 'a':
            href = element.get('href', '')
            if href and href.strip().lower().startswith(('http', 'tg://')):
                safe_href = href.strip().replace('(', '%28').replace(')', '%29')
                return f"[{children_md}]({safe_href})"
            return children_md 

        if tag_name == 'br': return '\n'
        
        if tag_name in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'table', 'figure']:
            processed_content = children_md.rstrip('\n') 
            if processed_content: return f"{processed_content}\n\n"
            return "\n\n" 
        
        if tag_name in ['ul', 'ol']: return children_md 
        if tag_name == 'li':
            prefix = f"{self.RLM}• " 
            return f"{prefix}{children_md.strip()}\n"
        return children_md

    def _prepare_description_for_markdown_v2(self, html_content: str) -> str:
        # (این متد از پاسخ قبلی با نرمال‌سازی اصلاح شده خطوط جدید کپی شده)
        if not html_content: return ""
        soup = BeautifulSoup(html_content, "html.parser")

        first_p = soup.find('p', recursive=False)
        if first_p and first_p.get_text(strip=True).lower().startswith("forwarded from"):
            first_p.decompose()
        
        markdown_text = self._convert_node_to_markdown_v2_recursive(soup.body if soup.body else soup)
        
        markdown_text = markdown_text.replace('\r\n', '\n').replace('\r', '\n')
        markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text)
        markdown_text = markdown_text.strip()
        
        lines = markdown_text.splitlines()
        stripped_lines = [line.strip() for line in lines]
        markdown_text = "\n".join(stripped_lines) # این باید \n\n را حفظ کند

        return markdown_text.strip() # .strip() نهایی برای حذف هرگونه فضای خالی کلی باقیمانده

    def format_event_message(self, event: EventInfo) -> str:
        # (این متد از پاسخ قبلی که عنوان جداگانه را حذف می‌کند، کپی شده)
        description_md = self._prepare_description_for_markdown_v2(event.description)
        
        if not description_md.strip():
            logger.info(f"MarkdownV2 description is empty for event (Original title: {event.title[:30]}...). Skipping.")
            return ""

        message_prefix = self.RLM if description_md and not re.match(r"^\s*[\u0600-\u06FF*_[~`#]", description_md) else ""
        message_parts = [f"{message_prefix}{description_md}"]

        meta_info_parts = []
        if event.link:
            escap
