"""Custom goal: the top 30 Hacker News stories, using the site's official API instead of scraping HTML.

Run from the repo root:  python python/examples/custom_goal.py
"""
import asyncio
from webaccess import Config, Engine, Goal, Task
from webaccess.sinks import JsonlSink

API = "https://hacker-news.firebaseio.com/v0"


class TopStories(Goal):
    def start(self):
        return [Task(f"{API}/topstories.json")]

    def parse(self, r):
        data = r.json()
        if isinstance(data, list):  # the list of story ids -> one task per story
            yield from (r.follow(f"item/{i}.json") for i in data[:30])
        else:  # one story -> one record
            yield {"title": data.get("title"), "url": data.get("url"), "score": data.get("score")}


if __name__ == "__main__":
    cfg = Config.from_dict({"host": {"max_rate": 5.0}})
    asyncio.run(Engine(cfg, TopStories(), [JsonlSink("out/hn.jsonl")]).run())
