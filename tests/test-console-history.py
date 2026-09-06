#!/usr/bin/env python3
"""Session history index: counts, peaks, compactions, subagents, launch periods."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("airlock_console_history", ROOT / "bin" / "airlock_console_history.py")
assert SPEC is not None and SPEC.loader is not None
hx = importlib.util.module_from_spec(SPEC)
sys.modules["airlock_console_history"] = hx
SPEC.loader.exec_module(hx)

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
SID = "0f3c9d2e-aaaa-4bbb-8ccc-1234567890ab"
CWD = r"C:\work\alpha"


def stamp(seconds_ago: float) -> str:
    return hx.format_timestamp(NOW - timedelta(seconds=seconds_ago))


def user(text: str, age: float, **extra: object) -> dict:
    return {"type": "user", "sessionId": SID, "cwd": CWD, "timestamp": stamp(age), "entrypoint": "cli",
            "version": "2.1.263", "gitBranch": "main", "message": {"role": "user", "content": text}, **extra}


def assistant(blocks: list, age: float, *, usage: dict | None = None, model: str = "claude-fable-5-1", **extra: object) -> dict:
    message = {"role": "assistant", "model": model, "content": blocks}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "sessionId": SID, "cwd": CWD, "timestamp": stamp(age), "entrypoint": "cli",
            "version": "2.1.263", "gitBranch": "main", "message": message, **extra}


class HistoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.projects = base / "projects"
        self.runtime = base / "runtime"
        self.projects.mkdir()
        self.runtime.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, folder: str, name: str, records: list[dict]) -> Path:
        directory = self.projects / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return path

    def index(self) -> "hx.HistoryIndex":
        return hx.HistoryIndex(self.projects, self.runtime, clock=lambda: NOW, window_for=lambda m: 1_000_000 if (m or "").startswith("claude") else None)

    def rich_session(self) -> Path:
        return self.write("C--work-alpha", SID, [
            {"type": "ai-title", "sessionId": SID, "aiTitle": "Fix the checkout retry loop"},
            user("first prompt", 3600),
            assistant([{"type": "text", "text": "ok"}], 3590, usage={"input_tokens": 1000, "cache_read_input_tokens": 200000}),
            assistant([
                {"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {"subagent_type": "airlock-sol", "description": "Fix tests", "run_in_background": True, "prompt": "secret"}},
                {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": "ls"}},
            ], 3580, usage={"input_tokens": 500, "cache_read_input_tokens": 500000, "cache_creation_input_tokens": 20000, "output_tokens": 300}),
            {"type": "user", "sessionId": SID, "cwd": CWD, "timestamp": stamp(3570), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "launched"}]},
             "toolUseResult": {"agentId": "a1b2c3d4e5f6a7b8c", "resolvedModel": "gpt-5.6-sol", "status": "async_launched", "isAsync": True, "description": "Fix tests", "prompt": "secret"}},
            {"type": "system", "subtype": "compact_boundary", "sessionId": SID, "timestamp": stamp(1800)},
            user("This session is being continued", 1790, isCompactSummary=True),
            assistant([{"type": "text", "text": "after compaction"}], 1780, usage={"input_tokens": 30000}, model="<synthetic>"),
            assistant([{"type": "text", "text": "after compaction"}], 1770, usage={"input_tokens": 30000}),
            {"type": "user", "sessionId": SID, "cwd": CWD, "timestamp": stamp(100), "isSidechain": True, "message": {"role": "user", "content": "subagent noise"}},
            user("second prompt", 60),
        ])


class ScanTests(HistoryTestCase):
    def test_counts_peaks_compactions_and_agents(self) -> None:
        path = self.rich_session()
        entry = hx.scan_transcript(path, None)
        assert entry is not None
        stats = entry["stats"]
        self.assertEqual(stats["session_id"], SID)
        self.assertEqual(stats["title"], "Fix the checkout retry loop")
        self.assertEqual(stats["branch"], "main")
        self.assertEqual(stats["prompts"], 2)
        self.assertEqual(stats["replies"], 4)
        self.assertEqual(stats["tool_calls"], 2)
        self.assertEqual(stats["agent_calls"], 1)
        self.assertEqual(stats["compactions"], 1)
        self.assertEqual(stats["compact_summaries"], 1)
        self.assertEqual(stats["peak_context"], 520500)
        self.assertEqual(stats["last_context"], 30000)
        self.assertEqual(stats["output_tokens"], 300)
        self.assertEqual(stats["models"], {"claude-fable-5-1": 3})
        self.assertEqual(stats["first_at"], stamp(3600))
        self.assertEqual(stats["last_at"], stamp(60))
        agent = stats["agents"]["a1b2c3d4e5f6a7b8c"]
        self.assertEqual(agent["agent_type"], "airlock-sol")
        self.assertEqual(agent["description"], "Fix tests")
        self.assertEqual(agent["model"], "gpt-5.6-sol")
        self.assertIs(agent["background"], True)
        self.assertEqual(agent["status"], "async_launched")
        # Prompts and tool arguments never reach the index.
        self.assertNotIn("secret", json.dumps(stats))
        self.assertNotIn("subagent noise", json.dumps(stats))

    def test_scan_is_incremental_and_restarts_after_truncation(self) -> None:
        path = self.write("C--work-alpha", SID, [user("one", 300), assistant([{"type": "text", "text": "a"}], 290, usage={"input_tokens": 10})])
        first = hx.scan_transcript(path, None)
        assert first is not None
        self.assertEqual(first["stats"]["prompts"], 1)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(user("two", 200)) + "\n")
            handle.write('{"type": "user", "partial')  # no newline yet
        second = hx.scan_transcript(path, first)
        assert second is not None
        self.assertEqual(second["stats"]["prompts"], 2)
        self.assertTrue(second["carry"].startswith('{"type": "user", "partial'))
        with path.open("a", encoding="utf-8") as handle:
            handle.write('"}\n')
        third = hx.scan_transcript(path, second)
        assert third is not None
        self.assertEqual(third["stats"]["prompts"], 2)
        self.assertEqual(third["carry"], "")
        path.write_text(json.dumps(user("fresh", 10)) + "\n", encoding="utf-8")
        fourth = hx.scan_transcript(path, third)
        assert fourth is not None
        self.assertEqual(fourth["stats"]["prompts"], 1)

    def test_own_chain_counts_a_subagent_transcript(self) -> None:
        path = self.write("C--work-alpha", "agent-x", [
            {**user("go", 50), "isSidechain": True, "agentId": "x"},
            {**assistant([{"type": "text", "text": "done"}], 40, usage={"input_tokens": 700}), "isSidechain": True, "agentId": "x"},
        ])
        plain = hx.scan_transcript(path, None)
        own = hx.scan_transcript(path, None, own_chain=True)
        assert plain is not None and own is not None
        self.assertEqual(plain["stats"]["replies"], 0)
        self.assertEqual(own["stats"]["replies"], 1)
        self.assertEqual(own["stats"]["peak_context"], 700)


class IndexTests(HistoryTestCase):
    def test_index_lists_filters_and_persists(self) -> None:
        self.rich_session()
        other = "12345678-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        self.write("C--work-bravo", other, [
            {**user("hi", 90000), "sessionId": other, "cwd": r"C:\work\bravo"},
            {**assistant([{"type": "text", "text": "hey"}], 89990, usage={"input_tokens": 5}, model="gpt-5.6-luna"), "sessionId": other, "cwd": r"C:\work\bravo"},
        ])
        index = self.index()
        scanned = index.refresh()
        self.assertGreater(scanned, 0)
        items = index.summaries()
        self.assertEqual([item["project"] for item in items], ["alpha", "bravo"])
        alpha = items[0]
        self.assertEqual(alpha["id"], "hx-0f3c9d2e-aaa")
        self.assertEqual(alpha["peak_context"], 520500)
        self.assertEqual(alpha["window"], 1_000_000)
        self.assertEqual(alpha["compactions"], 1)
        self.assertEqual(alpha["subagents"], 1)
        self.assertEqual(alpha["primary_model"], "claude-fable-5-1")
        self.assertEqual([i["project"] for i in index.filtered(model="luna")], ["bravo"])
        self.assertEqual([i["project"] for i in index.filtered(query="checkout")], ["alpha"])
        self.assertEqual([i["project"] for i in index.filtered(since=NOW - timedelta(hours=2))], ["alpha"])
        self.assertEqual([i["project"] for i in index.filtered(project="bravo")], ["bravo"])
        # Nothing new: no bytes scanned, and a fresh index reloads from the cache.
        self.assertEqual(index.refresh(), 0)
        reloaded = self.index()
        self.assertEqual(len(reloaded.summaries()), 2)
        self.assertEqual(reloaded.refresh(), 0)
        mode = os.stat(index.cache_path).st_mode & 0o777
        if os.name != "nt":
            self.assertEqual(mode, 0o600)

    def test_window_is_the_largest_among_a_sessions_models(self) -> None:
        # Most replies came from a 272k model, but the peak happened on the
        # 1M root, so the window reported is the 1M one. When the peak still
        # exceeds every known window, the window is unknown, not over 100%.
        big = "aaaaaaaa-1111-4222-8333-444444444444"
        self.write("C--work-charlie", big, [
            {**user("hi", 90000), "sessionId": big, "cwd": r"C:\work\charlie"},
            {**assistant([{"type": "text", "text": "a"}], 89990, usage={"input_tokens": 900_000}, model="claude-fable-5-1"), "sessionId": big, "cwd": r"C:\work\charlie"},
            {**assistant([{"type": "text", "text": "b"}], 89980, usage={"input_tokens": 5}, model="gpt-5.6-luna"), "sessionId": big, "cwd": r"C:\work\charlie"},
            {**assistant([{"type": "text", "text": "c"}], 89970, usage={"input_tokens": 5}, model="gpt-5.6-luna"), "sessionId": big, "cwd": r"C:\work\charlie"},
        ])
        over = "bbbbbbbb-1111-4222-8333-444444444444"
        self.write("C--work-delta", over, [
            {**user("hi", 90000), "sessionId": over, "cwd": r"C:\work\delta"},
            {**assistant([{"type": "text", "text": "a"}], 89990, usage={"input_tokens": 300_000}, model="gpt-5.6-luna"), "sessionId": over, "cwd": r"C:\work\delta"},
        ])
        index = hx.HistoryIndex(
            self.projects, self.runtime, clock=lambda: NOW,
            window_for=lambda m: 1_000_000 if (m or "").startswith("claude") else 272_000,
        )
        index.refresh()
        by_project = {item["project"]: item for item in index.summaries()}
        self.assertEqual(by_project["charlie"]["primary_model"], "gpt-5.6-luna")
        self.assertEqual(by_project["charlie"]["window"], 1_000_000)
        self.assertEqual(by_project["charlie"]["peak_context"], 900_000)
        self.assertIsNone(by_project["delta"]["window"])
        self.assertEqual(by_project["delta"]["peak_context"], 300_000)

    def test_detail_periods_and_subagents(self) -> None:
        path = self.rich_session()
        agents = path.with_suffix("") / "subagents"
        agents.mkdir(parents=True)
        (agents / "agent-a1b2c3d4e5f6a7b8c.meta.json").write_text(json.dumps({"agentType": "airlock-sol", "description": "Fix tests", "spawnDepth": 1}), encoding="utf-8")
        with (agents / "agent-a1b2c3d4e5f6a7b8c.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({**user("do it", 3000), "isSidechain": True, "agentId": "a1b2c3d4e5f6a7b8c"}) + "\n")
            handle.write(json.dumps({**assistant([{"type": "text", "text": "fixed"}], 2900, usage={"input_tokens": 4000}, model="gpt-5.6-sol"), "isSidechain": True, "agentId": "a1b2c3d4e5f6a7b8c"}) + "\n")
        launches = self.runtime / hx.LAUNCH_LOG_FILENAME
        with launches.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"schema_version": 1, "event": "start", "at": stamp(3700), "instance_id": "r1", "workdir": "C:/work/alpha", "profile": "hybrid-openai-root", "root_model": "gpt-5.6-sol"}) + "\n")
            handle.write(json.dumps({"schema_version": 1, "event": "stop", "at": stamp(2000), "instance_id": "r1", "workdir": "C:/work/alpha"}) + "\n")
            handle.write(json.dumps({"schema_version": 1, "event": "start", "at": stamp(500), "instance_id": "r2", "workdir": "C:/work/alpha", "profile": "hybrid-anthropic-root", "root_model": "claude-opus-5[1m]"}) + "\n")
            handle.write(json.dumps({"schema_version": 1, "event": "start", "at": stamp(400), "instance_id": "r3", "workdir": "C:/work/other", "profile": "hybrid-openai-root", "root_model": "gpt-5.6-sol"}) + "\n")
        index = self.index()
        index.refresh()
        detail = index.detail("hx-0f3c9d2e-aaa")
        assert detail is not None
        self.assertEqual([a["agent_type"] for a in detail["agents"]], ["airlock-sol"])
        self.assertEqual(detail["periods"], [
            {"kind": "airlock", "from": stamp(3700), "to": stamp(2000), "profile": "hybrid-openai-root", "root_model": "gpt-5.6-sol", "open": False},
            {"kind": "airlock", "from": stamp(500), "to": stamp(0), "profile": "hybrid-anthropic-root", "root_model": "claude-opus-5[1m]", "open": True},
        ])
        self.assertIs(detail["airlock_inferred"], False)
        subagents = index.subagents("hx-0f3c9d2e-aaa")
        self.assertEqual(len(subagents), 1)
        sub = subagents[0]
        self.assertEqual(sub["id"], "a1b2c3d4e5f6a7b8c")
        self.assertEqual(sub["agent_type"], "airlock-sol")
        self.assertEqual(sub["model"], "gpt-5.6-sol")
        self.assertEqual(sub["replies"], 1)
        self.assertEqual(sub["peak_context"], 4000)
        self.assertEqual(sub["finished_at"], stamp(2900))
        self.assertEqual(sub["depth"], 1)
        self.assertEqual(index.subagent_path("hx-0f3c9d2e-aaa", "a1b2c3d4e5f6a7b8c"), agents / "agent-a1b2c3d4e5f6a7b8c.jsonl")
        self.assertIsNone(index.subagent_path("hx-0f3c9d2e-aaa", "../escape"))
        self.assertEqual(index.stats_for_session(SID)["id"], "hx-0f3c9d2e-aaa")
        self.assertEqual(index.stats_for_workdir("c:/WORK/alpha/")["id"], "hx-0f3c9d2e-aaa")


class QueryTests(HistoryTestCase):
    def two_sessions(self) -> "hx.HistoryIndex":
        self.rich_session()
        other = "12345678-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        # Every record carries the branch and entrypoint, as Claude Code writes them.
        bravo = {"sessionId": other, "cwd": r"C:\work\bravo", "gitBranch": "feature/x", "entrypoint": "sdk-cli"}
        self.write("C--work-bravo", other, [
            {**user("hi", 90000), **bravo},
            {**assistant([{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a"}}], 89990,
                         usage={"input_tokens": 5, "output_tokens": 40}, model="gpt-5.6-luna"), **bravo},
            {"type": "system", "subtype": "compact_boundary", "sessionId": other, "timestamp": stamp(89980)},
            {**assistant([{"type": "text", "text": "again"}], 89970, usage={"input_tokens": 5, "output_tokens": 10}, model="gpt-5.6-luna"), **bravo},
        ])
        index = self.index()
        index.refresh()
        return index

    def test_compactions_are_attributed_to_the_last_model(self) -> None:
        index = self.two_sessions()
        alpha = index.detail("hx-0f3c9d2e-aaa")
        assert alpha is not None
        self.assertEqual(alpha["compactions_by_model"], {"claude-fable-5-1": 1})
        self.assertEqual(alpha["tools"], {"Agent": 1, "Bash": 1})
        self.assertEqual(alpha["agent_types"], {"airlock-sol": 1})
        by_model = index.query(dimension="model", order_by="compactions")
        rows = {row["key"]: row for row in by_model["rows"]}
        self.assertEqual(rows["claude-fable-5-1"]["compactions"], 1)
        self.assertEqual(rows["gpt-5.6-luna"]["compactions"], 1)
        self.assertEqual(rows["gpt-5.6-luna"]["replies"], 2)
        self.assertEqual(rows["gpt-5.6-luna"]["tool_calls"], 1)
        self.assertEqual(rows["gpt-5.6-luna"]["output_tokens"], 50)
        self.assertEqual(rows["claude-fable-5-1"]["tool_calls"], 2)
        self.assertIsNone(rows["claude-fable-5-1"]["prompts"])
        self.assertIsNone(rows["claude-fable-5-1"]["agent_launches"])
        # "Which model compacted most in project bravo": one row, luna.
        bravo = index.query(dimension="model", project="bravo", order_by="compactions")
        self.assertEqual([row["key"] for row in bravo["rows"]], ["gpt-5.6-luna"])
        self.assertEqual(bravo["sessions_matched"], 1)
        self.assertEqual(bravo["filters"]["project"], "bravo")

    def test_every_dimension_answers(self) -> None:
        index = self.two_sessions()
        for dimension in hx.QUERY_DIMENSIONS:
            with self.subTest(dimension=dimension):
                result = index.query(dimension=dimension)
                self.assertEqual(result["dimension"], dimension)
                self.assertGreater(len(result["rows"]), 0)
                for row in result["rows"]:
                    self.assertEqual(set(row), {"key", "id", "label", *hx.QUERY_MEASURES})
                    self.assertGreaterEqual(row["sessions"], 1)
        by_project = {row["key"]: row for row in index.query(dimension="project")["rows"]}
        self.assertEqual(by_project["alpha"]["prompts"], 2)
        self.assertEqual(by_project["alpha"]["agent_launches"], 1)
        self.assertEqual(by_project["alpha"]["peak_context_max"], 520500)
        self.assertEqual(by_project["bravo"]["compactions"], 1)
        by_provider = {row["key"]: row for row in index.query(dimension="provider")["rows"]}
        self.assertEqual(by_provider["openai"]["replies"], 2)
        self.assertEqual(by_provider["anthropic"]["replies"], 3)
        by_agent = index.query(dimension="agent_type")["rows"]
        self.assertEqual(by_agent[0], {**by_agent[0], "key": "airlock-sol", "agent_launches": 1, "prompts": None})
        by_tool = {row["key"]: row["tool_calls"] for row in index.query(dimension="tool")["rows"]}
        self.assertEqual(by_tool, {"Agent": 1, "Bash": 1, "Read": 1})
        sessions = index.query(dimension="session")["rows"]
        self.assertEqual(sessions[0]["id"], "hx-0f3c9d2e-aaa")
        self.assertEqual(sessions[0]["label"], "Fix the checkout retry loop")
        days = index.query(dimension="day")
        self.assertEqual(days["order_by"], "key")
        self.assertFalse(days["descending"])
        self.assertEqual([row["key"] for row in days["rows"]], sorted(row["key"] for row in days["rows"]))
        self.assertEqual({row["key"] for row in index.query(dimension="entrypoint")["rows"]}, {"sdk-cli", "cli"})
        self.assertEqual({row["key"] for row in index.query(dimension="branch")["rows"]}, {"feature/x", "main"})
        weekdays = {row["key"] for row in index.query(dimension="weekday")["rows"]}
        self.assertTrue(weekdays <= {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"})

    def test_filters_ordering_and_limits(self) -> None:
        index = self.two_sessions()
        self.assertEqual([r["key"] for r in index.query(dimension="project", provider="openai")["rows"]], ["bravo"])
        self.assertEqual([r["key"] for r in index.query(dimension="project", agent_type="sol")["rows"]], ["alpha"])
        self.assertEqual([r["key"] for r in index.query(dimension="project", tool="read")["rows"]], ["bravo"])
        self.assertEqual([r["key"] for r in index.query(dimension="project", branch="feature")["rows"]], ["bravo"])
        self.assertEqual([r["key"] for r in index.query(dimension="project", entrypoint="sdk-cli")["rows"]], ["bravo"])
        self.assertEqual([r["key"] for r in index.query(dimension="project", query="checkout")["rows"]], ["alpha"])
        self.assertEqual([r["key"] for r in index.query(dimension="project", since=NOW - timedelta(hours=2))["rows"]], ["alpha"])
        self.assertEqual([r["key"] for r in index.query(dimension="project", model="luna")["rows"]], ["bravo"])
        ascending = index.query(dimension="project", order_by="prompts", descending=False)["rows"]
        self.assertEqual([r["key"] for r in ascending], ["bravo", "alpha"])
        limited = index.query(dimension="tool", limit=1)
        self.assertEqual(len(limited["rows"]), 1)
        self.assertTrue(limited["truncated"])
        self.assertEqual(limited["total_rows"], 3)
        with self.assertRaises(ValueError):
            index.query(dimension="user")
        with self.assertRaises(ValueError):
            index.query(dimension="model", order_by="cost")
        empty = index.query(dimension="project", project="nope")
        self.assertEqual(empty["rows"], [])
        self.assertEqual(empty["sessions_matched"], 0)


class UsageTests(HistoryTestCase):
    def test_usage_series_rankings_and_filters(self) -> None:
        self.rich_session()
        other = "12345678-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        self.write("C--work-bravo", other, [
            {**user("hi", 90000), "sessionId": other, "cwd": r"C:\work\bravo"},
            {**assistant([{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a"}}], 89990,
                         usage={"input_tokens": 5, "output_tokens": 40}, model="gpt-5.6-luna"), "sessionId": other, "cwd": r"C:\work\bravo"},
        ])
        index = self.index()
        index.refresh()
        report = index.usage(group="day")
        self.assertEqual(report["group"], "day")
        totals = report["totals"]
        self.assertEqual(totals["sessions"], 2)
        self.assertEqual(totals["prompts"], 3)
        self.assertEqual(totals["replies"], 5)
        self.assertEqual(totals["tool_calls"], 3)
        self.assertEqual(totals["output_tokens"], 340)
        self.assertEqual(totals["compactions"], 1)
        self.assertEqual(totals["subagents"], 1)
        self.assertEqual(totals["peak_context_max"], 520500)
        # One row per day, oldest first, sessions counted per day.
        days = [row["period"] for row in report["series"]]
        self.assertEqual(days, sorted(days))
        self.assertEqual(len(days), 2)
        today = report["series"][-1]
        self.assertEqual(today["sessions"], 1)
        self.assertEqual(today["prompts"], 2)
        self.assertEqual(today["by_provider"], {"anthropic": 3})
        yesterday = report["series"][0]
        self.assertEqual(yesterday["by_provider"], {"openai": 1})
        self.assertEqual(report["agent_types"], [{"name": "airlock-sol", "count": 1}])
        self.assertEqual([t["name"] for t in report["tools"]], ["Agent", "Bash", "Read"])
        self.assertEqual([p["name"] for p in report["projects"]], ["alpha", "bravo"])
        self.assertEqual(report["models"][0], {"name": "claude-fable-5-1", "replies": 3, "provider": "anthropic"})
        self.assertEqual(report["peaks"][0]["peak_context"], 520500)
        # alpha peaked at 52% of a 1M window; bravo has no known window.
        self.assertEqual(report["peak_buckets"], {"under_25": 0, "25_to_50": 0, "50_to_75": 1, "over_75": 0, "unknown": 1})
        # Month grouping folds both days into one bucket; filters narrow the rest.
        monthly = index.usage(group="month")
        self.assertEqual(len(monthly["series"]), 1)
        self.assertEqual(monthly["series"][0]["period"], NOW.strftime("%Y-%m"))
        weekly = index.usage(group="week")
        self.assertTrue(all("-W" in row["period"] for row in weekly["series"]))
        only_bravo = index.usage(project="bravo")
        self.assertEqual(only_bravo["totals"]["sessions"], 1)
        self.assertEqual(only_bravo["tools"], [{"name": "Read", "count": 1}])
        recent = index.usage(since=NOW - timedelta(hours=6))
        self.assertEqual(recent["totals"]["sessions"], 1)
        self.assertEqual(recent["totals"]["prompts"], 2)
        by_model = index.usage(model="luna")
        self.assertEqual(by_model["totals"]["sessions"], 1)


if __name__ == "__main__":
    unittest.main()
