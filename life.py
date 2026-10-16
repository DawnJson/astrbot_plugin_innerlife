from __future__ import annotations

import asyncio
import datetime
import html
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; innerlife)"}
TITLE_LIMIT = 80
SUMMARY_LIMIT = 160
RAIN_CHANCE = 50

DEFAULT_NEWS_FEEDS = [
    "https://www.ithome.com/rss/",
    "https://sspai.com/feed",
    "https://www.ifanr.com/feed",
    "https://www.gcores.com/rss",
    "https://www.yystv.cn/rss/feed",
    "https://www.douban.com/feed/review/movie",
    "https://feeds.appinn.com/appinns/",
]

DEFAULT_NEWS_BLOCKED_TERMS = [
    "政治", "政府", "外交", "总统", "总理", "国务院", "两会", "选举",
    "制裁", "关税", "贸易战", "军方", "军队", "军事", "战火", "战场", "导弹", "空袭", "俄乌",
    "乌克兰", "俄罗斯", "以色列", "巴勒斯坦", "加沙", "台海", "台独", "南海", "朝鲜",
    "恐怖", "袭击", "爆炸", "枪击", "枪杀", "杀害", "谋杀", "遇难", "身亡", "自杀",
    "犯罪", "逮捕", "拘留", "判刑", "诈骗", "涉嫌", "性侵", "猥亵", "强奸", "贩毒",
    "地震", "海啸", "洪灾", "洪水", "台风", "火灾", "坠机", "空难", "矿难", "疫情", "新冠",
    "trump", "biden", "putin", "congress", "sanction", "war on", "airstrike", "terror", "shooting",
    "murder", "killed",
]

WMO_CODES = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "有雾", 48: "有雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "大阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}

WEEKDAYS = "周一", "周二", "周三", "周四", "周五", "周六", "周日"


@dataclass(frozen=True)
class NewsItem:
    title: str
    summary: str
    source: str
    link: str
    published_at: float | None


def _period(hour: int) -> str:
    if hour < 6:
        return "凌晨"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 17:
        return "下午"
    if hour < 20:
        return "傍晚"
    return "晚上"


async def fetch_weather(
    client: httpx.AsyncClient, latitude: float, longitude: float, timezone: str
) -> str:
    response = await client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "current": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min",
            "hourly": "precipitation_probability",
            "forecast_days": 1,
        },
        headers=HEADERS,
    )
    response.raise_for_status()
    data = response.json()
    current = data["current"]
    daily = data["daily"]
    text = (
        f"现在{WMO_CODES.get(current['weather_code'], '天气不明')} "
        f"{round(current['temperature_2m'])}°C，"
        f"今天 {round(daily['temperature_2m_min'][0])}~{round(daily['temperature_2m_max'][0])}°C"
    )
    now_hour = int(current["time"][11:13])
    upcoming = [
        (chance, int(time[11:13]))
        for time, chance in zip(data["hourly"]["time"], data["hourly"]["precipitation_probability"])
        if chance is not None and int(time[11:13]) >= now_hour
    ]
    if upcoming:
        chance, hour = max(upcoming, key=lambda item: item[0])
        if chance >= RAIN_CHANCE:
            text += f"，{_period(hour)}可能有雨"
    return text


def day_kind_from_weekday(day: datetime.date) -> str:
    return "周末" if day.weekday() >= 5 else "工作日"


async def fetch_day_kind(client: httpx.AsyncClient, day: datetime.date, holiday_url: str) -> str:
    response = await client.get(holiday_url.format(year=day.year), headers=HEADERS)
    response.raise_for_status()
    iso = day.isoformat()
    for entry in response.json()["days"]:
        if entry["date"] == iso:
            return f"节假日：{entry['name']}" if entry["isOffDay"] else "调休上班"
    return day_kind_from_weekday(day)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _clean(text: str | None, limit: int) -> str:
    text = re.sub(r"<[^>]*>", " ", html.unescape(text or ""))
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, *names: str) -> str | None:
    for name in names:
        for child in element:
            if _local(child.tag) == name and (child.text or "").strip():
                return child.text
    return None


def _parse_time(text: str | None) -> float | None:
    if not text:
        return None
    text = text.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def _entry_link(entry: ET.Element) -> str | None:
    link = _child_text(entry, "link")
    if link:
        return link.strip()
    for child in entry:
        if _local(child.tag) == "link" and child.get("href"):
            if child.get("rel", "alternate") == "alternate":
                return child.get("href")
    return _child_text(entry, "guid", "id")


def _parse_feed(content: bytes, blocked: list[str], per_feed: int) -> list[NewsItem]:
    root = ET.fromstring(content)
    source = _clean(
        next((_child_text(node, "title") for node in root.iter() if _local(node.tag) in ("channel", "feed")), None),
        TITLE_LIMIT,
    )
    items: list[NewsItem] = []
    for entry in root.iter():
        if _local(entry.tag) not in ("item", "entry"):
            continue
        link = _entry_link(entry)
        title = _clean(_child_text(entry, "title"), TITLE_LIMIT)
        if not link or not title:
            continue
        summary = _clean(_child_text(entry, "description", "summary", "encoded", "content"), SUMMARY_LIMIT)
        haystack = _normalize(f"{title} {summary}")
        if any(term in haystack for term in blocked):
            continue
        published = _parse_time(_child_text(entry, "pubDate", "published", "updated", "date"))
        items.append(NewsItem(title, summary, source, link, published))
        if len(items) >= per_feed:
            break
    return items


async def _fetch_feed(
    client: httpx.AsyncClient, url: str, blocked: list[str], per_feed: int
) -> list[NewsItem]:
    try:
        response = await client.get(url, headers=HEADERS, follow_redirects=True)
        response.raise_for_status()
        return _parse_feed(response.content, blocked, per_feed)
    except (httpx.HTTPError, ET.ParseError) as exc:
        logger.warning("news feed %s failed: %r", url, exc)
        return []


async def fetch_news(
    client: httpx.AsyncClient,
    feeds: list[str],
    blocked_terms: Sequence[str],
    per_feed: int,
) -> list[NewsItem]:
    blocked = [term for term in map(_normalize, blocked_terms) if term]
    results = await asyncio.gather(*(_fetch_feed(client, url, blocked, per_feed) for url in feeds))
    seen: set[str] = set()
    items: list[NewsItem] = []
    for item in (item for result in results for item in result):
        if item.link not in seen:
            seen.add(item.link)
            items.append(item)
    items.sort(key=lambda item: item.published_at or 0, reverse=True)
    return items
