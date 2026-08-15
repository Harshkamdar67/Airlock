---
name: airlock-fast
description: Arm this Airlock session for one parent-owned Fast handoff
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash(airlock fast --arm --session-id "${CLAUDE_SESSION_ID}")
---

<!-- Managed by https://github.com/Harshkamdar67/Airlock -->

Run the exact arming command below once. Do not invoke a model, mutate saved or global configuration, start another session, or terminate Claude Code. After it succeeds, tell the user that this session is armed for a one-shot Fast handoff and that they must exit cleanly. Do not claim that the switch already happened.

!`airlock fast --arm --session-id "${CLAUDE_SESSION_ID}"`
