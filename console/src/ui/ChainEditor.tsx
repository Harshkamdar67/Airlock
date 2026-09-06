// The global chain editor. It edits one source model's ordered peers at a
// time, saves directly with a digest compare-and-swap, and shows a pending
// agent proposal beside the current chains.
//
// The notice is permanent and never conditional: a chain change cannot reach a
// running session, and a person must be able to see that without asking.

import { useEffect, useMemo, useState } from 'preact/hooks';
import { Glyph } from './Glyph';
import { titled } from '../lib/names';
import {
  addPeer,
  chainsEqual,
  cloneChains,
  diffChains,
  movePeer,
  prunedChains,
  removePeer,
  chainSnapshotSaveAllowed,
  unavailableChainExplanation,
  validateChains,
  type ChainMap,
} from '../lib/chains';
import { proposalStatus } from '../lib/proposals';
import { Status } from './Glyph';
import { CHAIN_NOTICE, type ChainChangeProposal, type ChainSnapshot, type RouteStatus } from '../types';

export interface ChainEditorProps {
  snapshot: ChainSnapshot | null;
  routes: RouteStatus[];
  proposal: ChainChangeProposal | null;
  humanApi: boolean;
  busy: boolean;
  error: string | null;
  onSave(chains: ChainMap, expectedDigest: string): void;
  onReload(): void;
  onApproveProposal(proposal: ChainChangeProposal): void;
  onRejectProposal(proposal: ChainChangeProposal): void;
}

export function ChainEditor(props: ChainEditorProps) {
  const { snapshot, routes } = props;
  const enabled = useMemo(() => routes.map((r) => r.model), [routes]);
  const shortOf = (model: string) =>
    routes.find((r) => r.model === model)?.short_name ?? model;

  const [draft, setDraft] = useState<ChainMap>(() => cloneChains(snapshot?.chains ?? {}));
  const [source, setSource] = useState<string>(() => {
    const first = Object.keys(snapshot?.chains ?? {})[0];
    return first ?? enabled[0] ?? '';
  });
  const [added, setAdded] = useState('');

  // A newer snapshot from SSE or a conflict reload replaces the draft, but
  // only while the person has not started editing.
  const digest = snapshot?.digest ?? '';
  const unavailable = unavailableChainExplanation(snapshot);
  useEffect(() => {
    setDraft(cloneChains(snapshot?.chains ?? {}));
  }, [digest]);

  const peers = draft[source] ?? [];
  const problems = validateChains(draft, enabled);
  const dirty = !chainsEqual(draft, snapshot?.chains ?? {});
  const canSave =
    props.humanApi &&
    dirty &&
    !problems.length &&
    !props.busy &&
    chainSnapshotSaveAllowed(snapshot);

  const setPeers = (next: string[]) => setDraft({ ...draft, [source]: next });

  return (
    <section class="sec" id="chain-editor" aria-label="Global failover chains">
      <h2 class="micro">Global chains</h2>

      <div class="chain-editor">
        {unavailable ? (
          <p class="chain-empty" role="status">
            {unavailable}
          </p>
        ) : null}

        <label class="chain-source">
          <span class="micro">For</span>
          <select
            aria-label="Chain source model"
            value={source}
            disabled={unavailable != null}
            onChange={(e) => setSource((e.target as HTMLSelectElement).value)}
          >
            {[...new Set([...Object.keys(draft), ...enabled])].map((model) => (
              <option value={model} key={model}>
                {shortOf(model)}
              </option>
            ))}
          </select>
        </label>

        {peers.length ? (
          <ol class="chain-peers">
            {peers.map((peer, index) => (
              <li class="chain-peer" key={`${peer}-${index}`}>
                <span class="pos">{index + 1}</span>
                <span>{shortOf(peer)}</span>
                <span class="peer-actions">
                  <button
                    type="button"
                    class="icon-btn"
                    aria-label={`Move ${shortOf(peer)} earlier`}
                    disabled={unavailable != null || index === 0}
                    onClick={() => setPeers(movePeer(peers, index, -1))}
                  >
                    <span aria-hidden="true">↑</span>
                  </button>
                  <button
                    type="button"
                    class="icon-btn"
                    aria-label={`Move ${shortOf(peer)} later`}
                    disabled={unavailable != null || index === peers.length - 1}
                    onClick={() => setPeers(movePeer(peers, index, 1))}
                  >
                    <span aria-hidden="true">↓</span>
                  </button>
                  <button
                    type="button"
                    class="icon-btn"
                    aria-label={`Remove ${shortOf(peer)}`}
                    disabled={unavailable != null}
                    onClick={() => setPeers(removePeer(peers, index))}
                  >
                    <span aria-hidden="true">×</span>
                  </button>
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p class="chain-empty">{`${titled(shortOf(source))} has no declared chain, so the router derives one.`}</p>
        )}

        <div class="chain-source">
          <label class="micro" for="chain-add">
            Add
          </label>
          <select
            id="chain-add"
            value={added}
            disabled={unavailable != null}
            onChange={(e) => setAdded((e.target as HTMLSelectElement).value)}
          >
            <option value="">Choose a route</option>
            {enabled
              .filter((model) => model !== source && !peers.includes(model))
              .map((model) => (
                <option value={model} key={model}>
                  {shortOf(model)}
                </option>
              ))}
          </select>
          <button
            type="button"
            class="btn"
            disabled={unavailable != null || !added}
            onClick={() => {
              setPeers(addPeer(peers, added, source));
              setAdded('');
            }}
          >
            Add route
          </button>
        </div>

        {problems.length ? (
          <ul class="chain-diff" aria-label="Chain problems">
            {problems.map((problem) => (
              <li class="chain-invalid" key={`${problem.source}-${problem.message}`}>
                {`${shortOf(problem.source)}: ${problem.message}`}
              </li>
            ))}
          </ul>
        ) : null}

        <p class="chain-notice">{CHAIN_NOTICE}</p>

        <div class="proposal-actions">
          <button
            type="button"
            class="btn btn-primary"
            disabled={!canSave}
            onClick={() => props.onSave(prunedChains(draft), digest)}
          >
            {props.busy ? 'Saving' : 'Save chains'}
          </button>
          {dirty ? (
            <button
              type="button"
              class="btn"
              disabled={props.busy}
              onClick={() => setDraft(cloneChains(snapshot?.chains ?? {}))}
            >
              Discard changes
            </button>
          ) : null}
        </div>

        {props.error ? (
          <p class="proposal-error" role="status">
            {props.error}
            <button type="button" class="disc" onClick={props.onReload}>
              Reload chains
            </button>
          </p>
        ) : null}

        {snapshot?.digest ? (
          <p class="chain-digest">{`digest ${snapshot.digest.slice(0, 12)}`}</p>
        ) : null}

        {props.proposal ? (
          <div class="chain-compare">
            <h3>
              Proposed by {props.proposal.created_by === 'human' ? 'you' : 'an agent'}
            </h3>
            <p class="proposal-why">{props.proposal.reason}</p>
            <ul class="chain-diff">
              {diffChains(snapshot?.chains ?? {}, props.proposal.chains)
                .filter((line) => line.change !== 'same')
                .map((line) => (
                  <li key={line.source}>
                    <span class={line.change === 'removed' ? 'removed' : 'added'}>
                      {`${shortOf(line.source)}: `}
                      {line.before.map(shortOf).join(' → ') || 'no chain'}
                      {' becomes '}
                      {line.after.map(shortOf).join(' → ') || 'no chain'}
                    </span>
                  </li>
                ))}
            </ul>
            <Status d={proposalStatus(props.proposal.status)} />
            {props.humanApi && props.proposal.status === 'pending' ? (
              <div class="proposal-actions" style="margin-top:var(--s5)">
                <button
                  type="button"
                  class="btn btn-primary"
                  disabled={props.busy}
                  onClick={() => props.onApproveProposal(props.proposal!)}
                >
                  {props.busy ? 'Applying' : 'Approve and save these chains'}
                </button>
                <button
                  type="button"
                  class="btn"
                  disabled={props.busy}
                  onClick={() => props.onRejectProposal(props.proposal!)}
                >
                  Reject
                </button>
              </div>
            ) : null}
            {props.proposal.last_error ? (
              <p class="proposal-error" role="status">
                {props.proposal.last_error.message}
              </p>
            ) : null}
          </div>
        ) : null}

        {!props.humanApi ? (
          <p class="chain-empty">
            <Glyph id="unknown" /> This page cannot save chains; open it from the console.
          </p>
        ) : null}
      </div>
    </section>
  );
}
