#test
import sys
import subprocess

# ═════════════════════════════════════════════════════════════════════════════
# TỰ ĐỘNG CÀI ĐẶT THƯ VIỆN CẦN THIẾT
# ═════════════════════════════════════════════════════════════════════════════

REQUIRED_PACKAGES = {
    "telegram":         "python-telegram-bot",
    "nacl":             "pynacl",
    "requests":         "requests",
    "winrm":            "pywinrm",
    "firebase_admin":   "firebase-admin",
}

def install_package(pip_name: str):
    print(f"📦 Đang cài: {pip_name} ...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", pip_name, "--quiet"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"  ✅ Cài thành công: {pip_name}")
    else:
        print(f"  ⚠️  Lỗi khi cài {pip_name}: {result.stderr.strip()}")

def auto_install():
    missing = []
    for module, pip_name in REQUIRED_PACKAGES.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print("🔍 Phát hiện thư viện chưa được cài đặt:")
        for pkg in missing:
            print(f"   • {pkg}")
        print()
        for pkg in missing:
            install_package(pkg)
        print("\n✅ Hoàn tất cài đặt! Đang khởi động bot...\n")
    else:
        print("✅ Tất cả thư viện đã được cài đặt.\n")

auto_install()

# ═════════════════════════════════════════════════════════════════════════════

import os
import zipfile
import io
import requests
import base64
import time
import logging
import re
import threading
import tempfile
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           filters, ContextTypes, ConversationHandler,
                           CallbackQueryHandler)
import asyncio
from nacl import encoding, public
import secrets
import string

# ── Firebase Admin SDK ────────────────────────────────────────────────────────
try:
    import firebase_admin
    from firebase_admin import credentials, db as firebase_db
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

# ── Cố gắng import winrm (tuỳ chọn) ─────────────────────────────────────────
try:
    import winrm
    WINRM_AVAILABLE = True
except ImportError:
    WINRM_AVAILABLE = False

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

GITHUB_TOKEN_STATE, TAILSCALE_KEY_STATE, TAILSCALE_API_STATE, DURATION_STATE, CONFIRM_STATE = range(5)
REMOTE_IP_STATE, REMOTE_USER_STATE, REMOTE_PASS_STATE = range(5, 8)
SETTINGS_GH_STATE, SETTINGS_TS_STATE, SETTINGS_API_STATE = range(8, 11)
FEEDBACK_TEXT_STATE = 11

# ── Lưu trạng thái người dùng ────────────────────────────────────────────────
user_data = {}
active_sessions = {}
remote_sessions = {}

BOT_TOKEN = os.environ.get("BOT_TOKEN", "7000771103:AAGttf2jhIYuaT5063iabVwZsA4isgE-LLw")
ADMIN_ID  = 5738766741

NORMAL_MIN = 60
NORMAL_MAX = 180
ADMIN_MIN  = 15
ADMIN_MAX  = 360

BOT_FILE_URL = "https://raw.githubusercontent.com/phuctrongytb16-ctrl/Win11/main/h.py"

# ── Firebase Configuration ────────────────────────────────────────────────────
FIREBASE_CONFIG = {
    "apiKey": "AIzaSyAJh6-2mxzFADaA_qNlw-MAXZ_wc9zgKL4",
    "authDomain": "sever-login-ae5cc.firebaseapp.com",
    "databaseURL": "https://sever-login-ae5cc-default-rtdb.firebaseio.com",
    "projectId": "sever-login-ae5cc",
    "storageBucket": "sever-login-ae5cc.firebasestorage.app",
    "messagingSenderId": "966951494514",
    "appId": "1:966951494514:web:2663ca6c5814108716b3eb",
    "measurementId": "G-6C22LDBYGK"
}

FIREBASE_DB_URL = FIREBASE_CONFIG["databaseURL"]


# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE HELPERS — Dùng REST API (không cần service account)
# ═════════════════════════════════════════════════════════════════════════════

def firebase_get(path: str) -> Optional[dict]:
    """Lấy dữ liệu từ Firebase Realtime Database qua REST."""
    try:
        url = f"{FIREBASE_DB_URL}/{path}.json"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"Firebase GET error ({path}): {e}")
    return None


def firebase_set(path: str, data: dict) -> bool:
    """Ghi dữ liệu lên Firebase Realtime Database qua REST."""
    try:
        url = f"{FIREBASE_DB_URL}/{path}.json"
        r = requests.put(url, json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Firebase SET error ({path}): {e}")
    return False


def firebase_delete(path: str) -> bool:
    """Xóa dữ liệu khỏi Firebase Realtime Database qua REST."""
    try:
        url = f"{FIREBASE_DB_URL}/{path}.json"
        r = requests.delete(url, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Firebase DELETE error ({path}): {e}")
    return False



# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE — Lưu / lấy token của user
# ═════════════════════════════════════════════════════════════════════════════

def save_user_tokens(user_id: int, github_token: str,
                     tailscale_key: str, tailscale_api_key: str) -> bool:
    """Lưu 3 token của user lên Firebase."""
    data = {
        "github_token":      github_token,
        "tailscale_key":     tailscale_key,
        "tailscale_api_key": tailscale_api_key,
        "saved_at": datetime.now(timezone(timedelta(hours=7))).strftime("%H:%M:%S %d/%m/%Y"),
    }
    return firebase_set(f"user_tokens/{user_id}", data)


def get_user_tokens(user_id: int) -> Optional[dict]:
    """Lấy token đã lưu của user từ Firebase."""
    return firebase_get(f"user_tokens/{user_id}")


def delete_user_tokens(user_id: int) -> bool:
    """Xóa token đã lưu của user."""
    return firebase_delete(f"user_tokens/{user_id}")


# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE — Lưu / lấy lịch sử máy của user
# ═════════════════════════════════════════════════════════════════════════════

def save_rdp_history(user_id: int, ip: str, rdp_user: str,
                     duration_minutes: int, start_ts: float) -> bool:
    """Lưu 1 bản ghi lịch sử vào Firebase (max 20 bản ghi)."""
    expire_ts = start_ts + duration_minutes * 60
    tz_vn = timezone(timedelta(hours=7))
    record = {
        "ip":               ip,
        "rdp_user":         rdp_user,
        "duration_minutes": duration_minutes,
        "created_at":       datetime.fromtimestamp(start_ts, tz=tz_vn).strftime("%H:%M:%S %d/%m/%Y"),
        "expires_at":       datetime.fromtimestamp(expire_ts, tz=tz_vn).strftime("%H:%M:%S %d/%m/%Y"),
        "created_ts":       start_ts,
    }
    # Lấy history hiện tại
    history = firebase_get(f"rdp_history/{user_id}") or []
    if isinstance(history, dict):
        history = list(history.values())
    history.append(record)
    # Chỉ giữ 20 bản ghi gần nhất
    if len(history) > 20:
        history = history[-20:]
    return firebase_set(f"rdp_history/{user_id}", history)


def get_rdp_history(user_id: int) -> list:
    """Lấy lịch sử máy của user từ Firebase."""
    data = firebase_get(f"rdp_history/{user_id}")
    if not data:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.values())
    return []


def save_rdp_to_firebase(user_id: int, username_tg: str, ip: str,
                          rdp_user: str, rdp_pass: str,
                          duration_minutes: int, start_ts: float) -> bool:
    """Lưu thông tin RDP lên Firebase."""
    expire_ts = start_ts + duration_minutes * 60
    data = {
        "user_id":          user_id,
        "username_telegram": username_tg,
        "ip":               ip,
        "rdp_user":         rdp_user,
        "rdp_pass":         rdp_pass,
        "duration_minutes": duration_minutes,
        "created_at":       datetime.fromtimestamp(start_ts, tz=timezone(timedelta(hours=7))).strftime("%H:%M:%S %d/%m/%Y (GMT+7)"),
        "expires_at":       datetime.fromtimestamp(expire_ts, tz=timezone(timedelta(hours=7))).strftime("%H:%M:%S %d/%m/%Y (GMT+7)"),
        "created_ts":       start_ts,
        "expires_ts":       expire_ts,
        "status":           "active"
    }
    return firebase_set(f"rdp_sessions/{user_id}", data)


def get_rdp_from_firebase(user_id: int) -> Optional[dict]:
    """Lấy thông tin RDP của user từ Firebase."""
    return firebase_get(f"rdp_sessions/{user_id}")


def mark_rdp_expired_firebase(user_id: int) -> bool:
    """Đánh dấu RDP đã hết hạn trên Firebase."""
    data = firebase_get(f"rdp_sessions/{user_id}")
    if data:
        data["status"] = "expired"
        return firebase_set(f"rdp_sessions/{user_id}", data)
    return False


def check_user_has_active_rdp_firebase(user_id: int) -> Optional[dict]:
    """
    Kiểm tra user có RDP đang active trên Firebase không.
    Trả về dict nếu còn hạn, None nếu không.
    """
    data = firebase_get(f"rdp_sessions/{user_id}")
    if not data:
        return None
    if data.get("status") != "active":
        return None
    expire_ts = data.get("expires_ts", 0)
    if time.time() < expire_ts:
        return data
    # Hết hạn → cập nhật status
    mark_rdp_expired_firebase(user_id)
    return None


# ═════════════════════════════════════════════════════════════════════════════
# WINRM — KẾT NỐI MÁY ẢO KHÁC
# ═════════════════════════════════════════════════════════════════════════════

def winrm_connect(ip: str, username: str, password: str):
    if not WINRM_AVAILABLE:
        return None
    try:
        session = winrm.Session(
            f'http://{ip}:5985/wsman',
            auth=(username, password),
            transport='ntlm'
        )
        r = session.run_cmd('echo', ['OK'])
        if r.status_code == 0:
            return session
    except Exception as e:
        logger.error(f"WinRM connect error: {e}")
    return None


def winrm_run_bot(session, username: str, file_url: str) -> dict:
    remote_path = f"C:\\Users\\{username}\\h.py"
    results = {}
    try:
        dl_cmd = f'curl -L {file_url} -o {remote_path}'
        r = session.run_cmd("cmd", ["/c", dl_cmd])
        results['download'] = {
            'stdout': r.std_out.decode(errors='replace'),
            'stderr': r.std_err.decode(errors='replace'),
            'ok': r.status_code == 0
        }
        time.sleep(3)

        r = session.run_cmd("cmd", ["/c",
            "python -m pip install python-telegram-bot pynacl requests pywin32 pillow"])
        results['install'] = {
            'stdout': r.std_out.decode(errors='replace'),
            'stderr': r.std_err.decode(errors='replace'),
            'ok': r.status_code == 0
        }
        time.sleep(3)

        r = session.run_cmd("cmd", ["/c",
            f"start cmd /k python {remote_path}"])
        results['run'] = {
            'stdout': r.std_out.decode(errors='replace'),
            'ok': r.status_code == 0
        }
    except Exception as e:
        results['error'] = str(e)
    return results


# ═════════════════════════════════════════════════════════════════════════════
# /connect — Lệnh kết nối máy ảo từ xa
# ═════════════════════════════════════════════════════════════════════════════

async def connect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WINRM_AVAILABLE:
        await update.message.reply_text(
            "⚠️ Thư viện <code>pywinrm</code> chưa được cài.\n"
            "Chạy: <code>pip install pywinrm</code>",
            parse_mode='HTML')
        return ConversationHandler.END

    user_id  = update.effective_user.id
    is_admin = (user_id == ADMIN_ID)
    user_data[user_id] = {'_connect': True, '_is_admin': is_admin}
    await update.message.reply_text(
        "╔══════════════════════════╗\n"
        "║  🔌  KẾT NỐI MÁY TỪ XA  ║\n"
        "╚══════════════════════════╝\n\n"
        + ("🔑 <b>Chế độ Admin</b> — Deploy bot\n\n" if is_admin else
           "👤 <b>Chế độ thường</b> — Kiểm tra kết nối & chụp màn hình\n\n") +
        "📍 <b>Bước 1/3</b> — Nhập địa chỉ IP:",
        parse_mode='HTML')
    return REMOTE_IP_STATE


async def get_remote_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ip = update.message.text.strip()
    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        await update.message.reply_text("❌ IP không hợp lệ. Vui lòng nhập lại:")
        return REMOTE_IP_STATE
    user_data[user_id]['remote_ip'] = ip
    await update.message.reply_text(
        f"✅ IP đã nhận: <code>{ip}</code>\n\n"
        "📍 <b>Bước 2/3</b> — Nhập Username:",
        parse_mode='HTML')
    return REMOTE_USER_STATE


async def get_remote_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id]['remote_username'] = update.message.text.strip()
    await update.message.reply_text(
        "✅ Username đã nhận!\n\n"
        "📍 <b>Bước 3/3</b> — Nhập Password:",
        parse_mode='HTML')
    return REMOTE_PASS_STATE


async def get_remote_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    password = update.message.text.strip()
    ip       = user_data[user_id]['remote_ip']
    username = user_data[user_id]['remote_username']
    is_admin = user_data[user_id].get('_is_admin', False)

    msg = await update.message.reply_text(
        "🔄 Đang thiết lập kết nối, vui lòng chờ...")

    loop = asyncio.get_running_loop()
    session = await loop.run_in_executor(None, lambda: winrm_connect(ip, username, password))

    if not session:
        await msg.edit_text(
            "╔══════════════════════════╗\n"
            "║  ❌  KẾT NỐI THẤT BẠI   ║\n"
            "╚══════════════════════════╝\n\n"
            "Vui lòng kiểm tra:\n"
            "• Địa chỉ IP\n"
            "• Thông tin đăng nhập\n"
            "• WinRM đã được bật chưa\n\n"
            "Dùng /connect để thử lại.")
        user_data.pop(user_id, None)
        return ConversationHandler.END

    remote_sessions[user_id] = {
        'ip': ip, 'username': username, 'password': password, 'session': session
    }

    if is_admin:
        # Admin: deploy bot như cũ
        await msg.edit_text(
            "╔══════════════════════════════╗\n"
            "║  ✅  KẾT NỐI THÀNH CÔNG!    ║\n"
            "╚══════════════════════════════╝\n\n"
            f"🖥️ IP       : <code>{ip}</code>\n"
            f"👤 Username : <code>{username}</code>\n\n"
            "🤖 Đang triển khai bot trên máy từ xa...",
            parse_mode='HTML')
        asyncio.create_task(run_remote_bot_task(context.bot, user_id, session, username))
    else:
        # Người thường: chỉ chụp màn hình và gửi lại
        await msg.edit_text(
            "╔══════════════════════════════╗\n"
            "║  ✅  KẾT NỐI THÀNH CÔNG!    ║\n"
            "╚══════════════════════════════╝\n\n"
            f"🖥️ IP       : <code>{ip}</code>\n"
            f"👤 Username : <code>{username}</code>\n\n"
            "📸 Đang chụp màn hình để kiểm tra...",
            parse_mode='HTML')
        asyncio.create_task(run_screenshot_task(context.bot, user_id, session))

    user_data.pop(user_id, None)
    return ConversationHandler.END


async def run_remote_bot_task(bot, user_id: int, session, username: str):
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(None, lambda:
            winrm_run_bot(session, username, BOT_FILE_URL))

        dl  = results.get('download', {})
        ins = results.get('install', {})
        run = results.get('run', {})
        err = results.get('error', '')

        if err:
            text = (
                "╔══════════════════════════╗\n"
                "║  ❌  LỖI TRIỂN KHAI BOT  ║\n"
                "╚══════════════════════════╝\n\n"
                f"Chi tiết: <code>{err}</code>"
            )
        else:
            def status_icon(ok): return "✅" if ok else "❌"
            text = (
                "╔══════════════════════════════╗\n"
                "║  📋  KẾT QUẢ TRIỂN KHAI BOT  ║\n"
                "╚══════════════════════════════╝\n\n"
                f"{status_icon(dl.get('ok'))}  Tải file bot\n"
                f"{status_icon(ins.get('ok'))}  Cài thư viện\n"
                f"{status_icon(run.get('ok'))}  Khởi chạy bot\n\n"
                "💡 Bot đã được kích hoạt trên máy từ xa."
            )

        await bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Remote bot task error: {e}")
        await bot.send_message(chat_id=user_id,
                               text=f"❌ Lỗi khi triển khai bot: <code>{e}</code>",
                               parse_mode='HTML')


async def run_screenshot_task(bot, user_id: int, session):
    """Chụp màn hình RDP và gửi cho người dùng (dành cho người thường)."""
    loop = asyncio.get_running_loop()
    try:
        # Chạy lệnh PowerShell chụp màn hình và lưu vào file tạm
        screenshot_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
            "$bmp = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height); "
            "$g = [System.Drawing.Graphics]::FromImage($bmp); "
            "$g.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size); "
            "$path = \"$env:TEMP\\rdp_screenshot.png\"; "
            "$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png); "
            "Write-Host $path"
        )
        r = await loop.run_in_executor(None, lambda:
            session.run_ps(screenshot_cmd))

        if r.status_code != 0:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "╔══════════════════════════════╗\n"
                    "║  ✅  RDP ĐANG HOẠT ĐỘNG      ║\n"
                    "╚══════════════════════════════╝\n\n"
                    "✅ Kết nối WinRM thành công!\n"
                    "⚠️ Không thể chụp màn hình (có thể màn hình chưa hiển thị).\n\n"
                    "💡 RDP của bạn đang chạy bình thường."
                ),
                parse_mode='HTML')
            return

        # Đọc file ảnh từ máy từ xa qua WinRM
        remote_path = r.std_out.decode(errors='replace').strip().splitlines()[-1].strip()
        read_cmd = f"[Convert]::ToBase64String([IO.File]::ReadAllBytes('{remote_path}'))"
        r2 = await loop.run_in_executor(None, lambda:
            session.run_ps(read_cmd))

        if r2.status_code != 0 or not r2.std_out:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "╔══════════════════════════════╗\n"
                    "║  ✅  RDP ĐANG HOẠT ĐỘNG      ║\n"
                    "╚══════════════════════════════╝\n\n"
                    "✅ Kết nối thành công, RDP hoạt động tốt!\n"
                    "⚠️ Không thể đọc ảnh chụp màn hình.\n\n"
                    "💡 Bạn có thể kết nối RDP bình thường."
                ),
                parse_mode='HTML')
            return

        b64_data = r2.std_out.decode(errors='replace').strip()
        img_bytes = base64.b64decode(b64_data)

        await bot.send_photo(
            chat_id=user_id,
            photo=io.BytesIO(img_bytes),
            caption=(
                "╔══════════════════════════════╗\n"
                "║  ✅  RDP ĐANG HOẠT ĐỘNG      ║\n"
                "╚══════════════════════════════╝\n\n"
                "📸 Màn hình hiện tại của máy ảo.\n"
                "✅ Máy đang chạy bình thường!\n\n"
                "💡 Dùng /check để xem thông tin chi tiết."
            )
        )

    except Exception as e:
        logger.error(f"Screenshot task error: {e}")
        # Nếu lỗi chụp màn hình, vẫn thông báo kết nối thành công
        await bot.send_message(
            chat_id=user_id,
            text=(
                "╔══════════════════════════════╗\n"
                "║  ✅  RDP ĐANG HOẠT ĐỘNG      ║\n"
                "╚══════════════════════════════╝\n\n"
                "✅ Kết nối WinRM thành công!\n"
                f"⚠️ Không thể chụp màn hình: <code>{e}</code>\n\n"
                "💡 Máy của bạn đang chạy bình thường."
            ),
            parse_mode='HTML'
        )


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def generate_password(length=14) -> str:
    upper  = string.ascii_uppercase
    lower  = string.ascii_lowercase
    digits = string.digits
    pwd = [
        secrets.choice(upper),  secrets.choice(upper),
        secrets.choice(lower),  secrets.choice(lower),
        secrets.choice(digits), secrets.choice(digits),
    ]
    alphabet = upper + lower + digits
    pwd += [secrets.choice(alphabet) for _ in range(length - len(pwd))]
    secrets.SystemRandom().shuffle(pwd)
    return ''.join(pwd)


def generate_username() -> str:
    suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return "User" + suffix


def encrypt_secret(public_key: str, secret_value: str) -> str:
    public_key_obj = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key_obj)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def parse_duration(text: str, user_id: int = 0) -> Optional[tuple]:
    is_admin = (user_id == ADMIN_ID)
    min_m = ADMIN_MIN  if is_admin else NORMAL_MIN
    max_m = ADMIN_MAX  if is_admin else NORMAL_MAX

    text = text.strip().lower().replace(' ', '')
    hours = 0
    minutes = 0

    pattern = re.match(r'^(?:(\d+)h)?(?:(\d+)p(?:h)?)?$', text)
    if not pattern:
        only_num = re.match(r'^(\d+)$', text)
        if only_num:
            minutes = int(only_num.group(1))
        else:
            return None
    else:
        h_part = pattern.group(1)
        m_part = pattern.group(2)
        if h_part is None and m_part is None:
            return None
        if h_part:
            hours = int(h_part)
        if m_part:
            minutes = int(m_part)

    total = hours * 60 + minutes
    if total < min_m or total > max_m:
        return None

    if hours > 0 and minutes > 0:
        display = f"{hours}h {minutes}p"
    elif hours > 0:
        display = f"{hours}h"
    else:
        display = f"{minutes}p"

    return total, display


def format_remaining(seconds: float) -> str:
    if seconds <= 0:
        return "Đã hết hạn"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}p {s:02d}s"
    elif m > 0:
        return f"{m}p {s:02d}s"
    else:
        return f"{s}s"


def format_datetime_vn(ts: float) -> str:
    """Định dạng timestamp sang giờ Việt Nam (UTC+7)."""
    tz_vn = timezone(timedelta(hours=7))
    dt = datetime.fromtimestamp(ts, tz=tz_vn)
    return dt.strftime("%H:%M:%S %d/%m/%Y (GMT+7)")


def create_progress_bar(percent: int, length: int = 12) -> str:
    filled = int(percent / 100 * length)
    bar = "▰" * filled + "▱" * (length - filled)
    return bar


def create_workflow_content(random_password: str, duration_minutes: int, rdp_username: str) -> str:
    duration_seconds = duration_minutes * 60
    timeout_minutes  = duration_minutes + 10
    return """name: Windows RDP

on:
  workflow_dispatch:

jobs:
  rdp:
    runs-on: windows-latest
    timeout-minutes: """ + str(timeout_minutes) + """

    steps:
      - name: Enable Remote Desktop
        shell: powershell
        run: |
          Set-ItemProperty -Path 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server' -Name "fDenyTSConnections" -Value 0
          Enable-NetFirewallRule -DisplayGroup "Remote Desktop"
          Restart-Service -Name TermService -Force

      - name: Rename Computer
        shell: powershell
        run: |
          Rename-Computer -NewName "AI-STV" -Force

      - name: Create RDP User
        shell: powershell
        run: |
          $rdpUser = \"""" + rdp_username + """\"
          $rdpPass = \"""" + random_password + """\"
          $securePass = ConvertTo-SecureString $rdpPass -AsPlainText -Force
          if (Get-LocalUser -Name $rdpUser -ErrorAction SilentlyContinue) { Remove-LocalUser -Name $rdpUser }
          New-LocalUser -Name $rdpUser -Password $securePass -AccountNeverExpires -PasswordNeverExpires
          Add-LocalGroupMember -Group "Administrators" -Member $rdpUser
          Add-LocalGroupMember -Group "Remote Desktop Users" -Member $rdpUser

      - name: Install Tailscale
        shell: powershell
        run: |
          $url = "https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi"
          $file = "$env:TEMP\\tailscale.msi"
          $success = $false
          for ($try = 1; $try -le 3 -and -not $success; $try++) {
            try {
              Invoke-WebRequest $url -OutFile $file -UseBasicParsing -TimeoutSec 60
              if ((Test-Path $file) -and (Get-Item $file).Length -gt 1MB) {
                Start-Process msiexec.exe -ArgumentList "/i `"$file`" /quiet /norestart" -Wait
                $success = $true
              }
            } catch {
              Write-Host "Attempt $try failed, retrying..."
              Start-Sleep -Seconds 5
            }
          }
          if (-not $success) { exit 1 }

      - name: Connect Tailscale
        shell: powershell
        env:
          TAILSCALE_AUTH_KEY: ${{ secrets.TAILSCALE_AUTH_KEY }}
        run: |
          $tsExe = "$env:ProgramFiles\\Tailscale\\tailscale.exe"
          & $tsExe up --authkey="$env:TAILSCALE_AUTH_KEY" --hostname="github-rdp-${{ github.run_id }}" --accept-routes
          for ($i = 0; $i -lt 18; $i++) {
            Start-Sleep -Seconds 5
            $ip = (& $tsExe ip -4 2>$null) -replace '\\s',''
            if ($ip -match '^\\d+\\.\\d+\\.\\d+\\.\\d+$') {
              Write-Host "Tailscale connected: $ip"
              break
            }
            Write-Host "Waiting... ($i)"
          }

      - name: Report IP
        shell: powershell
        run: |
          $tsExe = "$env:ProgramFiles\\Tailscale\\tailscale.exe"
          $ip = (& $tsExe ip -4 2>$null) -replace '\\s',''
          Write-Host "TAILSCALE_IP=$ip"
          echo $ip | Out-File -FilePath "$env:GITHUB_WORKSPACE\\tailscale_ip.txt" -Encoding utf8

      - name: Upload IP Artifact
        uses: actions/upload-artifact@v4
        with:
          name: rdp-ip
          path: tailscale_ip.txt
          retention-days: 1

      - name: Keep Session Alive
        shell: powershell
        run: |
          Write-Host "Session active for """ + str(duration_minutes) + """ minutes..."
          Start-Sleep -Seconds """ + str(duration_seconds) + """
"""


# ─────────────────────────────────────────────────────────────────────────────
# GitHub / Tailscale helpers
# ─────────────────────────────────────────────────────────────────────────────

def gh_headers(token: str) -> dict:
    return {'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'}


def get_latest_run(token: str, username: str, repo: str) -> Optional[dict]:
    r = requests.get(
        f'https://api.github.com/repos/{username}/{repo}/actions/runs?per_page=1',
        headers=gh_headers(token))
    if r.status_code != 200:
        return None
    d = r.json()
    return d['workflow_runs'][0] if d['total_count'] > 0 else None


def get_jobs(token: str, username: str, repo: str, run_id: int) -> list:
    r = requests.get(
        f'https://api.github.com/repos/{username}/{repo}/actions/runs/{run_id}/jobs',
        headers=gh_headers(token))
    return r.json().get('jobs', []) if r.status_code == 200 else []


def tailscale_step_done(jobs: list) -> bool:
    for job in jobs:
        for step in job.get('steps', []):
            name   = step.get('name', '').lower()
            status = step.get('status', '')
            if 'report ip' in name and status == 'completed':
                return True
            if 'upload ip artifact' in name and status == 'completed':
                return True
    for job in jobs:
        for step in job.get('steps', []):
            if 'connect tailscale' in step.get('name', '').lower():
                if step.get('status') == 'completed':
                    return True
    return False


def workflow_finished(jobs: list) -> bool:
    return any(job.get('status') == 'completed' for job in jobs)


def get_ip_from_artifact(github_token: str, username: str, repo: str,
                          run_id: int) -> Optional[str]:
    headers = gh_headers(github_token)
    r = requests.get(
        f'https://api.github.com/repos/{username}/{repo}/actions/runs/{run_id}/artifacts',
        headers=headers, timeout=10)
    if r.status_code != 200:
        return None
    for art in r.json().get('artifacts', []):
        if art.get('name') == 'rdp-ip':
            dl = requests.get(art['archive_download_url'], headers=headers,
                               timeout=30, allow_redirects=True)
            if dl.status_code != 200:
                continue
            try:
                z = zipfile.ZipFile(io.BytesIO(dl.content))
                for name in z.namelist():
                    text = z.read(name).decode('utf-8', errors='replace').strip()
                    ip   = text.splitlines()[0].strip()
                    if re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
                        return ip
            except Exception as e:
                logger.error(f"Artifact parse error: {e}")
    return None


def get_tailscale_ip_from_api(tailscale_api_key: str, run_id: int) -> Optional[str]:
    exact_hostname = f"github-rdp-{run_id}"
    headers = {'Authorization': f'Bearer {tailscale_api_key}'}
    r = requests.get('https://api.tailscale.com/api/v2/tailnet/-/devices',
                     headers=headers, timeout=10)
    if r.status_code != 200:
        return None
    for device in r.json().get('devices', []):
        hostname = device.get('hostname', '') or device.get('name', '')
        if hostname == exact_hostname or hostname.startswith(exact_hostname):
            for addr in device.get('addresses', []):
                if re.match(r'^\d+\.\d+\.\d+\.\d+$', addr):
                    return addr
    return None


def delete_workflow_file(github_token: str, username: str, repo_name: str) -> bool:
    headers = gh_headers(github_token)
    r = requests.get(
        f'https://api.github.com/repos/{username}/{repo_name}/contents/.github/workflows/windows-rdp.yml',
        headers=headers, timeout=10)
    if r.status_code != 200:
        return False
    sha = r.json().get('sha')
    if not sha:
        return False
    dr = requests.delete(
        f'https://api.github.com/repos/{username}/{repo_name}/contents/.github/workflows/windows-rdp.yml',
        headers=headers,
        json={'message': 'Remove workflow file for security', 'sha': sha},
        timeout=10)
    return dr.status_code == 200


async def do_delete_repo(loop, github_token: str, username: str,
                          repo_name: str, bot, user_id: int):
    try:
        r = await loop.run_in_executor(None, lambda:
            requests.delete(
                f'https://api.github.com/repos/{username}/{repo_name}',
                headers=gh_headers(github_token)))
        if r.status_code == 204:
            logger.info(f"Repo {repo_name} deleted")
            await bot.send_message(
                chat_id=user_id,
                text="Xong")
        else:
            logger.warning(f"Delete repo failed: {r.status_code}")
    except Exception as e:
        logger.error(f"Delete repo error: {e}")


async def setup_github(github_token: str, repo_name: str,
                        workflow_content: str, tailscale_key: str) -> Optional[str]:
    loop = asyncio.get_running_loop()

    user_resp = await loop.run_in_executor(None, lambda:
        requests.get('https://api.github.com/user', headers=gh_headers(github_token)))
    if user_resp.status_code != 200:
        return None
    username = user_resp.json()['login']

    cr = await loop.run_in_executor(None, lambda:
        requests.post('https://api.github.com/user/repos',
                      headers=gh_headers(github_token),
                      json={'name': repo_name, 'private': False, 'auto_init': True,
                            'description': 'Windows RDP via GitHub Actions'}))
    if cr.status_code != 201:
        return None

    await asyncio.sleep(4)

    content_b64 = base64.b64encode(workflow_content.encode()).decode()
    fr = await loop.run_in_executor(None, lambda:
        requests.put(
            f'https://api.github.com/repos/{username}/{repo_name}/contents/.github/workflows/windows-rdp.yml',
            headers=gh_headers(github_token),
            json={'message': 'Add RDP workflow', 'content': content_b64, 'branch': 'main'}))
    if fr.status_code not in [200, 201]:
        return None

    pk_resp = await loop.run_in_executor(None, lambda:
        requests.get(
            f'https://api.github.com/repos/{username}/{repo_name}/actions/secrets/public-key',
            headers=gh_headers(github_token)))
    if pk_resp.status_code == 200:
        pk        = pk_resp.json()
        encrypted = encrypt_secret(pk['key'], tailscale_key)
        await loop.run_in_executor(None, lambda:
            requests.put(
                f'https://api.github.com/repos/{username}/{repo_name}/actions/secrets/TAILSCALE_AUTH_KEY',
                headers=gh_headers(github_token),
                json={'encrypted_value': encrypted, 'key_id': pk['key_id']}))

    await loop.run_in_executor(None, lambda:
        requests.post(
            f'https://api.github.com/repos/{username}/{repo_name}/actions/workflows/windows-rdp.yml/dispatches',
            headers=gh_headers(github_token), json={'ref': 'main'}))

    return username


# ═════════════════════════════════════════════════════════════════════════════
# Background task: tạo RDP + lưu Firebase
# ═════════════════════════════════════════════════════════════════════════════

async def create_rdp_background(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 user_id: int):
    loop = asyncio.get_running_loop()
    username     = None
    repo_name    = None
    github_token = None
    run_id       = None

    try:
        github_token      = user_data[user_id]['github_token']
        tailscale_key     = user_data[user_id]['tailscale_key']
        tailscale_api_key = user_data[user_id].get('tailscale_api_key')
        duration_minutes  = user_data[user_id].get('duration_minutes', 60)
        duration_display  = user_data[user_id].get('duration_display', '1h')
        tg_user           = update.effective_user
        username_tg       = tg_user.username or tg_user.full_name or str(user_id)
        del user_data[user_id]

        random_password  = generate_password()
        rdp_username     = generate_username()
        workflow_content = create_workflow_content(random_password, duration_minutes, rdp_username)
        repo_name        = f"rdp-{user_id}-{int(time.time())}"

        username = await setup_github(github_token, repo_name, workflow_content, tailscale_key)
        if not username:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "╔══════════════════════════╗\n"
                    "║  ❌  TẠO REPO THẤT BẠI   ║\n"
                    "╚══════════════════════════╝\n\n"
                    "GitHub Token cần có đủ quyền:\n"
                    "• <code>repo</code>\n"
                    "• <code>workflow</code>\n\n"
                    "Vui lòng kiểm tra và thử lại."
                ),
                parse_mode='HTML')
            active_sessions.pop(user_id, None)
            return

        start_time = time.time()

        active_sessions[user_id] = {
            'expire_at':        start_time + duration_minutes * 60,
            'start_at':         start_time,
            'duration_minutes': duration_minutes,
            'duration_display': duration_display,
            'github_token':     github_token,
            'username':         username,
            'repo_name':        repo_name,
            'run_id':           None,
            'rdp_user':         rdp_username,
            'rdp_pass':         random_password,
            'rdp_ip':           None,
        }

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "╔════════════════════════════════╗\n"
                "║  ⚙️   ĐANG KHỞI ĐỘNG HỆ THỐNG  ║\n"
                "╚════════════════════════════════╝\n\n"
                "✅ Workflow đã được kích hoạt thành công!\n\n"
                f"⏱️  Thời gian thuê  : <b>{duration_display}</b>\n"
                f"⏳  Trạng thái      : Đang cài đặt Windows...\n\n"
                "💬 Bạn sẽ nhận thông báo khi máy sẵn sàng."
            ),
            parse_mode='HTML',
            disable_web_page_preview=True)

        rdp_ip         = None
        last_status    = None
        tailscale_done = False

        # ── Phase 1: Chờ workflow chạy ────────────────────────
        for attempt in range(90):
            await asyncio.sleep(10)
            try:
                if run_id is None:
                    run = await loop.run_in_executor(None, lambda:
                        get_latest_run(github_token, username, repo_name))
                    if run:
                        run_id = run['id']
                        active_sessions[user_id]['run_id'] = run_id
                    continue

                jobs = await loop.run_in_executor(None, lambda:
                    get_jobs(github_token, username, repo_name, run_id))
                if not jobs:
                    continue

                job_status = jobs[0].get('status')
                if job_status != last_status:
                    last_status = job_status
                    if job_status == 'in_progress':
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=(
                                "🔧 <b>Tiến trình cài đặt:</b>\n\n"
                                "✅ Khởi động máy ảo\n"
                                "🔄 Cài đặt Windows 11...\n"
                                "⏳ Vui lòng chờ (5-10 phút)"
                            ),
                            parse_mode='HTML')

                if (job_status == 'completed'
                        and jobs[0].get('conclusion') not in ['success', None]):
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "╔═══════════════════════════╗\n"
                            "║  ❌  WORKFLOW THẤT BẠI    ║\n"
                            "╚═══════════════════════════╝\n\n"
                            "Máy ảo không thể khởi động.\n"
                            "Vui lòng dùng /create để thử lại."
                        ))
                    active_sessions.pop(user_id, None)
                    return

                if tailscale_step_done(jobs):
                    tailscale_done = True
                    break

            except Exception as e:
                logger.error(f"Poll phase1 #{attempt}: {e}")

        # ── Phase 2: Lấy IP ──────────────────────────────────
        if tailscale_done:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "🌐 <b>Tailscale đã kết nối!</b>\n"
                    "⏳ Đang lấy địa chỉ IP, vui lòng chờ..."
                ),
                parse_mode='HTML')

            for retry in range(12):
                rdp_ip = await loop.run_in_executor(None, lambda:
                    get_ip_from_artifact(github_token, username, repo_name, run_id))
                if rdp_ip:
                    break
                if tailscale_api_key:
                    rdp_ip = await loop.run_in_executor(None, lambda:
                        get_tailscale_ip_from_api(tailscale_api_key, run_id))
                    if rdp_ip:
                        break
                logger.info(f"IP retry {retry+1}/12")
                await asyncio.sleep(10)

        # ── Lưu lên Firebase ─────────────────────────────────
        if rdp_ip:
            active_sessions[user_id]['rdp_ip'] = rdp_ip
            loop.run_in_executor(None, lambda:
                save_rdp_to_firebase(
                    user_id, username_tg, rdp_ip,
                    rdp_username, random_password,
                    duration_minutes, start_time))
            # Lưu lịch sử tạo máy
            loop.run_in_executor(None, lambda:
                save_rdp_history(
                    user_id, rdp_ip, rdp_username,
                    duration_minutes, start_time))

        # ── Gửi thông tin login ──────────────────────────────
        expire_time_str = format_datetime_vn(start_time + duration_minutes * 60)
        create_time_str = format_datetime_vn(start_time)

        if rdp_ip:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "╔══════════════════════════════════╗\n"
                    "║  🎉  WINDOWS AI STV SẴN SÀNG!   ║\n"
                    "╚══════════════════════════════════╝\n\n"
                    "━━━━━━ 🖥️  THÔNG TIN KẾT NỐI ━━━━━━\n\n"
                    f"🌐  IP Address  : <code>{rdp_ip}</code>\n"
                    f"👤  Username    : <code>{rdp_username}</code>\n"
                    f"🔑  Password    : <code>{random_password}</code>\n\n"
                    "━━━━━━ ⏰  THỜI GIAN SỬ DỤNG ━━━━━━\n\n"
                    f"📅  Bắt đầu    : {create_time_str}\n"
                    f"⌛  Hết hạn    : {expire_time_str}\n"
                    f"⏱️  Thời lượng  : {duration_display}\n\n"
                    "━━━━━━ 📋  HƯỚNG DẪN KẾT NỐI ━━━━━━\n\n"
                    "1️⃣  Tải Tailscale: tailscale.com/download\n"
                    "2️⃣  Đăng nhập cùng tài khoản Tailscale\n"
                    "3️⃣  Bật kết nối → Mở Remote Desktop\n"
                    "4️⃣  Nhập IP, username và password\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "💡 Dùng /check để xem thời gian còn lại\n"
                    "⚠️  Máy ảo tự tắt khi hết thời gian"
                ),
                parse_mode='HTML',
                disable_web_page_preview=True)
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "╔════════════════════════════════╗\n"
                    "║  ⚠️   KHÔNG LẤY ĐƯỢC ĐỊA CHỈ IP  ║\n"
                    "╚════════════════════════════════╝\n\n"
                    f"👤  Username  : <code>{rdp_username}</code>\n"
                    f"🔑  Password  : <code>{random_password}</code>\n\n"
                    "🔍 Xem IP tại:\n"
                    "login.tailscale.com/admin/machines\n\n"
                    "💡 Dùng /check để xem thời gian còn lại."
                ),
                parse_mode='HTML',
                disable_web_page_preview=True)

        # ── Xóa workflow file ─────────────────────────────────
        await loop.run_in_executor(None, lambda:
            delete_workflow_file(github_token, username, repo_name))

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✨ <b>Chúc bạn sử dụng dịch vụ vui vẻ!</b>"
            ),
            parse_mode='HTML')

        # ── Phase 3: Chờ hết hạn ─────────────────────────────
        expire_at = active_sessions[user_id]['expire_at']
        wait_secs = max(expire_at - time.time(), 0)
        logger.info(f"Chờ {wait_secs:.0f}s trước khi thông báo hết hạn...")
        await asyncio.sleep(wait_secs)

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "╔══════════════════════════════╗\n"
                "║  ⏰  PHIÊN LÀM VIỆC HẾT HẠN  ║\n"
                "╚══════════════════════════════╝\n\n"
                f"Thời gian thuê <b>{duration_display}</b> đã kết thúc.\n"
                "Máy ảo sẽ tự động tắt.\n\n"
                "🔄 Dùng /create để tạo máy mới."
            ),
            parse_mode='HTML')

        # Cập nhật Firebase: hết hạn
        await loop.run_in_executor(None, lambda: mark_rdp_expired_firebase(user_id))
        active_sessions.pop(user_id, None)

        # Chờ workflow kết thúc rồi xóa repo
        max_polls = 15 * 6
        for attempt in range(max_polls):
            await asyncio.sleep(10)
            try:
                if run_id:
                    jobs = await loop.run_in_executor(None, lambda:
                        get_jobs(github_token, username, repo_name, run_id))
                    if jobs and workflow_finished(jobs):
                        break
            except Exception as e:
                logger.error(f"Poll phase3 #{attempt}: {e}")

        await do_delete_repo(loop, github_token, username, repo_name, context.bot, user_id)

    except Exception as e:
        logger.error(f"Background error: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "╔═══════════════════════╗\n"
                "║  ❌  ĐÃ XẢY RA LỖI   ║\n"
                "╚═══════════════════════╝\n\n"
                f"Chi tiết: <code>{str(e)}</code>\n\n"
                "🔄 Dùng /create để thử lại."
            ),
            parse_mode='HTML')
        active_sessions.pop(user_id, None)
        if username and repo_name and github_token:
            loop2 = asyncio.get_running_loop()
            await do_delete_repo(loop2, github_token, username, repo_name,
                                  context.bot, user_id)


# ═════════════════════════════════════════════════════════════════════════════
# /check — Xem thông tin RDP hiện tại
# ═════════════════════════════════════════════════════════════════════════════

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Kiểm tra session local trước
    session = active_sessions.get(user_id)

    # Nếu không có local thì kiểm tra Firebase
    if not session:
        fb_data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: check_user_has_active_rdp_firebase(user_id))

        if not fb_data:
            await update.message.reply_text(
                "╔═══════════════════════════════╗\n"
                "║  ℹ️   KHÔNG CÓ MÁY ĐANG CHẠY  ║\n"
                "╚═══════════════════════════════╝\n\n"
                "Bạn chưa có phiên Windows nào đang hoạt động.\n\n"
                "🚀 Dùng /create để tạo máy mới.",
                parse_mode='HTML')
            return

        # Hiển thị từ Firebase
        now       = time.time()
        exp_ts    = fb_data.get("expires_ts", 0)
        crt_ts    = fb_data.get("created_ts", now)
        remaining = exp_ts - now
        elapsed   = now - crt_ts
        total_secs = fb_data.get("duration_minutes", 60) * 60
        percent_used = min(int(elapsed / total_secs * 100), 100)
        bar = create_progress_bar(percent_used)

        await update.message.reply_text(
            "╔═════════════════════════════════╗\n"
            "║  🖥️   THÔNG TIN MÁY WINDOWS      ║\n"
            "╚═════════════════════════════════╝\n\n"
            "━━━━━━ 🔌  KẾT NỐI ━━━━━━\n\n"
            f"🌐  IP Address  : <code>{fb_data.get('ip', 'N/A')}</code>\n"
            f"👤  Username    : <code>{fb_data.get('rdp_user', 'N/A')}</code>\n"
            f"🔑  Password    : <code>{fb_data.get('rdp_pass', 'N/A')}</code>\n\n"
            "━━━━━━ ⏰  THỜI GIAN ━━━━━━\n\n"
            f"📅  Bắt đầu    : {fb_data.get('created_at', 'N/A')}\n"
            f"⌛  Hết hạn    : {fb_data.get('expires_at', 'N/A')}\n"
            f"⏱️  Còn lại    : <b>{format_remaining(remaining)}</b>\n\n"
            f"<code>[{bar}] {percent_used}%</code>",
            parse_mode='HTML')
        return

    # Hiển thị từ local session
    now        = time.time()
    expire_at  = session['expire_at']
    start_at   = session['start_at']
    remaining  = expire_at - now
    elapsed    = now - start_at
    total_secs = session['duration_minutes'] * 60

    if remaining <= 0:
        active_sessions.pop(user_id, None)
        await update.message.reply_text(
            "╔════════════════════════════╗\n"
            "║  ⏰  PHIÊN ĐÃ HẾT HẠN      ║\n"
            "╚════════════════════════════╝\n\n"
            "Máy ảo của bạn đã tắt.\n\n"
            "🔄 Dùng /create để tạo máy mới.",
            parse_mode='HTML')
        return

    percent_used = min(int(elapsed / total_secs * 100), 100)
    bar = create_progress_bar(percent_used)
    rdp_ip   = session.get('rdp_ip', 'Đang lấy...')
    rdp_user = session.get('rdp_user', 'N/A')
    rdp_pass = session.get('rdp_pass', 'N/A')

    expire_str = format_datetime_vn(expire_at)
    start_str  = format_datetime_vn(start_at)

    await update.message.reply_text(
        "╔═════════════════════════════════╗\n"
        "║  🖥️   THÔNG TIN MÁY WINDOWS      ║\n"
        "╚═════════════════════════════════╝\n\n"
        "━━━━━━ 🔌  KẾT NỐI ━━━━━━\n\n"
        f"🌐  IP Address  : <code>{rdp_ip}</code>\n"
        f"👤  Username    : <code>{rdp_user}</code>\n"
        f"🔑  Password    : <code>{rdp_pass}</code>\n\n"
        "━━━━━━ ⏰  THỜI GIAN ━━━━━━\n\n"
        f"📅  Bắt đầu    : {start_str}\n"
        f"⌛  Hết hạn    : {expire_str}\n"
        f"✅  Đã dùng    : {format_remaining(elapsed)}\n"
        f"⏳  Còn lại    : <b>{format_remaining(remaining)}</b>\n\n"
        f"<code>[{bar}] {percent_used}%</code>\n\n"
        "⚠️  Máy tự tắt khi hết thời gian.",
        parse_mode='HTML')


# ═════════════════════════════════════════════════════════════════════════════
# Conversation handlers (tạo RDP)
# ═════════════════════════════════════════════════════════════════════════════

async def create_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loop    = asyncio.get_running_loop()

    # Kiểm tra session đang chạy (local)
    session = active_sessions.get(user_id)
    if session:
        remaining = session['expire_at'] - time.time()
        if remaining > 0:
            await update.message.reply_text(
                "╔═══════════════════════════════════╗\n"
                "║  ⚠️   BẠN ĐÃ CÓ MÁY ĐANG CHẠY!    ║\n"
                "╚═══════════════════════════════════╝\n\n"
                f"⏳  Còn lại : <b>{format_remaining(remaining)}</b>\n\n"
                "Mỗi tài khoản chỉ được tạo <b>1 máy</b> tại một thời điểm.\n"
                "Chờ hết hạn mới tạo được máy mới.\n\n"
                "💡 Dùng /check để xem chi tiết.",
                parse_mode='HTML')
            return ConversationHandler.END
        else:
            active_sessions.pop(user_id, None)

    # Kiểm tra Firebase
    fb_data = await loop.run_in_executor(
        None, lambda: check_user_has_active_rdp_firebase(user_id))

    if fb_data:
        exp_ts    = fb_data.get("expires_ts", 0)
        remaining = exp_ts - time.time()
        await update.message.reply_text(
            "╔═══════════════════════════════════╗\n"
            "║  ⚠️   BẠN ĐÃ CÓ MÁY ĐANG CHẠY!    ║\n"
            "╚═══════════════════════════════════╝\n\n"
            f"🌐  IP       : <code>{fb_data.get('ip', 'N/A')}</code>\n"
            f"👤  Username : <code>{fb_data.get('rdp_user', 'N/A')}</code>\n"
            f"⏳  Còn lại  : <b>{format_remaining(remaining)}</b>\n"
            f"⌛  Hết hạn  : {fb_data.get('expires_at', 'N/A')}\n\n"
            "Mỗi tài khoản chỉ được tạo <b>1 máy</b> tại một thời điểm.\n"
            "Chờ hết hạn mới tạo được máy mới.\n\n"
            "💡 Dùng /check để xem đầy đủ thông tin.",
            parse_mode='HTML')
        return ConversationHandler.END

    user_data[user_id] = {}

    # Kiểm tra token đã lưu
    saved_tokens = await loop.run_in_executor(None, lambda: get_user_tokens(user_id))
    if saved_tokens:
        gh   = saved_tokens.get("github_token", "")
        ts   = saved_tokens.get("tailscale_key", "")
        api  = saved_tokens.get("tailscale_api_key", "")
        saved_at = saved_tokens.get("saved_at", "N/A")
        user_data[user_id]["github_token"]      = gh
        user_data[user_id]["tailscale_key"]     = ts
        user_data[user_id]["tailscale_api_key"] = api

        keyboard = [[
            InlineKeyboardButton("✅ Dùng token đã lưu", callback_data="use_saved_tokens"),
            InlineKeyboardButton("✏️ Nhập token mới",    callback_data="enter_new_tokens"),
        ]]
        await update.message.reply_text(
            "╔══════════════════════════════╗\n"
            "║  🚀  TẠO WINDOWS AI STV MỚI  ║\n"
            "╚══════════════════════════════╝\n\n"
            "🔑 <b>Phát hiện token đã lưu!</b>\n\n"
            f"🐙 GitHub Token    : <code>{gh[:12]}...</code>\n"
            f"🔐 Tailscale Auth  : <code>{ts[:15]}...</code>\n"
            f"🗝️  Tailscale API   : <code>{api[:15]}...</code>\n"
            f"📅 Lưu lúc         : {saved_at}\n\n"
            "Bạn muốn dùng token nào?",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard))
        return CONFIRM_STATE  # Dùng lại CONFIRM_STATE để chờ callback

    await update.message.reply_text(
        "╔══════════════════════════════╗\n"
        "║  🚀  TẠO WINDOWS AI STV MỚI  ║\n"
        "╚══════════════════════════════╝\n\n"
        "📍 <b>Bước 1/4</b> — GitHub Personal Access Token\n\n"
        "Cần cấp quyền: <code>repo</code> và <code>workflow</code>\n"
        "Tạo tại: github.com/settings/tokens\n\n"
        "Vui lòng gửi token:",
        parse_mode='HTML')
    return GITHUB_TOKEN_STATE


async def get_github_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    token   = update.message.text.strip()
    if len(token) < 10:
        await update.message.reply_text("❌ Token không hợp lệ! Vui lòng gửi lại.")
        return ConversationHandler.END
    user_data[user_id]['github_token'] = token
    await update.message.reply_text(
        "✅ GitHub Token đã nhận!\n\n"
        "📍 <b>Bước 2/4</b> — Tailscale Auth Key\n\n"
        "Định dạng: <code>tskey-auth-...</code>\n"
        "Tạo tại: login.tailscale.com/admin/settings/keys\n\n"
        "Vui lòng gửi Auth Key:",
        parse_mode='HTML')
    return TAILSCALE_KEY_STATE


async def get_tailscale_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    key     = update.message.text.strip()
    if not key.startswith('tskey-') or len(key) < 40:
        await update.message.reply_text(
            "❌ Auth Key không hợp lệ!\n"
            "Phải bắt đầu bằng <code>tskey-auth-...</code>\n\n"
            "Dùng /create để thử lại.",
            parse_mode='HTML')
        return ConversationHandler.END
    user_data[user_id]['tailscale_key'] = key
    await update.message.reply_text(
        "✅ Tailscale Auth Key đã nhận!\n\n"
        "📍 <b>Bước 3/4</b> — Tailscale API Key\n\n"
        "Định dạng: <code>tskey-api-...</code>\n"
        "Chọn loại <b>API access token</b>\n"
        "Tạo tại: login.tailscale.com/admin/settings/keys\n\n"
        "Vui lòng gửi API Key:",
        parse_mode='HTML')
    return TAILSCALE_API_STATE


async def get_tailscale_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    api_key = update.message.text.strip()
    if not api_key.startswith('tskey-') or len(api_key) < 40:
        await update.message.reply_text(
            "❌ API Key không hợp lệ!\n"
            "Phải bắt đầu bằng <code>tskey-api-...</code>\n\n"
            "Dùng /create để thử lại.",
            parse_mode='HTML')
        return ConversationHandler.END
    user_data[user_id]['tailscale_api_key'] = api_key

    # ── Tự động lưu 3 token vào Firebase ──────────────────────────────────────
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: save_user_tokens(
        user_id,
        user_data[user_id]['github_token'],
        user_data[user_id]['tailscale_key'],
        api_key
    ))

    is_admin = (user_id == ADMIN_ID)
    if is_admin:
        keyboard = [
            [
                InlineKeyboardButton("15p", callback_data='dur_15'),
                InlineKeyboardButton("30p", callback_data='dur_30'),
                InlineKeyboardButton("1h",  callback_data='dur_60'),
            ],
            [
                InlineKeyboardButton("1h30p", callback_data='dur_90'),
                InlineKeyboardButton("2h",    callback_data='dur_120'),
                InlineKeyboardButton("3h",    callback_data='dur_180'),
            ],
            [
                InlineKeyboardButton("4h",    callback_data='dur_240'),
                InlineKeyboardButton("5h",    callback_data='dur_300'),
                InlineKeyboardButton("6h",    callback_data='dur_360'),
            ],
            [InlineKeyboardButton("⌨️ Tự nhập thời gian", callback_data='dur_custom')]
        ]
        note = "👑 <b>[ADMIN]</b> — Giới hạn: 15 phút → 6 giờ"
    else:
        keyboard = [
            [
                InlineKeyboardButton("1h",    callback_data='dur_60'),
                InlineKeyboardButton("1h30p", callback_data='dur_90'),
                InlineKeyboardButton("2h",    callback_data='dur_120'),
            ],
            [
                InlineKeyboardButton("2h30p", callback_data='dur_150'),
                InlineKeyboardButton("3h",    callback_data='dur_180'),
                InlineKeyboardButton("⌨️ Tự nhập", callback_data='dur_custom'),
            ]
        ]
        note = "⏱️ Giới hạn: 1 giờ → 3 giờ"

    await update.message.reply_text(
        "✅ Tailscale API Key đã nhận!\n\n"
        "╔══════════════════════════════╗\n"
        "║  ⏱️   BƯỚC 4/4 — THỜI GIAN   ║\n"
        "╚══════════════════════════════╝\n\n"
        f"{note}\n\n"
        "Chọn thời gian sử dụng máy ảo:",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard))
    return DURATION_STATE


async def duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    user_id  = update.effective_user.id
    is_admin = (user_id == ADMIN_ID)

    if query.data == 'dur_custom':
        limit_text = ("⚠️ Giới hạn: <b>15p → 6h</b>" if is_admin
                      else "⚠️ Giới hạn: <b>1h → 3h</b>")
        await query.edit_message_text(
            "⌨️ <b>Nhập thời gian tùy chỉnh:</b>\n\n"
            "📝 Định dạng hỗ trợ:\n"
            "• <code>1h</code>     → 1 giờ\n"
            "• <code>2h30p</code>  → 2 giờ 30 phút\n"
            "• <code>1h26p</code>  → 1 giờ 26 phút\n"
            "• <code>90p</code>    → 90 phút\n\n"
            f"{limit_text}\n\n"
            "Vui lòng nhập thời gian:",
            parse_mode='HTML')
        return DURATION_STATE

    minutes = int(query.data.replace('dur_', ''))
    h, m    = divmod(minutes, 60)
    raw_str = f"{h}h{m}p" if m else f"{h}h" if h else f"{minutes}p"
    result  = parse_duration(raw_str, user_id)
    if not result:
        await query.edit_message_text("❌ Lỗi thời gian. Dùng /create để thử lại.")
        return ConversationHandler.END

    total_minutes, display = result
    user_data[user_id]['duration_minutes'] = total_minutes
    user_data[user_id]['duration_display']  = display
    return await show_confirm(query, user_id, is_query=True)


async def get_duration_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    text     = update.message.text.strip()
    result   = parse_duration(text, user_id)
    is_admin = (user_id == ADMIN_ID)

    if not result:
        limit_text = ("⚠️ Giới hạn: 15p → 6h (15-360 phút)"
                      if is_admin else "⚠️ Giới hạn: 1h → 3h (60-180 phút)")
        await update.message.reply_text(
            "❌ <b>Thời gian không hợp lệ!</b>\n\n"
            f"{limit_text}\n\n"
            "📝 Ví dụ đúng:\n"
            "<code>1h</code> · <code>2h30p</code> · <code>1h26p</code> · <code>90p</code>\n\n"
            "Vui lòng nhập lại:",
            parse_mode='HTML')
        return DURATION_STATE

    total_minutes, display = result
    user_data[user_id]['duration_minutes'] = total_minutes
    user_data[user_id]['duration_display']  = display
    return await show_confirm(update, user_id, is_query=False)


async def show_confirm(update_or_query, user_id: int, is_query: bool):
    data     = user_data[user_id]
    keyboard = [[
        InlineKeyboardButton("🚀 Bắt đầu tạo máy", callback_data='start_create'),
        InlineKeyboardButton("❌ Hủy",              callback_data='cancel')
    ]]
    text = (
        "╔══════════════════════════════╗\n"
        "║  ✅  XÁC NHẬN TẠO WINDOWS    ║\n"
        "╚══════════════════════════════╝\n\n"
        f"🔑  GitHub Token    : <code>{data['github_token'][:10]}...</code>\n"
        f"🔐  Tailscale Auth  : <code>{data['tailscale_key'][:15]}...</code>\n"
        f"🗝️   Tailscale API   : <code>{data['tailscale_api_key'][:15]}...</code>\n"
        f"⏱️   Thời gian       : <b>{data['duration_display']}</b> ({data['duration_minutes']} phút)\n\n"
        "Nhấn <b>Bắt đầu tạo máy</b> để tiến hành!"
    )
    markup = InlineKeyboardMarkup(keyboard)
    if is_query:
        await update_or_query.edit_message_text(text, parse_mode='HTML', reply_markup=markup)
    else:
        await update_or_query.message.reply_text(text, parse_mode='HTML', reply_markup=markup)
    return CONFIRM_STATE


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == 'cancel':
        user_data.pop(user_id, None)
        await query.edit_message_text(
            "❌ Đã hủy tạo máy.\n\n"
            "Dùng /create để bắt đầu lại.")
        return ConversationHandler.END

    # ── Xử lý chọn token đã lưu / nhập mới ──────────────────────────────────
    if query.data == 'use_saved_tokens':
        is_admin = (user_id == ADMIN_ID)
        if is_admin:
            keyboard = [
                [InlineKeyboardButton("15p", callback_data='dur_15'),
                 InlineKeyboardButton("30p", callback_data='dur_30'),
                 InlineKeyboardButton("1h",  callback_data='dur_60')],
                [InlineKeyboardButton("1h30p", callback_data='dur_90'),
                 InlineKeyboardButton("2h",   callback_data='dur_120'),
                 InlineKeyboardButton("3h",   callback_data='dur_180')],
                [InlineKeyboardButton("4h",   callback_data='dur_240'),
                 InlineKeyboardButton("5h",   callback_data='dur_300'),
                 InlineKeyboardButton("6h",   callback_data='dur_360')],
                [InlineKeyboardButton("⌨️ Tự nhập thời gian", callback_data='dur_custom')]
            ]
            note = "👑 <b>[ADMIN]</b> — Giới hạn: 15 phút → 6 giờ"
        else:
            keyboard = [
                [InlineKeyboardButton("1h",    callback_data='dur_60'),
                 InlineKeyboardButton("1h30p", callback_data='dur_90'),
                 InlineKeyboardButton("2h",    callback_data='dur_120')],
                [InlineKeyboardButton("2h30p", callback_data='dur_150'),
                 InlineKeyboardButton("3h",    callback_data='dur_180'),
                 InlineKeyboardButton("⌨️ Tự nhập", callback_data='dur_custom')],
            ]
            note = "⏱️ Giới hạn: 1 giờ → 3 giờ"
        await query.edit_message_text(
            "✅ Dùng token đã lưu!\n\n"
            "╔══════════════════════════════╗\n"
            "║  ⏱️   BƯỚC 2/2 — THỜI GIAN   ║\n"
            "╚══════════════════════════════╝\n\n"
            f"{note}\n\nChọn thời gian sử dụng máy ảo:",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard))
        return DURATION_STATE

    if query.data == 'enter_new_tokens':
        user_data[user_id] = {}
        await query.edit_message_text(
            "✏️ <b>Nhập token mới</b>\n\n"
            "📍 <b>Bước 1/4</b> — GitHub Personal Access Token\n\n"
            "Cần cấp quyền: <code>repo</code> và <code>workflow</code>\n"
            "Tạo tại: github.com/settings/tokens\n\n"
            "Vui lòng gửi token:",
            parse_mode='HTML')
        return GITHUB_TOKEN_STATE

    if query.data.startswith('dur_'):
        return await duration_callback(update, context)

    await query.edit_message_text(
        "╔══════════════════════════════╗\n"
        "║  🔄  ĐANG KHỞI TẠO WINDOWS   ║\n"
        "╚══════════════════════════════╝\n\n"
        "Hệ thống đang chuẩn bị máy ảo...\n"
        "Quá trình mất khoảng 5-10 phút.\n\n"
        "⏳ Bạn sẽ nhận thông báo khi hoàn tất.",
        parse_mode='HTML')
    asyncio.create_task(create_rdp_background(update, context, user_id))
    return ConversationHandler.END


# ═════════════════════════════════════════════════════════════════════════════
# Lệnh chung
# ═════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "bạn"
    await update.message.reply_text(
        f"👋 Xin chào, <b>{name}</b>!\n\n"
        "╔══════════════════════════════╗\n"
        "║  🖥️   WINDOWS AI STV BOT      ║\n"
        "╚══════════════════════════════╝\n\n"
        "Tạo máy ảo Windows 11 miễn phí\n"
        "thông qua GitHub Actions + Tailscale.\n\n"
        "━━━━━━ 📋  LỆNH CÓ SẴN ━━━━━━\n\n"
        "🚀 /create     — Tạo máy Windows mới\n"
        "📊 /check      — Xem thông tin & thời gian\n"
        "⚙️ /settings   — Lưu & quản lý token\n"
        "📜 /history    — Lịch sử các máy đã tạo\n"
        "💬 /feedback   — Gửi phản hồi tới Admin\n"
        "🔌 /connect    — Kết nối máy ảo từ xa\n"
        "❓ /help       — Hướng dẫn chi tiết\n"
        "❌ /cancel     — Hủy thao tác\n\n"
        "━━━━━━ 🔑  CẦN CHUẨN BỊ ━━━━━━\n\n"
        "1️⃣  GitHub Token (quyền repo + workflow)\n"
        "2️⃣  Tailscale Auth Key (tskey-auth-...)\n"
        "3️⃣  Tailscale API Key (tskey-api-...)\n\n"
        "━━━━━━ ⏱️  THỜI GIAN ━━━━━━\n\n"
        "Từ <b>1 giờ</b> đến <b>3 giờ</b>\n"
        "⚠️ Mỗi người chỉ tạo được <b>1 máy</b>\n\n"
        "🚀 Gõ /create để bắt đầu!",
        parse_mode='HTML')


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data.pop(update.effective_user.id, None)
    await update.message.reply_text(
        "❌ Đã hủy thao tác hiện tại.\n\n"
        "Dùng /create để bắt đầu lại.")
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "╔══════════════════════════════╗\n"
        "║  📚  HƯỚNG DẪN SỬ DỤNG       ║\n"
        "╚══════════════════════════════╝\n\n"
        "━━━━━━ 🛠️  CÁC LỆNH ━━━━━━\n\n"
        "🚀 /create     — Tạo máy Windows mới\n"
        "📊 /check      — Xem thông tin máy hiện tại\n"
        "⚙️ /settings   — Lưu & cập nhật 3 token\n"
        "📜 /history    — Lịch sử các máy đã tạo\n"
        "💬 /feedback   — Gửi phản hồi tới Admin\n"
        "🔌 /connect    — Kết nối & chạy bot trên máy khác\n"
        "❌ /cancel     — Hủy thao tác đang thực hiện\n\n"
        "━━━━━━ 🔑  CÁCH LẤY KEY ━━━━━━\n\n"
        "GitHub Token:\n"
        "→ github.com/settings/tokens\n"
        "→ Cấp quyền: repo + workflow\n\n"
        "Tailscale Auth Key:\n"
        "→ login.tailscale.com/admin/settings/keys\n"
        "→ Chọn: Auth Keys\n\n"
        "Tailscale API Key:\n"
        "→ login.tailscale.com/admin/settings/keys\n"
        "→ Chọn: API access tokens\n\n"
        "━━━━━━ ℹ️  LƯU Ý ━━━━━━\n\n"
        "⏱️  Thời gian: 1 giờ — 3 giờ\n"
        "👤  Mỗi người chỉ tạo được <b>1 máy</b>\n"
        "🔒  Thông tin được lưu an toàn\n"
        "🗑️  Repo tự động xóa sau khi hết hạn",
        parse_mode='HTML')



# ═════════════════════════════════════════════════════════════════════════════
# /settings — Xem & cập nhật 3 token đã lưu
# ═════════════════════════════════════════════════════════════════════════════

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loop    = asyncio.get_running_loop()
    saved   = await loop.run_in_executor(None, lambda: get_user_tokens(user_id))

    if saved:
        gh  = saved.get("github_token", "")
        ts  = saved.get("tailscale_key", "")
        api = saved.get("tailscale_api_key", "")
        saved_at = saved.get("saved_at", "N/A")
        keyboard = [[
            InlineKeyboardButton("✏️ Cập nhật token mới", callback_data="settings_update"),
            InlineKeyboardButton("🗑️ Xóa token",          callback_data="settings_delete"),
        ]]
        await update.message.reply_text(
            "╔══════════════════════════════╗\n"
            "║  ⚙️   CÀI ĐẶT TOKEN          ║\n"
            "╚══════════════════════════════╝\n\n"
            "✅ <b>Token đã được lưu:</b>\n\n"
            f"🐙 GitHub Token   : <code>{gh[:12]}...{gh[-4:]}</code>\n"
            f"🔐 Tailscale Auth : <code>{ts[:15]}...{ts[-4:]}</code>\n"
            f"🗝️  Tailscale API  : <code>{api[:15]}...{api[-4:]}</code>\n"
            f"📅 Lưu lúc        : {saved_at}\n\n"
            "Bạn muốn làm gì?",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        keyboard = [[InlineKeyboardButton("➕ Lưu token mới", callback_data="settings_update")]]
        await update.message.reply_text(
            "╔══════════════════════════════╗\n"
            "║  ⚙️   CÀI ĐẶT TOKEN          ║\n"
            "╚══════════════════════════════╝\n\n"
            "ℹ️ Chưa có token nào được lưu.\n\n"
            "Nhấn bên dưới để lưu token,\n"
            "lần sau dùng /create sẽ tự điền!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "settings_delete":
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, lambda: delete_user_tokens(user_id))
        await query.edit_message_text(
            "✅ Đã xóa token đã lưu.\n\n"
            "Lần sau dùng /create bạn sẽ cần nhập lại 3 token.\n"
            "Hoặc dùng /settings để lưu mới.",
            parse_mode='HTML')
        return ConversationHandler.END

    if query.data == "settings_update":
        user_data[user_id] = {"_settings": True}
        await query.edit_message_text(
            "╔══════════════════════════════╗\n"
            "║  ⚙️   CẬP NHẬT TOKEN MỚI     ║\n"
            "╚══════════════════════════════╝\n\n"
            "📍 <b>Bước 1/3</b> — GitHub Personal Access Token\n\n"
            "Cần cấp quyền: <code>repo</code> và <code>workflow</code>\n"
            "Tạo tại: github.com/settings/tokens\n\n"
            "Vui lòng gửi token:",
            parse_mode='HTML')
        return SETTINGS_GH_STATE

    return ConversationHandler.END


async def settings_get_github(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    token   = update.message.text.strip()
    if len(token) < 10:
        await update.message.reply_text("❌ Token không hợp lệ! Vui lòng gửi lại.")
        return SETTINGS_GH_STATE
    user_data[user_id]["github_token"] = token
    await update.message.reply_text(
        "✅ GitHub Token đã nhận!\n\n"
        "📍 <b>Bước 2/3</b> — Tailscale Auth Key\n\n"
        "Định dạng: <code>tskey-auth-...</code>\n"
        "Tạo tại: login.tailscale.com/admin/settings/keys\n\n"
        "Vui lòng gửi Auth Key:",
        parse_mode='HTML')
    return SETTINGS_TS_STATE


async def settings_get_tailscale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    key     = update.message.text.strip()
    if not key.startswith("tskey-") or len(key) < 40:
        await update.message.reply_text(
            "❌ Auth Key không hợp lệ!\nPhải bắt đầu bằng <code>tskey-auth-...</code>\n\nThử lại:",
            parse_mode='HTML')
        return SETTINGS_TS_STATE
    user_data[user_id]["tailscale_key"] = key
    await update.message.reply_text(
        "✅ Tailscale Auth Key đã nhận!\n\n"
        "📍 <b>Bước 3/3</b> — Tailscale API Key\n\n"
        "Định dạng: <code>tskey-api-...</code>\n"
        "Tạo tại: login.tailscale.com/admin/settings/keys\n\n"
        "Vui lòng gửi API Key:",
        parse_mode='HTML')
    return SETTINGS_API_STATE


async def settings_get_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    api_key = update.message.text.strip()
    if not api_key.startswith("tskey-") or len(api_key) < 40:
        await update.message.reply_text(
            "❌ API Key không hợp lệ!\nPhải bắt đầu bằng <code>tskey-api-...</code>\n\nThử lại:",
            parse_mode='HTML')
        return SETTINGS_API_STATE

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, lambda: save_user_tokens(
        user_id,
        user_data[user_id]["github_token"],
        user_data[user_id]["tailscale_key"],
        api_key
    ))
    user_data.pop(user_id, None)

    if ok:
        await update.message.reply_text(
            "╔══════════════════════════════╗\n"
            "║  ✅  LƯU TOKEN THÀNH CÔNG!   ║\n"
            "╚══════════════════════════════╝\n\n"
            "3 token đã được lưu an toàn trên Firebase.\n\n"
            "🚀 Lần sau dùng /create sẽ <b>tự động điền</b>!\n"
            "⚙️ Dùng /settings để xem hoặc cập nhật.",
            parse_mode='HTML')
    else:
        await update.message.reply_text("❌ Lỗi khi lưu Firebase. Thử lại sau.")
    return ConversationHandler.END


# ═════════════════════════════════════════════════════════════════════════════
# /history — Xem lịch sử các máy đã tạo
# ═════════════════════════════════════════════════════════════════════════════

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loop    = asyncio.get_running_loop()

    msg     = await update.message.reply_text("🔄 Đang tải lịch sử...")
    history = await loop.run_in_executor(None, lambda: get_rdp_history(user_id))

    if not history:
        await msg.edit_text(
            "╔══════════════════════════════╗\n"
            "║  📜  LỊCH SỬ TẠO MÁY         ║\n"
            "╚══════════════════════════════╝\n\n"
            "Bạn chưa tạo máy ảo nào.\n\n"
            "🚀 Dùng /create để tạo máy đầu tiên!",
            parse_mode='HTML')
        return

    # Sắp xếp mới nhất lên trên
    history_sorted = sorted(history, key=lambda x: x.get("created_ts", 0), reverse=True)

    text = (
        "╔══════════════════════════════════╗\n"
        "║  📜  LỊCH SỬ TẠO MÁY ẢO          ║\n"
        "╚══════════════════════════════════╝\n\n"
        f"📦 Tổng cộng: <b>{len(history_sorted)}</b> lần tạo máy\n"
        "(Hiển thị 20 lần gần nhất)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for i, record in enumerate(history_sorted, 1):
        ip       = record.get("ip", "N/A")
        rdp_user = record.get("rdp_user", "N/A")
        created  = record.get("created_at", "N/A")
        expires  = record.get("expires_at", "N/A")
        dur_min  = record.get("duration_minutes", 0)
        h, m     = divmod(dur_min, 60)
        dur_str  = f"{h}h {m}p" if m else f"{h}h" if h else f"{dur_min}p"

        text += (
            f"<b>#{i}</b>  📅 {created}\n"
            f"  🌐 IP      : <code>{ip}</code>\n"
            f"  👤 User    : <code>{rdp_user}</code>\n"
            f"  ⏱️  Thời gian: {dur_str}\n"
            f"  ⌛ Hết hạn : {expires}\n\n"
        )

    await msg.edit_text(text, parse_mode='HTML')


# ═════════════════════════════════════════════════════════════════════════════
# /feedback — Gửi phản hồi tới Admin
# ═════════════════════════════════════════════════════════════════════════════

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "╔══════════════════════════════╗\n"
        "║  💬  GỬI PHẢN HỒI TỚI ADMIN  ║\n"
        "╚══════════════════════════════╝\n\n"
        "✍️ Nhập nội dung phản hồi của bạn:\n\n"
        "• Báo lỗi / góp ý / yêu cầu tính năng\n"
        "• Tối đa 1000 ký tự\n\n"
        "❌ /cancel để hủy",
        parse_mode='HTML')
    return FEEDBACK_TEXT_STATE


async def feedback_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    user_id = user.id
    text    = update.message.text.strip()

    if len(text) > 1000:
        await update.message.reply_text(
            "❌ Phản hồi quá dài! Tối đa 1000 ký tự.\n"
            f"Hiện tại: {len(text)} ký tự.\n\nVui lòng rút gọn lại:")
        return FEEDBACK_TEXT_STATE

    name     = user.full_name or user.first_name or "N/A"
    username = f"@{user.username}" if user.username else "Không có"
    now_str  = datetime.now(timezone(timedelta(hours=7))).strftime("%H:%M:%S %d/%m/%Y")

    # Lưu feedback vào Firebase
    fb_key  = f"feedbacks/{user_id}_{int(time.time())}"
    fb_data = {
        "user_id":   user_id,
        "name":      name,
        "username":  username,
        "text":      text,
        "sent_at":   now_str,
    }
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: firebase_set(fb_key, fb_data))

    # Gửi thông báo tới Admin
    admin_text = (
        "╔══════════════════════════════╗\n"
        "║  💬  PHẢN HỒI MỚI TỪ USER    ║\n"
        "╚══════════════════════════════╝\n\n"
        f"👤 Tên      : {name}\n"
        f"🔗 Username : {username}\n"
        f"🆔 User ID  : <code>{user_id}</code>\n"
        f"🕐 Thời gian: {now_str}\n\n"
        "━━━━━━ 💬  NỘI DUNG ━━━━━━\n\n"
        f"{text}"
    )
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            parse_mode='HTML')
    except Exception as e:
        logger.error(f"Feedback to admin error: {e}")

    await update.message.reply_text(
        "╔══════════════════════════════╗\n"
        "║  ✅  ĐÃ GỬI PHẢN HỒI!        ║\n"
        "╚══════════════════════════════╝\n\n"
        "Cảm ơn bạn đã gửi phản hồi! 🙏\n"
        "Admin sẽ xem xét và phản hồi sớm nhất.",
        parse_mode='HTML')
    return ConversationHandler.END


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=True)


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation: tạo RDP
    rdp_conv = ConversationHandler(
        entry_points=[CommandHandler('create', create_command)],
        states={
            GITHUB_TOKEN_STATE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_github_token)],
            TAILSCALE_KEY_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tailscale_key)],
            TAILSCALE_API_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tailscale_api_key)],
            DURATION_STATE: [
                CallbackQueryHandler(duration_callback, pattern='^dur_'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_duration_text),
            ],
            CONFIRM_STATE: [CallbackQueryHandler(button_callback)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_message=False
    )

    # Conversation: kết nối máy từ xa
    connect_conv = ConversationHandler(
        entry_points=[CommandHandler('connect', connect_command)],
        states={
            REMOTE_IP_STATE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_remote_ip)],
            REMOTE_USER_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_remote_user)],
            REMOTE_PASS_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_remote_pass)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_message=False
    )

    # Conversation: cài đặt token
    settings_conv = ConversationHandler(
        entry_points=[
            CommandHandler('settings', settings_command),
            CallbackQueryHandler(settings_callback, pattern='^settings_'),
        ],
        states={
            SETTINGS_GH_STATE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, settings_get_github)],
            SETTINGS_TS_STATE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, settings_get_tailscale)],
            SETTINGS_API_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, settings_get_api)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_message=False
    )

    # Conversation: feedback
    feedback_conv = ConversationHandler(
        entry_points=[CommandHandler('feedback', feedback_command)],
        states={
            FEEDBACK_TEXT_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_receive)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_message=False
    )

    application.add_handler(CommandHandler('start',    start))
    application.add_handler(CommandHandler('help',     help_command))
    application.add_handler(CommandHandler('check',    check_command))
    application.add_handler(CommandHandler('cancel',   cancel_command))
    application.add_handler(CommandHandler('history',  history_command))
    application.add_handler(rdp_conv)
    application.add_handler(connect_conv)
    application.add_handler(settings_conv)
    application.add_handler(feedback_conv)
    application.add_error_handler(error_handler)

    print("🤖 Bot đang chạy...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
        for pkg in missing:
            print(f"   • {pkg}")
        print()
        for pkg in missing:
            install_package(pkg)
        print("\n✅ Hoàn tất cài đặt! Đang khởi động bot...\n")
    else:
        print("✅ Tất cả thư viện đã được cài đặt.\n")

auto_install()

# ═════════════════════════════════════════════════════════════════════════════

import os
import zipfile
import io
import requests
import base64
import time
import logging
import re
import threading
import tempfile
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           filters, ContextTypes, ConversationHandler,
                           CallbackQueryHandler)
import asyncio
from nacl import encoding, public
import secrets
import string

# ── Firebase Admin SDK ────────────────────────────────────────────────────────
try:
    import firebase_admin
    from firebase_admin import credentials, db as firebase_db
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

# ── Cố gắng import winrm (tuỳ chọn) ─────────────────────────────────────────
try:
    import winrm
    WINRM_AVAILABLE = True
except ImportError:
    WINRM_AVAILABLE = False

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

GITHUB_TOKEN_STATE, TAILSCALE_KEY_STATE, TAILSCALE_API_STATE, DURATION_STATE, CONFIRM_STATE = range(5)
REMOTE_IP_STATE, REMOTE_USER_STATE, REMOTE_PASS_STATE = range(5, 8)

# ── Lưu trạng thái người dùng ────────────────────────────────────────────────
user_data = {}
active_sessions = {}
remote_sessions = {}

BOT_TOKEN = os.environ.get("BOT_TOKEN", "7000771103:AAGttf2jhIYuaT5063iabVwZsA4isgE-LLw")
ADMIN_ID  = 5738766741

NORMAL_MIN = 60
NORMAL_MAX = 180
ADMIN_MIN  = 15
ADMIN_MAX  = 360

BOT_FILE_URL = "https://raw.githubusercontent.com/phuctrongytb16-ctrl/Win11/main/h.py"

# ── Firebase Configuration ────────────────────────────────────────────────────
FIREBASE_CONFIG = {
    "apiKey": "AIzaSyAJh6-2mxzFADaA_qNlw-MAXZ_wc9zgKL4",
    "authDomain": "sever-login-ae5cc.firebaseapp.com",
    "databaseURL": "https://sever-login-ae5cc-default-rtdb.firebaseio.com",
    "projectId": "sever-login-ae5cc",
    "storageBucket": "sever-login-ae5cc.firebasestorage.app",
    "messagingSenderId": "966951494514",
    "appId": "1:966951494514:web:2663ca6c5814108716b3eb",
    "measurementId": "G-6C22LDBYGK"
}

FIREBASE_DB_URL = FIREBASE_CONFIG["databaseURL"]


# ═════════════════════════════════════════════════════════════════════════════
# FIREBASE HELPERS — Dùng REST API (không cần service account)
# ═════════════════════════════════════════════════════════════════════════════

def firebase_get(path: str) -> Optional[dict]:
    """Lấy dữ liệu từ Firebase Realtime Database qua REST."""
    try:
        url = f"{FIREBASE_DB_URL}/{path}.json"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"Firebase GET error ({path}): {e}")
    return None


def firebase_set(path: str, data: dict) -> bool:
    """Ghi dữ liệu lên Firebase Realtime Database qua REST."""
    try:
        url = f"{FIREBASE_DB_URL}/{path}.json"
        r = requests.put(url, json=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Firebase SET error ({path}): {e}")
    return False


def firebase_delete(path: str) -> bool:
    """Xóa dữ liệu khỏi Firebase Realtime Database qua REST."""
    try:
        url = f"{FIREBASE_DB_URL}/{path}.json"
        r = requests.delete(url, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Firebase DELETE error ({path}): {e}")
    return False


def save_rdp_to_firebase(user_id: int, username_tg: str, ip: str,
                          rdp_user: str, rdp_pass: str,
                          duration_minutes: int, start_ts: float) -> bool:
    """Lưu thông tin RDP lên Firebase."""
    expire_ts = start_ts + duration_minutes * 60
    data = {
        "user_id":          user_id,
        "username_telegram": username_tg,
        "ip":               ip,
        "rdp_user":         rdp_user,
        "rdp_pass":         rdp_pass,
        "duration_minutes": duration_minutes,
        "created_at":       datetime.fromtimestamp(start_ts, tz=timezone(timedelta(hours=7))).strftime("%H:%M:%S %d/%m/%Y (GMT+7)"),
        "expires_at":       datetime.fromtimestamp(expire_ts, tz=timezone(timedelta(hours=7))).strftime("%H:%M:%S %d/%m/%Y (GMT+7)"),
        "created_ts":       start_ts,
        "expires_ts":       expire_ts,
        "status":           "active"
    }
    return firebase_set(f"rdp_sessions/{user_id}", data)


def get_rdp_from_firebase(user_id: int) -> Optional[dict]:
    """Lấy thông tin RDP của user từ Firebase."""
    return firebase_get(f"rdp_sessions/{user_id}")


def mark_rdp_expired_firebase(user_id: int) -> bool:
    """Đánh dấu RDP đã hết hạn trên Firebase."""
    data = firebase_get(f"rdp_sessions/{user_id}")
    if data:
        data["status"] = "expired"
        return firebase_set(f"rdp_sessions/{user_id}", data)
    return False


def check_user_has_active_rdp_firebase(user_id: int) -> Optional[dict]:
    """
    Kiểm tra user có RDP đang active trên Firebase không.
    Trả về dict nếu còn hạn, None nếu không.
    """
    data = firebase_get(f"rdp_sessions/{user_id}")
    if not data:
        return None
    if data.get("status") != "active":
        return None
    expire_ts = data.get("expires_ts", 0)
    if time.time() < expire_ts:
        return data
    # Hết hạn → cập nhật status
    mark_rdp_expired_firebase(user_id)
    return None


# ═════════════════════════════════════════════════════════════════════════════
# WINRM — KẾT NỐI MÁY ẢO KHÁC
# ═════════════════════════════════════════════════════════════════════════════

def winrm_connect(ip: str, username: str, password: str):
    if not WINRM_AVAILABLE:
        return None
    try:
        session = winrm.Session(
            f'http://{ip}:5985/wsman',
            auth=(username, password),
            transport='ntlm'
        )
        r = session.run_cmd('echo', ['OK'])
        if r.status_code == 0:
            return session
    except Exception as e:
        logger.error(f"WinRM connect error: {e}")
    return None


def winrm_run_bot(session, username: str, file_url: str) -> dict:
    remote_path = f"C:\\Users\\{username}\\h.py"
    results = {}
    try:
        dl_cmd = f'curl -L {file_url} -o {remote_path}'
        r = session.run_cmd("cmd", ["/c", dl_cmd])
        results['download'] = {
            'stdout': r.std_out.decode(errors='replace'),
            'stderr': r.std_err.decode(errors='replace'),
            'ok': r.status_code == 0
        }
        time.sleep(3)

        r = session.run_cmd("cmd", ["/c",
            "python -m pip install python-telegram-bot pynacl requests pywin32 pillow"])
        results['install'] = {
            'stdout': r.std_out.decode(errors='replace'),
            'stderr': r.std_err.decode(errors='replace'),
            'ok': r.status_code == 0
        }
        time.sleep(3)

        r = session.run_cmd("cmd", ["/c",
            f"start cmd /k python {remote_path}"])
        results['run'] = {
            'stdout': r.std_out.decode(errors='replace'),
            'ok': r.status_code == 0
        }
    except Exception as e:
        results['error'] = str(e)
    return results


# ═════════════════════════════════════════════════════════════════════════════
# /connect — Lệnh kết nối máy ảo từ xa
# ═════════════════════════════════════════════════════════════════════════════

async def connect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WINRM_AVAILABLE:
        await update.message.reply_text(
            "⚠️ Thư viện <code>pywinrm</code> chưa được cài.\n"
            "Chạy: <code>pip install pywinrm</code>",
            parse_mode='HTML')
        return ConversationHandler.END

    user_id  = update.effective_user.id
    is_admin = (user_id == ADMIN_ID)
    user_data[user_id] = {'_connect': True, '_is_admin': is_admin}
    await update.message.reply_text(
        "╔══════════════════════════╗\n"
        "║  🔌  KẾT NỐI MÁY TỪ XA  ║\n"
        "╚══════════════════════════╝\n\n"
        + ("🔑 <b>Chế độ Admin</b> — Deploy bot\n\n" if is_admin else
           "👤 <b>Chế độ thường</b> — Kiểm tra kết nối & chụp màn hình\n\n") +
        "📍 <b>Bước 1/3</b> — Nhập địa chỉ IP:",
        parse_mode='HTML')
    return REMOTE_IP_STATE


async def get_remote_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ip = update.message.text.strip()
    if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        await update.message.reply_text("❌ IP không hợp lệ. Vui lòng nhập lại:")
        return REMOTE_IP_STATE
    user_data[user_id]['remote_ip'] = ip
    await update.message.reply_text(
        f"✅ IP đã nhận: <code>{ip}</code>\n\n"
        "📍 <b>Bước 2/3</b> — Nhập Username:",
        parse_mode='HTML')
    return REMOTE_USER_STATE


async def get_remote_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id]['remote_username'] = update.message.text.strip()
    await update.message.reply_text(
        "✅ Username đã nhận!\n\n"
        "📍 <b>Bước 3/3</b> — Nhập Password:",
        parse_mode='HTML')
    return REMOTE_PASS_STATE


async def get_remote_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    password = update.message.text.strip()
    ip       = user_data[user_id]['remote_ip']
    username = user_data[user_id]['remote_username']
    is_admin = user_data[user_id].get('_is_admin', False)

    msg = await update.message.reply_text(
        "🔄 Đang thiết lập kết nối, vui lòng chờ...")

    loop = asyncio.get_running_loop()
    session = await loop.run_in_executor(None, lambda: winrm_connect(ip, username, password))

    if not session:
        await msg.edit_text(
            "╔══════════════════════════╗\n"
            "║  ❌  KẾT NỐI THẤT BẠI   ║\n"
            "╚══════════════════════════╝\n\n"
            "Vui lòng kiểm tra:\n"
            "• Địa chỉ IP\n"
            "• Thông tin đăng nhập\n"
            "• WinRM đã được bật chưa\n\n"
            "Dùng /connect để thử lại.")
        user_data.pop(user_id, None)
        return ConversationHandler.END

    remote_sessions[user_id] = {
        'ip': ip, 'username': username, 'password': password, 'session': session
    }

    if is_admin:
        # Admin: deploy bot như cũ
        await msg.edit_text(
            "╔══════════════════════════════╗\n"
            "║  ✅  KẾT NỐI THÀNH CÔNG!    ║\n"
            "╚══════════════════════════════╝\n\n"
            f"🖥️ IP       : <code>{ip}</code>\n"
            f"👤 Username : <code>{username}</code>\n\n"
            "🤖 Đang triển khai bot trên máy từ xa...",
            parse_mode='HTML')
        asyncio.create_task(run_remote_bot_task(context.bot, user_id, session, username))
    else:
        # Người thường: chỉ chụp màn hình và gửi lại
        await msg.edit_text(
            "╔══════════════════════════════╗\n"
            "║  ✅  KẾT NỐI THÀNH CÔNG!    ║\n"
            "╚══════════════════════════════╝\n\n"
            f"🖥️ IP       : <code>{ip}</code>\n"
            f"👤 Username : <code>{username}</code>\n\n"
            "📸 Đang chụp màn hình để kiểm tra...",
            parse_mode='HTML')
        asyncio.create_task(run_screenshot_task(context.bot, user_id, session))

    user_data.pop(user_id, None)
    return ConversationHandler.END


async def run_remote_bot_task(bot, user_id: int, session, username: str):
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(None, lambda:
            winrm_run_bot(session, username, BOT_FILE_URL))

        dl  = results.get('download', {})
        ins = results.get('install', {})
        run = results.get('run', {})
        err = results.get('error', '')

        if err:
            text = (
                "╔══════════════════════════╗\n"
                "║  ❌  LỖI TRIỂN KHAI BOT  ║\n"
                "╚══════════════════════════╝\n\n"
                f"Chi tiết: <code>{err}</code>"
            )
        else:
            def status_icon(ok): return "✅" if ok else "❌"
            text = (
                "╔══════════════════════════════╗\n"
                "║  📋  KẾT QUẢ TRIỂN KHAI BOT  ║\n"
                "╚══════════════════════════════╝\n\n"
                f"{status_icon(dl.get('ok'))}  Tải file bot\n"
                f"{status_icon(ins.get('ok'))}  Cài thư viện\n"
                f"{status_icon(run.get('ok'))}  Khởi chạy bot\n\n"
                "💡 Bot đã được kích hoạt trên máy từ xa."
            )

        await bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Remote bot task error: {e}")
        await bot.send_message(chat_id=user_id,
                               text=f"❌ Lỗi khi triển khai bot: <code>{e}</code>",
                               parse_mode='HTML')


async def run_screenshot_task(bot, user_id: int, session):
    """Chụp màn hình RDP và gửi cho người dùng (dành cho người thường)."""
    loop = asyncio.get_running_loop()
    try:
        # Chạy lệnh PowerShell chụp màn hình và lưu vào file tạm
        screenshot_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
            "$bmp = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height); "
            "$g = [System.Drawing.Graphics]::FromImage($bmp); "
            "$g.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size); "
            "$path = \"$env:TEMP\\rdp_screenshot.png\"; "
            "$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png); "
            "Write-Host $path"
        )
        r = await loop.run_in_executor(None, lambda:
            session.run_ps(screenshot_cmd))

        if r.status_code != 0:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "╔══════════════════════════════╗\n"
                    "║  ✅  RDP ĐANG HOẠT ĐỘNG      ║\n"
                    "╚══════════════════════════════╝\n\n"
                    "✅ Kết nối WinRM thành công!\n"
                    "⚠️ Không thể chụp màn hình (có thể màn hình chưa hiển thị).\n\n"
                    "💡 RDP của bạn đang chạy bình thường."
                ),
                parse_mode='HTML')
            return

        # Đọc file ảnh từ máy từ xa qua WinRM
        remote_path = r.std_out.decode(errors='replace').strip().splitlines()[-1].strip()
        read_cmd = f"[Convert]::ToBase64String([IO.File]::ReadAllBytes('{remote_path}'))"
        r2 = await loop.run_in_executor(None, lambda:
            session.run_ps(read_cmd))

        if r2.status_code != 0 or not r2.std_out:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "╔══════════════════════════════╗\n"
                    "║  ✅  RDP ĐANG HOẠT ĐỘNG      ║\n"
                    "╚══════════════════════════════╝\n\n"
                    "✅ Kết nối thành công, RDP hoạt động tốt!\n"
                    "⚠️ Không thể đọc ảnh chụp màn hình.\n\n"
                    "💡 Bạn có thể kết nối RDP bình thường."
                ),
                parse_mode='HTML')
            return

        b64_data = r2.std_out.decode(errors='replace').strip()
        img_bytes = base64.b64decode(b64_data)

        await bot.send_photo(
            chat_id=user_id,
            photo=io.BytesIO(img_bytes),
            caption=(
                "╔══════════════════════════════╗\n"
                "║  ✅  RDP ĐANG HOẠT ĐỘNG      ║\n"
                "╚══════════════════════════════╝\n\n"
                "📸 Màn hình hiện tại của máy ảo.\n"
                "✅ Máy đang chạy bình thường!\n\n"
                "💡 Dùng /check để xem thông tin chi tiết."
            )
        )

    except Exception as e:
        logger.error(f"Screenshot task error: {e}")
        # Nếu lỗi chụp màn hình, vẫn thông báo kết nối thành công
        await bot.send_message(
            chat_id=user_id,
            text=(
                "╔══════════════════════════════╗\n"
                "║  ✅  RDP ĐANG HOẠT ĐỘNG      ║\n"
                "╚══════════════════════════════╝\n\n"
                "✅ Kết nối WinRM thành công!\n"
                f"⚠️ Không thể chụp màn hình: <code>{e}</code>\n\n"
                "💡 Máy của bạn đang chạy bình thường."
            ),
            parse_mode='HTML'
        )


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def generate_password(length=14) -> str:
    upper  = string.ascii_uppercase
    lower  = string.ascii_lowercase
    digits = string.digits
    pwd = [
        secrets.choice(upper),  secrets.choice(upper),
        secrets.choice(lower),  secrets.choice(lower),
        secrets.choice(digits), secrets.choice(digits),
    ]
    alphabet = upper + lower + digits
    pwd += [secrets.choice(alphabet) for _ in range(length - len(pwd))]
    secrets.SystemRandom().shuffle(pwd)
    return ''.join(pwd)


def generate_username() -> str:
    suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return "User" + suffix


def encrypt_secret(public_key: str, secret_value: str) -> str:
    public_key_obj = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key_obj)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def parse_duration(text: str, user_id: int = 0) -> Optional[tuple]:
    is_admin = (user_id == ADMIN_ID)
    min_m = ADMIN_MIN  if is_admin else NORMAL_MIN
    max_m = ADMIN_MAX  if is_admin else NORMAL_MAX

    text = text.strip().lower().replace(' ', '')
    hours = 0
    minutes = 0

    pattern = re.match(r'^(?:(\d+)h)?(?:(\d+)p(?:h)?)?$', text)
    if not pattern:
        only_num = re.match(r'^(\d+)$', text)
        if only_num:
            minutes = int(only_num.group(1))
        else:
            return None
    else:
        h_part = pattern.group(1)
        m_part = pattern.group(2)
        if h_part is None and m_part is None:
            return None
        if h_part:
            hours = int(h_part)
        if m_part:
            minutes = int(m_part)

    total = hours * 60 + minutes
    if total < min_m or total > max_m:
        return None

    if hours > 0 and minutes > 0:
        display = f"{hours}h {minutes}p"
    elif hours > 0:
        display = f"{hours}h"
    else:
        display = f"{minutes}p"

    return total, display


def format_remaining(seconds: float) -> str:
    if seconds <= 0:
        return "Đã hết hạn"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}p {s:02d}s"
    elif m > 0:
        return f"{m}p {s:02d}s"
    else:
        return f"{s}s"


def format_datetime_vn(ts: float) -> str:
    """Định dạng timestamp sang giờ Việt Nam (UTC+7)."""
    tz_vn = timezone(timedelta(hours=7))
    dt = datetime.fromtimestamp(ts, tz=tz_vn)
    return dt.strftime("%H:%M:%S %d/%m/%Y (GMT+7)")


def create_progress_bar(percent: int, length: int = 12) -> str:
    filled = int(percent / 100 * length)
    bar = "▰" * filled + "▱" * (length - filled)
    return bar


def create_workflow_content(random_password: str, duration_minutes: int, rdp_username: str) -> str:
    duration_seconds = duration_minutes * 60
    timeout_minutes  = duration_minutes + 10
    return """name: Windows RDP

on:
  workflow_dispatch:

jobs:
  rdp:
    runs-on: windows-latest
    timeout-minutes: """ + str(timeout_minutes) + """

    steps:
      - name: Enable Remote Desktop
        shell: powershell
        run: |
          Set-ItemProperty -Path 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server' -Name "fDenyTSConnections" -Value 0
          Enable-NetFirewallRule -DisplayGroup "Remote Desktop"
          Restart-Service -Name TermService -Force

      - name: Rename Computer
        shell: powershell
        run: |
          Rename-Computer -NewName "AI-STV" -Force

      - name: Create RDP User
        shell: powershell
        run: |
          $rdpUser = \"""" + rdp_username + """\"
          $rdpPass = \"""" + random_password + """\"
          $securePass = ConvertTo-SecureString $rdpPass -AsPlainText -Force
          if (Get-LocalUser -Name $rdpUser -ErrorAction SilentlyContinue) { Remove-LocalUser -Name $rdpUser }
          New-LocalUser -Name $rdpUser -Password $securePass -AccountNeverExpires -PasswordNeverExpires
          Add-LocalGroupMember -Group "Administrators" -Member $rdpUser
          Add-LocalGroupMember -Group "Remote Desktop Users" -Member $rdpUser

      - name: Install Tailscale
        shell: powershell
        run: |
          $url = "https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi"
          $file = "$env:TEMP\\tailscale.msi"
          $success = $false
          for ($try = 1; $try -le 3 -and -not $success; $try++) {
            try {
              Invoke-WebRequest $url -OutFile $file -UseBasicParsing -TimeoutSec 60
              if ((Test-Path $file) -and (Get-Item $file).Length -gt 1MB) {
                Start-Process msiexec.exe -ArgumentList "/i `"$file`" /quiet /norestart" -Wait
                $success = $true
              }
            } catch {
              Write-Host "Attempt $try failed, retrying..."
              Start-Sleep -Seconds 5
            }
          }
          if (-not $success) { exit 1 }

      - name: Connect Tailscale
        shell: powershell
        env:
          TAILSCALE_AUTH_KEY: ${{ secrets.TAILSCALE_AUTH_KEY }}
        run: |
          $tsExe = "$env:ProgramFiles\\Tailscale\\tailscale.exe"
          & $tsExe up --authkey="$env:TAILSCALE_AUTH_KEY" --hostname="github-rdp-${{ github.run_id }}" --accept-routes
          for ($i = 0; $i -lt 18; $i++) {
            Start-Sleep -Seconds 5
            $ip = (& $tsExe ip -4 2>$null) -replace '\\s',''
            if ($ip -match '^\\d+\\.\\d+\\.\\d+\\.\\d+$') {
              Write-Host "Tailscale connected: $ip"
              break
            }
            Write-Host "Waiting... ($i)"
          }

      - name: Report IP
        shell: powershell
        run: |
          $tsExe = "$env:ProgramFiles\\Tailscale\\tailscale.exe"
          $ip = (& $tsExe ip -4 2>$null) -replace '\\s',''
          Write-Host "TAILSCALE_IP=$ip"
          echo $ip | Out-File -FilePath "$env:GITHUB_WORKSPACE\\tailscale_ip.txt" -Encoding utf8

      - name: Upload IP Artifact
        uses: actions/upload-artifact@v4
        with:
          name: rdp-ip
          path: tailscale_ip.txt
          retention-days: 1

      - name: Keep Session Alive
        shell: powershell
        run: |
          Write-Host "Session active for """ + str(duration_minutes) + """ minutes..."
          Start-Sleep -Seconds """ + str(duration_seconds) + """
"""


# ─────────────────────────────────────────────────────────────────────────────
# GitHub / Tailscale helpers
# ─────────────────────────────────────────────────────────────────────────────

def gh_headers(token: str) -> dict:
    return {'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'}


def get_latest_run(token: str, username: str, repo: str) -> Optional[dict]:
    r = requests.get(
        f'https://api.github.com/repos/{username}/{repo}/actions/runs?per_page=1',
        headers=gh_headers(token))
    if r.status_code != 200:
        return None
    d = r.json()
    return d['workflow_runs'][0] if d['total_count'] > 0 else None


def get_jobs(token: str, username: str, repo: str, run_id: int) -> list:
    r = requests.get(
        f'https://api.github.com/repos/{username}/{repo}/actions/runs/{run_id}/jobs',
        headers=gh_headers(token))
    return r.json().get('jobs', []) if r.status_code == 200 else []


def tailscale_step_done(jobs: list) -> bool:
    for job in jobs:
        for step in job.get('steps', []):
            name   = step.get('name', '').lower()
            status = step.get('status', '')
            if 'report ip' in name and status == 'completed':
                return True
            if 'upload ip artifact' in name and status == 'completed':
                return True
    for job in jobs:
        for step in job.get('steps', []):
            if 'connect tailscale' in step.get('name', '').lower():
                if step.get('status') == 'completed':
                    return True
    return False


def workflow_finished(jobs: list) -> bool:
    return any(job.get('status') == 'completed' for job in jobs)


def get_ip_from_artifact(github_token: str, username: str, repo: str,
                          run_id: int) -> Optional[str]:
    headers = gh_headers(github_token)
    r = requests.get(
        f'https://api.github.com/repos/{username}/{repo}/actions/runs/{run_id}/artifacts',
        headers=headers, timeout=10)
    if r.status_code != 200:
        return None
    for art in r.json().get('artifacts', []):
        if art.get('name') == 'rdp-ip':
            dl = requests.get(art['archive_download_url'], headers=headers,
                               timeout=30, allow_redirects=True)
            if dl.status_code != 200:
                continue
            try:
                z = zipfile.ZipFile(io.BytesIO(dl.content))
                for name in z.namelist():
                    text = z.read(name).decode('utf-8', errors='replace').strip()
                    ip   = text.splitlines()[0].strip()
                    if re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
                        return ip
            except Exception as e:
                logger.error(f"Artifact parse error: {e}")
    return None


def get_tailscale_ip_from_api(tailscale_api_key: str, run_id: int) -> Optional[str]:
    exact_hostname = f"github-rdp-{run_id}"
    headers = {'Authorization': f'Bearer {tailscale_api_key}'}
    r = requests.get('https://api.tailscale.com/api/v2/tailnet/-/devices',
                     headers=headers, timeout=10)
    if r.status_code != 200:
        return None
    for device in r.json().get('devices', []):
        hostname = device.get('hostname', '') or device.get('name', '')
        if hostname == exact_hostname or hostname.startswith(exact_hostname):
            for addr in device.get('addresses', []):
                if re.match(r'^\d+\.\d+\.\d+\.\d+$', addr):
                    return addr
    return None


def delete_workflow_file(github_token: str, username: str, repo_name: str) -> bool:
    headers = gh_headers(github_token)
    r = requests.get(
        f'https://api.github.com/repos/{username}/{repo_name}/contents/.github/workflows/windows-rdp.yml',
        headers=headers, timeout=10)
    if r.status_code != 200:
        return False
    sha = r.json().get('sha')
    if not sha:
        return False
    dr = requests.delete(
        f'https://api.github.com/repos/{username}/{repo_name}/contents/.github/workflows/windows-rdp.yml',
        headers=headers,
        json={'message': 'Remove workflow file for security', 'sha': sha},
        timeout=10)
    return dr.status_code == 200


async def do_delete_repo(loop, github_token: str, username: str,
                          repo_name: str, bot, user_id: int):
    try:
        r = await loop.run_in_executor(None, lambda:
            requests.delete(
                f'https://api.github.com/repos/{username}/{repo_name}',
                headers=gh_headers(github_token)))
        if r.status_code == 204:
            logger.info(f"Repo {repo_name} deleted")
            await bot.send_message(
                chat_id=user_id,
                text="Xong")
        else:
            logger.warning(f"Delete repo failed: {r.status_code}")
    except Exception as e:
        logger.error(f"Delete repo error: {e}")


async def setup_github(github_token: str, repo_name: str,
                        workflow_content: str, tailscale_key: str) -> Optional[str]:
    loop = asyncio.get_running_loop()

    user_resp = await loop.run_in_executor(None, lambda:
        requests.get('https://api.github.com/user', headers=gh_headers(github_token)))
    if user_resp.status_code != 200:
        return None
    username = user_resp.json()['login']

    cr = await loop.run_in_executor(None, lambda:
        requests.post('https://api.github.com/user/repos',
                      headers=gh_headers(github_token),
                      json={'name': repo_name, 'private': False, 'auto_init': True,
                            'description': 'Windows RDP via GitHub Actions'}))
    if cr.status_code != 201:
        return None

    await asyncio.sleep(4)

    content_b64 = base64.b64encode(workflow_content.encode()).decode()
    fr = await loop.run_in_executor(None, lambda:
        requests.put(
            f'https://api.github.com/repos/{username}/{repo_name}/contents/.github/workflows/windows-rdp.yml',
            headers=gh_headers(github_token),
            json={'message': 'Add RDP workflow', 'content': content_b64, 'branch': 'main'}))
    if fr.status_code not in [200, 201]:
        return None

    pk_resp = await loop.run_in_executor(None, lambda:
        requests.get(
            f'https://api.github.com/repos/{username}/{repo_name}/actions/secrets/public-key',
            headers=gh_headers(github_token)))
    if pk_resp.status_code == 200:
        pk        = pk_resp.json()
        encrypted = encrypt_secret(pk['key'], tailscale_key)
        await loop.run_in_executor(None, lambda:
            requests.put(
                f'https://api.github.com/repos/{username}/{repo_name}/actions/secrets/TAILSCALE_AUTH_KEY',
                headers=gh_headers(github_token),
                json={'encrypted_value': encrypted, 'key_id': pk['key_id']}))

    await loop.run_in_executor(None, lambda:
        requests.post(
            f'https://api.github.com/repos/{username}/{repo_name}/actions/workflows/windows-rdp.yml/dispatches',
            headers=gh_headers(github_token), json={'ref': 'main'}))

    return username


# ═════════════════════════════════════════════════════════════════════════════
# Background task: tạo RDP + lưu Firebase
# ═════════════════════════════════════════════════════════════════════════════

async def create_rdp_background(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 user_id: int):
    loop = asyncio.get_running_loop()
    username     = None
    repo_name    = None
    github_token = None
    run_id       = None

    try:
        github_token      = user_data[user_id]['github_token']
        tailscale_key     = user_data[user_id]['tailscale_key']
        tailscale_api_key = user_data[user_id].get('tailscale_api_key')
        duration_minutes  = user_data[user_id].get('duration_minutes', 60)
        duration_display  = user_data[user_id].get('duration_display', '1h')
        tg_user           = update.effective_user
        username_tg       = tg_user.username or tg_user.full_name or str(user_id)
        del user_data[user_id]

        random_password  = generate_password()
        rdp_username     = generate_username()
        workflow_content = create_workflow_content(random_password, duration_minutes, rdp_username)
        repo_name        = f"rdp-{user_id}-{int(time.time())}"

        username = await setup_github(github_token, repo_name, workflow_content, tailscale_key)
        if not username:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "╔══════════════════════════╗\n"
                    "║  ❌  TẠO REPO THẤT BẠI   ║\n"
                    "╚══════════════════════════╝\n\n"
                    "GitHub Token cần có đủ quyền:\n"
                    "• <code>repo</code>\n"
                    "• <code>workflow</code>\n\n"
                    "Vui lòng kiểm tra và thử lại."
                ),
                parse_mode='HTML')
            active_sessions.pop(user_id, None)
            return

        start_time = time.time()

        active_sessions[user_id] = {
            'expire_at':        start_time + duration_minutes * 60,
            'start_at':         start_time,
            'duration_minutes': duration_minutes,
            'duration_display': duration_display,
            'github_token':     github_token,
            'username':         username,
            'repo_name':        repo_name,
            'run_id':           None,
            'rdp_user':         rdp_username,
            'rdp_pass':         random_password,
            'rdp_ip':           None,
        }

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "╔════════════════════════════════╗\n"
                "║  ⚙️   ĐANG KHỞI ĐỘNG HỆ THỐNG  ║\n"
                "╚════════════════════════════════╝\n\n"
                "✅ Workflow đã được kích hoạt thành công!\n\n"
                f"⏱️  Thời gian thuê  : <b>{duration_display}</b>\n"
                f"⏳  Trạng thái      : Đang cài đặt Windows...\n\n"
                "💬 Bạn sẽ nhận thông báo khi máy sẵn sàng."
            ),
            parse_mode='HTML',
            disable_web_page_preview=True)

        rdp_ip         = None
        last_status    = None
        tailscale_done = False

        # ── Phase 1: Chờ workflow chạy ────────────────────────
        for attempt in range(90):
            await asyncio.sleep(10)
            try:
                if run_id is None:
                    run = await loop.run_in_executor(None, lambda:
                        get_latest_run(github_token, username, repo_name))
                    if run:
                        run_id = run['id']
                        active_sessions[user_id]['run_id'] = run_id
                    continue

                jobs = await loop.run_in_executor(None, lambda:
                    get_jobs(github_token, username, repo_name, run_id))
                if not jobs:
                    continue

                job_status = jobs[0].get('status')
                if job_status != last_status:
                    last_status = job_status
                    if job_status == 'in_progress':
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=(
                                "🔧 <b>Tiến trình cài đặt:</b>\n\n"
                                "✅ Khởi động máy ảo\n"
                                "🔄 Cài đặt Windows 11...\n"
                                "⏳ Vui lòng chờ (5-10 phút)"
                            ),
                            parse_mode='HTML')

                if (job_status == 'completed'
                        and jobs[0].get('conclusion') not in ['success', None]):
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "╔═══════════════════════════╗\n"
                            "║  ❌  WORKFLOW THẤT BẠI    ║\n"
                            "╚═══════════════════════════╝\n\n"
                            "Máy ảo không thể khởi động.\n"
                            "Vui lòng dùng /create để thử lại."
                        ))
                    active_sessions.pop(user_id, None)
                    return

                if tailscale_step_done(jobs):
                    tailscale_done = True
                    break

            except Exception as e:
                logger.error(f"Poll phase1 #{attempt}: {e}")

        # ── Phase 2: Lấy IP ──────────────────────────────────
        if tailscale_done:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "🌐 <b>Tailscale đã kết nối!</b>\n"
                    "⏳ Đang lấy địa chỉ IP, vui lòng chờ..."
                ),
                parse_mode='HTML')

            for retry in range(12):
                rdp_ip = await loop.run_in_executor(None, lambda:
                    get_ip_from_artifact(github_token, username, repo_name, run_id))
                if rdp_ip:
                    break
                if tailscale_api_key:
                    rdp_ip = await loop.run_in_executor(None, lambda:
                        get_tailscale_ip_from_api(tailscale_api_key, run_id))
                    if rdp_ip:
                        break
                logger.info(f"IP retry {retry+1}/12")
                await asyncio.sleep(10)

        # ── Lưu lên Firebase ─────────────────────────────────
        if rdp_ip:
            active_sessions[user_id]['rdp_ip'] = rdp_ip
            loop.run_in_executor(None, lambda:
                save_rdp_to_firebase(
                    user_id, username_tg, rdp_ip,
                    rdp_username, random_password,
                    duration_minutes, start_time))

        # ── Gửi thông tin login ──────────────────────────────
        expire_time_str = format_datetime_vn(start_time + duration_minutes * 60)
        create_time_str = format_datetime_vn(start_time)

        if rdp_ip:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "╔══════════════════════════════════╗\n"
                    "║  🎉  WINDOWS AI STV SẴN SÀNG!   ║\n"
                    "╚══════════════════════════════════╝\n\n"
                    "━━━━━━ 🖥️  THÔNG TIN KẾT NỐI ━━━━━━\n\n"
                    f"🌐  IP Address  : <code>{rdp_ip}</code>\n"
                    f"👤  Username    : <code>{rdp_username}</code>\n"
                    f"🔑  Password    : <code>{random_password}</code>\n\n"
                    "━━━━━━ ⏰  THỜI GIAN SỬ DỤNG ━━━━━━\n\n"
                    f"📅  Bắt đầu    : {create_time_str}\n"
                    f"⌛  Hết hạn    : {expire_time_str}\n"
                    f"⏱️  Thời lượng  : {duration_display}\n\n"
                    "━━━━━━ 📋  HƯỚNG DẪN KẾT NỐI ━━━━━━\n\n"
                    "1️⃣  Tải Tailscale: tailscale.com/download\n"
                    "2️⃣  Đăng nhập cùng tài khoản Tailscale\n"
                    "3️⃣  Bật kết nối → Mở Remote Desktop\n"
                    "4️⃣  Nhập IP, username và password\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "💡 Dùng /check để xem thời gian còn lại\n"
                    "⚠️  Máy ảo tự tắt khi hết thời gian"
                ),
                parse_mode='HTML',
                disable_web_page_preview=True)
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "╔════════════════════════════════╗\n"
                    "║  ⚠️   KHÔNG LẤY ĐƯỢC ĐỊA CHỈ IP  ║\n"
                    "╚════════════════════════════════╝\n\n"
                    f"👤  Username  : <code>{rdp_username}</code>\n"
                    f"🔑  Password  : <code>{random_password}</code>\n\n"
                    "🔍 Xem IP tại:\n"
                    "login.tailscale.com/admin/machines\n\n"
                    "💡 Dùng /check để xem thời gian còn lại."
                ),
                parse_mode='HTML',
                disable_web_page_preview=True)

        # ── Xóa workflow file ─────────────────────────────────
        await loop.run_in_executor(None, lambda:
            delete_workflow_file(github_token, username, repo_name))

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✨ <b>Chúc bạn sử dụng dịch vụ vui vẻ!</b>"
            ),
            parse_mode='HTML')

        # ── Phase 3: Chờ hết hạn ─────────────────────────────
        expire_at = active_sessions[user_id]['expire_at']
        wait_secs = max(expire_at - time.time(), 0)
        logger.info(f"Chờ {wait_secs:.0f}s trước khi thông báo hết hạn...")
        await asyncio.sleep(wait_secs)

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "╔══════════════════════════════╗\n"
                "║  ⏰  PHIÊN LÀM VIỆC HẾT HẠN  ║\n"
                "╚══════════════════════════════╝\n\n"
                f"Thời gian thuê <b>{duration_display}</b> đã kết thúc.\n"
                "Máy ảo sẽ tự động tắt.\n\n"
                "🔄 Dùng /create để tạo máy mới."
            ),
            parse_mode='HTML')

        # Cập nhật Firebase: hết hạn
        await loop.run_in_executor(None, lambda: mark_rdp_expired_firebase(user_id))
        active_sessions.pop(user_id, None)

        # Chờ workflow kết thúc rồi xóa repo
        max_polls = 15 * 6
        for attempt in range(max_polls):
            await asyncio.sleep(10)
            try:
                if run_id:
                    jobs = await loop.run_in_executor(None, lambda:
                        get_jobs(github_token, username, repo_name, run_id))
                    if jobs and workflow_finished(jobs):
                        break
            except Exception as e:
                logger.error(f"Poll phase3 #{attempt}: {e}")

        await do_delete_repo(loop, github_token, username, repo_name, context.bot, user_id)

    except Exception as e:
        logger.error(f"Background error: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "╔═══════════════════════╗\n"
                "║  ❌  ĐÃ XẢY RA LỖI   ║\n"
                "╚═══════════════════════╝\n\n"
                f"Chi tiết: <code>{str(e)}</code>\n\n"
                "🔄 Dùng /create để thử lại."
            ),
            parse_mode='HTML')
        active_sessions.pop(user_id, None)
        if username and repo_name and github_token:
            loop2 = asyncio.get_running_loop()
            await do_delete_repo(loop2, github_token, username, repo_name,
                                  context.bot, user_id)


# ═════════════════════════════════════════════════════════════════════════════
# /check — Xem thông tin RDP hiện tại
# ═════════════════════════════════════════════════════════════════════════════

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Kiểm tra session local trước
    session = active_sessions.get(user_id)

    # Nếu không có local thì kiểm tra Firebase
    if not session:
        fb_data = await asyncio.get_running_loop().run_in_executor(
            None, lambda: check_user_has_active_rdp_firebase(user_id))

        if not fb_data:
            await update.message.reply_text(
                "╔═══════════════════════════════╗\n"
                "║  ℹ️   KHÔNG CÓ MÁY ĐANG CHẠY  ║\n"
                "╚═══════════════════════════════╝\n\n"
                "Bạn chưa có phiên Windows nào đang hoạt động.\n\n"
                "🚀 Dùng /create để tạo máy mới.",
                parse_mode='HTML')
            return

        # Hiển thị từ Firebase
        now       = time.time()
        exp_ts    = fb_data.get("expires_ts", 0)
        crt_ts    = fb_data.get("created_ts", now)
        remaining = exp_ts - now
        elapsed   = now - crt_ts
        total_secs = fb_data.get("duration_minutes", 60) * 60
        percent_used = min(int(elapsed / total_secs * 100), 100)
        bar = create_progress_bar(percent_used)

        await update.message.reply_text(
            "╔═════════════════════════════════╗\n"
            "║  🖥️   THÔNG TIN MÁY WINDOWS      ║\n"
            "╚═════════════════════════════════╝\n\n"
            "━━━━━━ 🔌  KẾT NỐI ━━━━━━\n\n"
            f"🌐  IP Address  : <code>{fb_data.get('ip', 'N/A')}</code>\n"
            f"👤  Username    : <code>{fb_data.get('rdp_user', 'N/A')}</code>\n"
            f"🔑  Password    : <code>{fb_data.get('rdp_pass', 'N/A')}</code>\n\n"
            "━━━━━━ ⏰  THỜI GIAN ━━━━━━\n\n"
            f"📅  Bắt đầu    : {fb_data.get('created_at', 'N/A')}\n"
            f"⌛  Hết hạn    : {fb_data.get('expires_at', 'N/A')}\n"
            f"⏱️  Còn lại    : <b>{format_remaining(remaining)}</b>\n\n"
            f"<code>[{bar}] {percent_used}%</code>",
            parse_mode='HTML')
        return

    # Hiển thị từ local session
    now        = time.time()
    expire_at  = session['expire_at']
    start_at   = session['start_at']
    remaining  = expire_at - now
    elapsed    = now - start_at
    total_secs = session['duration_minutes'] * 60

    if remaining <= 0:
        active_sessions.pop(user_id, None)
        await update.message.reply_text(
            "╔════════════════════════════╗\n"
            "║  ⏰  PHIÊN ĐÃ HẾT HẠN      ║\n"
            "╚════════════════════════════╝\n\n"
            "Máy ảo của bạn đã tắt.\n\n"
            "🔄 Dùng /create để tạo máy mới.",
            parse_mode='HTML')
        return

    percent_used = min(int(elapsed / total_secs * 100), 100)
    bar = create_progress_bar(percent_used)
    rdp_ip   = session.get('rdp_ip', 'Đang lấy...')
    rdp_user = session.get('rdp_user', 'N/A')
    rdp_pass = session.get('rdp_pass', 'N/A')

    expire_str = format_datetime_vn(expire_at)
    start_str  = format_datetime_vn(start_at)

    await update.message.reply_text(
        "╔═════════════════════════════════╗\n"
        "║  🖥️   THÔNG TIN MÁY WINDOWS      ║\n"
        "╚═════════════════════════════════╝\n\n"
        "━━━━━━ 🔌  KẾT NỐI ━━━━━━\n\n"
        f"🌐  IP Address  : <code>{rdp_ip}</code>\n"
        f"👤  Username    : <code>{rdp_user}</code>\n"
        f"🔑  Password    : <code>{rdp_pass}</code>\n\n"
        "━━━━━━ ⏰  THỜI GIAN ━━━━━━\n\n"
        f"📅  Bắt đầu    : {start_str}\n"
        f"⌛  Hết hạn    : {expire_str}\n"
        f"✅  Đã dùng    : {format_remaining(elapsed)}\n"
        f"⏳  Còn lại    : <b>{format_remaining(remaining)}</b>\n\n"
        f"<code>[{bar}] {percent_used}%</code>\n\n"
        "⚠️  Máy tự tắt khi hết thời gian.",
        parse_mode='HTML')


# ═════════════════════════════════════════════════════════════════════════════
# Conversation handlers (tạo RDP)
# ═════════════════════════════════════════════════════════════════════════════

async def create_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loop    = asyncio.get_running_loop()

    # Kiểm tra session đang chạy (local)
    session = active_sessions.get(user_id)
    if session:
        remaining = session['expire_at'] - time.time()
        if remaining > 0:
            await update.message.reply_text(
                "╔═══════════════════════════════════╗\n"
                "║  ⚠️   BẠN ĐÃ CÓ MÁY ĐANG CHẠY!    ║\n"
                "╚═══════════════════════════════════╝\n\n"
                f"⏳  Còn lại : <b>{format_remaining(remaining)}</b>\n\n"
                "Mỗi tài khoản chỉ được tạo <b>1 máy</b> tại một thời điểm.\n"
                "Chờ hết hạn mới tạo được máy mới.\n\n"
                "💡 Dùng /check để xem chi tiết.",
                parse_mode='HTML')
            return ConversationHandler.END
        else:
            active_sessions.pop(user_id, None)

    # Kiểm tra Firebase
    fb_data = await loop.run_in_executor(
        None, lambda: check_user_has_active_rdp_firebase(user_id))

    if fb_data:
        exp_ts    = fb_data.get("expires_ts", 0)
        remaining = exp_ts - time.time()
        await update.message.reply_text(
            "╔═══════════════════════════════════╗\n"
            "║  ⚠️   BẠN ĐÃ CÓ MÁY ĐANG CHẠY!    ║\n"
            "╚═══════════════════════════════════╝\n\n"
            f"🌐  IP       : <code>{fb_data.get('ip', 'N/A')}</code>\n"
            f"👤  Username : <code>{fb_data.get('rdp_user', 'N/A')}</code>\n"
            f"⏳  Còn lại  : <b>{format_remaining(remaining)}</b>\n"
            f"⌛  Hết hạn  : {fb_data.get('expires_at', 'N/A')}\n\n"
            "Mỗi tài khoản chỉ được tạo <b>1 máy</b> tại một thời điểm.\n"
            "Chờ hết hạn mới tạo được máy mới.\n\n"
            "💡 Dùng /check để xem đầy đủ thông tin.",
            parse_mode='HTML')
        return ConversationHandler.END

    user_data[user_id] = {}
    await update.message.reply_text(
        "╔══════════════════════════════╗\n"
        "║  🚀  TẠO WINDOWS AI STV MỚI  ║\n"
        "╚══════════════════════════════╝\n\n"
        "📍 <b>Bước 1/4</b> — GitHub Personal Access Token\n\n"
        "Cần cấp quyền: <code>repo</code> và <code>workflow</code>\n"
        "Tạo tại: github.com/settings/tokens\n\n"
        "Vui lòng gửi token:",
        parse_mode='HTML')
    return GITHUB_TOKEN_STATE


async def get_github_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    token   = update.message.text.strip()
    if len(token) < 10:
        await update.message.reply_text("❌ Token không hợp lệ! Vui lòng gửi lại.")
        return ConversationHandler.END
    user_data[user_id]['github_token'] = token
    await update.message.reply_text(
        "✅ GitHub Token đã nhận!\n\n"
        "📍 <b>Bước 2/4</b> — Tailscale Auth Key\n\n"
        "Định dạng: <code>tskey-auth-...</code>\n"
        "Tạo tại: login.tailscale.com/admin/settings/keys\n\n"
        "Vui lòng gửi Auth Key:",
        parse_mode='HTML')
    return TAILSCALE_KEY_STATE


async def get_tailscale_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    key     = update.message.text.strip()
    if not key.startswith('tskey-') or len(key) < 40:
        await update.message.reply_text(
            "❌ Auth Key không hợp lệ!\n"
            "Phải bắt đầu bằng <code>tskey-auth-...</code>\n\n"
            "Dùng /create để thử lại.",
            parse_mode='HTML')
        return ConversationHandler.END
    user_data[user_id]['tailscale_key'] = key
    await update.message.reply_text(
        "✅ Tailscale Auth Key đã nhận!\n\n"
        "📍 <b>Bước 3/4</b> — Tailscale API Key\n\n"
        "Định dạng: <code>tskey-api-...</code>\n"
        "Chọn loại <b>API access token</b>\n"
        "Tạo tại: login.tailscale.com/admin/settings/keys\n\n"
        "Vui lòng gửi API Key:",
        parse_mode='HTML')
    return TAILSCALE_API_STATE


async def get_tailscale_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    api_key = update.message.text.strip()
    if not api_key.startswith('tskey-') or len(api_key) < 40:
        await update.message.reply_text(
            "❌ API Key không hợp lệ!\n"
            "Phải bắt đầu bằng <code>tskey-api-...</code>\n\n"
            "Dùng /create để thử lại.",
            parse_mode='HTML')
        return ConversationHandler.END
    user_data[user_id]['tailscale_api_key'] = api_key

    is_admin = (user_id == ADMIN_ID)
    if is_admin:
        keyboard = [
            [
                InlineKeyboardButton("15p", callback_data='dur_15'),
                InlineKeyboardButton("30p", callback_data='dur_30'),
                InlineKeyboardButton("1h",  callback_data='dur_60'),
            ],
            [
                InlineKeyboardButton("1h30p", callback_data='dur_90'),
                InlineKeyboardButton("2h",    callback_data='dur_120'),
                InlineKeyboardButton("3h",    callback_data='dur_180'),
            ],
            [
                InlineKeyboardButton("4h",    callback_data='dur_240'),
                InlineKeyboardButton("5h",    callback_data='dur_300'),
                InlineKeyboardButton("6h",    callback_data='dur_360'),
            ],
            [InlineKeyboardButton("⌨️ Tự nhập thời gian", callback_data='dur_custom')]
        ]
        note = "👑 <b>[ADMIN]</b> — Giới hạn: 15 phút → 6 giờ"
    else:
        keyboard = [
            [
                InlineKeyboardButton("1h",    callback_data='dur_60'),
                InlineKeyboardButton("1h30p", callback_data='dur_90'),
                InlineKeyboardButton("2h",    callback_data='dur_120'),
            ],
            [
                InlineKeyboardButton("2h30p", callback_data='dur_150'),
                InlineKeyboardButton("3h",    callback_data='dur_180'),
                InlineKeyboardButton("⌨️ Tự nhập", callback_data='dur_custom'),
            ]
        ]
        note = "⏱️ Giới hạn: 1 giờ → 3 giờ"

    await update.message.reply_text(
        "✅ Tailscale API Key đã nhận!\n\n"
        "╔══════════════════════════════╗\n"
        "║  ⏱️   BƯỚC 4/4 — THỜI GIAN   ║\n"
        "╚══════════════════════════════╝\n\n"
        f"{note}\n\n"
        "Chọn thời gian sử dụng máy ảo:",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard))
    return DURATION_STATE


async def duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    user_id  = update.effective_user.id
    is_admin = (user_id == ADMIN_ID)

    if query.data == 'dur_custom':
        limit_text = ("⚠️ Giới hạn: <b>15p → 6h</b>" if is_admin
                      else "⚠️ Giới hạn: <b>1h → 3h</b>")
        await query.edit_message_text(
            "⌨️ <b>Nhập thời gian tùy chỉnh:</b>\n\n"
            "📝 Định dạng hỗ trợ:\n"
            "• <code>1h</code>     → 1 giờ\n"
            "• <code>2h30p</code>  → 2 giờ 30 phút\n"
            "• <code>1h26p</code>  → 1 giờ 26 phút\n"
            "• <code>90p</code>    → 90 phút\n\n"
            f"{limit_text}\n\n"
            "Vui lòng nhập thời gian:",
            parse_mode='HTML')
        return DURATION_STATE

    minutes = int(query.data.replace('dur_', ''))
    h, m    = divmod(minutes, 60)
    raw_str = f"{h}h{m}p" if m else f"{h}h" if h else f"{minutes}p"
    result  = parse_duration(raw_str, user_id)
    if not result:
        await query.edit_message_text("❌ Lỗi thời gian. Dùng /create để thử lại.")
        return ConversationHandler.END

    total_minutes, display = result
    user_data[user_id]['duration_minutes'] = total_minutes
    user_data[user_id]['duration_display']  = display
    return await show_confirm(query, user_id, is_query=True)


async def get_duration_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    text     = update.message.text.strip()
    result   = parse_duration(text, user_id)
    is_admin = (user_id == ADMIN_ID)

    if not result:
        limit_text = ("⚠️ Giới hạn: 15p → 6h (15-360 phút)"
                      if is_admin else "⚠️ Giới hạn: 1h → 3h (60-180 phút)")
        await update.message.reply_text(
            "❌ <b>Thời gian không hợp lệ!</b>\n\n"
            f"{limit_text}\n\n"
            "📝 Ví dụ đúng:\n"
            "<code>1h</code> · <code>2h30p</code> · <code>1h26p</code> · <code>90p</code>\n\n"
            "Vui lòng nhập lại:",
            parse_mode='HTML')
        return DURATION_STATE

    total_minutes, display = result
    user_data[user_id]['duration_minutes'] = total_minutes
    user_data[user_id]['duration_display']  = display
    return await show_confirm(update, user_id, is_query=False)


async def show_confirm(update_or_query, user_id: int, is_query: bool):
    data     = user_data[user_id]
    keyboard = [[
        InlineKeyboardButton("🚀 Bắt đầu tạo máy", callback_data='start_create'),
        InlineKeyboardButton("❌ Hủy",              callback_data='cancel')
    ]]
    text = (
        "╔══════════════════════════════╗\n"
        "║  ✅  XÁC NHẬN TẠO WINDOWS    ║\n"
        "╚══════════════════════════════╝\n\n"
        f"🔑  GitHub Token    : <code>{data['github_token'][:10]}...</code>\n"
        f"🔐  Tailscale Auth  : <code>{data['tailscale_key'][:15]}...</code>\n"
        f"🗝️   Tailscale API   : <code>{data['tailscale_api_key'][:15]}...</code>\n"
        f"⏱️   Thời gian       : <b>{data['duration_display']}</b> ({data['duration_minutes']} phút)\n\n"
        "Nhấn <b>Bắt đầu tạo máy</b> để tiến hành!"
    )
    markup = InlineKeyboardMarkup(keyboard)
    if is_query:
        await update_or_query.edit_message_text(text, parse_mode='HTML', reply_markup=markup)
    else:
        await update_or_query.message.reply_text(text, parse_mode='HTML', reply_markup=markup)
    return CONFIRM_STATE


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == 'cancel':
        user_data.pop(user_id, None)
        await query.edit_message_text(
            "❌ Đã hủy tạo máy.\n\n"
            "Dùng /create để bắt đầu lại.")
        return ConversationHandler.END

    if query.data.startswith('dur_'):
        return await duration_callback(update, context)

    await query.edit_message_text(
        "╔══════════════════════════════╗\n"
        "║  🔄  ĐANG KHỞI TẠO WINDOWS   ║\n"
        "╚══════════════════════════════╝\n\n"
        "Hệ thống đang chuẩn bị máy ảo...\n"
        "Quá trình mất khoảng 5-10 phút.\n\n"
        "⏳ Bạn sẽ nhận thông báo khi hoàn tất.",
        parse_mode='HTML')
    asyncio.create_task(create_rdp_background(update, context, user_id))
    return ConversationHandler.END


# ═════════════════════════════════════════════════════════════════════════════
# Lệnh chung
# ═════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "bạn"
    await update.message.reply_text(
        f"👋 Xin chào, <b>{name}</b>!\n\n"
        "╔══════════════════════════════╗\n"
        "║  🖥️   WINDOWS AI STV BOT      ║\n"
        "╚══════════════════════════════╝\n\n"
        "Tạo máy ảo Windows 11 miễn phí\n"
        "thông qua GitHub Actions + Tailscale.\n\n"
        "━━━━━━ 📋  LỆNH CÓ SẴN ━━━━━━\n\n"
        "🚀 /create     — Tạo máy Windows mới\n"
        "📊 /check      — Xem thông tin & thời gian\n"
        "🔌 /connect    — Kết nối máy ảo từ xa\n"
        "❓ /help       — Hướng dẫn chi tiết\n"
        "❌ /cancel     — Hủy thao tác\n\n"
        "━━━━━━ 🔑  CẦN CHUẨN BỊ ━━━━━━\n\n"
        "1️⃣  GitHub Token (quyền repo + workflow)\n"
        "2️⃣  Tailscale Auth Key (tskey-auth-...)\n"
        "3️⃣  Tailscale API Key (tskey-api-...)\n\n"
        "━━━━━━ ⏱️  THỜI GIAN ━━━━━━\n\n"
        "Từ <b>1 giờ</b> đến <b>3 giờ</b>\n"
        "⚠️ Mỗi người chỉ tạo được <b>1 máy</b>\n\n"
        "🚀 Gõ /create để bắt đầu!",
        parse_mode='HTML')


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data.pop(update.effective_user.id, None)
    await update.message.reply_text(
        "❌ Đã hủy thao tác hiện tại.\n\n"
        "Dùng /create để bắt đầu lại.")
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "╔══════════════════════════════╗\n"
        "║  📚  HƯỚNG DẪN SỬ DỤNG       ║\n"
        "╚══════════════════════════════╝\n\n"
        "━━━━━━ 🛠️  CÁC LỆNH ━━━━━━\n\n"
        "🚀 /create   — Tạo máy Windows mới\n"
        "📊 /check    — Xem thông tin máy hiện tại\n"
        "🔌 /connect  — Kết nối & chạy bot trên máy khác\n"
        "❌ /cancel   — Hủy thao tác đang thực hiện\n\n"
        "━━━━━━ 🔑  CÁCH LẤY KEY ━━━━━━\n\n"
        "GitHub Token:\n"
        "→ github.com/settings/tokens\n"
        "→ Cấp quyền: repo + workflow\n\n"
        "Tailscale Auth Key:\n"
        "→ login.tailscale.com/admin/settings/keys\n"
        "→ Chọn: Auth Keys\n\n"
        "Tailscale API Key:\n"
        "→ login.tailscale.com/admin/settings/keys\n"
        "→ Chọn: API access tokens\n\n"
        "━━━━━━ ℹ️  LƯU Ý ━━━━━━\n\n"
        "⏱️  Thời gian: 1 giờ — 3 giờ\n"
        "👤  Mỗi người chỉ tạo được <b>1 máy</b>\n"
        "🔒  Thông tin được lưu an toàn\n"
        "🗑️  Repo tự động xóa sau khi hết hạn",
        parse_mode='HTML')


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=True)


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation: tạo RDP
    rdp_conv = ConversationHandler(
        entry_points=[CommandHandler('create', create_command)],
        states={
            GITHUB_TOKEN_STATE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_github_token)],
            TAILSCALE_KEY_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tailscale_key)],
            TAILSCALE_API_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tailscale_api_key)],
            DURATION_STATE: [
                CallbackQueryHandler(duration_callback, pattern='^dur_'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_duration_text),
            ],
            CONFIRM_STATE: [CallbackQueryHandler(button_callback)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_message=False
    )

    # Conversation: kết nối máy từ xa
    connect_conv = ConversationHandler(
        entry_points=[CommandHandler('connect', connect_command)],
        states={
            REMOTE_IP_STATE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_remote_ip)],
            REMOTE_USER_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_remote_user)],
            REMOTE_PASS_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_remote_pass)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_message=False
    )

    application.add_handler(CommandHandler('start',  start))
    application.add_handler(CommandHandler('help',   help_command))
    application.add_handler(CommandHandler('check',  check_command))
    application.add_handler(CommandHandler('cancel', cancel_command))
    application.add_handler(rdp_conv)
    application.add_handler(connect_conv)
    application.add_error_handler(error_handler)

    print("🤖 Bot đang chạy...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()


