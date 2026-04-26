"""
predict.py — 基拉韦厄火山喷发概率预测模型 v2
以 HVO 官方窗口为中心，结合实时监测指标计算概率。
"""

import math
import re
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

KILAUEA_LAT = 19.421
KILAUEA_LON = -155.287

# 预警级别基础评分
ALERT_SCORES = {
    "NORMAL":   0.05,
    "ADVISORY": 0.20,
    "WATCH":    0.60,
    "WARNING":  0.90,
}


def strip_html(text: str) -> str:
    """去除 HTML 标签，保留纯文本。"""
    if not text:
        return ""
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', ' ', text)
    # 还原实体
    text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&#39;', "'").replace('&quot;', '"').replace('&amp;', '&')
    # 规范化空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_hvo_window(alerts: list) -> tuple:
    """
    从 HANS alerts 中解析 HVO 官方预测窗口。
    返回 (window_start: date, window_end: date) 或 (None, None)
    使用已获取的 alert["content"]，无需额外 HTTP 请求。
    """
    month_names = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12
    }

    for alert in alerts:
        content = alert.get("content", "")
        if not content:
            continue

        text = strip_html(content).lower()

        # 格式1: "between Monday, April 20 and Saturday, April 25"
        # 提取月份名 + 日期
        m = re.search(
            r"between\s+\w+,?\s+([a-z]+)\s+(\d{1,2})\s+and\s+\w+,?\s+([a-z]+)\s+(\d{1,2})",
            text
        )
        if m:
            month1_name, day1, month2_name, day2 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
            month1 = month_names.get(month1_name)
            month2 = month_names.get(month2_name)
            if month1 and month2:
                now = datetime.now(timezone(timedelta(hours=-10)))
                year = now.year
                try:
                    start = datetime(year, month1, day1).date()
                    end = datetime(year, month2, day2).date()
                    log.info(f"HVO window parsed: {start} to {end}")
                    return start, end
                except ValueError:
                    pass

        # 格式2: "between 4/20 and 4/25" 或 "episode 45 ... 4/20-4/25"
        patterns2 = [
            r"between\s+(\d{1,2})[/\-]\s*(\d{1,2})\s*[-–and]+\s*(\d{1,2})[/\-]\s*(\d{1,2})",
            r"episode\s+\d+.*?(\d{1,2})[/\-](\d{1,2})\s*[-–]\s*(\d{1,2})[/\-]\s*(\d{1,2})",
        ]
        for pattern in patterns2:
            m = re.search(pattern, text)
            if m:
                start_month, start_day = int(m.group(1)), int(m.group(2))
                end_month, end_day = int(m.group(3)), int(m.group(4))
                now = datetime.now(timezone(timedelta(hours=-10)))
                year = now.year
                if end_month < start_month:
                    year += 1
                try:
                    start = datetime(year, start_month, start_day).date()
                    end = datetime(year, end_month, end_day).date()
                    log.info(f"HVO window parsed: {start} to {end}")
                    return start, end
                except ValueError:
                    pass

    log.warning("No HVO window found in alerts")
    return None, None


def parse_visual_signal(alerts: list) -> dict:
    """
    从 alerts 解析摄像头/视觉信号强度。
    返回 {"level": "none"|"glow"|"flames"|"intense_flaming", "progress": 0-100}
    """
    all_text = " ".join(
        str(a.get("content", "") or a.get("title", "") or a.get("body", "")).lower()
        for a in alerts
    )

    if "intense flaming" in all_text or "prolonged periods of intense flaming" in all_text:
        return {"level": "intense_flaming", "progress": 90}
    elif "flames" in all_text or "flame" in all_text:
        return {"level": "flames", "progress": 75}
    elif "glow" in all_text or "incandescence" in all_text:
        return {"level": "glow", "progress": 50}
    else:
        return {"level": "none", "progress": 10}


def parse_tilt_status(alerts: list) -> dict:
    """
    从 alerts 解析倾斜仪状态。
    返回 {"rebound_μrad": float, "total_μrad": float, "progress": 0-100}
    """
    all_text = " ".join(
        str(a.get("content", "") or a.get("title", "") or a.get("body", "")).lower()
        for a in alerts
    )

    # 匹配 "X.X microradians" 或 "X.X μrad"
    rebound_m = re.findall(r"(\d+\.?\d*)\s*(?:μrad|microradians)", all_text)
    if rebound_m:
        rebound = float(rebound_m[0])
    else:
        rebound = None

    # 根据当前 episode 估算总偏转量
    # 第44次：17.6 μrad（HVO 官方数据）
    # 检测当前是第几次喷发
    total = 17.6  # 默认第44次
    if "episode 45" in all_text or "episode 45 will" in all_text:
        # 如果提到第45次，说明第44次已经结束，使用第45次参考值
        total = 20.0  # 估算值
    elif "episode 43" in all_text:
        total = 33.7

    if rebound is not None:
        progress = min(100, int(rebound / total * 100))
        return {"rebound_μrad": rebound, "total_μrad": total, "progress": progress}

    return {"rebound_μrad": 0, "total_μrad": total, "progress": 0}


def parse_daily_changes(alerts: list) -> dict:
    """
    从最新 HANS 通报正文提取今日关键变化，返回 change_alert dict。
    覆盖规则：只在能提取到至少1条有效信息时才更新。
    """
    if not alerts:
        return {"has_changes": False, "items": []}

    latest = alerts[0]
    content = latest.get("content", "")
    if not content:
        return {"has_changes": False, "items": []}

    raw = strip_html(content)
    text_low = raw.lower()
    items = []

    def first_sentence(pattern):
        """找含该 pattern 的第一个完整句子（>20字符）。"""
        for sent in re.split(r'(?<=[.!?])\s+', raw):
            if re.search(pattern, sent, re.IGNORECASE) and len(sent.strip()) > 20:
                return sent.strip()[:180]
        return ""

    # ── 1. 预警等级变化 ────────────────────────────────────
    alert_raise = re.search(
        r"raised?\s+from\s+([A-Z]+)[/\s]+([A-Z]+)\s+to\s+([A-Z]+)[/\s]+([A-Z]+)",
        raw, re.IGNORECASE
    )
    if alert_raise:
        frm = alert_raise.group(1).upper()
        to  = alert_raise.group(3).upper()
        items.append({
            "icon": "⚡",
            "highlight": f"预警等级升级：{frm} → {to}",
            "detail": first_sentence(r"raised.*from.*to|alert.*level.*raised")
                      or alert_raise.group(0)[:180]
        })

    # ── 2. 前驱活动 / 岩浆溢流 ────────────────────────────
    precursor_rules = [
        (r"precursory overflow|precursory activity",       "▲", "第45次前驱活动已开始"),
        (r"lava flow(?:ed|ing)? from.{0,30}vent",         "▲", "北喷口岩浆溢出"),
        (r"spatter.{0,40}(?:north|south)\s+vent|vent.{0,40}spatter", "▲", "喷口强溅射"),
        (r"dome fountain",                                 "▲", "出现小型熔岩喷泉"),
    ]
    for pattern, icon, label in precursor_rules:
        sent = first_sentence(pattern)
        if sent:
            items.append({"icon": icon, "highlight": label, "detail": sent})
            break

    # ── 3. 倾斜仪充能 ──────────────────────────────────────
    # 优先匹配 "approximately X microradians of inflationary tilt"
    tilt_m = re.search(
        r"((?:approximately\s+)?(\d+\.?\d*)\s*microradians?\s+of\s+inflation[^.]*\.)",
        raw, re.IGNORECASE
    )
    if not tilt_m:
        tilt_m = re.search(
            r"(UWD[^.]*(\d+\.?\d*)\s*microrad[^.]*\.)",
            raw, re.IGNORECASE
        )
    if tilt_m:
        val_m = re.search(r"(\d+\.?\d*)\s*microrad", tilt_m.group(1), re.IGNORECASE)
        val_str = f"{val_m.group(1)} μrad" if val_m else "更新"
        # 尝试计算充能百分比（与上次喷发总偏转对比）
        total_m = re.search(r"(\d+\.?\d*)\s*microradians?\s+of\s+deflation", raw, re.IGNORECASE)
        pct = ""
        if val_m and total_m:
            try:
                pct = f"（充能 {min(100, round(float(val_m.group(1)) / float(total_m.group(1)) * 100))}%）"
            except Exception:
                pass
        items.append({
            "icon": "▲",
            "highlight": f"倾斜充能：{val_str}{pct}",
            "detail": tilt_m.group(1).strip()[:180]
        })

    # ── 4. HVO预测窗口 + 最可能时间 ────────────────────────
    # 匹配 "between today, April 22, and Sunday, April 26"
    win_m = re.search(
        r"(episode\s+\d+\s+(?:lava\s+fountain[^.]*)?(?:will\s+)?(?:start|occur)[^.]*"
        r"between\s+[^.]*?[A-Za-z]+\s+\d+[^.]*\.)",
        raw, re.IGNORECASE
    )
    if not win_m:
        win_m = re.search(
            r"(between\s+\w+,?\s*[A-Za-z]+\s+\d+[^.]*and\s+\w+,?\s*[A-Za-z]+\s+\d+[^.]*\.)",
            raw, re.IGNORECASE
        )
    # 附加"最可能日期"
    likely_m = re.search(
        r"([A-Za-z]+\s+\d+\s+or\s+\d+\s+most\s+likely[^.]*\.)",
        raw, re.IGNORECASE
    )
    if win_m or likely_m:
        detail = (win_m.group(1) if win_m else "") + (" " + likely_m.group(1) if likely_m else "")
        items.append({"icon": "▲", "highlight": "HVO预测窗口更新", "detail": detail.strip()[:200]})

    # ── 5. 视觉辉光 / 火焰（仅在未被前驱条目覆盖时添加）───
    already_has_visual = any(
        kw in i["highlight"] for i in items
        for kw in ("溢出", "溅射", "前驱", "岩浆", "喷泉")
    )
    if not already_has_visual:
        cam_rules = [
            (r"intense flaming|prolonged.*flaming", "▲", "南喷口剧烈持续火焰"),
            (r"flaming|flame",                      "▲", "喷口火焰活动"),
            (r"glow.{0,60}both\s+vent|both\s+vent.{0,60}glow", "●", "双喷口持续辉光"),
            (r"incandescence|glow",                 "●", "喷口辉光"),
        ]
        for pattern, icon, label in cam_rules:
            sent = first_sentence(pattern)
            if sent:
                items.append({"icon": icon, "highlight": label, "detail": sent})
                break

    if not items:
        return {"has_changes": False, "items": []}

    return {"has_changes": True, "items": items[:5]}


def compute_current_probability(alert_level: str, visual: dict, tilt: dict,
                                 hvo_window: tuple) -> float:
    """
    综合计算当前喷发概率。
    基于：预警级别 + 视觉信号 + 倾斜状态 + HVO窗口距离
    """
    # 1. 预警级别基础分
    a_score = ALERT_SCORES.get(alert_level.upper(), 0.05)

    # 2. 视觉信号权重（最重要）
    visual_weight = {
        "intense_flaming": 0.85,
        "flames": 0.65,
        "glow": 0.40,
        "none": 0.15,
    }.get(visual["level"], 0.15)

    # 3. 倾斜充能进度
    tilt_progress = tilt.get("progress", 0) / 100.0

    # 4. HVO窗口因素：窗口越近概率越高
    if hvo_window[0] and hvo_window[1]:
        today = datetime.now(timezone(timedelta(hours=-10))).date()
        window_start, window_end = hvo_window
        days_to_window = (window_start - today).days

        if days_to_window < 0:
            # 窗口已开始
            if today <= window_end:
                window_factor = 1.0  # 窗口内，概率最高
            else:
                window_factor = 0.3  # 窗口已过
        elif days_to_window <= 3:
            window_factor = 0.95  # 窗口临近
        elif days_to_window <= 7:
            window_factor = 0.80  # 一周内
        else:
            window_factor = 0.60  # 超过一周
    else:
        window_factor = 0.40  # 无窗口信息

    # 综合计算
    # 视觉信号最重要(40%) + 预警级别(25%) + 倾斜充能(20%) + 窗口因素(15%)
    p = (0.40 * visual_weight +
         0.25 * a_score +
         0.20 * tilt_progress +
         0.15 * window_factor)

    return max(0.0, min(1.0, p))


def generate_forecast(current_prob: float, hvo_window: tuple,
                      alert_level: str) -> pd.DataFrame:
    """
    生成未来30天概率预测曲线。
    以 HVO 官方窗口为中心，峰值在窗口中点。
    窗口期概率 76-88%，窗口前从当前概率逐渐升至窗口值。
    """
    hst = timezone(timedelta(hours=-10))
    today = datetime.now(hst).date()

    if hvo_window[0] and hvo_window[1]:
        window_start, window_end = hvo_window
    else:
        window_start = today + timedelta(days=7)
        window_end = today + timedelta(days=14)

    window_center = window_start + (window_end - window_start) / 2
    window_width_days = (window_end - window_start).days

    dates = [today + timedelta(days=i) for i in range(30)]
    probabilities = []

    # 窗口期峰值概率
    window_peak = 0.88
    # 窗口起始概率
    window_start_prob = 0.76

    for d in dates:
        days_from_center = (d - window_center).days

        if d >= window_start and d <= window_end:
            # 窗口内：76% - 88% 区间
            if d == window_center:
                prob = window_peak
            elif d == window_start:
                prob = window_start_prob
            elif d == window_end:
                prob = 0.72
            else:
                # 窗口内其他天：正弦插值
                window_day_idx = (d - window_start).days
                window_total = (window_end - window_start).days
                if window_total > 0:
                    t = window_day_idx / window_total
                    prob = window_start_prob + (window_peak - window_start_prob) * math.sin(t * math.pi)
                else:
                    prob = window_peak
        elif d < window_start:
            # 窗口前：从当前概率逐渐升到窗口起始值
            days_before_window = (window_start - d).days
            # 越接近窗口，概率越高
            if days_before_window >= 7:
                prob = current_prob
            else:
                # 线性插值：从 current_prob 升到 window_start_prob
                t = 1 - (days_before_window / 7)
                prob = current_prob + (window_start_prob - current_prob) * t
        else:
            # 窗口后：高斯衰减
            sigma = max(window_width_days / 2, 3)
            gaussian = math.exp(-0.5 * (days_from_center / sigma) ** 2)
            prob = window_peak * 0.60 * gaussian

        # 置信度：窗口中心最高 82%，向两侧递减
        days_to_center = abs(days_from_center)
        base_conf = 82
        conf = max(10, base_conf - days_to_center * 2.5)

        probabilities.append({
            "date": d,
            "probability": max(0.0, min(1.0, prob)),
            "confidence": conf
        })

    df = pd.DataFrame(probabilities)
    return df


def run_prediction(earthquakes_geojson: dict, volcano_status: dict, alerts: list) -> dict:
    """
    主预测入口。
    """
    hst = timezone(timedelta(hours=-10))

    # 1. 解析 HVO 官方窗口
    hvo_window = parse_hvo_window(alerts)

    # 2. 解析监测指标
    visual = parse_visual_signal(alerts)
    tilt = parse_tilt_status(alerts)

    alert_level = volcano_status.get("alert_level", "NORMAL")

    # 3. 计算当前概率
    current_prob = compute_current_probability(
        alert_level, visual, tilt, hvo_window
    )

    # 4. 生成30天预测曲线
    forecast_df = generate_forecast(current_prob, hvo_window, alert_level)

    peak_prob = float(forecast_df["probability"].max())

    result = {
        "current_probability": current_prob,
        "alert_level": alert_level,
        "aviation_color": volcano_status.get("aviation_color", "GREEN"),
        "volcano_name": volcano_status.get("volcano_name", "Kīlauea"),
        "hvo_window": hvo_window,
        "peak_prob_30d": peak_prob,
        "forecast_df": forecast_df,
        "monitoring": {
            "visual": visual,
            "tilt": tilt,
        },
        "generated_at": datetime.now(hst).strftime("%Y-%m-%d %H:%M HST"),
    }

    log.info(
        f"Prediction: P={current_prob:.1%}, peak={peak_prob:.1%}, "
        f"window={hvo_window}, visual={visual['level']}, tilt_progress={tilt.get('progress', 0)}%"
    )
    return result