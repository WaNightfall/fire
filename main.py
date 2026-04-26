"""
main.py — 基拉韦厄火山喷发预测系统主编排器
运行方式：python main.py
由 scheduler.py 每日 08:00 HST 自动调用。
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 项目模块
from fetch_data import fetch_all, load_raw_cache, FetchError
from predict import run_prediction
from render import render_dashboard

# ── 路径 ──────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
LOG_FILE   = DATA_DIR / "update.log"

HST = timezone(timedelta(hours=-10))


# ── 日志初始化 ────────────────────────────────────────────────────────────────
def setup_logging():
    DATA_DIR.mkdir(exist_ok=True)
    fmt = "%(asctime)s [%(levelname)-8s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ── 离线缓存降级 ──────────────────────────────────────────────────────────────
def load_cache_fallback():
    """
    网络失败时强制读取本地缓存（忽略过期）。
    返回 (eq_data, status_data, alerts_data) 或 None（无缓存时）。
    """
    log = logging.getLogger(__name__)
    log.warning("Attempting offline cache fallback...")

    eq_data     = load_raw_cache("earthquakes")
    status_data = load_raw_cache("volcano_status")
    alerts_data = load_raw_cache("alerts")

    if eq_data is None and status_data is None:
        return None, None, None

    if eq_data is None:
        eq_data = {"type": "FeatureCollection", "features": []}
    if status_data is None:
        status_data = {"alert_level": "NORMAL", "aviation_color": "GREEN"}
    if alerts_data is None:
        alerts_data = []

    log.warning("Using stale cached data (network unavailable)")
    return eq_data, status_data, alerts_data


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    setup_logging()
    log = logging.getLogger(__name__)

    start_ts = datetime.now(HST)
    log.info("=" * 60)
    log.info(f"Kīlauea Watch — Run started at {start_ts.strftime('%Y-%m-%d %H:%M HST')}")
    log.info("=" * 60)

    # ── Step 1：获取数据 ──────────────────────────────────────────────────────
    log.info("[1/3] Fetching USGS data...")
    eq_data, status_data, alerts_data = fetch_all()

    # 若关键数据均为空，尝试离线缓存
    if not eq_data.get("features") and not status_data:
        eq_data, status_data, alerts_data = load_cache_fallback()
        if eq_data is None:
            log.critical("No data available (network failed, no cache). Aborting.")
            sys.exit(1)

    log.info(
        f"  [OK] Earthquakes: {len(eq_data.get('features', []))} events | "
        f"Alert: {status_data.get('alert_level','?')} | "
        f"Alerts: {len(alerts_data)} notices"
    )

    # ── Step 2：运行预测模型 ──────────────────────────────────────────────────
    log.info("[2/3] Running prediction model...")
    try:
        result = run_prediction(eq_data, status_data, alerts_data)
    except Exception as e:
        log.error(f"Prediction failed: {e}", exc_info=True)
        sys.exit(1)

    log.info(
        f"  [OK] Today P={result['current_probability']:.1%} | "
        f"30d peak={result['peak_prob_30d']:.1%} | "
        f"Trend={result['trend_direction']}"
    )

    # ── Step 3：渲染仪表板 ────────────────────────────────────────────────────
    log.info("[3/3] Rendering dashboard...")
    output_path = OUTPUT_DIR / "dashboard.html"
    try:
        render_dashboard(result, output_path)
    except Exception as e:
        log.error(f"Dashboard render failed: {e}", exc_info=True)
        # 写出最小错误页，避免显示旧数据
        _write_error_page(output_path, str(e))
        sys.exit(1)

    elapsed = (datetime.now(HST) - start_ts).total_seconds()
    log.info(f"  [OK] Dashboard: {output_path}")
    log.info(f"Done in {elapsed:.1f}s — open output/dashboard.html in your browser.")
    log.info("=" * 60)


def _write_error_page(path: Path, error_msg: str):
    """渲染失败时写出简单错误页面。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"<!DOCTYPE html><html><head><meta charset='UTF-8'><title>错误</title></head>"
        f"<body style='background:#0a0e1a;color:#e2e8f0;font-family:sans-serif;padding:40px'>"
        f"<h2>⚠ 仪表板渲染失败</h2><pre style='color:#f87171'>{error_msg}</pre>"
        f"<p>请检查 data/update.log 获取详情。</p></body></html>",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
