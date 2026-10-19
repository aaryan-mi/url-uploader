import asyncio
import os
import re
import shutil
import time
import urllib.parse
import aiohttp

# Workaround for Python 3.12+ asyncio event loop initialization with Pyrogram
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config

os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)

app = Client(
    "laptop_uploader",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN
)

# Active link sessions: {user_id: dict}
USER_SESSIONS = {}

# Active tasks tracking: {user_id: {"proc": Process, "cancelled": bool}}
ACTIVE_TASKS = {}

def humanbytes(size):
    if not size:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.2f} {unit}"

def cancel_markup(user_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Task", callback_data=f"cancel_{user_id}")]
    ])

def is_cancelled(user_id: int) -> bool:
    return ACTIVE_TASKS.get(user_id, {}).get("cancelled", False)

def is_authorized(message: Message) -> bool:
    if not config.ALLOWED_USERS:
        return True
    u_id = message.from_user.id if message.from_user else None
    c_id = message.chat.id if message.chat else None
    return (u_id in config.ALLOWED_USERS) or (c_id in config.ALLOWED_USERS)

async def get_rclone_remote():
    cfg_remote = getattr(config, "RCLONE_REMOTE", "").strip()
    if cfg_remote:
        if not cfg_remote.endswith(":"):
            cfg_remote += ":"
        return cfg_remote

    if not shutil.which("rclone"):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "rclone", "listremotes",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        remotes = [r.strip() for r in stdout.decode().splitlines() if r.strip()]
        if not remotes:
            return None
        return remotes[0]
    except Exception:
        return None

def get_device_storage_dir():
    android_download = "/sdcard/Download"
    if os.path.exists(android_download):
        return android_download
    user_downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.exists(user_downloads):
        return user_downloads
    fallback = "./saved_files"
    os.makedirs(fallback, exist_ok=True)
    return fallback

# Progress bar for Telegram 2GB upload with cancellation check
async def upload_progress(current, total, client, status_message, start_time, user_id):
    if is_cancelled(user_id):
        client.stop_transmission()
        return

    now = time.time()
    diff = now - start_time
    if round(diff % 3.0) == 0 or current == total:
        pct = (current * 100) / total if total > 0 else 0
        speed = current / diff if diff > 0 else 0
        eta = round((total - current) / speed) if speed > 0 else 0

        filled = int(10 * current // total) if total > 0 else 0
        bar = '█' * filled + '░' * (10 - filled)

        text = (
            f"📤 **Uploading to Telegram (2GB Mode)**\n\n"
            f"[{bar}] {pct:.1f}%\n"
            f"⚡ **Speed:** {humanbytes(speed)}/s\n"
            f"📦 **Sent:** {humanbytes(current)} / {humanbytes(total)}\n"
            f"⏱️ **ETA:** {eta}s"
        )
        try:
            await status_message.edit_text(text, reply_markup=cancel_markup(user_id))
        except Exception:
            pass

# Progress bar for downloading Telegram media
async def tg_download_progress(current, total, client, status_message, start_time, user_id):
    if is_cancelled(user_id):
        client.stop_transmission()
        return

    now = time.time()
    diff = now - start_time
    if round(diff % 3.0) == 0 or current == total:
        pct = (current * 100) / total if total > 0 else 0
        speed = current / diff if diff > 0 else 0
        eta = round((total - current) / speed) if speed > 0 else 0

        filled = int(10 * current // total) if total > 0 else 0
        bar = '█' * filled + '░' * (10 - filled)

        text = (
            f"📥 **Downloading Telegram File...**\n\n"
            f"[{bar}] {pct:.1f}%\n"
            f"⚡ **Speed:** {humanbytes(speed)}/s\n"
            f"📦 **Downloaded:** {humanbytes(current)} / {humanbytes(total)}\n"
            f"⏱️ **ETA:** {eta}s"
        )
        try:
            await status_message.edit_text(text, reply_markup=cancel_markup(user_id))
        except Exception:
            pass

async def download_file_http(url: str, dest_path: str, status_message, user_id: int):
    start_time = time.time()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*"
    }
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver(), ssl=False)
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=120)

    async with aiohttp.ClientSession(connector=connector, headers=headers, timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status not in (200, 206):
                raise Exception(f"HTTP status {resp.status} - {resp.reason}")

            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 512):
                    if is_cancelled(user_id):
                        raise asyncio.CancelledError("Task was cancelled by user.")

                    f.write(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    diff = now - start_time
                    if total_size > 0 and (round(diff % 3.0) == 0 or downloaded == total_size):
                        pct = (downloaded * 100) / total_size
                        speed = downloaded / diff if diff > 0 else 0
                        filled = int(10 * downloaded // total_size)
                        bar = '█' * filled + '░' * (10 - filled)

                        try:
                            await status_message.edit_text(
                                f"📥 **Downloading...**\n\n"
                                f"[{bar}] {pct:.1f}%\n"
                                f"⚡ **Speed:** {humanbytes(speed)}/s\n"
                                f"📦 **Size:** {humanbytes(downloaded)} / {humanbytes(total_size)}",
                                reply_markup=cancel_markup(user_id)
                            )
                        except Exception:
                            pass

@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    if not is_authorized(message):
        return
    await message.reply_text(
        "⚡ **Universal 2GB Uploader Bot**\n\n"
        "**Supported Sources:**\n"
        "• Paste any web / direct download / video link\n"
        "• Send or forward any Telegram file / video / photo\n"
        "• Reply `/download` to any file in a group or chat\n\n"
        "**Destinations:**\n"
        "• 💬 Telegram Chat (Up to 2 GB)\n"
        "• 📱 Save directly to Phone / PC Storage\n"
        "• ☁️ Upload directly to Google Drive\n\n"
        "Cancel active tasks anytime with **❌ Cancel Task**."
    )

# 1. TELEGRAM FILE HANDLER (Directly sent files OR replying /download to a file)
@app.on_message((filters.document | filters.video | filters.audio | filters.photo) | (filters.command(["download", "dl", "save"]) & filters.reply))
async def media_handler(client: Client, message: Message):
    if not is_authorized(message):
        return

    # Check if this message has a URL instead of a file
    text_content = message.text or message.caption or ""
    if re.search(r"https?://\S+", text_content) and not message.reply_to_message:
        return await link_handler(client, message)

    target_msg = message.reply_to_message if message.reply_to_message and (message.reply_to_message.document or message.reply_to_message.video or message.reply_to_message.audio or message.reply_to_message.photo) else message
    media = target_msg.document or target_msg.video or target_msg.audio or (target_msg.photo[-1] if target_msg.photo else None)

    if not media:
        if message.command:
            await message.reply_text("ℹ️ Reply `/download` to any file, video, or photo to save it.")
        return

    file_name = getattr(media, "file_name", None) or f"file_{int(time.time())}.{'jpg' if target_msg.photo else 'dat'}"
    file_size = getattr(media, "file_size", 0)

    user_id = message.from_user.id if message.from_user else message.chat.id
    if user_id in ACTIVE_TASKS:
        await message.reply_text("⚠️ You already have an active task running. Please wait or cancel it first.")
        return

    USER_SESSIONS[user_id] = {
        "type": "telegram_media",
        "media_msg": target_msg,
        "file_name": file_name,
        "file_size": file_size,
        "chat_id": message.chat.id
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📱 Save to Phone Storage", callback_data="dest_phone"),
            InlineKeyboardButton("☁️ Google Drive", callback_data="dest_gdrive")
        ]
    ])

    await message.reply_text(
        f"📦 **Telegram File Detected:** `{file_name}`\n"
        f"📊 **Size:** `{humanbytes(file_size)}`\n\n"
        f"Where would you like to save this file?",
        reply_markup=keyboard
    )

# 2. URL HANDLER (Direct links & /download <url>)
@app.on_message((filters.regex(r"https?://") | filters.command(["download", "dl"])))
async def link_handler(client: Client, message: Message):
    if not is_authorized(message):
        return

    # Extract URL from text or command
    if message.command and len(message.command) > 1:
        url = message.command[1].strip()
    else:
        text = message.text or message.caption or ""
        match = re.search(r"https?://\S+", text)
        if not match:
            return
        url = match.group(0).strip()

    user_id = message.from_user.id if message.from_user else message.chat.id
    if user_id in ACTIVE_TASKS:
        await message.reply_text("⚠️ You already have an active task running. Please wait or cancel it first.")
        return

    USER_SESSIONS[user_id] = {
        "type": "url",
        "url": url,
        "chat_id": message.chat.id
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Telegram (Up to 2GB)", callback_data="dest_tg"),
            InlineKeyboardButton("📱 Save to Phone Storage", callback_data="dest_phone")
        ],
        [
            InlineKeyboardButton("☁️ Google Drive", callback_data="dest_gdrive")
        ]
    ])

    await message.reply_text("🎯 **Where would you like to save/upload this file?**", reply_markup=keyboard)

@app.on_callback_query(filters.regex(r"^cancel_"))
async def handle_cancel(client: Client, callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[1])
    if target_user_id != callback.from_user.id:
        await callback.answer("⚠️ This is not your task!", show_alert=True)
        return

    task_info = ACTIVE_TASKS.get(target_user_id)
    if not task_info:
        await callback.answer("No active task to cancel.")
        return

    task_info["cancelled"] = True
    proc = task_info.get("proc")
    if proc:
        try:
            proc.kill()
        except Exception:
            pass

    await callback.answer("Cancelling task...")
    try:
        await callback.message.edit_text("🚫 **Task has been cancelled.** Cleaned up temporary files.")
    except Exception:
        pass

@app.on_callback_query(filters.regex(r"^dest_"))
async def destination_chosen(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    dest = callback.data.split("_")[1]
    session = USER_SESSIONS.get(user_id)

    if not session:
        await callback.message.edit_text("❌ Session expired. Please send the link or file again.")
        return

    is_tg_media = session.get("type") == "telegram_media"
    target_chat_id = session.get("chat_id", callback.message.chat.id)

    # Register active task
    ACTIVE_TASKS[user_id] = {"proc": None, "cancelled": False}

    status = await callback.message.edit_text(
        "⏳ **Starting download engine...**",
        reply_markup=cancel_markup(user_id)
    )
    user_dir = os.path.join(config.DOWNLOAD_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)

    try:
        if is_tg_media:
            filename = session.get("file_name", f"file_{int(time.time())}")
            filepath = os.path.join(user_dir, filename)
            await status.edit_text(f"📥 **Downloading Telegram file:** `{filename}`...", reply_markup=cancel_markup(user_id))
            start_t = time.time()
            downloaded_path = await client.download_media(
                message=session["media_msg"],
                file_name=filepath,
                progress=tg_download_progress,
                progress_args=(client, status, start_t, user_id)
            )
            if is_cancelled(user_id):
                return
            if not downloaded_path or not os.path.exists(downloaded_path):
                raise Exception("Failed to download Telegram media file")
            filepath = downloaded_path
            filename = os.path.basename(filepath)
        else:
            url = session["url"]
            filename = os.path.basename(urllib.parse.urlparse(url).path) or f"file_{int(time.time())}"
            filepath = os.path.join(user_dir, filename)
            is_media_site = any(x in url for x in ["youtube.com", "youtu.be", "instagram.com", "twitter.com", "x.com", "tiktok.com"])

            if is_media_site:
                await status.edit_text("📥 **Extracting media with yt-dlp...**", reply_markup=cancel_markup(user_id))
                cmd = ["yt-dlp", "-P", user_dir, "-o", "%(title)s.%(ext)s", url]
                proc = await asyncio.create_subprocess_exec(*cmd)
                ACTIVE_TASKS[user_id]["proc"] = proc
                await proc.wait()

                if is_cancelled(user_id):
                    return

                files = os.listdir(user_dir)
                if not files:
                    raise Exception("yt-dlp failed to download media")
                filepath = os.path.join(user_dir, files[0])
                filename = files[0]
            else:
                try:
                    await download_file_http(url, filepath, status, user_id)
                except asyncio.CancelledError:
                    return
                except Exception as http_err:
                    if is_cancelled(user_id):
                        return
                    await status.edit_text("⏳ Retrying with generic engine...", reply_markup=cancel_markup(user_id))
                    cmd = ["yt-dlp", "-P", user_dir, "--no-playlist", url]
                    proc = await asyncio.create_subprocess_exec(*cmd)
                    ACTIVE_TASKS[user_id]["proc"] = proc
                    await proc.wait()

                    if is_cancelled(user_id):
                        return

                    files = os.listdir(user_dir)
                    if not files:
                        raise Exception(f"Download failed: {http_err}")
                    filepath = os.path.join(user_dir, files[0])
                    filename = files[0]

        if is_cancelled(user_id):
            return

        filesize = os.path.getsize(filepath)

        # 1. TELEGRAM UPLOAD
        if dest == "tg":
            if filesize > 2097152000:  # 2 GB
                await status.edit_text(f"⚠️ File is {humanbytes(filesize)} (>2 GB). Telegram allows max 2 GB.")
                return

            await status.edit_text(
                f"📤 Preparing Telegram upload: **{filename}** ({humanbytes(filesize)})",
                reply_markup=cancel_markup(user_id)
            )
            start_t = time.time()
            try:
                await client.send_document(
                    chat_id=target_chat_id,
                    document=filepath,
                    caption=f"📁 `{filename}`\n📦 `{humanbytes(filesize)}`",
                    progress=upload_progress,
                    progress_args=(client, status, start_t, user_id)
                )
                if not is_cancelled(user_id):
                    await status.delete()
            except Exception as e:
                if is_cancelled(user_id):
                    return
                await status.edit_text(f"❌ Upload Error: {e}")

        # 2. SAVE TO PHONE / LOCAL DEVICE STORAGE
        elif dest == "phone":
            storage_dir = get_device_storage_dir()
            target_path = os.path.join(storage_dir, filename)

            # Avoid overwriting existing files
            base_name, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(target_path):
                target_path = os.path.join(storage_dir, f"{base_name}_{counter}{ext}")
                counter += 1

            shutil.move(filepath, target_path)
            await status.edit_text(
                f"✅ **Saved to Device Storage!**\n\n"
                f"📄 **File:** `{os.path.basename(target_path)}`\n"
                f"📦 **Size:** `{humanbytes(filesize)}`\n"
                f"📍 **Location:** `{target_path}`"
            )

        # 3. GOOGLE DRIVE UPLOAD
        elif dest == "gdrive":
            if not shutil.which("rclone"):
                await status.edit_text(
                    f"ℹ️ File downloaded ({humanbytes(filesize)}).\n\n"
                    f"`rclone` is not installed on this system. On Termux, run `pkg install rclone` to enable Google Drive."
                )
            else:
                remote_name = await get_rclone_remote()
                if not remote_name:
                    await status.edit_text(
                        f"❌ **No Google Drive remote configured in rclone!**\n\n"
                        f"Please run `rclone config` in terminal and create a remote (e.g. `gdrive`)."
                    )
                    return

                upload_folder = getattr(config, "RCLONE_FOLDER", "TelegramUploads").strip("/")
                dest_target = f"{remote_name}{upload_folder}" if upload_folder else remote_name

                await status.edit_text(
                    f"☁️ Uploading **{filename}** to Google Drive (`{dest_target}`)...",
                    reply_markup=cancel_markup(user_id)
                )
                cmd = [
                    "rclone", "copy", filepath, dest_target,
                    "--drive-chunk-size", "32M",
                    "--stats", "3s",
                    "-v"
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                ACTIVE_TASKS[user_id]["proc"] = proc

                last_edit = 0
                err_buffer = []

                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    raw = line.decode("utf-8", errors="ignore").strip()
                    err_buffer.append(raw)
                    if len(err_buffer) > 25:
                        err_buffer.pop(0)

                    if is_cancelled(user_id):
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        return

                    now = time.time()
                    if now - last_edit > 3.0:
                        m = re.search(r"Transferred:\s+([\d\.]+\s+[KMGTP]?i?B)\s+/\s+([\d\.]+\s+[KMGTP]?i?B),\s*(\d+)%,\s*([\d\.]+\s+[KMGTP]?i?B/s),\s*ETA\s*(\S+)", raw)
                        if m:
                            sent, tot, pct_s, spd, eta = m.groups()
                            try:
                                pct = int(pct_s.replace("%", "").strip())
                            except Exception:
                                pct = 0
                            filled = int(10 * pct // 100)
                            bar = '█' * filled + '░' * (10 - filled)
                            last_edit = now
                            try:
                                await status.edit_text(
                                    f"☁️ **Uploading to Google Drive...**\n\n"
                                    f"[{bar}] {pct}%\n"
                                    f"⚡ **Speed:** {spd}\n"
                                    f"📦 **Sent:** {sent} / {tot}\n"
                                    f"⏱️ **ETA:** {eta}",
                                    reply_markup=cancel_markup(user_id)
                                )
                            except Exception:
                                pass

                await proc.wait()

                if is_cancelled(user_id):
                    return

                if proc.returncode != 0:
                    err = "\n".join(err_buffer[-6:]) or f"Exit code {proc.returncode}"
                    await status.edit_text(
                        f"❌ **Google Drive Upload Failed:**\n\n"
                        f"⚠️ Error from rclone:\n`{err[:400]}`\n\n"
                        f"Run `rclone config` to verify your Google Drive login."
                    )
                else:
                    drive_url = ""
                    try:
                        link_proc = await asyncio.create_subprocess_exec(
                            "rclone", "link", f"{dest_target}/{filename}",
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        l_out, _ = await link_proc.communicate()
                        drive_url = l_out.decode().strip()
                    except Exception:
                        pass

                    link_text = f"\n🔗 **Link:** {drive_url}" if drive_url and drive_url.startswith("http") else ""
                    await status.edit_text(
                        f"✅ **Uploaded to Google Drive!**\n\n"
                        f"📁 **File:** `{filename}`\n"
                        f"📦 **Size:** `{humanbytes(filesize)}`\n"
                        f"📂 **Folder:** `{dest_target}`{link_text}"
                    )

    except Exception as e:
        if not is_cancelled(user_id):
            await status.edit_text(f"❌ Error: {e}")
    finally:
        # Guarantee cleanup of disk storage
        shutil.rmtree(user_dir, ignore_errors=True)
        USER_SESSIONS.pop(user_id, None)
        ACTIVE_TASKS.pop(user_id, None)

if __name__ == "__main__":
    print("🚀 Starting Pyrogram Uploader Bot (Telegram Media & URL modes active)...")
    app.run()
