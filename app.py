import asyncio
import re
import logging
from datetime import datetime
import html # For unescaping HTML entities if necessary, and escaping for HTML parse mode
import aiohttp
import feedparser
from telegram import Bot
from telegram.constants import ParseMode # To specify parse_mode
# from telegram.helpers import escape_markdown # Using custom escaper due to previous issues
import os
from dataclasses import dataclass
from typing import List, Optional
from bs4 import BeautifulSoup, NavigableString, Tag # For parsing HTML
from aiohttp import web

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class EventInfo:
    """Class to store event information extracted from RSS entries."""
    title: str          # Original title from RSS entry, used for event detection and logging
    description: str    # Raw HTML description from RSS entry
    link: str           # Link to the original post/event
    published: str      # Publication date string
    source_channel: str # Display name of the source channel
    source_channel_username: Optional[str] = None # Telegram username of the source channel (for linking)

class EventDetector:
    """Class to detect if an RSS entry likely contains event information."""
    # Keywords to identify event-related posts (Persian and English)
    EVENT_KEYWORDS = [
        'وبینار', 'webinar', 'کارگاه', 'workshop', 'سمینار', 'seminar', 'کنفرانس', 'conference', 
        'همایش', 'congress', 'نشست', 'meeting', 'دوره آموزشی', 'course', 'کلاس', 'class', 
        'ایونت', 'event', 'برگزار', 'organize', 'شرکت', 'participate', 'ثبت نام', 'register',
        'رایگان', 'free', 'آنلاین', 'online', 'مجازی', 'virtual', 'آموزش', 'training', 
        'فراخوان', 'call', 'گواهی', 'certificate', 'مدرک', 'certification', 'لایو', 'live'
    ]

    def detect_event(self, title: str, description_html: str) -> bool:
        """
        Detects if the content (title and description) contains event information.
        Uses BeautifulSoup to get cleaner text from HTML description for keyword matching.
        """
        soup = BeautifulSoup(description_html, "html.parser")
        # Remove "Forwarded From" part before text extraction for detection, as it might contain irrelevant keywords
        first_p_tag = soup.find('p')
        if first_p_tag and first_p_tag.get_text(strip=True).lower().startswith("forwarded from"):
            first_p_tag.decompose()
        
        text_only_description = soup.get_text(separator=' ', strip=True)
        
        title_lower = title.lower()
        # Strong keywords that if present in title, highly indicate an event
        strong_title_keywords = ['وبینار', 'کارگاه', 'سمینار', 'دوره', 'کنفرانس', 'همایش', 'نشست آموزشی', 'کلاس آنلاین']
        if any(keyword in title_lower for keyword in strong_title_keywords):
            return True # If title is very clear, consider it an event

        # If title wasn't clear enough, check combined title and description text
        full_text_desc_lower = text_only_description.lower()
        combined_text_lower = f"{title_lower} {full_text_desc_lower}"

        matches = sum(1 for keyword in self.EVENT_KEYWORDS if keyword in combined_text_lower)
        
        # Specific patterns that strongly indicate an event, mostly in description
        has_specific_pattern = any([
            'ثبت نام' in combined_text_lower, 
            'شرکت در' in combined_text_lower, # e.g., "شرکت در این کارگاه"
            'لینک ثبت نام' in combined_text_lower,
            'جهت ثبت نام' in combined_text_lower,
            'هزینه دوره' in full_text_desc_lower, # These are more likely in description
            'سرفصل های دوره' in full_text_desc_lower,
            'مدرس دوره' in full_text_desc_lower,
            'اطلاعات بیشتر و ثبت نام' in full_text_desc_lower,
            'register for' in combined_text_lower, 
            'join this' in combined_text_lower
        ])
        
        # Other keywords in title that might indicate an event
        title_has_other_keywords = any(keyword in title_lower for keyword in ['آموزش', 'فراخوان', 'لایو'])
        
        return title_has_other_keywords or has_specific_pattern or matches >= 2 # Threshold can be adjusted


class RSSTelegramBot:
    """Main class for the RSS Telegram Bot."""
    def __init__(self, bot_token: str, target_channel: str):
        self.bot_token = bot_token
        self.target_channel = target_channel
        self.bot = Bot(token=bot_token)
        self.detector = EventDetector()
        self.processed_items = set() # Stores IDs of processed items to avoid duplicates (in-memory)
        
        # List of RSS feeds to monitor
        self.rss_feeds = [
            {'name': 'GENETIXgroup', 'url': 'https://rsshub.app/telegram/channel/GENETIXgroup', 'channel': 'GENETIXgroup'},
            {'name': 'IDS_Med', 'url': 'https://rsshub.app/telegram/channel/IDS_Med', 'channel': 'IDS_Med'},
            {'name': 'IranianBioinformaticsSociety', 'url': 'https://rsshub.app/telegram/channel/IranianBioinformaticsSociety', 'channel': 'IranianBioinformaticsSociety'},
            {'name': 'KHU_nanobiotech', 'url': 'https://rsshub.app/telegram/channel/KHU_nanobiotech', 'channel': 'KHU_nanobiotech'},
            {'name': 'Khubioinformatics', 'url': 'https://rsshub.app/telegram/channel/Khubioinformatics', 'channel': 'Khubioinformatics'},
            {'name': 'NationalBrainMappingLab', 'url': 'https://rsshub.app/telegram/channel/NationalBrainMappingLab', 'channel': 'NationalBrainMappingLab'},
            {'name': 'Rayazistazma', 'url': 'https://rsshub.app/telegram/channel/Rayazistazma', 'channel': 'Rayazistazma'},
            {'name': 'SBUBIOSOCIETY', 'url': 'https://rsshub.app/telegram/channel/SBUBIOSOCIETY', 'channel': 'SBUBIOSOCIETY'},
            {'name': 'SystemsBioML', 'url': 'https://rsshub.app/telegram/channel/SystemsBioML', 'channel': 'SystemsBioML'},
            {'name': 'UTBiologyAssociation', 'url': 'https://rsshub.app/telegram/channel/UTBiologyAssociation', 'channel': 'UTBiologyAssociation'},
            {'name': 'amagene', 'url': 'https://rsshub.app/telegram/channel/amagene', 'channel': 'amagene'},
            {'name': 'aubiotechnology', 'url': 'https://rsshub.app/telegram/channel/aubiotechnology', 'channel': 'aubiotechnology'},
            {'name': 'biodc', 'url': 'https://rsshub.app/telegram/channel/biodc', 'channel': 'biodc'},
            {'name': 'biophileTeam', 'url': 'https://rsshub.app/telegram/channel/biophileTeam', 'channel': 'biophileTeam'},
            {'name': 'biotechku', 'url': 'https://rsshub.app/telegram/channel/biotechku', 'channel': 'biotechku'},
            {'name': 'cellandmolecularbiology', 'url': 'https://rsshub.app/telegram/channel/cellandmolecularbiology', 'channel': 'cellandmolecularbiology'},
            {'name': 'ipmbio', 'url': 'https://rsshub.app/telegram/channel/ipmbio', 'channel': 'ipmbio'},
            {'name': 'ir_micro_academy', 'url': 'https://rsshub.app/telegram/channel/ir_micro_academy', 'channel': 'ir_micro_academy'},
            {'name': 'utBioEvent', 'url': 'https://rsshub.app/telegram/channel/utBioEvent', 'channel': 'utBioEvent'},
            {'name': 'wincellco', 'url': 'https://rsshub.app/telegram/channel/wincellco', 'channel': 'wincellco'},
            {'name': 'yazd_bioinformatics_association', 'url': 'https://rsshub.app/telegram/channel/yazd_bioinformatics_association', 'channel': 'yazd_bioinformatics_association'},
            {'name': 'zistotech', 'url': 'https://rsshub.app/telegram/channel/zistotech', 'channel': 'zistotech'},
        ]
        self.RLM = "\u200F" # Right-to-Left Mark for RTL text handling
        # Characters that must be escaped in MarkdownV2 according to Telegram documentation
        self.MDV2_ESCAPE_CHARS = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']

    async def fetch_feed(self, session: aiohttp.ClientSession, feed_info: dict) -> List[EventInfo]:
        """Fetches and parses a single RSS feed, returning a list of detected EventInfo objects."""
        events = []
        feed_url, feed_name = feed_info['url'], feed_info['name']
        logger.info(f"Fetching feed: {feed_name} from {feed_url}")
        try:
            # Add headers to try to bypass some caches (might not always be effective for RSSHub's own cache)
            headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
            async with session.get(feed_url, timeout=45, headers=headers) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = feedparser.parse(content)
                    logger.info(f"Fetched {feed_name}. Entries: {len(feed.entries)}. LastBuildDate: {feed.feed.get('updated', feed.feed.get('published', 'N/A'))}")
                    
                    # Process a limited number of recent entries (e.g., last 7)
                    for entry in feed.entries[:7]: 
                        # Create a unique ID for each entry to track processed items
                        entry_id = f"{feed_info.get('channel', feed_name)}_{entry.get('id', entry.get('link', ''))}"
                        if entry_id not in self.processed_items:
                            raw_title = entry.get('title', '').strip()
                            raw_description_html = entry.get('description', entry.get('summary', ''))
                            
                            if self.detector.detect_event(raw_title, raw_description_html):
                                event = EventInfo(
                                    title=raw_title, # Store original title for logging/detection
                                    description=raw_description_html, # Store raw HTML for later conversion
                                    link=entry.get('link', ''), 
                                    published=entry.get('published', ''),
                                    source_channel=feed_name, 
                                    source_channel_username=feed_info.get('channel')
                                )
                                events.append(event)
                                self.processed_items.add(entry_id)
                        
                        # Memory management for processed_items set
                        if len(self.processed_items) > 1500: # If set grows too large
                            # Convert to list, take a slice of more recent items, convert back to set
                            self.processed_items = set(list(self.processed_items)[500:]) 
                            logger.info(f"Cleaned up processed_items. New size: {len(self.processed_items)}")
                elif response.status == 429: # Specifically log Rate Limit errors
                    logger.warning(f"Rate limited (429) while fetching {feed_name}. Will retry later. Content: {await response.text(encoding='utf-8', errors='ignore')[:200]}")
                else: # Log other HTTP errors
                    logger.error(f"Error fetching {feed_name}: Status {response.status} - {await response.text(encoding='utf-8', errors='ignore')}")
        except Exception as e: # Catch any other exceptions during fetch/parse
            logger.error(f"Exception in fetch_feed for {feed_name} ({feed_url}): {e}", exc_info=True)
        return events

    def _escape_md_v2(self, text: Optional[str]) -> str:
        """Custom function to escape text for Telegram MarkdownV2."""
        if text is None: return ""
        text = str(text)
        # Backslash must be escaped first as it's used for escaping other characters
        text = text.replace('\\', '\\\\')
        for char_to_escape in self.MDV2_ESCAPE_CHARS:
            text = text.replace(char_to_escape, f'\\{char_to_escape}')
        return text

    def _convert_node_to_markdown_v2_recursive(self, element, list_level=0, inside_pre=False) -> str:
        """Recursively converts a BeautifulSoup node to a Telegram MarkdownV2 string."""
        if isinstance(element, NavigableString):
            # Text nodes should be escaped, unless they are inside <pre> or <code> (within <pre>)
            if inside_pre: 
                return str(element) # Content inside <pre> should not be Markdown-escaped
            return self._escape_md_v2(str(element))
        
        tag_name = element.name
        
        # Determine if children should also be processed as 'inside_pre'
        current_children_inside_pre = inside_pre or (tag_name == 'pre')
        
        # Recursively process children first
        children_md_parts = [self._convert_node_to_markdown_v2_recursive(child, 
                                                                        list_level + (1 if tag_name in ['ul', 'ol'] else 0), 
                                                                        current_children_inside_pre) 
                             for child in element.children]
        children_md = "".join(children_md_parts)

        # Convert HTML tags to MarkdownV2 syntax
        if tag_name in ['b', 'strong']: return f"*{children_md}*"
        if tag_name in ['i', 'em']: return f"_{children_md}_"
        if tag_name == 'u': return f"__{children_md}__" # Telegram uses double underscore for underline
        if tag_name in ['s', 'strike', 'del']: return f"~{children_md}~"
        
        if tag_name == 'code':
            if element.parent.name == 'pre': return children_md # If inside <pre>, <pre> tag handles formatting
            # For inline code, content should ideally not contain backticks.
            # Telegram doesn't support escaping backticks within inline code with double backticks.
            return f"`{children_md}`" 

        if tag_name == 'pre':
            code_child = element.find('code', class_=lambda x: x and isinstance(x, list) and x[0].startswith('language-'))
            if code_child:
                lang = code_child['class'][0].split('-',1)[1] # Language name should not be escaped for Markdown
                # Content of code block is already processed (and not escaped if inside_pre was true)
                pre_content = "".join(self._convert_node_to_markdown_v2_recursive(c, 0, True) for c in code_child.children)
                return f"```{lang}\n{pre_content.strip()}\n```"
            # For plain <pre> block, content is already processed by children_md with inside_pre=True
            return f"```\n{children_md.strip()}\n```"

        if tag_name == 'a':
            href = element.get('href', '')
            # Validate and prepare URL (URL-encode parentheses)
            if href and href.strip().lower().startswith(('http', 'tg://')):
                safe_href = href.strip().replace('(', '%28').replace(')', '%29')
                # children_md (link text) is already MarkdownV2 escaped from recursive calls
                return f"[{children_md}]({safe_href})"
            return children_md # If link is invalid, return only the text

        if tag_name == 'br': return '\n' # <br> becomes a newline
        
        # Block-level tags that should be followed by paragraph breaks (\n\n)
        if tag_name in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'table', 'figure']:
            processed_content = children_md.rstrip('\n') 
            if processed_content: return f"{processed_content}\n\n"
            return "\n\n" # Return double newline even for empty blocks to maintain spacing
        
        # List handling (simplified)
        if tag_name == 'ul' or tag_name == 'ol': # Ordered lists are treated like unordered for simplicity
            return children_md # Children (li) will handle their own formatting and newlines

        if tag_name == 'li':
            prefix = f"{self.RLM}• " # Use a bullet for all list items
            return f"{prefix}{children_md.strip()}\n" # Each list item ends with a newline

        # If tag is not recognized, strip the tag but keep its processed content
        return children_md

    def _prepare_description_for_markdown_v2(self, html_content: str) -> str:
        """Converts raw HTML description to a MarkdownV2 formatted string."""
        if not html_content: return ""
        # It's generally assumed feedparser unescapes common HTML entities.
        # If not, html_content = html.unescape(html_content) might be needed first.
        soup = BeautifulSoup(html_content, "html.parser")

        # Remove "Forwarded From" paragraph if it's the first one
        first_p = soup.find('p', recursive=False) # Check only top-level <p>
        if first_p and first_p.get_text(strip=True).lower().startswith("forwarded from"):
            first_p.decompose()
        
        # Convert the rest of the soup (or its body) to MarkdownV2
        markdown_text = self._convert_node_to_markdown_v2_recursive(soup.body if soup.body else soup)
        
        # Final newline normalization
        markdown_text = markdown_text.replace('\r\n', '\n').replace('\r', '\n') # Normalize all newline types
        markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text) # Collapse 3+ newlines to 2 (paragraph break)
        markdown_text = markdown_text.strip('\n') # Remove leading/trailing newlines from the whole block
        
        # Strip leading/trailing whitespace from each individual line, then rejoin.
        # This preserves single \n (line breaks) and double \n (paragraph breaks).
        lines = markdown_text.splitlines()
        stripped_lines = [line.strip() for line in lines]
        markdown_text = "\n".join(stripped_lines)

        return markdown_text.strip() # Final strip for the whole content

    def format_event_message(self, event: EventInfo) -> str:
        """Formats the event data into a MarkdownV2 string for Telegram."""
        # Title is no longer displayed separately as per user request.
        # The entire content comes from the description.
        
        description_md = self._prepare_description_for_markdown_v2(event.description)
        
        if not description_md.strip():
            logger.info(f"MarkdownV2 description is empty for event (Original title: {event.title[:30]}...). Skipping.")
            return ""

        # Add RLM at the beginning of the entire message if it starts with LTR or a non-RTL Markdown char
        message_prefix = ""
        if description_md:
            # Check the first non-whitespace character of the description
            first_char_for_rtl_check = description_md.lstrip()[0] if description_md.lstrip() else ""
            # If it's not a Persian character and not a common Markdown starting character (like *, _, etc.)
            if first_char_for_rtl_check and not re.match(r"[\u0600-\u06FF]", first_char_for_rtl_check) and \
               first_char_for_rtl_check not in ['*', '_', '~', '`', '[']:
                message_prefix = self.RLM
        
        message_parts = [f"{message_prefix}{description_md}"]

        # Meta information (link, source, date)
        meta_info_parts = []
        if event.link:
            escaped_link_text = self._escape_md_v2("مشاهده کامل رویداد")
            # URLs for Markdown links should have parentheses URL-encoded, not Markdown-escaped
            safe_url = event.link.replace('(', '%28').replace(')', '%29')
            meta_info_parts.append(f"{self.RLM}🔗 [{escaped_link_text}]({safe_url})")
        
        source_text_escaped = self._escape_md_v2(event.source_channel)
        if event.source_channel_username:
            # Telegram channel username for t.me link should not be Markdown-escaped
            tg_url = f"https://t.me/{event.source_channel_username}" 
            meta_info_parts.append(f"{self.RLM}📢 *{self._escape_md_v2('منبع:')}* [{source_text_escaped}]({tg_url})")
        else:
            meta_info_parts.append(f"{self.RLM}📢 *{self._escape_md_v2('منبع:')}* {source_text_escaped}")

        #if event.published:
            #formatted_date_unescaped = event.published
            #try: # Try to parse and reformat the date
                #date_obj = datetime.strptime(event.published, "%a, %d %b %Y %H:%M:%S %Z") # Example: Fri, 23 May 2025 22:41:11 GMT
                #formatted_date_unescaped = date_obj.strftime("%d %b %Y - %H:%M (%Z)") # Example: 23 May 2025 - 22:41 (GMT)
            #except ValueError:
                #logger.debug(f"Could not parse date '{event.published}' with standard format. Using as is (or simplified).")
                # Fallback for other date formats if needed, or just use raw
            #if formatted_date_unescaped:
                 #meta_info_parts.append(f"{self.RLM}📅 *{self._escape_md_v2('انتشار:')}* {self._escape_md_v2(formatted_date_unescaped)}")

        if meta_info_parts:
            # Add a clear separation (two newlines) before the meta information block
            message_parts.append("\n\n" + "\n".join(meta_info_parts))

        final_message_md = "\n".join(message_parts).strip()
                                                          
        # Check for Telegram's message length limit
        if len(final_message_md) > 4096:
             logger.warning(f"MarkdownV2 Message (Original title: '{event.title[:30]}') is too long ({len(final_message_md)} chars). Telegram might truncate or reject it.")
             # Implement a strategy to truncate Markdown safely if this becomes a frequent issue.
             # For now, just log a warning.
        return final_message_md

    async def publish_event(self, event: EventInfo):
        """Publishes a single event to the Telegram channel."""
        try:
            message_md = self.format_event_message(event)
            
            if not message_md: # If formatted message is empty (e.g., description was empty)
                logger.info(f"Skipping event publish because formatted message is empty. Original title: {event.title[:60]}...")
                return

            await self.bot.send_message(
                chat_id=self.target_channel, 
                text=message_md,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True # Set to False if you want link previews for the main event link
            )
            logger.info(f"Published event (MarkdownV2): {event.title[:60]}... from {event.source_channel}")
        except Exception as e:
            logger.error(f"Failed to publish event ({event.title[:60]}...) using MarkdownV2 mode: {e}", exc_info=True)

    async def run_monitoring_loop(self):
        """Main loop to periodically check feeds and publish events."""
        logger.info("Starting RSS monitoring...")
        await asyncio.sleep(10) # Initial delay before first check
        while True:
            logger.info("Checking all feeds...")
            all_new_events = []
            # Use a single aiohttp session for all requests in one cycle
            async with aiohttp.ClientSession() as session:
                tasks = [self.fetch_feed(session, feed_info) for feed_info in self.rss_feeds]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, list): 
                        all_new_events.extend(result)
                    elif isinstance(result, Exception): 
                        logger.error(f"Feed processing task resulted in an exception: {result}", exc_info=result)
            
            if all_new_events:
                logger.info(f"Found {len(all_new_events)} new event(s) to publish.")
                # You could sort events here if needed, e.g., by publication date
                # all_new_events.sort(key=lambda ev: ev.published_timestamp_or_default)
                for event_to_publish in all_new_events:
                    await self.publish_event(event_to_publish)
                    await asyncio.sleep(5) # Delay between sending messages to avoid hitting Telegram rate limits
            else:
                logger.info("No new events found in this cycle.")
            
            check_interval_seconds = 600 # Check every 10 minutes
            logger.info(f"Next check in {check_interval_seconds // 60} minutes.")
            await asyncio.sleep(check_interval_seconds)

# --- Configuration and Web Server (for Hugging Face Spaces) ---
class Config:
    """Loads configuration from environment variables."""
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TARGET_CHANNEL = os.getenv('TARGET_CHANNEL')

rss_bot_instance: Optional[RSSTelegramBot] = None # Global instance for health check

async def health_check(request):
    """Simple health check endpoint."""
    global rss_bot_instance
    if rss_bot_instance:
        return web.Response(text=f"Bot is running! Processed items in memory: {len(rss_bot_instance.processed_items)}", status=200)
    return web.Response(text="Bot instance not fully initialized yet.", status=200)

async def start_web_server():
    """Starts the aiohttp web server for health checks."""
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check) # Health check on root as well
    runner = web.AppRunner(app)
    await runner.setup()
    # Read port from environment, default to 7860 for Hugging Face Spaces
    port = int(os.environ.get("PORT", "7860"))
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        logger.info(f"Web server started on port {port}")
    except OSError as e: # Catch errors like "address already in use"
        logger.error(f"Failed to start web server on port {port}: {e}.")
        # Decide if this is critical. For now, the bot can run without the web server.

async def main():
    """Main entry point for the application."""
    global rss_bot_instance

    if not Config.BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN environment variable is required!")
        return
    if not Config.TARGET_CHANNEL:
        logger.critical("TARGET_CHANNEL environment variable is required!")
        return
    
    logger.info("Application starting...")
    rss_bot_instance = RSSTelegramBot(bot_token=Config.BOT_TOKEN, target_channel=Config.TARGET_CHANNEL)
    
    web_server_task = None # Initialize to None
    try:
        bot_info = await rss_bot_instance.bot.get_me()
        logger.info(f"Bot started: @{bot_info.username}")

        # Start the web server as a concurrent task
        web_server_task = asyncio.create_task(start_web_server())
        
        # Start the main RSS monitoring loop
        await rss_bot_instance.run_monitoring_loop()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logger.error(f"Critical bot error in main execution: {e}", exc_info=True)
    finally:
        logger.info("Bot shutting down...")
        if web_server_task and not web_server_task.done(): 
            web_server_task.cancel()
            try: 
                await web_server_task
            except asyncio.CancelledError: 
                logger.info("Web server task cancelled.")
        logger.info("Application fully stopped.")

if __name__ == "__main__":
    asyncio.run(main())
