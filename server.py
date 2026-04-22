"""
server.py — 基拉韦厄日报本地服务器
提供静态文件服务 + /api/refresh 实时 USGS 数据更新接口。

运行方式：
    E:/miniconda1/python.exe server.py
访问：
    http://localhost:8080
"""
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

# 引入火山预测模块
PREDICT_DIR = Path(__file__).parent.parent / "火山预测"
sys.path.insert(0, str(PREDICT_DIR))
from fetch_data import fetch_all
from predict import run_prediction

BASE = Path(__file__).parent
DATA_JSON = BASE / "data.json"
HST = timezone(timedelta(hours=-10))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.route("/<path:filename>")
def static_file(filename):
    return send_from_directory(BASE, filename)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    try:
        log.info("刷新请求 — 正在拉取 USGS 数据...")
        eq_data, status_data, alerts_data = fetch_all()
        result = run_prediction(eq_data, status_data, alerts_data)

        current = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        _patch(current, result, status_data)

        DATA_JSON.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info(
            f"data.json 已更新 — 预警: {result['alert_level']}, "
            f"今日概率: {result['current_probability']:.0%}, "
            f"峰值概率: {result['peak_prob_30d']:.0%}"
        )
        return jsonify(current)

    except Exception as e:
        log.exception("刷新失败")
        return jsonify({"error": str(e)}), 500


def _patch(data: dict, result: dict, status_data: dict = None) -> None:
    """只更新可由 USGS API 可靠获取的字段，人工维护字段（chart、metrics、窗口等）保持不变。"""
    now = datetime.now(HST)
    data["report_date"]   = now.strftime("%Y-%m-%d")
    data["data_source"]   = f"USGS HVO · {result['generated_at']}"
    data["alert_level"]   = result["alert_level"]
    data["aviation_code"] = result["aviation_color"]
    last_eruption = datetime(2026, 4, 9, tzinfo=timezone.utc)
    data["stats"]["days_since_last"] = (datetime.now(timezone.utc) - last_eruption).days
    # 自动更新原始日报链接
    if status_data and status_data.get("notice_url"):
        data["source_url"] = status_data["notice_url"]


if __name__ == "__main__":
    print("=" * 50)
    print("  Kīlauea Dashboard → http://localhost:8080")
    print("  按 Ctrl+C 停止服务器")
    print("=" * 50)
    app.run(host="0.0.0.0", port=8080, debug=False)
