"""
scheduler.py — 基拉韦厄火山预测系统每日自动更新守护进程
运行方式：python scheduler.py
每日 18:00 UTC（= 08:00 HST，夏威夷标准时间）触发 main.py。

Windows 任务计划集成：
  启动时自动生成 update.bat，并打印注册命令。
"""

import logging
import subprocess
import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import schedule
import time

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
MAIN_PY  = BASE_DIR / "main.py"
LOG_FILE = DATA_DIR / "update.log"
BAT_FILE = BASE_DIR / "update.bat"

HST = timezone(timedelta(hours=-10))
UTC = timezone.utc

# ── 日志 ──────────────────────────────────────────────────────────────────────
def setup_logging():
    DATA_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ── 生成 update.bat ───────────────────────────────────────────────────────────
def generate_bat():
    """生成可注册到 Windows 任务计划的批处理文件。"""
    python_exe = sys.executable
    bat_content = f"""@echo off
REM ============================================================
REM Kilauea Watch — 每日自动更新脚本
REM 触发时间：每日 18:00 UTC = 08:00 HST（夏威夷标准时间）
REM           中国标准时间 UTC+8：次日 02:00 CST
REM ============================================================
cd /d "{BASE_DIR}"
"{python_exe}" "{MAIN_PY}" >> "{LOG_FILE}" 2>&1
"""
    BAT_FILE.write_text(bat_content, encoding="gbk")
    return BAT_FILE


def print_task_scheduler_instructions():
    """打印 Windows 任务计划注册命令（需管理员权限）。"""
    bat = BAT_FILE.resolve()
    log = logging.getLogger(__name__)

    # 当前机器时区偏移
    local_offset = datetime.now().astimezone().utcoffset()
    local_hrs = int(local_offset.total_seconds() / 3600)
    # 18:00 UTC 对应的本地时间
    trigger_local = (18 + local_hrs) % 24
    trigger_day = "当天" if (18 + local_hrs) < 24 else "次日"

    log.info("")
    log.info("=" * 60)
    log.info("  Windows 任务计划注册命令（以管理员身份运行 CMD）：")
    log.info("")
    log.info(f'  schtasks /create /tn "KilaueaWatch" ^')
    log.info(f'    /tr "{bat}" ^')
    log.info(f'    /sc daily /st {trigger_local:02d}:00 /f')
    log.info("")
    log.info(f"  触发时间说明：")
    log.info(f"    · 18:00 UTC = 08:00 HST（夏威夷）")
    log.info(f"    · 您的本地时间 UTC{local_hrs:+d} = {trigger_day} {trigger_local:02d}:00")
    log.info("=" * 60)
    log.info("")


# ── 执行更新 ──────────────────────────────────────────────────────────────────
def run_update():
    log = logging.getLogger(__name__)
    now_hst = datetime.now(HST).strftime("%Y-%m-%d %H:%M HST")
    log.info(f"⏰ Scheduled trigger at {now_hst}")

    try:
        result = subprocess.run(
            [sys.executable, str(MAIN_PY)],
            cwd=str(BASE_DIR),
            timeout=300,     # 5 分钟超时
        )
        if result.returncode == 0:
            log.info("✅ Update completed successfully")
        else:
            log.error(f"❌ main.py exited with code {result.returncode}")
    except subprocess.TimeoutExpired:
        log.error("⏱ Update timed out after 5 minutes")
    except Exception as e:
        log.error(f"❌ Unexpected error: {e}", exc_info=True)


# ── 主循环 ────────────────────────────────────────────────────────────────────
def main():
    setup_logging()
    log = logging.getLogger(__name__)

    log.info("🌋 Kīlauea Watch Scheduler 启动")
    log.info(f"   Python: {sys.executable}")
    log.info(f"   脚本目录: {BASE_DIR}")

    # 生成 update.bat
    bat = generate_bat()
    log.info(f"   已生成: {bat}")
    print_task_scheduler_instructions()

    # 立即执行一次（确保初始数据就绪）
    log.info("▶ 立即执行首次更新...")
    run_update()

    # 设置每日 18:00 UTC 调度
    # （schedule 库使用本地时间，需转换）
    local_offset_hrs = int(datetime.now().astimezone().utcoffset().total_seconds() / 3600)
    trigger_local_hr = (18 + local_offset_hrs) % 24
    trigger_time_str = f"{trigger_local_hr:02d}:00"

    schedule.every().day.at(trigger_time_str).do(run_update)
    log.info(f"📅 已设置每日 {trigger_time_str}（本地时间）触发 = 18:00 UTC = 08:00 HST")
    log.info("   调度器运行中，按 Ctrl+C 停止...")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)   # 每 30 秒检查一次
    except KeyboardInterrupt:
        log.info("调度器已停止。")


if __name__ == "__main__":
    main()
