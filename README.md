# Universal 2GB Telegram Uploader Bot

A high-performance Telegram bot built with **Pyrogram (MTProto)** that downloads files from direct links, social media videos, or torrents, and provides three storage destinations:
- **Telegram Chat** (up to 2 GB per file)
- **Local Device / Phone Storage** (`/sdcard/Download` or `Downloads`)
- **Google Drive** (via `rclone`)

---

## Features
- **2 GB File Uploads**: Uses Telegram MTProto protocol to bypass the standard 50 MB Bot API limitation.
- **Save to Device Storage**: Downloads files directly to `/sdcard/Download` on Android (Termux) or your `Downloads` directory on PC.
- **Universal Media Downloader**: Supports direct download links and video sites via `yt-dlp` (YouTube, Twitter/X, Instagram, etc.).
- **Live Progress Tracking**: Real-time display of percentage, transfer speed, bytes uploaded, and ETA.
- **Task Cancellation**: Interactive button to instantly halt active downloads or uploads.
- **User Whitelist**: Restricts execution strictly to authorized Telegram user IDs.

---

## Prerequisites
1. **Bot Token**: Obtained from [@BotFather](https://t.me/BotFather) on Telegram.
2. **API ID & API Hash**: Obtained from [my.telegram.org](https://my.telegram.org) under *API development tools*.
3. **Telegram User ID**: Obtained from [@userinfobot](https://t.me/userinfobot) on Telegram.

---

## Setup & Running on Windows

### 1. Clone the Repository
```powershell
git clone https://github.com/aaryan-mi/url-uploader.git
cd url-uploader
```

### 2. Set Up Virtual Environment
```powershell
python -m venv venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
```powershell
pip install -r requirements.txt
```

### 4. Configure Credentials
Edit `config.py` with your credentials:
```python
API_ID = 12345678
API_HASH = "your_api_hash_here"
BOT_TOKEN = "your_bot_token"
ALLOWED_USERS = [123456789]
DOWNLOAD_DIR = "./downloads"
```

### 5. Start the Bot
```powershell
python bot.py
```

---

## Setup & Running on Android (Termux)

### 1. Install System Packages
```bash
pkg update && pkg upgrade -y
pkg install python git clang tmux aria2 rclone libffi -y
```

### 2. Grant Storage Access
```bash
termux-setup-storage
```

### 3. Clone the Repository
```bash
git clone https://github.com/aaryan-mi/url-uploader.git
cd url-uploader
```

### 4. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Configure Credentials
```bash
nano config.py
```
*(Enter your credentials, save with `Ctrl + O`, `Enter`, and exit with `Ctrl + X`)*

### 6. Run in Background (24/7)
```bash
termux-wake-lock
tmux new -s uploader
python bot.py
```
- Detach from session: `Ctrl + B`, then press `D`.
- Re-attach later: `tmux attach -t uploader`.

---

## Usage
1. Open the bot in Telegram and send `/start`.
2. Paste any direct download link or video link.
3. Select the desired destination:
   - `💬 Telegram (Up to 2GB)`: Sends the file directly to your Telegram chat.
   - `📱 Save to Phone Storage`: Saves the file straight into device storage.
   - `☁️ Google Drive`: Copies the file to Google Drive.
4. Click `❌ Cancel Task` to abort an active transfer at any time.
