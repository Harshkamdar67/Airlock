// The console API contract, transcribed from console/SPEC.md and validated
// against console/fixtures/. Every field the page reads is declared here; the
// page reads nothing else, even if a router sends it.

export type SessionState = 'running' | 'blocked' | 'idle' | 'ended';
export type BlockedReason =
  | 'rate_limit'
  | 'provider_cooldown'
  | 'context_overflow'
  | 'chain_exhausted';
export type RouteState = 'ready' | 'cooling' | 'unavailable';
export type RouteCategory = 'included' | 'extra' | 'metered' | 'unknown';

export interface SessionContext {
  input_tokens: number | null;
  window: number | null;
}

export interface WorkerUse {
  model: string;
  requests: number;
}

export interface SessionSummary {
  id: string;
  state: SessionState;
  blocked_reason: BlockedReason | null;
  profile: string;
  root_model: string;
  root_provider: string;
  active_model: string;
  workdir: string;
  project: string;
  started_at: string;
  last_activity_at: string | null;
  context: SessionContext | null;
  workers: WorkerUse[];
  recent_handoffs: number;
  url: string;
  /** Phase 3 shape, already tolerated so nothing needs renaming later. */
  source?: 'airlock' | 'claude-code' | 'airlock-direct' | 'generic';
  /** Claude Code's own title for the conversation, when it wrote one. */
  title?: string | null;
  branch?: string | null;
  /** The history index entry for this session, when its transcript is known. */
  history_id?: string | null;
}

/** One line of what a session did, read from its transcript's tail. */
export interface ActivityItem {
  at: string;
  kind: 'prompt' | 'reply' | 'tool';
  tool?: string;
  preview?: string | null;
}

export interface RouteStatus {
  model: string;
  short_name: string;
  provider: string;
  category: RouteCategory;
  metered: boolean;
  context_window: number | null;
  effort_ceiling: string | null;
  status: RouteState;
  cooldown_remaining_seconds: number | null;
  /** Present only inside a SessionDetail; null means the router cannot tell. */
  fits_context?: boolean | null;
  sessions_using: string[];
}

export interface Cooldown {
  scope: 'model' | 'provider';
  model?: string;
  provider: string;
  remaining_seconds: number | null;
  until?: string;
}

export interface ProviderHeadroom {
  provider: string;
  window: string;
  used_percent: number | null;
  resets_at: string | null;
  source: string;
}

export interface AttentionItem {
  kind: string;
  session_id: string;
  since: string;
  summary: string;
}

export interface Usage {
  provider: string;
  model: string;
  requests: number;
  completed: number;
  errors: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
}

/** Only the event keys allowlisted by console/SPEC.md are declared here. */
export interface AirEvent {
  timestamp: string;
  kind?: string;
  model?: string;
  provider?: string;
  status?: number;
  outcome?: string;
  failover_from?: string;
  to_model?: string;
  from_model?: string;
  models_considered?: number;
  reason?: string;
  remaining_seconds?: number;
  duration_ms?: number;
  usage?: Record<string, number>;
}

export interface Handoff {
  at: string;
  from_model: string;
  to_model: string;
  reason: string;
}

export interface SessionDetail extends SessionSummary {
  routes: RouteStatus[];
  /** Phase 2. A v1-registry or ended session cannot be controlled. */
  controllable?: boolean;
  pinned_model?: string | null;
  proposals?: Proposal[];
  current_handoff?: SessionHandoffProposal | null;
  cooldowns: Cooldown[];
  chains: Record<string, string[]>;
  usage: Usage[];
  events: AirEvent[];
  last_handoff: Handoff | null;
  /** What the session did lately, from its transcript. Oldest first. */
  activity?: ActivityItem[];
  /** The history index's facts for this session, when it has any. */
  history?: HistoryFacts | null;
}

/** One stretch of time a directory's session ran through an Airlock router. */
export interface AirlockPeriod {
  kind: 'airlock';
  from: string;
  to: string;
  profile: string | null;
  root_model: string | null;
  open: boolean;
}

export interface HistoryFacts {
  id: string;
  session_id: string;
  started_at: string | null;
  last_activity_at: string | null;
  prompts: number;
  replies: number;
  tool_calls: number;
  compactions: number;
  peak_context: number | null;
  last_context: number | null;
  window: number | null;
  output_tokens: number;
  subagents: number;
  models: string[];
  periods: AirlockPeriod[];
  airlock_inferred: boolean;
  entrypoint: string | null;
  version: string | null;
}

/** One past or present session as the history index lists it. */
export interface HistoryItem {
  id: string;
  session_id: string;
  project: string;
  workdir: string | null;
  title: string | null;
  branch: string | null;
  models: string[];
  primary_model: string | null;
  provider: string;
  started_at: string | null;
  last_activity_at: string | null;
  prompts: number;
  replies: number;
  tool_calls: number;
  compactions: number;
  peak_context: number | null;
  last_context: number | null;
  window: number | null;
  output_tokens: number;
  subagents: number;
  entrypoint: string | null;
  version: string | null;
}

export interface SubagentSummary {
  id: string;
  agent_type?: string | null;
  description?: string | null;
  model?: string | null;
  background?: boolean;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  last_activity_at?: string | null;
  prompts?: number;
  replies?: number;
  tool_calls?: number;
  peak_context?: number | null;
  output_tokens?: number;
  depth?: number;
}

export interface HistoryDetail extends HistoryItem {
  agents: SubagentSummary[];
  periods: AirlockPeriod[];
  airlock_inferred: boolean;
  activity: ActivityItem[];
}

export interface HistoryListing {
  generated_at: string;
  refreshed_at: string | null;
  total: number;
  projects: string[];
  models: string[];
  sessions: HistoryItem[];
}

export interface UsagePeriod {
  period: string;
  sessions: number;
  prompts: number;
  replies: number;
  tool_calls: number;
  output_tokens: number;
  compactions: number;
  by_provider: Record<string, number>;
}

export interface UsageReport {
  generated_at: string;
  group: 'day' | 'week' | 'month';
  since: string | null;
  until: string | null;
  filters: { project: string | null; model: string | null };
  totals: {
    sessions: number;
    prompts: number;
    replies: number;
    tool_calls: number;
    output_tokens: number;
    compactions: number;
    subagents: number;
    peak_context_max: number;
  };
  series: UsagePeriod[];
  agent_types: { name: string; count: number }[];
  tools: { name: string; count: number }[];
  projects: { name: string; sessions: number; prompts: number; tool_calls: number; output_tokens: number }[];
  models: { name: string; replies: number; provider: string }[];
  peaks: { id: string | null; project: string; title: string | null; peak_context: number; window: number | null }[];
  /** Sessions by how close their peak came to the window. */
  peak_buckets: { under_25: number; '25_to_50': number; '50_to_75': number; over_75: number; unknown: number };
}

export interface SubagentFeed {
  id: string;
  model: string | null;
  last_activity_at: string | null;
  context: SessionContext | null;
  activity: ActivityItem[];
}

// ---------------------------------------------------------------------------
// Phase 2: proposals and global chains
// ---------------------------------------------------------------------------

export type ProposalStatus =
  | 'pending'
  | 'applying'
  | 'applied'
  | 'rejected'
  | 'expired'
  | 'superseded'
  | 'conflicted'
  | 'failed';

export type ProposalOperation = 'pin' | 'restore_root';

export interface ProposalBasis {
  observed_at: string | null;
  active_model: string | null;
  pinned_model: string | null;
  context_input_tokens: number | null;
  route_status: string | null;
}

/** Records what the apply attempt did. Carries no secret, per CONTROL-SPEC. */
export interface ProposalApplication {
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
  router_instance_id: string | null;
  previous_pinned_model: string | null;
  pinned_model: string | null;
  changed: boolean;
  already_applied: boolean;
}

/** The server's safe error. Exactly two keys, never a raw upstream body. */
export interface ProposalError {
  code: string;
  message: string;
}

interface ProposalBase {
  id: string;
  reason: string;
  created_by: string;
  created_at: string;
  expires_at: string | null;
  revision: number;
  status: ProposalStatus;
  application: ProposalApplication | null;
  last_error: ProposalError | null;
}

export interface SessionHandoffProposal extends ProposalBase {
  kind: 'session_handoff';
  session_id: string;
  operation: ProposalOperation;
  target_model: string | null;
  allow_metered: boolean;
  basis: ProposalBasis | null;
}

export interface ChainChangeProposal extends ProposalBase {
  kind: 'chain_change';
  chains: Record<string, string[]>;
  base_digest: string | null;
}

export type Proposal = SessionHandoffProposal | ChainChangeProposal;

export function isHandoffProposal(p: Proposal): p is SessionHandoffProposal {
  return p.kind === 'session_handoff';
}

export function isChainProposal(p: Proposal): p is ChainChangeProposal {
  return p.kind === 'chain_change';
}

/**
 * The overview carries summary rows, not whole proposals. Only these keys are
 * guaranteed; the handoff keys appear only on a session_handoff row.
 */
export interface ProposalSummary {
  id: string;
  kind: 'session_handoff' | 'chain_change';
  status: ProposalStatus;
  created_by: string;
  created_at: string;
  expires_at: string | null;
  revision: number;
  session_id?: string;
  operation?: ProposalOperation;
  target_model?: string | null;
  base_digest?: string | null;
}

/** GET /api/chains, and the body of a successful PUT /api/human/chains. */
export interface ChainSnapshot {
  chains: Record<string, string[]> | null;
  digest: string | null;
  notice: string;
  unavailable?: boolean;
  reason?: 'unreadable' | 'helper_unavailable' | string;
  /** Only on the PUT response. */
  changed?: boolean;
}

/** The one sentence the server owns about global chain scope. */
export const CHAIN_NOTICE =
  'Applies to sessions started after this change. Running sessions keep their frozen chains.';

/** A tool definition from GET /api/tools/manifest. */
export interface ToolDefinition {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  annotations?: Record<string, unknown>;
}

/** The server's error envelope: { "type": "error", "error": { type, message } } */
export interface ApiErrorBody {
  type?: string;
  error?: { type?: string; message?: string };
  /** Present on chain_conflict so the editor can recover in one response. */
  current?: ChainSnapshot;
}

export interface Overview {
  generated_at: string;
  console_version: string;
  sessions: SessionSummary[];
  routes: RouteStatus[];
  headroom: ProviderHeadroom[];
  attention: AttentionItem[];
  /** Phase 2. Absent on a phase 1 server. Summary rows, not whole proposals. */
  proposals?: ProposalSummary[];
  chain_digest?: string | null;
}
