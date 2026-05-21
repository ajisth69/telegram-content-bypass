# 🚀 Telegram Content Bypass Bot (`telegram-content-bypass`)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot%20API%20%26%20MTProto-blue.svg?logo=telegram)](https://core.telegram.org/)
[![Deployment](https://img.shields.io/badge/Deploy-Render-brightgreen.svg)](https://render.com)

A powerful, high-performance Telegram Bot + Userbot hybrid designed to bypass restricted content saving settings (`protect_content`) on channels and groups. 

Easily download and re-upload restricted photos, videos, voice messages, documents, stickers, and animations directly to your chat—completely watermark-free and saveable!

---

## 🔥 Key Features

*   ⚡ **Turbo Parallel Downloader:** Utilizes raw MTProto chunks downloading via up to 8 concurrent threads (1MB parts) for extreme speed.
*   ⚡ **Smart File Caching:** Instantly forwards cached file IDs for repeat links without downloading again.
*   🎨 **Premium Telegram UI:** Beautifully formatted responses with Telegram's **new native button colors** (Success, Primary, Danger styles) and expressive icons.
*   🔄 **Dual-Engine Integration:** Python Telegram Bot (v22.7) for client interaction and Pyrogram for raw MTProto session fetching.
*   📁 **Universal Format Support:** Photos, high-definition Videos, Documents, Audios, Voice notes, Stickers, and Animations.
*   ☁️ **Cloud Ready:** Completely configured out-of-the-box for serverless deployment on Render.

---

## 🛠️ How It Works

Most telegram saver bots fail because they rely solely on the standard Telegram Bot API, which respects the server-side `protect_content` constraint. 

**Our bypass flow:**
1.  **Request:** Paste any valid `t.me/...` message link.
2.  **Fetch:** An internal MTProto userbot (authenticated session) fetches the raw message directly from Telegram.
3.  **Speedy Download:** The media is downloaded locally in parallel chunks.
4.  **Re-upload:** The file is clean-uploaded via the Bot API directly to your chat, allowing you to freely copy, save, or forward it!

---

## 🚀 Quick Setup & Installation

### Prerequisites
*   Python 3.8+ installed.
*   Telegram `API_ID` & `API_HASH` (get it from [my.telegram.org](https://my.telegram.org)).
*   Telegram `BOT_TOKEN` (get it from [@BotFather](https://t.me/BotFather)).

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/ajisth69/telegram-content-bypass.git
cd telegram-content-bypass/pyrogram-userbot
pip install -r requirements.txt
```

### 2. Generate Session String
Authenticate your userbot session by running the helper script:
```bash
python gen_session.py
```
Copy the generated `SESSION_STRING` output.

### 3. Environment Variables
Create a `.env` file inside the `pyrogram-userbot/` folder:
```ini
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
SESSION_STRING=your_session_string
```

### 4. Run the Bot
```bash
python main.py
```

---

## ☁️ Deploy to Render

This repository is ready for immediate deployment on **Render** using the provided `render.yaml` profile:

1. Fork this repository.
2. Go to [Render Dashboard](https://dashboard.render.com/) -> **Blueprints**.
3. Connect your repository.
4. Provide environment variables (`BOT_TOKEN`, `SESSION_STRING`) when prompted.
5. Deploy!

---

## 📄 API & Buttons Feature Note
This bot makes use of Telegram Bot API 9.4+ features, utilizing:
*   `style="success"` (Green button style)
*   `style="primary"` (Blue button style)
*   `style="danger"` (Red button style)

Ensure your Telegram client is updated to the latest version to view the colored interactive buttons properly!

---

## 🤝 Contributing
Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://github.com/ajisth69/telegram-content-bypass/issues).

---

## 📝 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

### 🏷️ SEO Tags
`telegram` `telegram-bot` `telegram-userbot` `pyrogram` `telegram-bypass` `content-saver` `protect-content-bypass` `telegram-saver-bot` `save-restricted-content` `tg-bypass` `python-telegram-bot` `turbo-download` `media-downloader` `telegram-media-saver` `telegram-restricted`
