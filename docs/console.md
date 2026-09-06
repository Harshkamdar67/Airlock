# Airlock Console

The console is a visual view of every Airlock session running on your machine. It answers the questions a terminal `airlock status` answers badly, especially at 2 AM when one of your sessions has gone quiet:

1. Which session stopped?
2. Why did it stop?
3. What did the router already try, so you do not repeat it?
4. What still has room, and does that session's conversation fit in it?

Question 4 is a join across routes, cooldowns, and a session's own context size. The console does that join for you instead of leaving it as something you work out in your head.

The console is local-only software. It binds to `127.0.0.1`, reads only what the routers already expose, and never renders prompts, responses, credentials, or provider error bodies.

## Starting it

```bash
airlock console
```

This starts the console server, prints `Airlock Console: http://127.0.0.1:4783`, and opens the address in your browser. Flags:

```bash
airlock console --port 4900   # use a different port
airlock console --no-open     # start the server without opening a browser
airlock console --scan        # also probe for routers that have no registry file yet
airlock console --once        # print one JSON overview to stdout and exit
```

The default port is 4783. If that port is already used by something that is not an Airlock console, the console refuses to start rather than guessing.

Only one live console is allowed for each runtime root. It holds a private, process-lifetime `console.lock` next to the address marker. A second start exits safely; when its marker validates, the launcher opens the existing console instead. Operating-system lock release makes recovery after a hard crash possible. A stale non-secret address marker may remain after a crash, and the next start that obtains the lock replaces it. Different runtime roots are independent.

Whenever the console is serving, on port 4783 or one passed through `--port`, it atomically writes a private, non-secret address marker at its runtime root: `<console runtime root>/console-address.json`. The marker has a schema version, its loopback URL, the console process ID, and a random console instance identity. It carries no CSRF token or router control token. Because a runtime root permits only one live console, that console owns the only marker. On clean shutdown, the console removes only the marker that belongs to its own instance.

## The page

The page has three regions that stay in place, plus a strip that appears only when it has something to say.

- **Sessions rail (left).** Every session on this machine, grouped by the project directory it runs in. A project with a blocked session rises to the top, and inside a project the session that needs a person comes first, then running, idle, and ended sessions by recency. Each row shows the active model with a dot in its provider's colour, the state, how long since the last activity, the profile, a short session id, whether the session is pinned away from its root, and a slim bar showing context used once the session has sent a request. Arrow keys move between rows, Enter opens one, `/` filters the list. When no session is running, the rail explains how to start one and that the console will notice it automatically.
- **Session view (main).** The header shows the project, its working directory, the active model and provider, the state and why, context used against the window, and how long the session has been up. When the session is blocked, a "What unblocks this" block sits right under the header: a plain sentence describing what is blocking the session and what would unblock it, built from the session's routes, cooldowns, chains, and context, never inventing a fact the router does not have. Below that is the timeline: newest events first, grouped by minute, each one written as a plain sentence such as "Sol was rate limited, handed off to Terra after considering 2 routes." The timeline can be filtered by kind (handoffs, overflow, cooldowns, requests, other) and by model. A button copies the session's Markdown incident report.
- **Inspector (right).** Every route available to this session, sorted ready first, then cooling with a countdown, then unavailable, each marked with its provider, tier, context window, whether this session's conversation fits, and whether it is metered. Below that, the handoff chain for the active model, provider headroom (shown as unknown where the console has no source), and the workers this session has spawned with their request counts.
- **Attention strip (top).** Appears only when at least one session is blocked. One line per blocked session; clicking a line selects that session.

### Command palette and shortcuts

`Ctrl+K` (or `Cmd+K`) opens a command palette: jump to any session, filter the timeline, toggle the theme, or copy the current session's report. `?` lists every keyboard shortcut. Every action in the console is reachable from the keyboard; nothing depends on a pointer.

### Light and dark

The console follows your system's light or dark preference by default. You can override that choice from the command palette or the `t` shortcut, and the choice is remembered for the next visit.

Live updates arrive over a server-sent events stream. If the stream drops, the page shows a quiet "reconnecting" line and keeps the last known data on screen; it never pops up a modal for this.

## Session states

- **Running.** A request completed or started in the last five minutes and nothing is blocking the active model.
- **Blocked.** The active model, or its provider, is in cooldown, or the newest event in the last ten minutes is a chain exhaustion or an unresolved context overflow. The console names the exact reason: rate limited, provider cooling, conversation too large, or chain exhausted.
- **Idle.** The session is alive and not blocked, but nothing has happened in the last five minutes.
- **Ended.** The session's owning process is gone. It stays visible for a short while so you can still read its timeline, then it drops off the rail.

## Route status

- **Ready.** The route can serve a request right now.
- **Cooling.** The route is in cooldown; the console shows a countdown to when it should be ready again.
- **Unavailable.** The route cannot serve a request and has no known cooldown to wait out.
- **Metered.** Shown as a separate tag next to a route's status. A metered route costs extra usage even when it is ready.
- **Unknown.** Anything the router has not observed yet, such as a provider's plan headroom before a usage source exists. Unknown always renders as the word "unknown," never as zero.

## Handoff proposals

A handoff proposal is a suggestion to pin one live session to a different model, or to restore its root model after a pin. Either a person or a connected agent can create one; only a person can approve or reject it.

What a person sees for a pending proposal: the session's current root or pin, the proposed target model, whether that route is ready, whether the session's conversation fits the target's context window, a warning if the target is metered, the reason given for the proposal, when it expires, its revision number, and the trail of edits and status changes it has been through.

Approving a proposal applies it immediately: the console pins the session's live router to the target model for future requests. Nothing about the session restarts. The existing conversation, process, worktree, and task keep running exactly as they were; only where the next request goes changes. Restoring the root works the same way in reverse: the wire target is empty and the page shows the root name, and approving it unpins the router.

You can edit a pending, failed, or conflicted proposal before approving it, changing its target model or its reason. Each edit moves the proposal to a new revision. A proposal expires fifteen minutes after it is created or last edited; an expired proposal cannot be approved. Once a proposal is applied, rejected, expired, or failed, it stays visible for an hour so you have a record of what happened, even though it can no longer be acted on.

## The global handoff chain editor

The inspector and command palette include an editor for the global failover chain, the same chains `airlock handoff` manages from the terminal. Editing and saving it here works the same as editing `failover.json` directly: it changes the order future sessions use, never a session that is already running.

Applies to sessions started after this change. Running sessions keep their frozen chains.

## Agents

The console exposes a second read-and-propose channel for agents, separate from the browser. Connect Codex, Claude Code, or any other MCP-capable client to the stdio server named `airlock-console-tools`. A Claude Code session started through `airlock` gets it automatically, alongside `airlock-web-tools`; set `AIRLOCK_CONSOLE_TOOLS=off` before launching to skip it for one session. For any other MCP client, point it at the same stdio server the way you would point it at any other local MCP tool.

Every tool call is answered by the console server itself; the tools never read a router registry file or talk to a router directly. On every call, `airlock-console-tools` rereads the console address marker, so a console started with `--port` works without changing an MCP configuration. If the marker is absent or stale, it falls back to `http://127.0.0.1:4783`. An explicit MCP `--console-url` bypasses marker discovery. If no console is listening at the selected address, the tool returns a clear message telling you to start it with `airlock console`.

The seventeen tools:

- `airlock_list_sessions` lists every live session with its public summary fields.
- `airlock_get_session` gets one session's full detail, including its routes, chains, and events.
- `airlock_get_session_events` lists one session's events, optionally filtered by kind, model, or a starting timestamp.
- `airlock_get_routes` lists route status rows, optionally limited to one session.
- `airlock_get_headroom` lists provider headroom rows the console currently knows about.
- `airlock_get_global_failover_chain` reads the current global chain map and its digest.
- `airlock_list_proposals` lists proposals, optionally filtered by session, kind, or status.
- `airlock_get_proposal` gets one proposal by its ID.
- `airlock_propose_session_handoff` creates a proposal to pin a session to a target model, for a person to review.
- `airlock_propose_restore_root` creates a proposal to restore a session's root model after a pin.
- `airlock_propose_chain_change` creates a proposal to change the global failover chain.
- `airlock_list_history` lists past and present sessions from the transcript index, with the same filters the History page has: project, model, time, and words in the title.
- `airlock_get_history_session` gets one indexed session in full: its facts, the subagents it launched, when it ran through an Airlock router, compactions per model, tool and subagent-type counts, and its latest activity.
- `airlock_list_subagents` lists the subagents one session launched, with type, model, turns, tool calls, peak context, and timing.
- `airlock_get_subagent_feed` reads one subagent's latest activity from its own transcript.
- `airlock_get_usage` returns the Usage page's numbers: activity by day, week, or month, replies by provider, and the rankings.
- `airlock_query_history` answers any counting question by grouping every measure by one dimension under filters. See the next section.

There is no approve tool on any transport, agent-facing HTTP endpoint, or WebMCP handler. An agent can read the console's state and propose a change; only a person clicking Approve in the browser can make that change take effect.

The page itself also registers these same tools through WebMCP when your browser supports it, so an agent connected to your browser session can read and propose the same way a stdio client can.

## Asking the console anything

The History and Usage pages show the reports we thought of. An agent connected through any of the three doors can ask the questions we did not, with `airlock_query_history`. Give it one dimension, any filters, and a measure to sort by, and it returns one row per value of that dimension with every measure it can attribute.

- Dimensions: `session`, `project`, `model`, `provider`, `agent_type`, `tool`, `day`, `week`, `month`, `weekday`, `branch`, `entrypoint`.
- Measures: `sessions`, `prompts`, `replies`, `tool_calls`, `output_tokens`, `compactions`, `agent_launches`, `peak_context_max`. A measure the dimension cannot attribute is null, never zero: a tool row has no prompts, a model row has no subagent launches.
- Filters: `project` (exact name), `model`, `agent_type`, `tool`, `branch` (substrings), `provider` and `entrypoint` (exact), `q` (words in the title, project, branch, or session id), and `since` and `until`. Filters select whole sessions; `since` and `until` then select the days inside them.

Some questions and the call that answers them:

- Which model compacted the most in project X: `{"dimension": "model", "project": "X", "order_by": "compactions"}`.
- Which weekday do I work most: `{"dimension": "weekday", "order_by": "prompts"}`.
- Which subagent types has the sales branch used: `{"dimension": "agent_type", "branch": "sales"}`.
- Which sessions this week hit the largest context: `{"dimension": "session", "since": "<Monday>", "order_by": "peak_context_max"}`, then `airlock_get_history_session` on the row's `id`.
- How much of OpenAI's work was tool calls last month: `{"dimension": "provider", "since": ..., "until": ...}`.

A compaction is attributed to the model that replied last before it, because that is the model whose context filled. The same query is available to scripts at `GET /api/query` with the same parameter names. Everything is a count of things that happened in the transcripts on this machine; the console does not know provider prices.

The index that answers these lives at `console-history.json` under the console runtime root. Changing what it records rebuilds it from the transcripts on the next start; on a machine with a few gigabytes of transcripts that takes some minutes in the background, and answers are partial until it finishes.

## The Markdown incident report

Every session has a report you can export as Markdown: its state, active model, context, routes, recent handoffs, and timeline, written in the same plain sentences the page uses. Use the copy button in the session view, or fetch `GET /api/sessions/{id}/report.md` directly from a script, to paste a session's story into an issue or a chat message without screenshotting the page.

## Plain Claude Code sessions

A session started with `claude` instead of `airlock` has no Airlock router, so nothing registers it. The console still lists it, read-only, so the rail answers "what is running on this machine" for every session. It finds these sessions through the transcript files Claude Code keeps under `~/.claude/projects`, reading only the tail of each file. The number of plain Claude Code processes alive on the machine caps how many transcripts are listed, so a session that was closed does not linger.

From that tail the console keeps: the session id, the working directory, the time of the last record, the model named in the last reply, the git branch, the title Claude Code wrote for the conversation, the token usage of the last reply, and a short activity feed of the latest twenty turns. The token usage is the session's live context size, which is why a plain session shows a real context bar. The activity feed is one line per prompt, reply, or tool call: the time, who spoke, and a one-line preview cut at 160 characters, or the tool name plus a short hint such as the Bash description or the file name. Subagent turns and thinking are left out. Nothing leaves the machine, and nothing beyond those lines is retained. Set `AIRLOCK_CONSOLE_PREVIEWS=off` before starting the console to keep the shape of the feed but drop every preview.

A routed Airlock session has a transcript too, because it is still Claude Code underneath. The console matches it by working directory and gives the routed session the same title, branch, and activity feed, and uses the transcript's usage as the context size whenever the router has not observed a request yet. Router facts always win when both exist.

A transcript whose last reply came from a GPT or Grok model cannot be plain Claude Code, so the console labels that session with the router-less Airlock profile it must be on, "OpenAI only" or "Grok only". Those profiles talk to the local proxy directly and have no session router, so they are listed the same read-only way; a hybrid profile is what gives a session routes and control.

In the rail a plain session carries the profile "Claude Code, not routed". Its page explains that Airlock is not routing it, shows no routes or timeline, and offers no handoff or pin. A proposal against it, from the page or from an agent tool, is refused with `not_routed`. Start the session with `airlock` in the same directory to get failover, handoffs, and control. Set `AIRLOCK_CONSOLE_NATIVE=off` before `airlock console` to list only routed sessions.

## Session history

The History page, the second tab at the top, lists every session this machine has run, past or present, from the same transcripts. Filter by project, by model, by how recently the session was active, or by words in its title, branch, or directory. Each row shows the project, Claude Code's title for the conversation, the main model with a count of any others it used, when it was last active, how many prompts and tool calls it made, how many times it was compacted, its peak context against the model's window, and how many subagents it spawned. A session that is still running carries a live tag and a button to jump to its live page.

Opening a session shows the same totals with the peak context bar, then when the session ran through an Airlock router. Those periods come from a small launch log the router appends to when it starts and stops, so they are exact from this Airlock version on; for earlier history the page can only say that a reply on a GPT or Grok model proves an Airlock period. Below that are the session's subagents, each with its type, description, model, turn and tool counts, peak context, and a fold-out feed of its own transcript, and finally the session's recent activity.

The index behind this page is incremental. Every transcript is read once from its byte offset onward, so a large history costs one pass of a few seconds per hundred megabytes and then only new lines. The index keeps counts, timestamps, titles, identifiers, and model names, and is written to a private file under the console runtime root so a restart does not repeat the pass. The launch log holds the router's start and stop times, working directory, profile, and root model, and nothing else.

## Usage over time

The Usage page, the third tab, turns the same index into counts over time: sessions active, prompts, replies, tool calls, output tokens, and compactions per day, week, or month, with a cumulative option, filtered by range, project, and model. Each chart carries one measure on one axis. Replies by provider is the one stacked chart, in a fixed colour order with a legend and a table view underneath. Rankings show the subagent types used most, the tools called most, models by replies, projects by prompts, and the largest contexts reached. Everything is a count of something that happened in a transcript; nothing on the page is a price, because Airlock does not know provider prices.

## Honest limits

Approval requires a person to review and press Approve in Airlock Console. This separates normal agent tool access from the approval path. It is not a security boundary against software running with your local account privileges. Such software can read local files, invoke the CLI, or automate the browser.

The console never restarts, signals, or writes to a router or a session outside of pinning and unpinning through an approved proposal or a direct pin from the browser. It never renders a control token, a CSRF token, an account identifier, raw upstream text, or an event field outside its documented allowlist.

## Troubleshooting

**Port in use.** `airlock console` refuses to start if port 4783 (or the port you passed with `--port`) is already used by something that does not answer as an Airlock console. Stop the other process, or start the console on a different port with `--port`. A second console for the same runtime root is refused even on a different port; its launcher opens the existing console when the address marker validates.

**The console shows no sessions.** The console only sees sessions that wrote a registry file, or that `--scan` finds by probing local ports. Confirm you started the session with `airlock hybrid`, `airlock om`, or another profile that starts a session router; an OpenAI-only or Anthropic-only session with no router has nothing for the console to discover. Restart the console with `--scan` if a session was already running before the console started.

**A session shows as ended when it is still running.** The console marks a session ended when its owning process is gone or its router no longer answers `/healthz` with a matching instance ID. If the session is genuinely still running, check that its router process has not crashed or been replaced; a session that outlives its own router will show as ended until a new router registers.

**Agent tools say the console is not running.** Start it with `airlock console`, then retry the tool call. `airlock-console-tools` rereads the console address marker on every call, so it follows a console started with `--port` automatically. When the marker is absent or stale, it falls back to `127.0.0.1:4783`. An MCP client configured with an explicit `--console-url` bypasses that discovery, so make sure it names the port where the console is serving.
