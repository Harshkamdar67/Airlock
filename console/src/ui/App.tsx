import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { Rail } from './Rail';
import { SessionView } from './SessionView';
import { Inspector } from './Inspector';
import { Palette, Shortcuts, type PaletteCommand } from './Overlays';
import { ProposalPanel } from './ProposalPanel';
import { ChainEditor } from './ChainEditor';
import {
  ApiError,
  approveProposal,
  createHandoffProposal,
  editProposal,
  getChains,
  getProposal,
  getToolManifest,
  rejectProposal,
  saveChains,
} from '../lib/control';
import { humanApiAvailable } from '../lib/csrf';
import { registerTools } from '../lib/webmcp';
import type { ChainMap } from '../lib/chains';
import { Glyph, Sprite, Status } from './Glyph';
import { getOverview, getSession, openOverviewStream, type StreamState } from '../lib/api';
import { filterSessions, groupSessionsByProject, railOrder } from '../lib/group';
import { siteHasNewerBuild } from '../lib/build';
import { HistoryView } from './HistoryView';
import { UsageView } from './UsageView';
import { buildReport, copyText } from '../lib/report';
import {
  filtersForDetail,
  filtersForSessionSwitch,
  mayApplyInitialOverview,
} from '../lib/state';
import { applyTheme, nextTheme, readTheme, THEME_LABEL, type ThemePref } from '../lib/theme';
import { relativeTime } from '../lib/time';
import { deriveShortName, sessionTitle, titled } from '../lib/names';
import {
  KIND_GROUPS,
  KIND_GROUP_TITLES,
  sessionState,
  type KindGroup,
} from '../lib/vocab';
import type { TimelineFilters } from '../lib/sentences';
import { eventModels } from '../lib/sentences';
import type {
  ChainChangeProposal,
  ChainSnapshot,
  Overview,
  RouteStatus,
  SessionDetail,
  SessionHandoffProposal,
} from '../types';

interface DetailFailure {
  id: string;
  message: string;
}

function shortNameOf(detail: SessionDetail, model: string): string {
  return detail.routes?.find((r) => r.model === model)?.short_name ?? model;
}

function hashSession(): string | null {
  const m = /#session=([\w.-]+)/.exec(location.hash);
  return m ? m[1] : null;
}

function Skeleton() {
  return (
    <div class="skeleton">
      <h1 class="sr-only">Loading Airlock sessions</h1>
      <div aria-hidden="true">
        <div class="sk sk-title" />
        <div class="sk sk-short" />
        <div class="sk sk-line" />
        <div class="sk sk-line" />
        <div class="sk sk-short" />
      </div>
    </div>
  );
}

function EmptyMachine() {
  return (
    <main class="main" id="session-view">
      <div class="empty">
        <h1>No Airlock sessions are running</h1>
        <p>
          Start one in any project directory and this page will pick it up within a second or two.
          Nothing needs restarting here.
        </p>
        <pre>
          <code>
            cd your-project{'\n'}
            airlock{'          '}# the saved profile{'\n'}
            airlock hybrid opus{'  '}# or name a root directly
          </code>
        </pre>
        <p class="hint">
          The console watches the session registry that every router writes when it starts and
          removes when it exits. It never writes to a router.
        </p>
      </div>
    </main>
  );
}

export function App() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(hashSession());
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState<TimelineFilters>({ group: 'all', model: null });
  const [theme, setTheme] = useState<ThemePref>(readTheme());
  const [stream, setStream] = useState<StreamState>('connecting');
  const streamStateRef = useRef<StreamState>('connecting');
  // Live shows what is running now; History is every session on this machine;
  // Usage is the counts over time. The hash remembers the page across reloads.
  type View = 'live' | 'history' | 'usage';
  const [view, setViewState] = useState<View>(() => {
    if (typeof location === 'undefined') return 'live';
    if (location.hash === '#history') return 'history';
    if (location.hash === '#usage') return 'usage';
    return 'live';
  });
  const setView = useCallback((next: View) => {
    setViewState(next);
    if (typeof location === 'undefined') return;
    if (next === 'live') {
      if (location.hash === '#history' || location.hash === '#usage') location.hash = '';
    } else {
      location.hash = `#${next}`;
    }
  }, []);
  // A hash typed or pasted into the address bar switches the page too.
  useEffect(() => {
    const onHash = () => {
      if (location.hash === '#history') setViewState('history');
      else if (location.hash === '#usage') setViewState('usage');
      else if (location.hash === '' || location.hash.startsWith('#session=')) setViewState('live');
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const [retryIn, setRetryIn] = useState<number | undefined>(undefined);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<DetailFailure | null>(null);
  const [detailRetry, setDetailRetry] = useState(0);
  const [now, setNow] = useState(Date.now());

  // Phase 2 control state. Kept beside the read state so an SSE frame can
  // refresh both without a second source of truth.
  const [chains, setChains] = useState<ChainSnapshot | null>(null);
  const [chainError, setChainError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [chainBusy, setChainBusy] = useState(false);
  const [controlNotice, setControlNotice] = useState('');
  const [toolCount, setToolCount] = useState(0);
  const [chainEditorOpen, setChainEditorOpen] = useState(false);
  const [chainProposal, setChainProposal] = useState<ChainChangeProposal | null>(null);
  const humanApi = useMemo(() => humanApiAvailable(), []);

  const filterRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const selectedIdRef = useRef<string | null>(selectedId);
  const streamVersionRef = useRef(0);

  const selectSession = useCallback((id: string) => {
    const same = selectedIdRef.current === id;
    selectedIdRef.current = id;
    if (same) {
      // Re-activating a row that failed is itself a retry. The detail effect
      // clears the error only when that new request starts.
      setDetailRetry((n) => n + 1);
    } else {
      setFilters(filtersForSessionSwitch);
      setDetailError(null);
    }
    setSelectedId(id);
  }, []);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => applyTheme(theme), [theme]);

  // Initial load, then the stream keeps it current.
  useEffect(() => {
    let live = true;
    const streamVersionWhenStarted = streamVersionRef.current;
    getOverview()
      .then((o) => {
        if (!mayApplyInitialOverview(live, streamVersionWhenStarted, streamVersionRef.current)) {
          return;
        }
        setOverview(o);
        setLoadError(null);
      })
      .catch((e: Error) => {
        if (mayApplyInitialOverview(live, streamVersionWhenStarted, streamVersionRef.current)) {
          setLoadError(e.message);
        }
      });
    const handle = openOverviewStream({
      onOverview: (o) => {
        streamVersionRef.current += 1;
        setOverview(o);
        setLoadError(null);
      },
      onState: (s, retry) => {
        // A reconnect usually means the Console restarted, often after a
        // reinstall. If the site now carries a newer build than this page,
        // reload rather than keep showing stale code against live data.
        if (s === 'open' && streamStateRef.current === 'reconnecting') {
          void siteHasNewerBuild().then((newer) => {
            if (newer && live) location.reload();
          });
        }
        streamStateRef.current = s;
        setStream(s);
        setRetryIn(retry);
      },
    });
    // The site can also change under a Console that never restarted, so
    // look occasionally as well.
    const buildTimer = setInterval(() => {
      void siteHasNewerBuild().then((newer) => {
        if (newer && live) location.reload();
      });
    }, 10 * 60 * 1000);
    return () => {
      live = false;
      clearInterval(buildTimer);
      handle.close();
    };
  }, []);

  const sessions = overview?.sessions ?? [];

  // Pick something sensible the first time: whatever needs attention.
  useEffect(() => {
    if (!sessions.length) return;
    if (selectedId && sessions.some((s) => s.id === selectedId)) return;
    const attention = overview?.attention?.[0]?.session_id;
    const blocked = sessions.find((s) => s.state === 'blocked');
    selectSession(attention ?? blocked?.id ?? sessions[0].id);
  }, [overview, selectedId, selectSession]);

  useEffect(() => {
    selectedIdRef.current = selectedId;
    if (selectedId) history.replaceState(null, '', `#session=${selectedId}`);
  }, [selectedId]);

  // Session detail, refetched whenever the overview changes underneath it.
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    const ac = new AbortController();
    getSession(selectedId, ac.signal)
      .then((next) => {
        if (!ac.signal.aborted && next.id === selectedId) {
          setDetail(next);
          setDetailError(null);
        }
      })
      .catch((error: unknown) => {
        if (ac.signal.aborted) return;
        const message = error instanceof Error ? error.message : 'The session detail did not answer';
        setDetailError({ id: selectedId, message });
      });
    return () => ac.abort();
  }, [selectedId, overview?.generated_at, detailRetry]);

  const groups = useMemo(
    () => groupSessionsByProject(filterSessions(sessions, query)),
    [sessions, query],
  );
  const projectCount = useMemo(() => groupSessionsByProject(sessions).length, [sessions]);
  const ordered = useMemo(() => railOrder(groups), [groups]);
  const activeDetail = detail?.id === selectedId ? detail : null;

  useEffect(() => {
    if (!activeDetail) return;
    const models = [...new Set((activeDetail.events ?? []).flatMap(eventModels))];
    setFilters((current) => filtersForDetail(current, models));
  }, [activeDetail]);

  // Chains, re-read whenever the server says the digest moved. The overview
  // carries only the digest, so the page never guesses the contents.
  // The overview carries summary rows only, so the full proposal is fetched
  // once by id. It is refetched when the summary's revision moves.
  const chainSummary = useMemo(() => {
    return (overview?.proposals ?? [])
      .filter((row) => row.kind === 'chain_change' && row.status === 'pending')
      .sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0))[0];
  }, [overview]);
  const chainSummaryKey = chainSummary ? `${chainSummary.id}:${chainSummary.revision}` : '';

  useEffect(() => {
    if (!chainSummary) {
      setChainProposal(null);
      return;
    }
    const ac = new AbortController();
    getProposal(chainSummary.id, ac.signal)
      .then((full) => {
        if (full.kind === 'chain_change') setChainProposal(full);
      })
      .catch(() => setChainProposal(null));
    return () => ac.abort();
  }, [chainSummaryKey]);

  // The newest chain proposal a person can still act on. Chain proposals are
  // global, so they come from the overview rather than a session detail.
  const pendingChainProposal = useMemo<ChainChangeProposal | null>(() => {
    if (!chainSummary || !chainProposal) return null;
    return chainProposal.id === chainSummary.id ? chainProposal : null;
  }, [chainSummary, chainProposal]);

  const chainDigest = overview?.chain_digest ?? null;
  const reloadChains = useCallback(() => {
    getChains()
      .then((snapshot) => {
        setChains(snapshot);
        setChainError(null);
      })
      .catch((error: unknown) => {
        // A phase 1 server has no /api/chains. That is not an error to show.
        if (error instanceof ApiError && error.status === 404) setChains(null);
      });
  }, []);

  useEffect(() => {
    reloadChains();
  }, [chainDigest, reloadChains]);

  // WebMCP, registered once, and only when the browser really offers it.
  useEffect(() => {
    let live = true;
    getToolManifest()
      .then((tools) => {
        if (!live) return;
        setToolCount(registerTools(tools).registered);
      })
      .catch(() => {
        /* No tools helper. The pill simply never appears. */
      });
    return () => {
      live = false;
    };
  }, []);

  const refreshAfterControl = useCallback(
    (message: string) => {
      setControlNotice(message);
      setTimeout(() => setControlNotice(''), 4000);
      setDetailRetry((n) => n + 1);
      reloadChains();
    },
    [reloadChains],
  );

  const failureText = (error: unknown): string => {
    if (error instanceof ApiError) {
      if (error.isStaleRevision) {
        return 'Someone edited this proposal first. The console reloaded it; check it and try again.';
      }
      if (error.isChainConflict) {
        return 'The chains changed while you were editing. Review the current chains and save again.';
      }
      if (error.isApplicationInProgress) {
        return 'This proposal is already being applied. Wait for the server to report the result.';
      }
      if (error.isRouteStateChanged) {
        return 'The session or route changed after this proposal was made. Review the latest state, edit if needed, then try again.';
      }
      return error.message;
    }
    return 'The console could not be reached.';
  };

  const onApproveProposal = useCallback(
    (proposal: SessionHandoffProposal | ChainChangeProposal) => {
      setBusyId(proposal.id);
      setChainBusy(proposal.kind === 'chain_change');
      approveProposal(proposal.id)
        .then((updated) => {
          // Approve answers 200 even when the apply failed, so the status is
          // the truth, not the HTTP result.
          refreshAfterControl(
            updated.status === 'applied'
              ? 'Applied.'
              : updated.last_error?.message ?? 'The proposal was not applied.',
          );
        })
        .catch((error: unknown) => refreshAfterControl(failureText(error)))
        .finally(() => {
          setBusyId(null);
          setChainBusy(false);
        });
    },
    [refreshAfterControl],
  );

  const onRejectProposal = useCallback(
    (proposal: SessionHandoffProposal | ChainChangeProposal) => {
      setBusyId(proposal.id);
      rejectProposal(proposal.id)
        .then(() => refreshAfterControl('Rejected.'))
        .catch((error: unknown) => refreshAfterControl(failureText(error)))
        .finally(() => setBusyId(null));
    },
    [refreshAfterControl],
  );

  const onEditProposal = useCallback(
    (
      proposal: SessionHandoffProposal,
      patch: { target_model?: string | null; reason?: string; allow_metered?: boolean },
    ) => {
      setBusyId(proposal.id);
      editProposal(proposal.id, { expected_revision: proposal.revision, ...patch })
        .then(() => refreshAfterControl('Saved.'))
        .catch((error: unknown) => refreshAfterControl(failureText(error)))
        .finally(() => setBusyId(null));
    },
    [refreshAfterControl],
  );

  const onProposeRoute = useCallback(
    (route: RouteStatus) => {
      if (!activeDetail) return;
      createHandoffProposal({
        session_id: activeDetail.id,
        operation: 'pin',
        target_model: route.model,
        allow_metered: false,
        reason: `Move this session to ${route.short_name} on ${route.provider}.`,
      })
        .then(() => refreshAfterControl(`Proposed ${route.short_name}. Review and approve it.`))
        .catch((error: unknown) => refreshAfterControl(failureText(error)));
    },
    [activeDetail, refreshAfterControl],
  );

  const onProposeRestoreRoot = useCallback(() => {
    if (!activeDetail) return;
    createHandoffProposal({
      session_id: activeDetail.id,
      operation: 'restore_root',
      reason: 'Return this session to the root model it started with.',
    })
      .then(() => refreshAfterControl('Proposed restoring the root. Review and approve it.'))
      .catch((error: unknown) => refreshAfterControl(failureText(error)));
  }, [activeDetail, refreshAfterControl]);

  const onSaveChains = useCallback(
    (next: ChainMap, expectedDigest: string) => {
      setChainBusy(true);
      setChainError(null);
      saveChains(next, expectedDigest)
        .then((snapshot) => {
          setChains(snapshot);
          refreshAfterControl(snapshot.changed ? 'Chains saved.' : 'Chains were already this.');
        })
        .catch((error: unknown) => {
          setChainError(failureText(error));
          if (error instanceof ApiError && error.isChainConflict) {
            // New servers carry the current snapshot in the 409 body. Older
            // servers do not; keep the GET fallback for that transition.
            if (error.current) {
              setChains(error.current);
            } else {
              reloadChains();
            }
          }
        })
        .finally(() => setChainBusy(false));
    },
    [refreshAfterControl, reloadChains],
  );

  const onCopyReport = useCallback(async () => {
    if (!activeDetail) return;
    const ok = await copyText(buildReport(activeDetail, Date.now()));
    const message = ok ? 'Report copied' : 'Copy failed';
    setCopied(ok ? 'Copied' : 'Copy failed');
    setNotice(message);
    setTimeout(() => {
      setCopied(null);
      setNotice(null);
    }, 2000);
  }, [activeDetail]);

  const focusSessionHeading = useCallback(
    (id: string | null) => {
      if (!id) return;
      const wanted = sessions.find((s) => s.id === id)?.project;
      const move = (tries = 0) => {
        const h = headingRef.current;
        if (h && h.textContent === wanted) h.focus();
        else if (tries < 40) setTimeout(() => move(tries + 1), 25);
      };
      move();
    },
    [sessions],
  );

  const focusInspector = useCallback((tries = 0) => {
    const panel = document.getElementById('inspector-panel');
    if (panel) panel.focus();
    else if (tries < 40) setTimeout(() => focusInspector(tries + 1), 25);
  }, []);

  const openSession = useCallback(
    (id: string) => {
      selectSession(id);
      setTimeout(() => focusSessionHeading(id), 0);
    },
    [focusSessionHeading, selectSession],
  );

  // Global keys. Single letters are ignored while typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const el = e.target as HTMLElement | null;
      const typing =
        !!el &&
        (el.tagName === 'INPUT' ||
          el.tagName === 'TEXTAREA' ||
          el.tagName === 'SELECT' ||
          el.isContentEditable);

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        e.stopImmediatePropagation();
        setSheetOpen(false);
        setPaletteOpen(true);
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // The DOM is the authority here. State cleanup after Escape can be one
      // render behind the removed dialog; do not swallow the next real key.
      if (document.querySelector('[role="dialog"]')) return;

      if (e.key === '?' && !typing) {
        e.preventDefault();
        e.stopImmediatePropagation();
        setPaletteOpen(false);
        setSheetOpen(true);
      } else if (e.key === '/' && !typing) {
        e.preventDefault();
        filterRef.current?.focus();
        filterRef.current?.select();
      } else if (!typing && (e.key === 'j' || e.key === 'k')) {
        e.preventDefault();
        const i = ordered.findIndex((s) => s.id === selectedIdRef.current);
        const next = ordered[Math.min(ordered.length - 1, Math.max(0, i + (e.key === 'j' ? 1 : -1)))];
        if (next) selectSession(next.id);
      } else if (!typing && e.key === 't') {
        setTheme((p) => nextTheme(p));
      } else if (!typing && e.key === 'c') {
        void onCopyReport();
      } else if (!typing && e.key === 'g') {
        focusSessionHeading(selectedIdRef.current);
      } else if (!typing && e.key === 'r') {
        focusInspector();
      } else if (e.key === 'Escape' && typing && el === filterRef.current) {
        setQuery('');
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [ordered, onCopyReport, focusSessionHeading, focusInspector, selectSession]);

  const commands: PaletteCommand[] = useMemo(() => {
    const out: PaletteCommand[] = [];
    for (const s of sessions) {
      out.push({
        id: `go-${s.id}`,
        label: `Go to ${sessionTitle(s)}`,
        group: 'Session',
        hint: sessionState(s.state, s.blocked_reason).label,
        keywords: [s.id, s.active_model, s.workdir, s.profile],
        run: () => openSession(s.id),
      });
    }
    for (const g of KIND_GROUPS) {
      out.push({
        id: `filter-${g}`,
        label: `Filter timeline to ${KIND_GROUP_TITLES[g].toLowerCase()}`,
        group: 'Timeline',
        keywords: ['filter', g],
        run: () => setFilters((f) => ({ ...f, group: g as KindGroup })),
      });
    }
    const timelineModels = activeDetail
      ? [...new Set((activeDetail.events ?? []).flatMap(eventModels))]
      : [];
    for (const model of timelineModels) {
      const route = activeDetail?.routes?.find((r) => r.model === model);
      const shortName = route?.short_name ?? deriveShortName(model);
      out.push({
        id: `filter-model-${model}`,
        label: `Filter timeline to ${titled(shortName)}`,
        group: 'Timeline',
        keywords: [model, route?.provider ?? 'historical model'],
        run: () => setFilters((f) => ({ ...f, model })),
      });
    }
    if (chains) {
      out.push({
        id: 'chains',
        label: 'Edit global failover chains',
        group: 'Chains',
        hint: 'New sessions only',
        keywords: ['chain', 'failover', 'order', 'peers'],
        run: () => {
          setChainEditorOpen(true);
          setTimeout(() => document.getElementById('chain-editor')?.scrollIntoView({ block: 'nearest' }), 0);
          setTimeout(() => document.getElementById('chain-editor')?.querySelector('select')?.focus(), 30);
        },
      });
    }
    if (humanApi && activeDetail?.controllable) {
      for (const route of activeDetail.routes ?? []) {
        if (route.sessions_using?.includes(activeDetail.id)) continue;
        out.push({
          id: `propose-${route.model}`,
          label: `Propose using ${titled(route.short_name)} for this session`,
          group: 'Session',
          keywords: [route.model, route.provider, 'handoff', 'pin'],
          run: () => onProposeRoute(route),
        });
      }
      if (activeDetail.pinned_model) {
        out.push({
          id: 'propose-restore-root',
          label: `Propose restoring ${titled(shortNameOf(activeDetail, activeDetail.root_model))}`,
          group: 'Session',
          keywords: ['root', 'unpin', 'restore'],
          run: onProposeRestoreRoot,
        });
      }
    }
    out.push(
      {
        id: 'copy-report',
        label: 'Copy report as Markdown',
        group: 'Session',
        keywords: ['export', 'markdown', 'incident'],
        run: () => void onCopyReport(),
      },
      {
        id: 'theme-cycle',
        label: 'Cycle theme',
        group: 'View',
        hint: THEME_LABEL[theme],
        keywords: ['toggle', 'appearance'],
        run: () => setTheme((p) => nextTheme(p)),
      },
      {
        id: 'theme-dark',
        label: 'Use dark theme',
        group: 'View',
        run: () => setTheme('dark'),
      },
      {
        id: 'theme-light',
        label: 'Use light theme',
        group: 'View',
        run: () => setTheme('light'),
      },
      {
        id: 'theme-system',
        label: 'Use system theme',
        group: 'View',
        run: () => setTheme('system'),
      },
      {
        id: 'shortcuts',
        label: 'Show keyboard shortcuts',
        group: 'Help',
        keywords: ['keys', 'help'],
        run: () => setSheetOpen(true),
      },
    );
    return out;
  }, [
    sessions,
    activeDetail,
    theme,
    onCopyReport,
    openSession,
    chains,
    humanApi,
    onProposeRoute,
    onProposeRestoreRoot,
  ]);

  const attention = overview?.attention ?? [];
  const loading = !overview && !loadError;
  const liveHistoryIds = useMemo(
    () => new Set(sessions.map((s) => s.history_id).filter((id): id is string => !!id)),
    [sessions],
  );

  return (
    <div class="app">
      <Sprite />
      <span class="sr-only" role="status">{notice}</span>
      <a class="skip" href="#session-view">
        Skip to the session
      </a>

      <header class="topbar">
        <div class="brand">
          Airlock <span>Console</span>
        </div>
        <div class="count">
          {overview
            ? `${sessions.length} ${sessions.length === 1 ? 'session' : 'sessions'} in ${projectCount} ${projectCount === 1 ? 'project' : 'projects'}`
            : 'reading the session registry'}
        </div>
        <div class="spacer" />
        {stream !== 'open' ? (
          <span class={`conn${stream === 'reconnecting' ? ' conn-reconnecting' : ''}`} role="status">
            <Glyph id={stream === 'reconnecting' ? 'unknown' : 'idle'} />
            {stream === 'reconnecting'
              ? retryIn
                ? `Reconnecting, next try in ${retryIn}s`
                : 'Reconnecting'
              : 'Connecting'}
          </span>
        ) : overview ? (
          <span class="conn">{`Updated ${relativeTime(overview.generated_at, now)}`}</span>
        ) : null}
        {toolCount > 0 ? (
          <span class="tools-pill" title="Read and propose only">
            {`Agent tools: ${toolCount}`}
          </span>
        ) : null}
        <div class="viewswitch" role="tablist" aria-label="Page">
          {(
            [
              ['live', 'Live'],
              ['history', 'History'],
              ['usage', 'Usage'],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              class={`tbtn${view === key ? ' tbtn-on' : ''}`}
              aria-selected={view === key}
              onClick={() => setView(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <button type="button" class="tbtn" onClick={() => setTheme((p) => nextTheme(p))}>
          {THEME_LABEL[theme]}
        </button>
        <button
          type="button"
          class="tbtn"
          onClick={() => {
            setSheetOpen(false);
            setPaletteOpen(true);
          }}
        >
          Command <span class="kbd">Ctrl K</span>
        </button>
        <button
          type="button"
          class="tbtn"
          onClick={() => {
            setPaletteOpen(false);
            setSheetOpen(true);
          }}
          aria-label="Keyboard shortcuts"
        >
          <span class="kbd">?</span>
        </button>
      </header>

      {controlNotice ? (
        <div class="control-notice" role="status" aria-live="polite">
          <Glyph id="unknown" />
          <span>{controlNotice}</span>
          <button type="button" class="disc" onClick={() => setControlNotice('')}>
            Dismiss
          </button>
        </div>
      ) : null}

      {attention.length ? (
        <section class="attention" aria-label="Needs attention" role="status">
          <ul>
            {attention.map((a) => {
              const s = sessions.find((x) => x.id === a.session_id);
              return (
                <li key={a.session_id}>
                  <button type="button" class="attn-row" onClick={() => openSession(a.session_id)}>
                    <Status
                      d={sessionState(s?.state ?? 'blocked', s?.blocked_reason)}
                    />
                    <span class="sum">
                      <span class="proj">
                        {s ? sessionTitle(s) : `Session ${a.session_id.replace(/^r-/, '').slice(0, 6)}`}
                      </span>
                      {` stopped ${relativeTime(a.since, now)}. ${a.summary}.`}
                    </span>
                    <span class="go">Open session</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {view === 'history' ? (
        <HistoryView
          now={now}
          liveHistoryIds={liveHistoryIds}
          onOpenLive={(historyId) => {
            const live = sessions.find((s) => s.history_id === historyId);
            setView('live');
            if (live) openSession(live.id);
          }}
        />
      ) : view === 'usage' ? (
        <UsageView now={now} />
      ) : (
      <div class="panes">
        <Rail
          groups={groups}
          selectedId={selectedId}
          query={query}
          now={now}
          onQuery={setQuery}
          onSelect={selectSession}
          onOpen={openSession}
          filterRef={filterRef}
          listRef={listRef}
          totalSessions={sessions.length}
          loading={loading}
        />

        <div class="stack">
          {loadError ? (
            <main class="main" id="session-view">
              <div class="empty">
                <h1>The console server did not answer</h1>
                <p>{loadError}</p>
                <p class="hint">
                  The page keeps retrying. Nothing on this page has been discarded.
                </p>
              </div>
            </main>
          ) : loading ? (
            <main class="main" id="session-view" aria-busy="true" aria-label="Loading session details">
              <Skeleton />
            </main>
          ) : sessions.length === 0 ? (
            <EmptyMachine />
          ) : detailError?.id === selectedId && !activeDetail ? (
            <main class="main" id="session-view">
              <div class="empty">
                <h1>This session did not answer</h1>
                <p>{detailError.message}</p>
                <p class="hint">
                  The session is still in the rail. Retry now, or wait for the next live update.
                </p>
                <button
                  type="button"
                  class="chip"
                  onClick={() => setDetailRetry((n) => n + 1)}
                >
                  Retry session
                </button>
              </div>
            </main>
          ) : activeDetail ? (
            <>
              <SessionView
                detail={activeDetail}
                now={now}
                filters={filters}
                onFilters={setFilters}
                onCopyReport={onCopyReport}
                copied={copied}
                headingRef={headingRef}
                proposals={
                  <ProposalPanel
                    detail={activeDetail}
                    now={now}
                    busyId={busyId}
                    humanApi={humanApi}
                    onApprove={onApproveProposal}
                    onReject={onRejectProposal}
                    onEdit={onEditProposal}
                    onRestoreRoot={onProposeRestoreRoot}
                  />
                }
              />
              <Inspector
                detail={activeDetail}
                headroom={overview?.headroom ?? []}
                now={now}
                onUseRoute={humanApi && activeDetail.controllable ? onProposeRoute : undefined}
                chainEditor={
                  chains && (chainEditorOpen || pendingChainProposal) ? (
                    <ChainEditor
                      snapshot={chains}
                      routes={activeDetail.routes ?? []}
                      proposal={pendingChainProposal}
                      humanApi={humanApi}
                      busy={chainBusy}
                      error={chainError}
                      onSave={onSaveChains}
                      onReload={reloadChains}
                      onApproveProposal={onApproveProposal}
                      onRejectProposal={onRejectProposal}
                    />
                  ) : chains ? (
                    <section class="sec">
                      <h2 class="micro">Global chains</h2>
                      <button
                        type="button"
                        class="btn"
                        onClick={() => setChainEditorOpen(true)}
                      >
                        Edit global chains
                      </button>
                    </section>
                  ) : null
                }
              />
            </>
          ) : (
            <main class="main" id="session-view" aria-busy="true" aria-label="Loading session details">
              <Skeleton />
            </main>
          )}
        </div>
      </div>
      )}

      {paletteOpen ? (
        <Palette commands={commands} onClose={() => setPaletteOpen(false)} />
      ) : sheetOpen ? (
        <Shortcuts onClose={() => setSheetOpen(false)} />
      ) : null}
    </div>
  );
}
