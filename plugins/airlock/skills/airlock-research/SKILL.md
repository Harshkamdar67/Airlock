---
name: airlock-research
description: Deep public-web research with a planned swarm of economical Airlock workers, a gap-filling second wave, quotation checks, and one cited synthesis. Use when the user asks for deep research, a literature or market scan, a comparison across many sources, or "research X thoroughly".
user-invocable: true
argument-hint: "<question> [--depth quick|standard|deep]"
---

<!-- Managed by https://github.com/Harshkamdar67/Airlock -->

# Airlock deep research

Research request: $ARGUMENTS

You are the lead researcher. You plan, dispatch workers, merge their evidence, find the gaps, check the key quotations, and write the report yourself. Workers gather evidence; they never write the report and never decide the conclusion. Everything below applies on top of this session's orchestration guidance, which still wins where they differ.

## 0. Ground rules

- Public web only. Worker tasks contain only public questions, names, and URLs, never repository content, local paths, credentials, tokens, untracked data, or private prompt text. Every worker task includes the exact line `Public web research authorized: yes`.
- Use the session's working web path. When the `airlock-web-tools` tools are listed (`web_search`, `fetch_page`), workers use those. Otherwise they use built-in WebSearch and WebFetch, following the session's effort rule for WebSearch.
- Workers are this session's automatic swarm routes: exact `airlock-luna`, `airlock-luna-fast`, or `airlock-composer`, whichever this session lists. Launch them with `run_in_background: true`, start the whole wave before waiting, and collect every complete response before merging. If the session has no swarm route, run one worker per lane with the enabled worker the guidance allows, or research the lanes yourself in sequence. Never pass a model override to a named airlock Agent.
- Workers do not write files. Their evidence comes back in their final message in the ledger format below. You keep the merged ledger in your own context. Write a file only if the user asks for one.
- Stop and tell the user rather than guess when the question is ambiguous in a way that changes what to research.

## 1. Scope

Read the depth from the request: `quick`, `standard` (default), or `deep`.

If the question is ambiguous about subject, time window, geography, audience, or deliverable, ask at most three short clarifying questions and wait. Otherwise write a brief for yourself:

- the exact question and what a complete answer must contain
- in scope and out of scope
- the recency window that matters (for example "sources from the last 12 months take priority")
- the deliverable shape (report, comparison table, recommendation, annotated source list)

At `deep`, show the brief and the lane plan from step 2 to the user and wait for approval or edits before dispatching any worker.

## 2. Plan the lanes

Classify the question:

- straightforward: one fact or a small set of facts. Use one worker, or answer directly with a few searches. Skip to step 6.
- breadth-first: several independent sub-questions. One lane per sub-question.
- depth-first: one issue that needs several angles. One lane per perspective.

Build lanes that do not overlap and together cover the brief. Draw them from more than one of these axes:

1. sub-questions the answer depends on
2. perspectives or stakeholders (for example regulator, vendor, practitioner, critic, end user)
3. source types: primary sources (official docs, specifications, filings, statistics, standards); academic papers; code, repositories, and changelogs; practitioner discussion (forums, issue trackers, conference talks)
4. one recency sweep over the last 6 to 12 months for changes that older sources miss
5. one contrarian lane at `standard` and `deep`, which looks for evidence against the emerging consensus, failures, retractions, and criticism

Wave size: `quick` 1 to 3 lanes, `standard` 4 to 8, `deep` 8 to 12. Stay under the session's concurrent Agent limit. The upper end is a ceiling, never a target; use it only when the brief needs it. Before dispatching, check the sufficiency test: if every worker did its lane well, would the combined results let you write an excellent answer? If not, fix the lanes, not the wave size.

## 3. Dispatch wave 1

Give each worker a natural, self-contained task built from this template. Fill every part. Name the neighboring lanes so the worker knows what to leave alone.

```
Public web research authorized: yes

Research brief (shared by all workers): <the brief from step 1>

Your lane: <one objective>
Questions to answer:
- <question>
- <question>
Leave to other workers: <the neighboring lanes by name>
Start with: <2 to 4 suggested queries or known authoritative sites>

Method:
- Search broad first, then narrow. When the web_search tool accepts `queries`,
  send several related queries in one call; use `timelimit` for recent material.
- Prefer primary and authoritative sources: official documentation, standards,
  filings, datasets, peer-reviewed papers, maintainers' own statements.
  Distrust content farms, SEO listicles, aggregators that do not link sources,
  unnamed sources, and marketing copy. Say when a source has a stake in the claim.
- Read the pages you cite. A search snippet is not evidence.
- Budget: about <5 | 10 | 15> tool calls, never more than 20. Stop early when new
  sources stop adding claims. If something cannot be found, say so plainly;
  "not found" is a valid result.

Return only this, with no preamble:

CLAIMS
<id> | <claim in one sentence> | "<exact short quote from the page supporting it>" | <URL> | <source type> | <publication date or "undated"> | <H/M/L confidence>
(ids are <lane letter><number>, such as B3; at most 25 claims; one claim per line;
no claim without a quote and a URL you read)

CONFLICTS
<claim id> conflicts with <claim id or source>: <one line>

NOT FOUND
<question you could not answer, and where you looked>

SOURCES CONSIDERED BUT REJECTED
<URL>: <why, in a few words>
```

Budget by depth: `quick` about 5 tool calls per worker, `standard` about 10, `deep` about 15.

## 4. Merge and find gaps

When every wave-1 worker has returned:

1. Merge all CLAIMS into one ledger. Treat two claims as duplicates when they state the same fact; keep one id and record every supporting URL on it.
2. Mark each claim:
   - corroborated: two or more independent sources (not one outlet quoting another)
   - single-source
   - contested: a CONFLICTS line or a contrary claim exists
3. Compare the ledger with the brief. List the gaps: brief items with no claim, NOT FOUND questions that matter, key claims resting on a single weak source, and contested points that decide the answer.

## 5. Wave 2 (targeted)

At `standard` and `deep`, if the gap list is not empty, dispatch a smaller second wave, one worker per gap cluster. Tell each worker exactly what is already established (claim text and URL) so it does not repeat it, and ask for independent confirmation, the missing fact, or the resolution of the conflict. Use the same return format with new ids. Merge as in step 4.

Run at most two waves. Stop earlier when a wave adds little.

## 6. Verify the claims that matter

Pick the claims the conclusion depends on, at most 15. At `standard` and `deep`, dispatch one or two verification workers with the claims split between them. Each verification task lists `claim id | exact quote | URL` and asks the worker to confirm that the quote appears on that page and supports the claim:

- with `airlock-web-tools`, call `fetch_page` with the URL and `find` set to the quote
- otherwise fetch the page and look for the quote

Each verification worker returns one line per claim: `<id> | VERIFIED | NOT ON PAGE | PAGE UNAVAILABLE | QUOTE DOES NOT SUPPORT CLAIM`, with a short note. Drop claims that fail, or downgrade them to unverified in the report. At `quick`, check the two or three most important quotes yourself.

## 7. Write the report

Write it yourself, in one pass, from the merged ledger. Outline first, then write.

- Open with a short direct answer to the question.
- Organize by the answer's logic, not by lane or by worker.
- Every factual sentence cites its evidence as `[n]`, and the sources list maps each `[n]` to the URL. Carry URLs over from the ledger exactly; never retype or reconstruct one from memory.
- State the confidence of important claims and why (corroborated, single-source, contested, verified).
- Include a "Contested or uncertain" section and a "Not found" section when they have content. Do not paper over disagreement between sources.
- Separate evidence from your own inference, and label the inference.
- End with the sources list, marking primary sources, and one line on method: lanes run, waves, claims kept, claims verified.

Save the report to a file only when the user asks, at the path they name.

## Before you deliver

- Every cited URL came from a worker's ledger or your own tool results in this session.
- No claim in the direct answer rests only on an unverified single source without saying so.
- Contested points appear as contested.
- Nothing from the repository or local machine was sent into a worker's public-web task.
