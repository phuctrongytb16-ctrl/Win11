import os
import zipfile
import io
import requests
import base64
import time
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           filters, ContextTypes, ConversationHandler,
                           CallbackQueryHandler)
import asyncio
from nacl import encoding, public
import secrets
import string

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

GITHUB_TOKEN_STATE, TAILSCALE_KEY_STATE, TAILSCALE_API_STATE, DURATION_STATE, CONFIRM_STATE = range(5)

# ── Lưu trạng thái người dùng ────────────────────────────────────────────────
user_data = {}          # Dữ liệu tạm trong conversation
active_sessions = {}    # {user_id: {'expire_at': timestamp, 'duration_display': str, 'run_id': int, ...}}

BOT_TOKEN = os.environ.get("BOT_TOKEN", "7000771103:AAGttf2jhIYuaT5063iabVwZsA4isgE-LLw")
ADMIN_ID = 5738766741   # Admin được phép dùng 15p - 6h

# ── Giới hạn thời gian ───────────────────────────────────────────────────────
NORMAL_MIN = 60    # phút
NORMAL_MAX = 180   # phút
ADMIN_MIN  = 15    # phút
ADMIN_MAX  = 360   # phút


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def generate_password(length=14) -> str:
    """Password chỉ gồm chữ hoa, chữ thường, số — không ký tự đặc biệt."""
    upper  = string.ascii_uppercase
    lower  = string.ascii_lowercase
    digits = string.digits
    # Đảm bảo có ít nhất 2 ký tự mỗi loại
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
    """Sinh username random dạng UserXXXXXX (chữ + số, bắt đầu bằng chữ)."""
    prefix = "User"
    suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return prefix + suffix


def encrypt_secret(public_key: str, secret_value: str) -> str:
    public_key_obj = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key_obj)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def parse_duration(text: str, user_id: int = 0) -> Optional[tuple]:
    """
    Parse chuỗi thời gian: 1h, 2h30p, 1h26p, 90p, 15p, 6h, v.v.
    Admin (ADMIN_ID): 15p – 6h
    Người thường     : 1h  – 3h
    Trả về (total_minutes, display_string) hoặc None nếu không hợp lệ.
    """
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
    """Chuyển số giây còn lại thành chuỗi dễ đọc."""
    if seconds <= 0:
        return "Đã hết hạn"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}p {s}s"
    elif m > 0:
        return f"{m}p {s}s"
    else:
        return f"{s}s"


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


def get_run_by_id(token: str, username: str, repo: str, run_id: int) -> Optional[dict]:
    r = requests.get(
        f'https://api.github.com/repos/{username}/{repo}/actions/runs/{run_id}',
        headers=gh_headers(token), timeout=10)
    return r.json() if r.status_code == 200 else None


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
            await bot.send_message(chat_id=user_id,
                                   text="🗑️ Repo GitHub đã được xóa tự động.")
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


# ─────────────────────────────────────────────────────────────────────────────
# Background task: tạo RDP + theo dõi hết hạn
# ─────────────────────────────────────────────────────────────────────────────

async def create_rdp_background(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 user_id: int):
    loop = asyncio.get_running_loop()
    username     = None
    repo_name    = None
    github_token = None
    run_id       = None

    try:
        github_token     = user_data[user_id]['github_token']
        tailscale_key    = user_data[user_id]['tailscale_key']
        tailscale_api_key = user_data[user_id].get('tailscale_api_key')
        duration_minutes = user_data[user_id].get('duration_minutes', 60)
        duration_display = user_data[user_id].get('duration_display', '1h')
        del user_data[user_id]

        random_password  = generate_password()
        rdp_username     = generate_username()
        workflow_content = create_workflow_content(random_password, duration_minutes, rdp_username)
        repo_name        = f"rdp-{user_id}-{int(time.time())}"

        username = await setup_github(github_token, repo_name, workflow_content, tailscale_key)
        if not username:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ Không thể tạo repo! Kiểm tra GitHub Token có đủ quyền `repo` và `workflow` không.")
            active_sessions.pop(user_id, None)
            return

        repo_url   = f"https://github.com/{username}/{repo_name}"
        start_time = time.time()

        # Lưu session NGAY sau khi workflow kích hoạt
        active_sessions[user_id] = {
            'expire_at':       start_time + duration_minutes * 60,
            'start_at':        start_time,
            'duration_minutes': duration_minutes,
            'duration_display': duration_display,
            'github_token':    github_token,
            'username':        username,
            'repo_name':       repo_name,
            'run_id':          None,
        }

        await context.bot.send_message(
            chat_id=user_id,
            text=(f"✅ *Workflow đã kích hoạt!*\n\n"
                  f"⏳ Đang chờ kết nối...\n"
                  f"⏱️ Thời gian thuê: *{duration_display}*"),
            parse_mode='Markdown', disable_web_page_preview=True)

        rdp_ip        = None
        last_status   = None
        tailscale_done = False

        # ── Phase 1: Chờ step Connect Tailscale xong ──────────
        for attempt in range(90):
            await asyncio.sleep(10)
            try:
                if run_id is None:
                    run = await loop.run_in_executor(None, lambda:
                        get_latest_run(github_token, username, repo_name))
                    if run:
                        run_id = run['id']
                        active_sessions[user_id]['run_id'] = run_id
                        logger.info(f"run_id={run_id}")
                    continue

                jobs = await loop.run_in_executor(None, lambda:
                    get_jobs(github_token, username, repo_name, run_id))
                if not jobs:
                    continue

                job_status = jobs[0].get('status')
                if job_status != last_status:
                    last_status = job_status
                    if job_status == 'in_progress':
                        await context.bot.send_message(chat_id=user_id,
                                                        text="⚙️ Setup Windows 11...")

                if (job_status == 'completed'
                        and jobs[0].get('conclusion') not in ['success', None]):
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"❌ Workflow thất bại!\n{repo_url}/actions/runs/{run_id}")
                    active_sessions.pop(user_id, None)
                    return

                if tailscale_step_done(jobs):
                    tailscale_done = True
                    break

            except Exception as e:
                logger.error(f"Poll phase1 #{attempt}: {e}")

        # ── Phase 2: Lấy IP ──────────────────────────────────
        if tailscale_done:
            await context.bot.send_message(chat_id=user_id, text="🖥️ Setup app Windows...")
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

        # ── Gửi thông tin login ──────────────────────────────
        if rdp_ip:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 <b>WINDOWS AI STV SẴN SÀNG!</b>\n\n"
                    f"🖥️ Máy chủ : <b>AI STV</b>\n"
                    f"👤 Username : <code>{rdp_username}</code>\n"
                    f"🔑 Password : <code>{random_password}</code>\n"
                    f"🌐 IP       : <code>{rdp_ip}</code>\n\n"
                    "📋 <b>Kết nối:</b>\n"
                    "1. Cài Tailscale: https://tailscale.com/download\n"
                    "2. Đăng nhập cùng tài khoản Tailscale\n"
                    "3. Bật kết nối và vào Windows app\n\n"
                    f"⏱️ Thời gian: {duration_display} ({duration_minutes} phút)\n"
                    f"⚠️ Máy ảo tự tắt sau {duration_display}\n"
                    f"💡 Dùng /check để xem thời gian còn lại."
                ),
                parse_mode='HTML',
                disable_web_page_preview=True)
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=("⚠️ *Không tự lấy được IP.*\n\n"
                      "Xem IP tại: https://login.tailscale.com/admin/machines\n"
                      "💡 Dùng /check để xem thời gian còn lại."),
                parse_mode='Markdown', disable_web_page_preview=True)

        # ── Xóa workflow file ─────────────────────────────────
        deleted = await loop.run_in_executor(None, lambda:
            delete_workflow_file(github_token, username, repo_name))
        if deleted:
            await context.bot.send_message(chat_id=user_id, text="Hoàn thành\n Chúc bạn sử dụng dịch vụ vui vẻ.")

        # ── Phase 3: Chờ hết hạn rồi thông báo & xóa repo ────
        expire_at = active_sessions[user_id]['expire_at']
        wait_secs = max(expire_at - time.time(), 0)
        logger.info(f"Chờ {wait_secs:.0f}s trước khi thông báo hết hạn...")
        await asyncio.sleep(wait_secs)

        # Thông báo hết hạn
        await context.bot.send_message(
            chat_id=user_id,
            text=(f"⏰ *Windows của bạn đã hết hạn!*\n\n"
                  f"Thời gian thuê *{duration_display}* đã kết thúc.\n"
                  f"Máy ảo sẽ tự tắt. Gõ /create để tạo máy mới."),
            parse_mode='Markdown')

        # Xóa session
        active_sessions.pop(user_id, None)

        # Chờ workflow finish rồi xóa repo
        max_polls = 15 * 6  # thêm tối đa 15 phút buffer
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
            text=f"❌ Lỗi: {str(e)}\nGõ /create để thử lại.")
        active_sessions.pop(user_id, None)
        if username and repo_name and github_token:
            loop2 = asyncio.get_running_loop()
            await do_delete_repo(loop2, github_token, username, repo_name,
                                  context.bot, user_id)


# ─────────────────────────────────────────────────────────────────────────────
# /check — Kiểm tra thời gian còn lại
# ─────────────────────────────────────────────────────────────────────────────

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = active_sessions.get(user_id)

    if not session:
        await update.message.reply_text(
            "ℹ️ Bạn chưa có máy Windows nào đang chạy.\n"
            "Gõ /create để tạo máy mới.")
        return

    now        = time.time()
    expire_at  = session['expire_at']
    start_at   = session['start_at']
    remaining  = expire_at - now
    elapsed    = now - start_at
    total_secs = session['duration_minutes'] * 60

    if remaining <= 0:
        active_sessions.pop(user_id, None)
        await update.message.reply_text(
            "⏰ *Windows của bạn đã hết hạn!*\n"
            "Gõ /create để tạo máy mới.",
            parse_mode='Markdown')
        return

    # Tính % đã dùng
    percent_used = min(int(elapsed / total_secs * 100), 100)
    bar_filled   = percent_used // 10
    bar          = "█" * bar_filled + "░" * (10 - bar_filled)

    await update.message.reply_text(
        f"🖥️ *Trạng thái Windows của bạn:*\n\n"
        f"⏱️ Thời gian thuê: *{session['duration_display']}*\n"
        f"✅ Đã dùng: `{format_remaining(elapsed)}`\n"
        f"⏳ Còn lại: *{format_remaining(remaining)}*\n\n"
        f"`[{bar}] {percent_used}%`\n\n"
        f"⚠️ Máy sẽ tự tắt khi hết thời gian.",
        parse_mode='Markdown')


# ─────────────────────────────────────────────────────────────────────────────
# Conversation handlers
# ─────────────────────────────────────────────────────────────────────────────

async def create_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Kiểm tra session đang chạy
    session = active_sessions.get(user_id)
    if session:
        remaining = session['expire_at'] - time.time()
        if remaining > 0:
            await update.message.reply_text(
                f"⚠️ *Bạn đang có máy Windows đang chạy!*\n\n"
                f"⏳ Thời gian còn lại: *{format_remaining(remaining)}*\n\n"
                f"Chỉ được tạo 1 máy tại một thời điểm.\n"
                f"Dùng /check để kiểm tra chi tiết.",
                parse_mode='Markdown')
            return ConversationHandler.END
        else:
            # Session hết hạn, dọn sạch
            active_sessions.pop(user_id, None)

    user_data[user_id] = {}
    await update.message.reply_text(
        "🔑 *Bước 1/4* — Gửi GitHub Personal Access Token:\n\nCần quyền: `repo` và `workflow`",
        parse_mode='Markdown')
    return GITHUB_TOKEN_STATE


async def get_github_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    token   = update.message.text.strip()
    if len(token) < 10:
        await update.message.reply_text("❌ Token không hợp lệ!")
        return ConversationHandler.END
    user_data[user_id]['github_token'] = token
    await update.message.reply_text(
        "✅ Đã nhận!\n\n"
        "🔑 *Bước 2/4* — Gửi Tailscale Auth Key:\n"
        "_(bắt đầu bằng `tskey-auth-...`)_\n\n"
        "Tạo tại: https://login.tailscale.com/admin/settings/keys",
        parse_mode='Markdown')
    return TAILSCALE_KEY_STATE


async def get_tailscale_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    key     = update.message.text.strip()
    if not key.startswith('tskey-') or len(key) < 40:
        await update.message.reply_text("❌ Auth key không hợp lệ! Gửi /create để thử lại.")
        return ConversationHandler.END
    user_data[user_id]['tailscale_key'] = key
    await update.message.reply_text(
        "✅ Đã nhận!\n\n"
        "🔑 *Bước 3/4* — Gửi Tailscale API Key:\n"
        "_(bắt đầu bằng `tskey-api-...`)_\n\n"
        "Tạo tại: https://login.tailscale.com/admin/settings/keys\n"
        "_(Chọn loại *API access token*)_",
        parse_mode='Markdown')
    return TAILSCALE_API_STATE


async def get_tailscale_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    api_key = update.message.text.strip()
    if not api_key.startswith('tskey-') or len(api_key) < 40:
        await update.message.reply_text("❌ API key không hợp lệ! Gửi /create để thử lại.")
        return ConversationHandler.END
    user_data[user_id]['tailscale_api_key'] = api_key

    is_admin = (user_id == ADMIN_ID)

    if is_admin:
        # Admin có thêm nút 15p, 30p, 4h, 5h, 6h
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
            [
                InlineKeyboardButton("⌨️ Tự nhập", callback_data='dur_custom'),
            ]
        ]
        note = "🔑 *[ADMIN]* Tối thiểu: *15p* | Tối đa: *6h*"
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
        note = "🕐 Tối thiểu: *1 giờ* | Tối đa: *3 giờ*"

    await update.message.reply_text(
        f"✅ Đã nhận!\n\n"
        f"⏱️ *Bước 4/4* — Chọn thời gian sử dụng máy ảo:\n\n"
        f"{note}\n\n"
        f"Chọn nhanh hoặc nhấn *Tự nhập* để nhập thủ công",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard))
    return DURATION_STATE


async def duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_ID)

    if query.data == 'dur_custom':
        if is_admin:
            limit_text = "⚠️ Giới hạn: từ *15p* đến *6h*"
        else:
            limit_text = "⚠️ Giới hạn: từ *1h* đến *3h*"
        await query.edit_message_text(
            f"⌨️ Nhập thời gian bạn muốn:\n\n"
            f"📝 *Định dạng:*\n"
            f"• `1h` = 1 giờ\n"
            f"• `2h30p` = 2 giờ 30 phút\n"
            f"• `1h26p` = 1 giờ 26 phút\n"
            f"• `90p` = 90 phút\n\n"
            f"{limit_text}",
            parse_mode='Markdown')
        return DURATION_STATE

    minutes = int(query.data.replace('dur_', ''))
    h, m    = divmod(minutes, 60)
    raw_str = f"{h}h{m}p" if m else f"{h}h" if h else f"{minutes}p"
    result  = parse_duration(raw_str, user_id)
    if not result:
        await query.edit_message_text("❌ Lỗi thời gian, thử lại /create")
        return ConversationHandler.END

    total_minutes, display = result
    user_data[user_id]['duration_minutes'] = total_minutes
    user_data[user_id]['duration_display']  = display
    return await show_confirm(query, user_id, is_query=True)


async def get_duration_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    result  = parse_duration(text, user_id)

    is_admin = (user_id == ADMIN_ID)
    if is_admin:
        limit_text = "⚠️ Giới hạn: từ *15p* đến *6h* (15 - 360 phút)"
    else:
        limit_text = "⚠️ Giới hạn: từ *1h* đến *3h* (60 - 180 phút)"

    if not result:
        await update.message.reply_text(
            f"❌ Thời gian không hợp lệ!\n\n"
            f"{limit_text}\n\n"
            f"📝 *Ví dụ đúng:*\n"
            f"• `1h` • `2h30p` • `1h26p` • `90p`\n\n"
            f"Nhập lại:",
            parse_mode='Markdown')
        return DURATION_STATE

    total_minutes, display = result
    user_data[user_id]['duration_minutes'] = total_minutes
    user_data[user_id]['duration_display']  = display
    return await show_confirm(update, user_id, is_query=False)


async def show_confirm(update_or_query, user_id: int, is_query: bool):
    data     = user_data[user_id]
    keyboard = [[
        InlineKeyboardButton("✅ Bắt đầu tạo", callback_data='start_create'),
        InlineKeyboardButton("❌ Hủy",          callback_data='cancel')
    ]]
    text = (
        f"✅ *Sẵn sàng tạo WINDOWS!*\n\n"
        f"• GitHub Token: `{data['github_token'][:10]}...`\n"
        f"• Tailscale Auth Key: `{data['tailscale_key'][:15]}...`\n"
        f"• Tailscale API Key: `{data['tailscale_api_key'][:15]}...`\n"
        f"• ⏱️ Thời gian: *{data['duration_display']}* ({data['duration_minutes']} phút)\n\n"
        f"Nhấn *Bắt đầu tạo* để tiến hành!"
    )
    markup = InlineKeyboardMarkup(keyboard)
    if is_query:
        await update_or_query.edit_message_text(text, parse_mode='Markdown', reply_markup=markup)
    else:
        await update_or_query.message.reply_text(text, parse_mode='Markdown', reply_markup=markup)
    return CONFIRM_STATE


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == 'cancel':
        user_data.pop(user_id, None)
        await query.edit_message_text("❌ Đã hủy.")
        return ConversationHandler.END

    if query.data.startswith('dur_'):
        return await duration_callback(update, context)

    # start_create
    await query.edit_message_text("🔄 *Đang tạo Windows AI STV...* ⏳", parse_mode='Markdown')
    asyncio.create_task(create_rdp_background(update, context, user_id))
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# Các lệnh chung
# ─────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *WINDOWS AI STV*\n\n"
        "Tạo miễn phí Windows 11\n\n"
        "Gõ /create để bắt đầu!\n\n"
        "📋 *Cần chuẩn bị:*\n"
        "1. GitHub Token (Cấp tất cả quyền)\n"
        "2. Tailscale Auth Key (`tskey-auth-...`)\n"
        "3. Tailscale API Key (`tskey-api-...`)\n\n"
        "⏱️ *Thời gian:* từ 1h đến 3h\n"
        "💡 /check — Xem thời gian còn lại",
        parse_mode='Markdown')


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data.pop(update.effective_user.id, None)
    await update.message.reply_text("❌ Đã hủy. Gõ /create để bắt đầu lại.")
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *HƯỚNG DẪN*\n\n"
        "/create — Tạo Windows mới\n"
        "/check  — Xem thời gian còn lại\n"
        "/cancel — Hủy\n\n"
        "*3 key cần có:*\n"
        "• GitHub Token: github.com/settings/tokens\n"
        "• Tailscale Auth Key: login.tailscale.com/admin/settings/keys\n"
        "• Tailscale API Key: login.tailscale.com/admin/settings/keys\n\n"
        "⏱️ *Thời gian:* 1h – 3h (tùy chọn khi tạo)\n"
        "⚠️ Mỗi người chỉ được tạo *1 máy* tại một thời điểm.",
        parse_mode='Markdown')


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler('create', create_command)],
        states={
            GITHUB_TOKEN_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_github_token)],
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
    application.add_handler(CommandHandler('start',  start))
    application.add_handler(CommandHandler('help',   help_command))
    application.add_handler(CommandHandler('check',  check_command))
    application.add_handler(CommandHandler('cancel', cancel_command))
    application.add_handler(conv)
    application.add_error_handler(error_handler)
    print("🤖 Bot đang chạy...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    try:
        import telegram
    except ImportError:
        os.system("pip install python-telegram-bot pynacl requests")
    main()
