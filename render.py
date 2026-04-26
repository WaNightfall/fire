"""
render.py — Plotly 自包含 HTML 仪表板生成器
暗色主题，三张交互图表 + 指标卡片 + 近期通报 + 模型透明度面板。
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

log = logging.getLogger(__name__)

# ── 配色方案 ──────────────────────────────────────────────────────────────────
C = {
    "bg_page":     "#080c18",
    "bg_card":     "#0f1623",
    "bg_chart":    "#0b1020",
    "border":      "#1e2a42",
    "text_pri":    "#e2e8f0",
    "text_muted":  "#64748b",
    "text_sub":    "#94a3b8",
    "blue":        "#3b82f6",
    "cyan":        "#06b6d4",
    "purple":      "#8b5cf6",
    "green":       "#22c55e",
    "amber":       "#f59e0b",
    "orange":      "#f97316",
    "red":         "#ef4444",
    "grid":        "rgba(255,255,255,0.05)",
    "axis":        "rgba(255,255,255,0.15)",
}

ALERT_COLORS = {
    "NORMAL":   C["green"],
    "ADVISORY": C["amber"],
    "WATCH":    C["orange"],
    "WARNING":  C["red"],
}
ALERT_LABELS = {
    "NORMAL":   "正常 Normal",
    "ADVISORY": "通报 Advisory",
    "WATCH":    "警戒 Watch",
    "WARNING":  "警告 Warning",
}

PLOTLY_LAYOUT_BASE = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, 'Noto Sans SC', sans-serif", color=C["text_sub"], size=12),
    margin=dict(l=50, r=30, t=40, b=50),
    hoverlabel=dict(
        bgcolor=C["bg_card"],
        bordercolor=C["border"],
        font=dict(family="Inter, sans-serif", size=12, color=C["text_pri"]),
    ),
)

# 通用图例样式（单独引用，避免与 update_layout 中的 legend 参数冲突）
LEGEND_STYLE = dict(
    bgcolor="rgba(15,22,35,0.8)",
    bordercolor=C["border"],
    borderwidth=1,
    font=dict(size=11),
)


def _fig_to_json(fig: go.Figure) -> str:
    return fig.to_json()


# ── Chart 1：30 天地震活动时序 ────────────────────────────────────────────────
def make_seismic_chart(seismic_df: pd.DataFrame) -> go.Figure:
    """双 y 轴：日均地震数（柱状）+ 最大震级（折线）。"""
    dates  = [str(d) for d in seismic_df["date"].tolist()]
    counts = seismic_df["count"].tolist()
    mags   = seismic_df["max_mag"].tolist()

    fig = go.Figure()

    # 柱状：地震次数
    fig.add_trace(go.Bar(
        name="日地震次数",
        x=dates, y=counts,
        marker=dict(color=C["blue"], opacity=0.75, line=dict(width=0)),
        hovertemplate="<b>%{x}</b><br>地震次数：%{y} 次<extra></extra>",
        yaxis="y",
    ))

    # 折线：最大震级
    fig.add_trace(go.Scatter(
        name="最大震级",
        x=dates, y=mags,
        mode="lines+markers",
        line=dict(color=C["cyan"], width=2),
        marker=dict(size=5, color=C["cyan"]),
        hovertemplate="<b>%{x}</b><br>最大震级：M %{y:.1f}<extra></extra>",
        yaxis="y2",
    ))

    fig.update_layout(
        **PLOTLY_LAYOUT_BASE,
        title=dict(text="过去 30 天地震活动", font=dict(size=14, color=C["text_pri"]), x=0.01),
        xaxis=dict(
            showgrid=False,
            tickfont=dict(size=10),
            linecolor=C["axis"],
        ),
        yaxis=dict(
            title="地震次数",
            showgrid=True, gridcolor=C["grid"],
            tickfont=dict(size=10),
            linecolor=C["axis"],
        ),
        yaxis2=dict(
            title="最大震级 (M)",
            overlaying="y",
            side="right",
            showgrid=False,
            tickfont=dict(size=10),
            linecolor=C["axis"],
            rangemode="tozero",
        ),
        barmode="overlay",
        legend=dict(**LEGEND_STYLE, x=0.01, y=0.99, xanchor="left", yanchor="top"),
    )
    return fig


# ── Chart 2：30 天喷发概率预测 ────────────────────────────────────────────────
def _prob_to_color(p: float) -> str:
    """概率 → RGBA 颜色（绿 → 琥珀 → 红）。"""
    if p < 0.2:
        r = int(34 + (245 - 34) * p / 0.2)
        g = int(197 - (197 - 158) * p / 0.2)
        b = int(94 - 94 * p / 0.2)
    else:
        t = (p - 0.2) / 0.8
        r = int(245 + (239 - 245) * t)
        g = int(158 - (158 - 68) * t)
        b = int(0 + 68 * t)
    return f"rgb({r},{g},{b})"


def make_forecast_chart(forecast_df: pd.DataFrame) -> go.Figure:
    """30 天概率面积图 + 置信区间阴影 + 今日竖线 + 概率阈值参考线。"""
    dates = [str(d) for d in forecast_df["date"].tolist()]
    probs = (forecast_df["probability"] * 100).tolist()
    ci_lo = (forecast_df["ci_lower"] * 100).tolist()
    ci_hi = (forecast_df["ci_upper"] * 100).tolist()

    colors = [_prob_to_color(p / 100) for p in probs]

    fig = go.Figure()

    # 置信区间上沿（不可见，用于 fill）
    fig.add_trace(go.Scatter(
        x=dates, y=ci_hi,
        mode="lines", line=dict(width=0),
        showlegend=False,
        hoverinfo="skip",
        name="_ci_upper",
    ))

    # 置信区间下沿（填充到上沿）
    fig.add_trace(go.Scatter(
        x=dates, y=ci_lo,
        mode="lines", line=dict(width=0),
        fill="tonexty",
        fillcolor="rgba(59,130,246,0.10)",
        showlegend=False,
        hoverinfo="skip",
        name="_ci_lower",
    ))

    # 概率面积图（主线）
    fig.add_trace(go.Scatter(
        name="喷发概率",
        x=dates, y=probs,
        mode="lines+markers",
        line=dict(color=C["blue"], width=2.5),
        marker=dict(
            size=7,
            color=colors,
            line=dict(color=C["bg_card"], width=1),
        ),
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.08)",
        hovertemplate=(
            "<b>%{x}</b><br>"
            "喷发概率：<b>%{y:.1f}%</b><br>"
            "<extra></extra>"
        ),
    ))

    # 置信区间标注线（透明，用于 hover 显示）
    fig.add_trace(go.Scatter(
        name="置信区间",
        x=dates + dates[::-1],
        y=ci_hi + ci_lo[::-1],
        fill="toself",
        fillcolor="rgba(59,130,246,0.06)",
        line=dict(color="rgba(0,0,0,0)"),
        showlegend=True,
        hoverinfo="skip",
    ))

    # 今日竖线（用 add_shape 避免 Plotly 对日期字符串做 sum() 计算的 bug）
    today_str = str(forecast_df["date"].iloc[0])
    fig.add_shape(
        type="line",
        x0=today_str, x1=today_str,
        y0=0, y1=1, yref="paper",
        line=dict(color=C["cyan"], width=1.5, dash="dot"),
    )
    fig.add_annotation(
        x=today_str, y=1.02, yref="paper",
        text="今日", showarrow=False,
        font=dict(color=C["cyan"], size=11),
        xanchor="left", yanchor="bottom",
    )

    # 参考阈值线
    for level, label, color in [
        (15, "通报阈值 15%", C["amber"]),
        (45, "警戒阈值 45%", C["orange"]),
    ]:
        fig.add_hline(
            y=level,
            line=dict(color=color, width=1, dash="dash"),
            annotation_text=label,
            annotation_position="right",
            annotation_font=dict(color=color, size=10),
        )

    fig.update_layout(
        **PLOTLY_LAYOUT_BASE,
        title=dict(text="未来 30 天喷发概率预测", font=dict(size=14, color=C["text_pri"]), x=0.01),
        xaxis=dict(showgrid=False, tickfont=dict(size=10), linecolor=C["axis"]),
        yaxis=dict(
            title="喷发概率 (%)",
            range=[0, max(max(ci_hi) * 1.15, 10)],
            showgrid=True, gridcolor=C["grid"],
            ticksuffix="%",
            tickfont=dict(size=10),
            linecolor=C["axis"],
        ),
        legend=dict(**LEGEND_STYLE, x=0.01, y=0.99, xanchor="left", yanchor="top"),
    )
    return fig


# ── Chart 3：地震深度-震级散点 ────────────────────────────────────────────────
def make_depth_scatter(raw_df: pd.DataFrame) -> go.Figure:
    """深度 vs 震级散点图，颜色/大小双重编码震级。"""
    fig = go.Figure()

    if raw_df.empty:
        fig.add_annotation(
            text="暂无地震数据", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color=C["text_muted"]),
        )
    else:
        recent = raw_df.copy()
        # 颜色映射：震级 → Viridis
        # 显式转换为 Python list，避免 pandas Series 在 Plotly JSON 序列化时丢失数据
        depths = recent["depth_km"].tolist()
        mags   = recent["mag"].tolist()
        places = recent["place"].tolist()

        fig.add_trace(go.Scatter(
            name="地震事件",
            x=depths,
            y=mags,
            mode="markers",
            marker=dict(
                size=[max(6, m * 4.5) for m in mags],
                color=mags,
                colorscale="Turbo",
                cmin=min(mags),
                cmax=max(mags),
                showscale=True,
                colorbar=dict(
                    title=dict(text="震级", font=dict(size=11, color=C["text_sub"])),
                    thickness=12,
                    len=0.7,
                    tickfont=dict(size=10, color=C["text_sub"]),
                    bgcolor="rgba(0,0,0,0)",
                ),
                opacity=0.85,
                line=dict(color="rgba(255,255,255,0.4)", width=1),
            ),
            text=places,
            hovertemplate=(
                "<b>%{text}</b><br>"
                "深度：%{x:.1f} km<br>"
                "震级：M %{y:.1f}<br>"
                "<extra></extra>"
            ),
        ))

        # 参考线
        for y_val, label, color in [
            (3.0, "M3.0 可感地震", C["amber"]),
            (4.0, "M4.0 显著地震", C["orange"]),
        ]:
            fig.add_hline(
                y=y_val,
                line=dict(color=color, width=1, dash="dash"),
                annotation_text=label,
                annotation_position="right",
                annotation_font=dict(color=color, size=10),
            )

    # 计算合理的轴范围
    if not raw_df.empty:
        y_min = max(0, raw_df["mag"].min() - 0.3)
        y_max = raw_df["mag"].max() + 0.3
        x_max = raw_df["depth_km"].max() * 1.1 + 1
    else:
        y_min, y_max, x_max = 0, 5, 30

    fig.update_layout(
        **PLOTLY_LAYOUT_BASE,
        title=dict(text="地震深度 vs 震级分布", font=dict(size=14, color=C["text_pri"]), x=0.01),
        xaxis=dict(
            title="震源深度 (km)",
            showgrid=True, gridcolor=C["grid"],
            tickfont=dict(size=10),
            linecolor=C["axis"],
            range=[0, x_max],
        ),
        yaxis=dict(
            title="震级 (M)",
            showgrid=True, gridcolor=C["grid"],
            tickfont=dict(size=10),
            linecolor=C["axis"],
            range=[y_min, y_max],
        ),
    )
    return fig


# ── HTML 辅助渲染 ──────────────────────────────────────────────────────────────
def _render_alert_badge(alert_level: str) -> str:
    color  = ALERT_COLORS.get(alert_level, C["green"])
    label  = ALERT_LABELS.get(alert_level, alert_level)
    return (
        f'<span class="badge" style="background:{color}20;color:{color};'
        f'border:1px solid {color}40;">{label}</span>'
    )


def _render_metric_cards(result: dict) -> str:
    al    = result["alert_level"]
    color = ALERT_COLORS.get(al, C["green"])
    label = ALERT_LABELS.get(al, al)
    prob  = result["current_probability"]
    peak  = result["peak_prob_30d"]

    def prob_color(p):
        if p < 0.15: return C["green"]
        if p < 0.45: return C["amber"]
        return C["red"]

    cards = [
        {
            "title": "当前预警级别",
            "value": label,
            "sub":   f'航空色码：{result.get("aviation_color","GREEN")}',
            "color": color,
            "icon":  "🌋",
        },
        {
            "title": "30 天地震次数",
            "value": f'{result["eq_count_30d"]:,}',
            "sub":   "Kīlauea 周边 30km",
            "color": C["blue"],
            "icon":  "📊",
        },
        {
            "title": "最大震级",
            "value": f'M {result["max_mag_30d"]:.1f}' if result["max_mag_30d"] > 0 else "—",
            "sub":   "过去 30 天",
            "color": C["purple"],
            "icon":  "📡",
        },
        {
            "title": "今日喷发概率",
            "value": f'{prob:.1%}',
            "sub":   f'30 天峰值：{peak:.1%}',
            "color": prob_color(prob),
            "icon":  "🔥",
        },
    ]

    html_parts = []
    for c in cards:
        html_parts.append(f"""
        <div class="metric-card" style="border-top:3px solid {c['color']};">
            <div class="metric-icon">{c['icon']}</div>
            <div class="metric-title">{c['title']}</div>
            <div class="metric-value" style="color:{c['color']};">{c['value']}</div>
            <div class="metric-sub">{c['sub']}</div>
        </div>""")
    return "\n".join(html_parts)


def _render_alerts(alerts: list) -> str:
    if not alerts:
        return '<div class="no-alerts">当前无 Kīlauea 专项通报</div>'

    def get_field(n, *keys, default=""):
        for k in keys:
            v = n.get(k)
            if v:
                return str(v)
        return default

    items = []
    for n in alerts:
        # HANS getRecentNotices 实际字段名
        title = get_field(n, "notice_type_title", "noticeTitle", "notice_title", "title", default="火山活动通报")
        body  = get_field(n, "notice_category", "noticeBody", "body", "text", default="")
        date  = get_field(n, "sent_utc", "noticeDate", "date", "publishedDate", default="")
        level = get_field(n, "highest_alert_level", "alertLevel", "alert_level", default="NORMAL").upper()
        color = ALERT_COLORS.get(level, C["blue"])

        # 日期格式化
        date_str = date[:10] if len(date) >= 10 else date
        # 摘要（截断过长正文）
        summary = (body[:180] + "…") if len(body) > 180 else body

        items.append(f"""
        <div class="alert-card" style="border-left:3px solid {color};">
            <div class="alert-header">
                <span class="alert-title">{title}</span>
                <span class="alert-date">{date_str}</span>
            </div>
            {f'<div class="alert-body">{summary}</div>' if summary else ''}
        </div>""")

    return "\n".join(items)


def _render_score_breakdown(result: dict) -> str:
    sb = result["score_breakdown"]
    td = result["trend_direction"]
    td_label = {"accelerating": "⬆ 加速", "decelerating": "⬇ 衰减", "stable": "→ 平稳"}.get(td, td)
    return f"""
    <div class="score-grid">
        <div class="score-item">
            <span class="score-label">预警级别分数</span>
            <span class="score-bar-wrap">
                <span class="score-bar" style="width:{sb['alert_score']*100:.0f}%;background:{C['orange']};"></span>
            </span>
            <span class="score-val">{sb['alert_score']:.1%} × 50%</span>
        </div>
        <div class="score-item">
            <span class="score-label">地震活动分数</span>
            <span class="score-bar-wrap">
                <span class="score-bar" style="width:{sb['seismic_score']*100:.0f}%;background:{C['blue']};"></span>
            </span>
            <span class="score-val">{sb['seismic_score']:.1%} × 35%</span>
        </div>
        <div class="score-item">
            <span class="score-label">趋势分数</span>
            <span class="score-bar-wrap">
                <span class="score-bar" style="width:{sb['trend_score']*100:.0f}%;background:{C['cyan']};"></span>
            </span>
            <span class="score-val">{sb['trend_score']:.1%} × 15%</span>
        </div>
    </div>
    <div class="score-meta">趋势方向：{td_label} &nbsp;|&nbsp; 合成概率：<b>{result['current_probability']:.1%}</b></div>
    """


# ── 完整 HTML 模板 ────────────────────────────────────────────────────────────
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kīlauea 火山喷发预测仪表板</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg-page:  {bg_page};
    --bg-card:  {bg_card};
    --border:   {border};
    --text-pri: {text_pri};
    --text-sub: {text_sub};
    --text-mut: {text_muted};
    --blue:     {blue};
    --cyan:     {cyan};
    --green:    {green};
  }}
  body {{
    background: var(--bg-page);
    color: var(--text-pri);
    font-family: 'Inter', 'Noto Sans SC', sans-serif;
    min-height: 100vh;
    line-height: 1.6;
  }}
  .container {{ max-width: 1280px; margin: 0 auto; padding: 0 24px 48px; }}

  /* ── 顶栏 ── */
  .header {{
    padding: 28px 0 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
  }}
  .header-left h1 {{
    font-size: 1.75rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--text-pri);
  }}
  .header-left h1 span {{ color: var(--blue); }}
  .header-left p {{
    font-size: 0.85rem;
    color: var(--text-mut);
    margin-top: 4px;
  }}
  .header-right {{
    text-align: right;
    font-size: 0.8rem;
    color: var(--text-mut);
    line-height: 1.8;
  }}
  .badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    margin-left: 8px;
  }}

  /* ── 指标卡片 ── */
  .metrics-row {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin: 24px 0;
  }}
  @media (max-width: 900px) {{ .metrics-row {{ grid-template-columns: repeat(2, 1fr); }} }}
  @media (max-width: 480px) {{ .metrics-row {{ grid-template-columns: 1fr; }} }}
  .metric-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    transition: transform 0.15s;
  }}
  .metric-card:hover {{ transform: translateY(-2px); }}
  .metric-icon {{ font-size: 1.5rem; margin-bottom: 8px; }}
  .metric-title {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-mut); }}
  .metric-value {{ font-size: 1.6rem; font-weight: 700; margin: 6px 0 4px; }}
  .metric-sub {{ font-size: 0.75rem; color: var(--text-mut); }}

  /* ── 图表区 ── */
  .section-title {{
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--text-mut);
    margin: 32px 0 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .section-title::after {{
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }}
  .charts-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
  }}
  @media (max-width: 900px) {{ .charts-grid {{ grid-template-columns: 1fr; }} }}
  .chart-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    overflow: hidden;
  }}
  .chart-card.full-width {{ grid-column: 1 / -1; }}
  .chart-div {{ width: 100%; height: 300px; }}

  /* ── 通报 ── */
  .alerts-grid {{
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .alert-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
  }}
  .alert-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 6px;
  }}
  .alert-title {{ font-size: 0.88rem; font-weight: 500; color: var(--text-pri); }}
  .alert-date  {{ font-size: 0.75rem; color: var(--text-mut); white-space: nowrap; }}
  .alert-body  {{ font-size: 0.8rem; color: var(--text-sub); line-height: 1.5; }}
  .no-alerts   {{ font-size: 0.85rem; color: var(--text-mut); padding: 20px 0; }}

  /* ── 模型详情 ── */
  details {{ margin-top: 24px; }}
  summary {{
    cursor: pointer;
    font-size: 0.82rem;
    font-weight: 500;
    color: var(--blue);
    user-select: none;
    padding: 12px 16px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    list-style: none;
  }}
  summary::-webkit-details-marker {{ display: none; }}
  summary::before {{ content: '▶ '; font-size: 0.65rem; }}
  details[open] summary::before {{ content: '▼ '; }}
  .model-body {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-top: none;
    border-radius: 0 0 8px 8px;
    padding: 20px;
  }}
  .score-grid {{ display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }}
  .score-item {{ display: grid; grid-template-columns: 130px 1fr 120px; align-items: center; gap: 12px; }}
  .score-label {{ font-size: 0.78rem; color: var(--text-sub); }}
  .score-bar-wrap {{ height: 6px; background: rgba(255,255,255,0.07); border-radius: 99px; overflow: hidden; }}
  .score-bar {{ display: block; height: 100%; border-radius: 99px; transition: width 0.6s ease; }}
  .score-val {{ font-size: 0.78rem; color: var(--text-pri); text-align: right; font-variant-numeric: tabular-nums; }}
  .score-meta {{ font-size: 0.8rem; color: var(--text-sub); margin-bottom: 14px; }}
  .disclaimer {{
    font-size: 0.75rem;
    color: var(--text-mut);
    border-top: 1px solid var(--border);
    padding-top: 12px;
    line-height: 1.7;
  }}

  /* ── 底部 ── */
  .footer {{
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid var(--border);
    font-size: 0.72rem;
    color: var(--text-mut);
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }}
  .footer a {{ color: var(--blue); text-decoration: none; }}
  .footer a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="container">

  <!-- 顶栏 -->
  <div class="header">
    <div class="header-left">
      <h1>🌋 <span>Kīlauea</span> 火山喷发预测仪表板</h1>
      <p>夏威夷大岛 · USGS 官方数据 · 启发式概率模型</p>
    </div>
    <div class="header-right">
      <div>当前预警级别 {alert_badge}</div>
      <div>数据更新：{generated_at}</div>
      <div>数据源：<a href="https://www.usgs.gov/observatories/hvo" target="_blank">USGS HVO</a></div>
    </div>
  </div>

  <!-- 指标卡片 -->
  <div class="metrics-row">
    {metric_cards}
  </div>

  <!-- 图表 1：地震时序（全宽）-->
  <div class="section-title">地震监测数据</div>
  <div class="chart-card">
    <div id="chart-seismic" class="chart-div"></div>
  </div>

  <!-- 图表 2 + 3：预测 + 散点 -->
  <div class="section-title" style="margin-top:16px;">喷发概率预测</div>
  <div class="charts-grid">
    <div class="chart-card">
      <div id="chart-forecast" class="chart-div"></div>
    </div>
    <div class="chart-card">
      <div id="chart-scatter" class="chart-div"></div>
    </div>
  </div>

  <!-- 近期通报 -->
  <div class="section-title">近期 HANS 通报</div>
  <div class="alerts-grid">
    {alerts_html}
  </div>

  <!-- 模型详情 -->
  <details>
    <summary>预测模型详情 &amp; 分数分解</summary>
    <div class="model-body">
      {score_breakdown}
      <p class="disclaimer">
        ⚠ 本系统为组合式启发式模型，综合 USGS 预警级别（权重 50%）、地震频率与能量（35%）及趋势加速（15%）三个维度。
        置信区间随预测日数线性展宽，反映认知不确定性。<b>本仪表板仅供教育与学习用途，不构成任何安全建议。</b>
        火山活动的实际观测请参阅 <a href="https://volcanoes.usgs.gov/observatories/hvo/" target="_blank">USGS HVO 官网</a>。
      </p>
    </div>
  </details>

  <!-- 底部 -->
  <div class="footer">
    <span>数据来源：<a href="https://earthquake.usgs.gov/fdsnws/event/1/" target="_blank">USGS FDSN API</a> &nbsp;|&nbsp;
      <a href="https://volcanoes.usgs.gov/hans-public/api/" target="_blank">USGS HANS API</a></span>
    <span>每日 08:00 HST 自动更新 &nbsp;·&nbsp; {generated_at}</span>
  </div>

</div><!-- /container -->

<script>
const cfg = {{responsive: true, displayModeBar: false}};
Plotly.newPlot('chart-seismic',  {chart1_json}.data, {chart1_json}.layout, cfg);
Plotly.newPlot('chart-forecast', {chart2_json}.data, {chart2_json}.layout, cfg);
Plotly.newPlot('chart-scatter',  {chart3_json}.data, {chart3_json}.layout, cfg);
</script>
</body>
</html>
"""


# ── 顶层渲染函数 ──────────────────────────────────────────────────────────────
def render_dashboard(result: dict, output_path: Path) -> None:
    """整合所有组件，写出自包含 HTML 仪表板。"""
    log.info("Rendering dashboard...")

    fig1 = make_seismic_chart(result["seismic_df"])
    fig2 = make_forecast_chart(result["forecast_df"])
    fig3 = make_depth_scatter(result["raw_df"])

    # 注意：HTML 模板中用 {chart1_json} 等占位符，需要特殊处理
    chart1_json = _fig_to_json(fig1)
    chart2_json = _fig_to_json(fig2)
    chart3_json = _fig_to_json(fig3)

    html = HTML_TEMPLATE.format(
        # 颜色变量
        bg_page=C["bg_page"], bg_card=C["bg_card"], border=C["border"],
        text_pri=C["text_pri"], text_sub=C["text_sub"], text_muted=C["text_muted"],
        blue=C["blue"], cyan=C["cyan"], green=C["green"],
        # 内容
        alert_badge=_render_alert_badge(result["alert_level"]),
        generated_at=result["generated_at"],
        metric_cards=_render_metric_cards(result),
        alerts_html=_render_alerts(result["alerts"]),
        score_breakdown=_render_score_breakdown(result),
        # 图表 JSON（直接嵌入 JS）
        chart1_json=chart1_json,
        chart2_json=chart2_json,
        chart3_json=chart3_json,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info(f"Dashboard written: {output_path} ({len(html):,} bytes)")
