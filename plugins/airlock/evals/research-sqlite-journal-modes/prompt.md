---
description: Standard-depth comparison; checks the worker swarm, the authorization line, and the report shape.
tags: [research, standard, swarm]
runs: 1
max_turns: 100
timeout_seconds: 1800
allowed_tools: [Skill, Agent, Read, Glob, Grep, TodoWrite]
---

Use the airlock-research skill for this: Compare SQLite's write-ahead log (WAL) mode with the default rollback journal: reader and writer concurrency, durability and crash behaviour, checkpointing, and the situations where WAL should not be used.
