"""Start the summons system on this computer without Docker.

    Windows: double-click start-local.bat      macOS/Linux: ./start-local.sh

First run: creates .venv, installs packages, creates data/ (SQLite database,
secret key) and an administrator account, then opens the browser.
Later runs: just starts the server (packages are re-installed only when
requirements.txt changes).

Options:
    demo          also load the fictional sample data and demo users (try-out only)
    --port 8000   port to listen on (default 8000, or PORT in local.env)

Optional local.env (KEY=VALUE per line) overrides settings, e.g. to use
PostgreSQL: POSTGRES_HOST=localhost, POSTGRES_DB=..., POSTGRES_USER=..., POSTGRES_PASSWORD=...
Uses only the Python standard library so it can run before anything is installed.
"""
from __future__ import annotations

import hashlib
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
DATA = ROOT / "data"
IS_WIN = os.name == "nt"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def say(msg=""):
    print(msg, flush=True)


def fail(msg):
    say("")
    say("✗ " + msg)
    sys.exit(1)


def run(args, env=None, check=True, capture=False):
    result = subprocess.run([str(a) for a in args], cwd=ROOT, env=env, text=True,
                            capture_output=capture, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        if capture:
            say(result.stdout)
            say(result.stderr)
        fail(f"คำสั่งล้มเหลว: {' '.join(str(a) for a in args[:4])} ...")
    return result


def ensure_python():
    if sys.version_info < (3, 11):
        fail(f"ต้องใช้ Python 3.11 ขึ้นไป (เครื่องนี้เป็น {platform.python_version()}) "
             "ดาวน์โหลดได้ที่ https://www.python.org/downloads/")


def ensure_venv():
    if not VENV_PY.exists():
        say("• สร้าง virtual environment (.venv) ...")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    req = (ROOT / "requirements.txt").read_bytes() + (ROOT / "requirements-local.txt").read_bytes()
    digest = hashlib.sha256(req + sys.version.encode()).hexdigest()
    marker = VENV / ".requirements.sha256"
    if marker.exists() and marker.read_text().strip() == digest:
        return
    say("• ติดตั้งแพ็กเกจ (ครั้งแรกใช้เวลาสักครู่) ...")
    run([VENV_PY, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"], check=False)
    run([VENV_PY, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", ROOT / "requirements-local.txt"])
    marker.write_text(digest)


def read_local_env() -> dict:
    path = ROOT / "local.env"
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def secret_key() -> str:
    path = DATA / "secret_key.txt"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(50), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def build_env(port: int) -> dict:
    env = dict(os.environ)
    env.pop("POSTGRES_HOST", None)  # SQLite unless local.env says otherwise
    env.update({
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_SECRET_KEY": secret_key(),
        "DJANGO_DEBUG": "0",
        "DJANGO_ALLOWED_HOSTS": "*",
        "DJANGO_CSRF_TRUSTED_ORIGINS": f"http://localhost:{port},http://127.0.0.1:{port}",
        "BACKGROUND_TASKS": "thread",
        "MEDIA_ROOT": str(ROOT / "media"),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(read_local_env())
    return env


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        if shutil.which(name):
            return shutil.which(name)
    for candidate in (r"C:\Program Files\LibreOffice\program\soffice.com",
                      r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
                      "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(candidate).exists():
            return candidate
    return None


def check_libreoffice():
    if find_soffice():
        return
    say("! ไม่พบ LibreOffice: ระบบใช้งานได้และส่งออก Word ได้ แต่สร้าง PDF ไม่ได้")
    marker = DATA / ".libreoffice-asked"
    if IS_WIN and shutil.which("winget") and not marker.exists() and sys.stdin.isatty():
        marker.write_text("asked")
        answer = input("  ติดตั้ง LibreOffice ให้ตอนนี้เลยไหม? (y/N): ").strip().lower()
        if answer in {"y", "yes", "ใช่"}:
            run(["winget", "install", "-e", "--id", "TheDocumentFoundation.LibreOffice",
                 "--accept-package-agreements", "--accept-source-agreements"], check=False)
            return
    say("  ติดตั้งเองได้จาก https://www.libreoffice.org/download/ แล้วเปิดระบบใหม่")


def check_fonts():
    if not IS_WIN:
        return
    folders = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
               Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    names = " ".join(p.name.lower() for f in folders if f.exists() for p in f.iterdir())
    if "sarabunit" not in names.replace(" ", ""):
        say("! ไม่พบฟอนต์ TH SarabunIT๙ ในเครื่อง: PDF/Word จะใช้ฟอนต์อื่นแทน "
            "(คลิกขวาไฟล์ฟอนต์ > Install for all users แล้วเปิดระบบใหม่)")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return ""


def main():
    ensure_python()
    args = sys.argv[1:]
    DATA.mkdir(exist_ok=True)
    (ROOT / "media").mkdir(exist_ok=True)
    local = read_local_env()
    port = int(local.get("PORT", "8000"))
    if "--port" in args:
        port = int(args[args.index("--port") + 1])

    say("ระบบออกหมายเรียกผู้กล่าวหา บก.สอท.4 — เริ่มทำงานบนเครื่องนี้")
    say("=" * 60)
    ensure_venv()
    env = build_env(port)
    manage = [VENV_PY, ROOT / "manage.py"]

    say("• เตรียมฐานข้อมูล ...")
    run(manage + ["migrate", "--noinput", "-v", "0"], env=env, capture=True)
    run(manage + ["collectstatic", "--noinput", "-v", "0"], env=env, capture=True)
    run(manage + ["init_system"], env=env, capture=True)
    run(manage + ["cleanup_media"], env=env, check=False, capture=True)
    pw_file = DATA / "admin-password.txt"
    created = run(manage + ["ensure_admin", "--password-file", pw_file], env=env, capture=True).stdout
    if "demo" in args:
        say("• โหลดข้อมูลตัวอย่าง (สมมติ) ...")
        out = run(manage + ["shell", "-c", "from apps.registry.models import Case; print(Case.objects.count())"],
                  env=env, capture=True).stdout.strip().splitlines()
        if out and out[-1] == "0":
            run(manage + ["load_demo"], env=env, capture=True)
            say("  ผู้ใช้ทดลอง: admin / head / clerk / inv  (รหัสผ่าน demo12345 สำหรับ head, clerk, inv)")
        else:
            say("  ข้าม: มีข้อมูลคดีอยู่แล้ว")
    check_libreoffice()
    check_fonts()

    if not port_free(port):
        if port_free(port + 1):
            port += 1
        else:
            fail(f"พอร์ต {port} ถูกใช้งานอยู่ (ระบบอาจเปิดอยู่แล้ว ลองเปิด http://localhost:{port})")
        env["DJANGO_CSRF_TRUSTED_ORIGINS"] = f"http://localhost:{port},http://127.0.0.1:{port}"

    url = f"http://localhost:{port}"
    say("=" * 60)
    if "CREATED" in created:
        _, user, password = created.strip().split()[-3:]
        say("บัญชีผู้ดูแลระบบ (สร้างครั้งแรก):")
        say(f"   ชื่อผู้ใช้: {user}    รหัสผ่าน: {password}")
        say(f"   (บันทึกไว้ที่ {pw_file} — เข้าระบบแล้วเปลี่ยนรหัสผ่าน และลบไฟล์นี้)")
    elif pw_file.exists():
        say(f"รหัสผ่านผู้ดูแลระบบครั้งแรกอยู่ในไฟล์ {pw_file}")
    say(f"เปิดใช้งานที่: {url}")
    ip = lan_ip()
    if ip:
        say(f"เครื่องอื่นในเครือข่ายภายใน: http://{ip}:{port}  (อาจต้องอนุญาตใน Windows Firewall)")
        env["DJANGO_CSRF_TRUSTED_ORIGINS"] += f",http://{ip}:{port}"
    say("ปิดระบบ: ปิดหน้าต่างนี้ หรือกด Ctrl+C")
    say("=" * 60)

    threading.Thread(target=lambda: (time.sleep(2.5), webbrowser.open(url)), daemon=True).start()
    try:
        subprocess.run([str(VENV_PY), "-m", "waitress", "--listen", f"0.0.0.0:{port}", "--threads", "8",
                        "--channel-timeout", "600", "config.wsgi:application"], cwd=ROOT, env=env)
    except KeyboardInterrupt:
        pass
    say("ปิดระบบแล้ว")


if __name__ == "__main__":
    main()
