from __future__ import annotations

import asyncio
import datetime
import json
import sys
import unittest
from pathlib import Path

import httpx

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from life import (  # noqa: E402
    DEFAULT_NEWS_BLOCKED_TERMS,
    DEFAULT_NEWS_FEEDS,
    day_kind_from_weekday,
    fetch_day_kind,
    fetch_news,
    fetch_weather,
)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>测试站</title>
<item><title>新手机发布</title><link>https://a.test/1</link>
<description><![CDATA[<p>屏幕&amp;相机 <b>升级</b></p>]]></description>
<pubDate>Fri, 16 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>某国总统发表讲话</title><link>https://a.test/2</link><description>x</description>
<pubDate>Fri, 16 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>ＴＲＵＭＰ visits</title><link>https://a.test/3</link><description>x</description></item>
<item><title>重复</title><link>https://a.test/1</link><description>x</description></item>
<item><title>很长的标题</title><link>https://a.test/4</link><description>{long}</description>
<pubDate>Fri, 16 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>""".replace("{long}", "字" * 300)

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>原子站</title>
<entry><title>咖啡馆探店</title><link rel="alternate" href="https://b.test/1"/>
<summary>周末去哪</summary><updated>2026-10-16T12:00:00+08:00</updated></entry>
</feed>"""

HOLIDAYS = {
    "days": [
        {"name": "国庆节", "date": "2026-10-01", "isOffDay": True},
        {"name": "国庆节", "date": "2026-10-10", "isOffDay": False},
    ]
}

WEATHER = {
    "current": {"time": "2026-10-16T10:00", "temperature_2m": 28.6, "weather_code": 2},
    "daily": {"temperature_2m_max": [31.2], "temperature_2m_min": [24.6]},
    "hourly": {
        "time": [f"2026-10-16T{h:02d}:00" for h in range(24)],
        "precipitation_probability": [80] * 6 + [10] * 12 + [70] * 6,
    },
}


def make_client(routes: dict[str, httpx.Response | Exception]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        result = routes[str(request.url.copy_with(query=None))]
        if isinstance(result, Exception):
            raise result
        return result

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


class WeatherTest(unittest.TestCase):
    def fetch(self, payload):
        async def go():
            async with make_client({"https://api.open-meteo.com/v1/forecast": httpx.Response(200, json=payload)}) as client:
                return await fetch_weather(client, 18.25, 109.51, "Asia/Shanghai")

        return run(go())

    def test_line_uses_upcoming_rain_peak_only(self):
        self.assertEqual(self.fetch(WEATHER), "现在多云 29°C，今天 25~31°C，傍晚可能有雨")

    def test_dry_day_has_no_rain_clause(self):
        payload = json.loads(json.dumps(WEATHER))
        payload["hourly"]["precipitation_probability"] = [5] * 24
        self.assertEqual(self.fetch(payload), "现在多云 29°C，今天 25~31°C")

    def test_http_error_raises(self):
        async def go():
            async with make_client({"https://api.open-meteo.com/v1/forecast": httpx.Response(500)}) as client:
                await fetch_weather(client, 0, 0, "UTC")

        with self.assertRaises(httpx.HTTPStatusError):
            run(go())


class DayKindTest(unittest.TestCase):
    URL = "https://h.test/{year}.json"

    def kind(self, day: datetime.date) -> str:
        async def go():
            async with make_client({"https://h.test/2026.json": httpx.Response(200, json=HOLIDAYS)}) as client:
                return await fetch_day_kind(client, day, self.URL)

        return run(go())

    def test_kinds(self):
        self.assertEqual(self.kind(datetime.date(2026, 10, 1)), "节假日：国庆节")
        self.assertEqual(self.kind(datetime.date(2026, 10, 10)), "调休上班")
        self.assertEqual(self.kind(datetime.date(2026, 10, 16)), "工作日")
        self.assertEqual(self.kind(datetime.date(2026, 10, 17)), "周末")

    def test_failure_raises(self):
        async def go():
            async with make_client({"https://h.test/2026.json": httpx.Response(404)}) as client:
                await fetch_day_kind(client, datetime.date(2026, 10, 16), self.URL)

        with self.assertRaises(httpx.HTTPStatusError):
            run(go())

    def test_weekday_fallback(self):
        self.assertEqual(day_kind_from_weekday(datetime.date(2026, 10, 18)), "周末")
        self.assertEqual(day_kind_from_weekday(datetime.date(2026, 10, 19)), "工作日")


class NewsTest(unittest.TestCase):
    def news(self, routes, feeds, blocked, per_feed=10):
        async def go():
            async with make_client(routes) as client:
                return await fetch_news(client, feeds, blocked, per_feed)

        return run(go())

    def test_parse_filter_dedupe_sort(self):
        items = self.news(
            {
                "https://a.test/rss": httpx.Response(200, content=RSS.encode()),
                "https://b.test/atom": httpx.Response(200, content=ATOM.encode()),
            },
            ["https://a.test/rss", "https://b.test/atom"],
            ["总统", "trump"],
        )
        self.assertEqual([i.link for i in items], ["https://a.test/4", "https://a.test/1", "https://b.test/1"])
        by_link = {i.link: i for i in items}
        self.assertEqual(by_link["https://a.test/1"].summary, "屏幕&相机 升级")
        self.assertEqual(by_link["https://a.test/1"].source, "测试站")
        self.assertEqual(by_link["https://b.test/1"].source, "原子站")
        self.assertEqual(len(by_link["https://a.test/4"].summary), 160)
        self.assertIsNotNone(by_link["https://b.test/1"].published_at)

    def test_per_feed_limit(self):
        items = self.news({"https://a.test/rss": httpx.Response(200, content=RSS.encode())}, ["https://a.test/rss"], [], 2)
        self.assertEqual(len(items), 2)

    def test_bad_feed_does_not_drop_others(self):
        items = self.news(
            {
                "https://a.test/rss": httpx.Response(200, content=b"<html><body>nope"),
                "https://c.test/rss": httpx.ConnectError("down"),
                "https://b.test/atom": httpx.Response(200, content=ATOM.encode()),
            },
            ["https://a.test/rss", "https://c.test/rss", "https://b.test/atom"],
            [],
        )
        self.assertEqual([i.link for i in items], ["https://b.test/1"])

    def test_defaults_are_populated(self):
        self.assertGreaterEqual(len(DEFAULT_NEWS_FEEDS), 4)
        self.assertTrue(DEFAULT_NEWS_BLOCKED_TERMS)


if __name__ == "__main__":
    unittest.main()
