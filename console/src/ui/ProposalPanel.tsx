// The proposal panel, attached to the selected session.
//
// One primary button says exactly what it will do. Everything else is context
// for that one decision: what is proposed, what it was based on, whether the
// route is ready and large enough, and what happened when it was applied.

import { useEffect, useState } from 'preact/hooks';
import { Glyph, Status } from './Glyph';
import { compactTokens, relativeTime } from '../lib/time';
import { isUnrouted, shortNameLookup, titled } from '../lib/names';
import { contextFitLabel, routeState } from '../lib/vocab';
import {
  approvalBlock,
  expiryWords,
  isActionable,
  isEditable,
  meteredWords,
  primaryLabel,
  proposalLead,
  proposalStatus,
  sortProposals,
} from '../lib/proposals';
import type { RouteStatus, SessionDetail, SessionHandoffProposal } from '../types';

export interface ProposalPanelProps {
  detail: SessionDetail;
  now: number;
  busyId: string | null;
  humanApi: boolean;
  onApprove(proposal: SessionHandoffProposal): void;
  onReject(proposal: SessionHandoffProposal): void;
  onEdit(
    proposal: SessionHandoffProposal,
    patch: { target_model?: string | null; reason?: string; allow_metered?: boolean },
  ): void;
  onRestoreRoot(): void;
}

function routeFor(detail: SessionDetail, model: string | null | undefined): RouteStatus | undefined {
  return model ? detail.routes?.find((r) => r.model === model) : undefined;
}

function Facts({
  proposal,
  detail,
  now,
}: {
  proposal: SessionHandoffProposal;
  detail: SessionDetail;
  now: number;
}) {
  const route = routeFor(detail, proposal.target_model);
  const basis = proposal.basis;
  return (
    <div class="proposal-facts">
      {route ? (
        <span>
          <Status d={routeState(route.status, route.cooldown_remaining_seconds)} />
        </span>
      ) : proposal.operation === 'pin' ? (
        <span>
          <Status d={{ label: 'route unknown', glyph: 'unknown', tone: 'faint' }} />
        </span>
      ) : null}

      {route ? (
        <span class={route.fits_context === false ? 'nofit' : undefined}>
          {route.fits_context == null ? <Glyph id="unknown" /> : null}
          {contextFitLabel(route.fits_context)}
        </span>
      ) : null}

      {route?.metered ? (
        <span class={proposal.allow_metered ? undefined : 'nofit'}>
          <Glyph id="block" />
          {meteredWords(proposal, true)}
        </span>
      ) : null}

      {basis?.context_input_tokens != null ? (
        <span class="tnum">seen at {compactTokens(basis.context_input_tokens)} of context</span>
      ) : null}

      {basis?.active_model ? (
        <span>was on {titled(shortNameLookup(detail.routes)(basis.active_model))}</span>
      ) : null}

      <span>{`${proposal.created_by === 'human' ? 'you' : 'an agent'}, ${relativeTime(proposal.created_at, now)}`}</span>
      <span>{expiryWords(proposal.expires_at, now)}</span>
    </div>
  );
}

function EditForm({
  proposal,
  detail,
  onEdit,
  onDone,
  busy,
}: {
  proposal: SessionHandoffProposal;
  detail: SessionDetail;
  onEdit: ProposalPanelProps['onEdit'];
  onDone(): void;
  busy: boolean;
}) {
  const [target, setTarget] = useState(proposal.target_model ?? '');
  const [reason, setReason] = useState(proposal.reason);
  const [metered, setMetered] = useState(proposal.allow_metered);
  const reasonOk = reason.trim().length >= 8 && reason.length <= 400;

  return (
    <form
      class="edit-grid"
      onSubmit={(e) => {
        e.preventDefault();
        if (!reasonOk) return;
        onEdit(proposal, {
          ...(proposal.operation === 'pin' ? { target_model: target || null } : {}),
          reason,
          ...(proposal.operation === 'pin' ? { allow_metered: metered } : {}),
        });
        onDone();
      }}
    >
      {proposal.operation === 'pin' ? (
        <label class="edit-field">
          <span class="micro">Target model</span>
          <select value={target} onChange={(e) => setTarget((e.target as HTMLSelectElement).value)}>
            {(detail.routes ?? []).map((r) => (
              <option value={r.model} key={r.model}>
                {`${r.short_name} (${r.provider})`}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <label class="edit-field">
        <span class="micro">Reason</span>
        <textarea
          value={reason}
          maxLength={400}
          onInput={(e) => setReason((e.target as HTMLTextAreaElement).value)}
        />
        {!reasonOk ? (
          <span class="chain-invalid">A reason needs 8 to 400 characters.</span>
        ) : null}
      </label>

      {proposal.operation === 'pin' ? (
        <label class="edit-check">
          <input
            type="checkbox"
            checked={metered}
            onChange={(e) => setMetered((e.target as HTMLInputElement).checked)}
          />
          <span>Allow a metered route, which bills extra usage</span>
        </label>
      ) : null}

      <div class="proposal-actions">
        <button type="submit" class="btn" disabled={busy || !reasonOk}>
          Save changes
        </button>
        <button type="button" class="btn" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ProposalCard({
  proposal,
  detail,
  now,
  busy,
  humanApi,
  onApprove,
  onReject,
  onEdit,
}: {
  proposal: SessionHandoffProposal;
  detail: SessionDetail;
  now: number;
  busy: boolean;
  humanApi: boolean;
  onApprove: ProposalPanelProps['onApprove'];
  onReject: ProposalPanelProps['onReject'];
  onEdit: ProposalPanelProps['onEdit'];
}) {
  const [editing, setEditing] = useState(false);
  const short = shortNameLookup(detail.routes);
  const state = proposalStatus(proposal.status);
  const actionable = isActionable(proposal.status) && humanApi;
  const applying = proposal.status === 'applying' || busy;
  const applied = proposal.application;
  const targetRoute = routeFor(detail, proposal.target_model);
  // The server would refuse this apply, so the button must not promise it.
  const blocked = approvalBlock(proposal, targetRoute?.metered === true);

  return (
    <article
      class={`proposal${proposal.status === 'pending' ? ' proposal-pending' : ''}`}
      aria-label={`Proposal ${proposal.id}`}
    >
      <div class="proposal-head">
        <h2>Proposed handoff</h2>
        <Status d={state} />
        <span class="rev tnum">{`revision ${proposal.revision}`}</span>
      </div>

      <div class="proposal-body">
        <p class="proposal-lead">{proposalLead(proposal, short, detail.root_model)}</p>
        <p class="proposal-why">{proposal.reason}</p>
        <Facts proposal={proposal} detail={detail} now={now} />

        {editing ? (
          <EditForm
            proposal={proposal}
            detail={detail}
            onEdit={onEdit}
            onDone={() => setEditing(false)}
            busy={busy}
          />
        ) : (
          <div class="proposal-actions">
            {actionable ? (
              <>
                <button
                  type="button"
                  class="btn btn-primary"
                  disabled={applying || !!blocked}
                  title={blocked ?? undefined}
                  onClick={() => onApprove(proposal)}
                >
                  {applying
                    ? 'Applying'
                    : primaryLabel(proposal, short, detail.root_model)}
                </button>
                {proposal.status === 'pending' ? (
                  <button
                    type="button"
                    class="btn"
                    disabled={applying}
                    onClick={() => onReject(proposal)}
                  >
                    Reject
                  </button>
                ) : null}
                {isEditable(proposal.status) ? (
                  <button
                    type="button"
                    class="btn"
                    disabled={applying}
                    onClick={() => setEditing(true)}
                  >
                    Edit
                  </button>
                ) : null}
              </>
            ) : null}
          </div>
        )}

        {actionable && blocked && !editing ? (
          <div class="proposal-consent">
            <p class="proposal-note">{blocked}</p>
            <label class="edit-check">
              <input
                type="checkbox"
                checked={false}
                disabled={applying}
                onChange={(e) => {
                  if (!(e.target as HTMLInputElement).checked) return;
                  // Sent as an edit so the server owns the revision, exactly
                  // as it does for every other change to a proposal.
                  onEdit(proposal, { allow_metered: true });
                }}
              />
              <span>Allow extra usage for this handoff</span>
            </label>
          </div>
        ) : null}

        {applied ? (
          <p class="proposal-note">
            {applied.already_applied
              ? 'This was already applied; the console re-read the recorded result.'
              : applied.changed
                ? `The router moved from ${titled(short(applied.previous_pinned_model ?? detail.root_model))} to ${titled(short(applied.pinned_model ?? detail.root_model))}.`
                : 'The router was already on this route, so nothing changed.'}
            {applied.finished_at ? ` ${relativeTime(applied.finished_at, now)}.` : ''}
          </p>
        ) : null}

        {proposal.last_error ? (
          <p class="proposal-error" role="status">
            {/* The server's message has no trailing stop, so one is added. */}
            {proposal.last_error.message.replace(/\.?$/, '.')}
            {proposal.status === 'failed' || proposal.status === 'conflicted'
              ? ' You can edit it and approve again; applying the same change twice is safe.'
              : ''}
          </p>
        ) : null}
      </div>
    </article>
  );
}

export function ProposalPanel(props: ProposalPanelProps) {
  const { detail, humanApi } = props;
  const short = shortNameLookup(detail.routes);
  const handoffs = sortProposals(
    (detail.proposals ?? []).filter(
      (p): p is SessionHandoffProposal => p.kind === 'session_handoff',
    ),
  );
  const controllable = detail.controllable === true;
  const native = isUnrouted(detail);
  const pinned = detail.pinned_model ?? null;

  // A session that ends while a proposal is open must not keep offering it.
  const [announced, setAnnounced] = useState('');
  useEffect(() => {
    if (!native && !controllable && handoffs.some((p) => p.status === 'pending')) {
      setAnnounced('This session can no longer be controlled.');
    } else {
      setAnnounced('');
    }
  }, [native, controllable, handoffs.length]);

  // A plain Claude Code session has no router to propose against; the session
  // view already says so in one place.
  if (native) return null;
  if (!handoffs.length && controllable && !pinned) return null;

  return (
    <section aria-label="Proposals for this session">
      <span class="sr-only" role="status">
        {announced}
      </span>

      {pinned ? (
        <div class="proposal">
          <div class="proposal-head">
            <h2>Current routing</h2>
          </div>
          <div class="proposal-body">
            <p class="proposal-lead">
              {`This session is pinned to ${titled(short(pinned))}, not its root ${titled(short(detail.root_model))}.`}
            </p>
            {controllable && humanApi ? (
              <div class="proposal-actions">
                <button type="button" class="btn" onClick={props.onRestoreRoot}>
                  {`Propose restoring ${titled(short(detail.root_model))}`}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {handoffs.map((proposal) => (
        <ProposalCard
          key={proposal.id}
          proposal={proposal}
          detail={detail}
          now={props.now}
          busy={props.busyId === proposal.id}
          humanApi={humanApi && controllable}
          onApprove={props.onApprove}
          onReject={props.onReject}
          onEdit={props.onEdit}
        />
      ))}

      {!controllable && handoffs.length ? (
        <p class="proposal-blocked">
          This session cannot be controlled, so nothing here can be applied. A session
          started before control support, or one that has ended, has no control channel.
        </p>
      ) : null}

      {controllable && !humanApi ? (
        <p class="proposal-blocked">
          This page was not served by the console itself, so it cannot approve or reject.
          Open it from `airlock console`.
        </p>
      ) : null}
    </section>
  );
}
