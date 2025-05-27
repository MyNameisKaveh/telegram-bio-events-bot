---
title: Telegram Event Aggregator Bot
emoji: 🤖
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860 # Port your app's health check server listens on
pinned: false 
---

# Telegram Event Aggregator Bot

This Python-based Telegram bot monitors specified RSS feeds (primarily from Telegram channels via RSSHub) for event announcements. It then parses these announcements, formats them using Telegram's MarkdownV2, and posts them to a designated target Telegram channel.

The bot is designed to be deployed on [Hugging Face Spaces](https://huggingface.co/spaces) using Docker.

## Features

* Monitors multiple RSS feeds concurrently.
* Detects potential events based on keywords in titles and descriptions.
* Parses HTML content from RSS descriptions to extract clean text.
* Formats messages for Telegram using **MarkdownV2**, preserving some formatting like bold, italics, and links.
* Handles "Forwarded From" information in RSS items by removing the attribution line.
* Avoids re-posting the same event if the RSS title is identical to the first line of the description.
* Sends messages to a specified target Telegram channel.
* Includes a simple health check web server for deployment platforms like Hugging Face Spaces.
* Configurable list of source RSS feeds and event detection keywords.
* Configurable polling interval for checking RSS feeds.

## Technology Stack

* **Language:** Python 3.10+
* **Core Libraries:**
    * `python-telegram-bot`: For interacting with the Telegram Bot API.
    * `aiohttp`: For asynchronous HTTP requests (fetching RSS feeds and running the web server).
    * `feedparser`: For parsing RSS/Atom feeds.
    * `BeautifulSoup4`: For parsing and cleaning HTML content from RSS descriptions.
* **Deployment:** Docker, Hugging Face Spaces.

## Setup and Installation

### Prerequisites

* Python 3.10 or higher.
* A Telegram Bot Token obtained from [BotFather](https://t.me/BotFather).
* The ID or username of the target Telegram channel where the bot will post messages (e.g., `@YourTargetChannel` or a numeric ID for private channels). The bot must be an administrator in this channel with permission to send messages.

### Local Setup (Optional, for development/testing)

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/YOUR_USERNAME/telegram-bio-events-bot.git](https://github.com/YOUR_USERNAME/telegram-bio-events-bot.git)
    cd telegram-bio-events-bot
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    Your `requirements.txt` should include:
    ```text
    python-telegram-bot
    aiohttp
    feedparser
    beautifulsoup4
    # lxml (optional, but recommended for faster HTML parsing with BeautifulSoup)
    ```

4.  **Set Environment Variables:**
    Create a `.env` file in the root directory (and add `.env` to your `.gitignore` file) or set these environment variables directly in your system:
    * `TELEGRAM_BOT_TOKEN`: Your Telegram Bot Token.
    * `TARGET_CHANNEL`: The username (e.g., `@mychannel`) or chat ID of your target Telegram channel.

    Example `.env` file:
    ```env
    TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    TARGET_CHANNEL="@YourEventsChannel"
    ```
    The script `app.py` uses `os.getenv()` to read these. For local development without Docker, you might use a library like `python-dotenv` to load `.env` files automatically (by adding `from dotenv import load_dotenv; load_dotenv()` at the start of your script).

### Running Locally

```bash
python app.py
```
## Configuration

All configurations are done directly within the `app.py` script in the `RSSTelegramBot` class.

1.  **Adding/Modifying RSS Feeds:**
    Edit the `self.rss_feeds` list in the `__init__` method of the `RSSTelegramBot` class. Each entry is a dictionary:
    ```python
    self.rss_feeds = [
        {
            'name': 'Display Name for Source', # e.g., 'Tech Conferences EU'
            'url': 'RSS_FEED_URL',             # e.g., '[https://rsshub.app/telegram/channel/techconf_eu](https://rsshub.app/telegram/channel/techconf_eu)'
            'channel': 'telegram_channel_username' # e.g., 'techconf_eu' (used for linking)
        },
        # ... more feeds
    ]
    ```

2.  **Adjusting Event Keywords:**
    Modify the `EVENT_KEYWORDS` list within the `EventDetector` class. Add or remove keywords (in Persian or English) to improve event detection accuracy.

3.  **Changing Feed Check Interval:**
    In the `run_monitoring_loop` method of the `RSSTelegramBot` class, modify `check_interval_seconds`. The default is `600` (10 minutes).
    ```python
    check_interval_seconds = 600 # Check every 10 minutes
    ```
    Be mindful of rate limits if you decrease this significantly.

## Deployment on Hugging Face Spaces

This bot is designed to be deployed as a Docker container on Hugging Face Spaces.

1.  **`Dockerfile`:**
    Ensure you have a `Dockerfile` in the root of your repository. A sample is provided in previous discussions (using `python:3.10-slim`, copying `requirements.txt`, installing dependencies, copying the app, and running `app.py`).

2.  **`README.md` Hugging Face Metadata:**
    The YAML front matter at the top of this `README.md` file helps configure the Hugging Face Space:
    ```yaml
    ---
    title: Telegram Event Aggregator Bot 
    emoji: 🤖
    colorFrom: green
    colorTo: blue
    sdk: docker
    app_port: 7860 # Matches the port in app.py's health_check server
    ---
    ```
    Make sure `sdk` is set to `docker` and `app_port` matches the port used by the `aiohttp` web server in `app.py` (defaulted to 7860 or `os.environ.get("PORT", "7860")`).

3.  **`requirements.txt`:**
    This file is crucial for the Docker build. Ensure it lists all necessary Python packages.

4.  **Setting Secrets in Hugging Face Spaces:**
    In your Hugging Face Space settings (under "Variables and secrets"), add the following repository secrets:
    * `TELEGRAM_BOT_TOKEN`: Your Telegram Bot Token.
    * `TARGET_CHANNEL`: Your target channel ID/username.
    The `app.py` script will read these as environment variables.

5.  **Connecting GitHub to Hugging Face Space:**
    Link your GitHub repository to the Hugging Face Space. Pushes to your `main` branch (or the branch configured in your GitHub Action workflow, if you use one for syncing) will trigger a rebuild and deployment of the Space.

## How the Bot Works

Once deployed and running:

1.  The bot starts by logging its status and the health check server.
2.  It enters a monitoring loop, periodically fetching content from the specified RSS feeds.
3.  For each new item in a feed, it uses the `EventDetector` to check if the item likely describes an event.
4.  If an item is identified as an event:
    * The HTML description is cleaned and converted to Telegram-compatible MarkdownV2.
    * "Forwarded From" notices are removed.
    * A message is formatted including the (cleaned) description (which contains the title if it was originally the first line), a link to the original post, the source channel (linked if username is available), and the publication date. No separate title line is added.
    * This formatted message is then posted to the `TARGET_CHANNEL`.
5.  The bot waits for the configured interval (`check_interval_seconds`) before checking feeds again.

## Contributing

Contributions, issues, and feature requests are welcome. Please feel free to open an issue or submit a pull request.

## License

(Optional: Specify your license here, e.g., MIT License. If you added `license: mit` to the YAML front matter, ensure you also have a `LICENSE` file in your repository.)

---
