"""
fetch_data.py — USGS 官方 API 数据抓取模块
支持 Kīlauea 火山地震、预警级别、HANS 通报数据获取，带本地 JSON 缓存。
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────
USGS_FDSN_BASE      = "https://earthquake.usgs.gov/fdsnws/event/1/query"
HANS_RECENT         = "https://volcanoes.usgs.gov/hans-public/api/notice/getRecentNotices"
HANS_DAILY          = "https://volcanoes.usgs.gov/vsc/api/hansApi/getDailySummaryData"
# 正确端点：getMonitoredVolcanoes 返回所有监测火山含实时预警级别
MONITORED_VOLCS     = "https://volcanoes.usgs.gov/hans-public/api/volcano/getMonitoredVolcanoes"
# Kīlauea 真实标识符：volcano_cd="hi3", vnum="332010"（非旧版 haw77）
KILAUEA_VNUM        = "332010"
KILAUEA_CD          = "hi3"

KILAUEA_LAT   = 19.421
KILAUEA_LON   = -155.287
RADIUS_KM     = 30
CACHE_TTL_HRS = 23          # 缓存有效期（小时）
DATA_DIR      = Path(__file__).parent / "data"

SESSION_HEADERS = {
    "User-Agent": "KilaueaWatchBot/1.0 (portfolio project; github.com/user/kilauea-watch)",
    "Accept": "application/json",
}

# 共享 Session（连接复用）
_session = requests.Session()
_session.headers.update(SESSION_HEADERS)


# ── 自定义异常 ────────────────────────────────────────────────────────────────
class FetchError(Exception):
    """API 调用在所有重试后仍失败时抛出。"""

class StaleDataWarning(UserWarning):
    """缓存数据超过 48 小时时发出警告。"""


# ── HTTP 基础助手 ─────────────────────────────────────────────────────────────
def _get(url: str, params: dict = None, timeout: int = 30, retries: int = 3):
    """带指数退避重试的 GET 请求，自动处理限速和服务器错误。"""
    last_exc = None
    for attempt in range(retries):
        try:
            resp = _session.get(url, params=params, timeout=timeout)

            # 429 限速：尊重 Retry-After
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                log.warning(f"Rate limited by {url}. Waiting {wait}s...")
                time.sleep(wait)
                continue

            # 5xx 服务器错误：重试
            if resp.status_code in (500, 502, 503, 504):
                log.warning(f"Server error {resp.status_code} from {url}, attempt {attempt+1}/{retries}")
                time.sleep(2 ** attempt)
                continue

            # 4xx 客户端错误：立即失败
            if resp.status_code >= 400:
                raise FetchError(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")

            # 检查 Content-Type（USGS 偶尔返回 HTML+200）
            ct = resp.headers.get("Content-Type", "")
            if "json" not in ct and "javascript" not in ct:
                log.warning(f"Unexpected Content-Type '{ct}' from {url}, attempting JSON parse anyway")

            return resp.json()

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
            wait = 2 ** attempt
            log.warning(f"Connection error ({e}), retrying in {wait}s... ({attempt+1}/{retries})")
            time.sleep(wait)
        except json.JSONDecodeError as e:
            raise FetchError(f"Response from {url} is not valid JSON: {e}") from e

    raise FetchError(f"All {retries} attempts failed for {url}") from last_exc


# ── 缓存工具 ──────────────────────────────────────────────────────────────────
def _cache_path(name: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"{name}.json"


def _load_cache(name: str) -> dict | None:
    """读取缓存，若存在且未过期则返回 data 字段，否则返回 None。"""
    p = _cache_path(name)
    if not p.exists():
        return None
    try:
        wrapper = json.loads(p.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(wrapper["fetched_at"])
        age_hrs = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age_hrs > CACHE_TTL_HRS:
            log.info(f"Cache '{name}' is {age_hrs:.1f}h old (TTL={CACHE_TTL_HRS}h), refreshing")
            return None
        if age_hrs > 48:
            import warnings
            warnings.warn(f"Cache '{name}' is very stale ({age_hrs:.0f}h)", StaleDataWarning)
        log.info(f"Cache hit: '{name}' ({age_hrs:.1f}h old)")
        return wrapper["data"]
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        log.warning(f"Corrupt cache '{name}': {e}, will re-fetch")
        return None


def _save_cache(name: str, data, source_url: str):
    wrapper = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
        "data": data,
    }
    _cache_path(name).write_text(
        json.dumps(wrapper, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"Cache saved: '{name}'")


def load_raw_cache(name: str):
    """强制加载缓存（忽略过期，用于离线降级）。"""
    p = _cache_path(name)
    if not p.exists():
        return None
    try:
        wrapper = json.loads(p.read_text(encoding="utf-8"))
        return wrapper.get("data")
    except Exception:
        return None


# ── 数据抓取函数 ──────────────────────────────────────────────────────────────
def fetch_earthquakes(days: int = 30) -> dict:
    """
    从 USGS FDSN API 获取 Kīlauea 周边 30km 内近 {days} 天的地震数据。
    返回原始 GeoJSON dict。
    """
    cached = _load_cache("earthquakes")
    if cached is not None:
        return cached

    now_utc = datetime.now(timezone.utc)
    start   = (now_utc - timedelta(days=days)).strftime("%Y-%m-%d")
    end     = now_utc.strftime("%Y-%m-%d")

    params = {
        "format":         "geojson",
        "starttime":      start,
        "endtime":        end,
        "latitude":       KILAUEA_LAT,
        "longitude":      KILAUEA_LON,
        "maxradiuskm":    RADIUS_KM,
        "minmagnitude":   0.5,
        "orderby":        "time",
        "limit":          20000,
    }

    log.info(f"Fetching earthquakes: {start} → {end}, radius={RADIUS_KM}km")
    data = _get(USGS_FDSN_BASE, params=params)
    _save_cache("earthquakes", data, USGS_FDSN_BASE)
    count = len(data.get("features", []))
    log.info(f"Fetched {count} earthquake events")
    return data


def fetch_volcano_status() -> dict:
    """
    从 getMonitoredVolcanoes 获取 Kīlauea 实时预警级别。
    Kīlauea 正确标识符：volcano_cd="hi3", vnum="332010"（旧版 haw77 已失效）。
    返回标准化 dict，含 alert_level / aviation_color 字段。
    """
    cached = _load_cache("volcano_status")
    if cached is not None:
        return cached

    result = {"alert_level": "NORMAL", "aviation_color": "GREEN",
              "volcano_name": "Kīlauea", "last_updated": ""}

    try:
        log.info(f"Fetching volcano status from {MONITORED_VOLCS}")
        volcs = _get(MONITORED_VOLCS)

        if not isinstance(volcs, list):
            raise FetchError(f"Expected list, got {type(volcs)}")

        # 精确匹配 Kīlauea（vnum=332010 / volcano_cd=hi3 / name 含 Kilauea）
        kilauea = next(
            (v for v in volcs
             if str(v.get("vnum", "")) == KILAUEA_VNUM
             or str(v.get("volcano_cd", "")).lower() == KILAUEA_CD
             or "kilauea" in str(v.get("volcano_name", "")).lower()),
            None
        )

        if kilauea is None:
            log.warning("Kīlauea not found in monitored volcanoes list → defaulting to NORMAL")
        else:
            # getMonitoredVolcanoes 字段：alert_level / color_code
            result["alert_level"]   = str(kilauea.get("alert_level", "NORMAL")).upper()
            result["aviation_color"] = str(kilauea.get("color_code", "GREEN")).upper()
            result["volcano_name"]  = kilauea.get("volcano_name", "Kīlauea")
            result["last_updated"]  = kilauea.get("sent_utc", "")
            result["notice_url"]    = kilauea.get("notice_url", "")
            log.info(
                f"Volcano status: alert={result['alert_level']}, "
                f"aviation={result['aviation_color']}, "
                f"updated={result['last_updated']}"
            )

    except FetchError as e:
        log.error(f"Volcano status fetch failed: {e}. Using default NORMAL.")

    # 归一化拼写
    level_map = {"NORMAL": "NORMAL", "ADVISORY": "ADVISORY",
                 "WATCH": "WATCH", "WARNING": "WARNING"}
    result["alert_level"] = level_map.get(result["alert_level"], "NORMAL")

    _save_cache("volcano_status", result, MONITORED_VOLCS)
    return result


def fetch_alerts() -> list:
    """
    从 HANS API 获取 Kīlauea 近期通报，过滤并去重，按时间倒序排列。
    返回 list[dict]，每条包含完整通报内容（从 notice_data URL 获取）。
    """
    cached = _load_cache("alerts")
    if cached is not None:
        return cached

    all_notices = []

    # 源 1：getRecentNotices（全球）
    try:
        log.info("Fetching HANS recent notices...")
        notices = _get(HANS_RECENT)
        if isinstance(notices, dict):
            notices = [notices]
        if isinstance(notices, list):
            all_notices.extend(notices)
        log.info(f"Got {len(notices) if isinstance(notices, list) else 1} global notices")
    except FetchError as e:
        log.warning(f"HANS recent notices failed: {e}")

    # 源 2：getDailySummaryData
    try:
        log.info("Fetching HANS daily summary...")
        summary = _get(HANS_DAILY)
        if isinstance(summary, dict):
            items = summary.get("notices", summary.get("data", []))
            if isinstance(items, list):
                all_notices.extend(items)
            elif isinstance(items, dict):
                all_notices.append(items)
    except FetchError as e:
        log.warning(f"HANS daily summary failed: {e}")

    # 过滤：只保留 HVO / Kīlauea 相关
    def is_kilauea(n: dict) -> bool:
        obs  = str(n.get("obs_abbr", n.get("observatoryAbbr", ""))).lower()
        vols = str(n.get("volcanoes", n.get("volcanoId", n.get("vName", "")))).lower()
        return obs == "hvo" or "kilauea" in vols or "haw77" in vols

    kilauea_notices = [n for n in all_notices if is_kilauea(n)]
    log.info(f"Filtered to {len(kilauea_notices)} Kilauea notices")

    # 去重
    seen = set()
    deduped = []
    for n in kilauea_notices:
        nid = str(n.get("notice_identifier", n.get("noticeId", n.get("id", id(n)))))
        if nid not in seen:
            seen.add(nid)
            deduped.append(n)

    # 过滤掉无日期的条目
    deduped = [n for n in deduped if n.get("sent_utc") or n.get("noticeDate") or n.get("date")]

    # 排序（最新在前）
    def notice_date(n):
        for key in ("sent_utc", "noticeDate", "date", "publishedDate", "issueDate"):
            val = n.get(key)
            if val:
                try:
                    return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                except ValueError:
                    try:
                        return datetime.strptime(str(val)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass
        return datetime.min.replace(tzinfo=timezone.utc)

    deduped.sort(key=notice_date, reverse=True)
    result = deduped[:10]

    # 获取完整通报内容（从 notice_data URL）
    for notice in result:
        notice_url = notice.get("notice_data", "")
        if notice_url and isinstance(notice_url, str) and notice_url.startswith("http"):
            try:
                notice_content = _get(notice_url)
                if isinstance(notice_content, dict):
                    # 合并完整通报内容到 notice
                    # 优先取 notice_html（HTML格式），其次取纯文本字段
                    notice["content"] = (
                        notice_content.get("notice_html", "") or
                        notice_content.get("content", "") or
                        notice_content.get("text", "") or
                        notice_content.get("body", "")
                    )
                    notice["title"] = notice_content.get("title", notice.get("notice_type_title", ""))
                elif isinstance(notice_content, str):
                    notice["content"] = notice_content
            except Exception as e:
                log.warning(f"Failed to fetch notice content from {notice_url}: {e}")
                notice["content"] = ""

    _save_cache("alerts", result, HANS_RECENT)
    log.info(f"Saved {len(result)} Kīlauea alerts to cache")
    return result


# ── 顶层接口 ──────────────────────────────────────────────────────────────────
def fetch_all() -> tuple:
    """
    抓取所有三类数据。单个数据源失败时降级为缓存，不中断整体流程。
    返回 (earthquakes_geojson, volcano_status_dict, alerts_list)
    """
    eq_data = status_data = alerts_data = None

    try:
        eq_data = fetch_earthquakes()
    except FetchError as e:
        log.error(f"Earthquake fetch failed: {e}")

    try:
        status_data = fetch_volcano_status()
    except FetchError as e:
        log.error(f"Volcano status fetch failed: {e}")

    try:
        alerts_data = fetch_alerts()
    except FetchError as e:
        log.error(f"Alerts fetch failed: {e}")

    # 使用默认值保证流程继续
    if eq_data is None:
        eq_data = {"type": "FeatureCollection", "features": []}
    if status_data is None:
        status_data = {"alert_level": "NORMAL", "aviation_color": "GREEN"}
    if alerts_data is None:
        alerts_data = []

    return eq_data, status_data, alerts_data
