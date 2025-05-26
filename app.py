import asyncio
import re
import logging
from datetime import datetime
import aiohttp
import feedparser
from telegram import Bot
from telegram.ext import Application
import os
from dataclasses import dataclass
from typing import List, Optional
import json
from aiohttp import web

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class EventInfo:
    """Class to store event information"""
    title: str
    description: str
    link: str
    published: str
    source_channel: str

class EventDetector:
    """Class to detect events in RSS feed content"""
    
    # Keywords for events (Persian and English)
    EVENT_KEYWORDS = [
        'وبینار', 'webinar', 'کارگاه', 'workshop', 'سمینار', 'seminar',
        'کنفرانس', 'conference', 'همایش', 'congress', 'نشست', 'meeting',
        'دوره آموزشی', 'course', 'کلاس', 'class', 'ایونت', 'event',
        'برگزار', 'organize', 'شرکت', 'participate', 'ثبت نام', 'register',
        'رایگان', 'free', 'آنلاین', 'online', 'مجازی', 'virtual',
        'آموزش', 'training', 'فراخوان', 'call'
    ]
    
    def detect_event(self, text: str) -> bool:
        """Detect if content contains event information"""
        text_lower = text.lower()
        
        # Count matching keywords
        matches = sum(1 for keyword in self.EVENT_KEYWORDS if keyword in text_lower)
        
        # Return True if we have at least 2 keywords or specific patterns
        return matches >= 2 or any([
            'ثبت نام' in text_lower,
            'شرکت در' in text_lower,
            'برگزار می' in text_lower,
            'register' in text_lower,
            'join' in text_lower
        ])

class RSSTelegramBot:
    """RSS-based Telegram Event Bot"""
    
    def __init__(self, bot_token: str, target_channel: str):
        self.bot_token = bot_token
        self.target_channel = target_channel
        self.bot = Bot(token=bot_token)
        self.detector = EventDetector()
        self.processed_items = set()  # To avoid duplicates
        
        # RSS feeds for Telegram channels
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
            }
        ]
    
    async def fetch_feed(self, session: aiohttp.ClientSession, feed_info: dict) -> List[EventInfo]:
        """Fetch and parse RSS feed"""
        events = []
        
        try:
            async with session.get(feed_info['url'], timeout=30) as response:
                if response.status == 200:
                    content = await response.text()
                    feed = feedparser.parse(content)
                    
                    for entry in feed.entries[:10]:  # Check last 10 entries
                        # Create unique ID for this entry
                        entry_id = f"{feed_info['channel']}_{entry.get('id', entry.get('link', ''))}"
                        
                        if entry_id not in self.processed_items:
                            # Check if it's an event
                            title = entry.get('title', '')
                            description = entry.get('description', entry.get('summary', ''))
                            full_text = f"{title} {description}"
                            
                            if self.detector.detect_event(full_text):
                                event = EventInfo(
                                    title=title,
                                    description=description,
                                    link=entry.get('link', ''),
                                    published=entry.get('published', ''),
                                    source_channel=feed_info['name']
                                )
                                events.append(event)
                                self.processed_items.add(entry_id)
                                
                                # Limit processed items to prevent memory issues
                                if len(self.processed_items) > 1000:
                                    # Remove oldest 200 items
                                    items_list = list(self.processed_items)
                                    self.processed_items = set(items_list[200:])
                        
        except Exception as e:
            logger.error(f"Error fetching feed {feed_info['name']}: {e}")
        
        return events
    
    async def check_all_feeds(self):
        """Check all RSS feeds for events"""
        all_events = []
        
        async with aiohttp.ClientSession() as session:
            tasks = []
            for feed_info in self.rss_feeds:
                task = self.fetch_feed(session, feed_info)
                tasks.append(task)
            
            # Wait for all feeds to be processed
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, list):
                    all_events.extend(result)
                elif isinstance(result, Exception):
                    logger.error(f"Feed processing error: {result}")
        
        return all_events
    
    async def publish_event(self, event: EventInfo):
        """Publish event to Telegram channel"""
        try:
            message = self.format_event_message(event)
            
            await self.bot.send_message(
                chat_id=self.target_channel,
                text=message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
            
            logger.info(f"Published event: {event.title[:50]}... from {event.source_channel}")
            
        except Exception as e:
            logger.error(f"Failed to publish event: {e}")
    
    def format_event_message(self, event: EventInfo) -> str:
        """Format event for Telegram"""
        # Clean up HTML tags from description
        description = re.sub(r'<[^>]+>', '', event.description)
        description = description.strip()[:300]  # Limit length
        
        message = f"🎯 **رویداد جدید**\n\n"
        message += f"📝 **{event.title}**\n\n"
        
        if description:
            message += f"{description}\n\n"
        
        if event.link:
            message += f"🔗 [مشاهده کامل]({event.link})\n\n"
        
        message += f"📢 **منبع:** {event.source_channel}\n"
        
        if event.published:
            message += f"📅 **تاریخ انتشار:** {event.published[:10]}"
        
        return message
    
    async def run_monitoring_loop(self):
        """Main monitoring loop"""
        logger.info("Starting RSS monitoring...")
        
        while True:
            try:
                # Check feeds for events
                events = await self.check_all_feeds()
                
                # Publish each event
                for event in events:
                    await self.publish_event(event)
                    await asyncio.sleep(2)  # Avoid rate limiting
                
                logger.info(f"Checked feeds, found {len(events)} new events")
                
                # Wait 10 minutes before next check
                await asyncio.sleep(600)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retry

class Config:
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TARGET_CHANNEL = os.getenv('TARGET_CHANNEL', '@your_events_channel')

async def health_check(request):
    """Health check endpoint for Hugging Face Spaces"""
    return web.Response(text="Bot is running!", status=200)

async def start_web_server():
    """Start web server for health checks"""
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 7860)
    await site.start()
    logger.info("Web server started on port 7860")

async def main():
    """Main function"""
    if not Config.BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is required!")
        return
    
    # Start web server for health checks
    await start_web_server()
    
    # Create bot instance
    bot = RSSTelegramBot(
        bot_token=Config.BOT_TOKEN,
        target_channel=Config.TARGET_CHANNEL
    )
    
    try:
        # Test bot connection
        bot_info = await bot.bot.get_me()
        logger.info(f"Bot started: @{bot_info.username}")
        
        # Start monitoring
        await bot.run_monitoring_loop()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
