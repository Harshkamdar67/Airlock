// What a session did lately: prompts, replies, and tool calls read from the
// tail of its Claude Code transcript. Newest first, one line each. The router
// timeline below it says how requests were routed; this says what the
// conversation was doing, which is the question a person actually has at 2 AM.

import { clockTime } from '../lib/time';
import { titled } from '../lib/names';
import type { ActivityItem } from '../types';

export interface ActivityProps {
  items: ActivityItem[];
  /** Short name of the model that replies, for the "Fable:" label. */
  modelShort: string;
  /** True for a session Airlock is not routing, where this is the main content. */
  primary?: boolean;
}

function label(item: ActivityItem, modelShort: string): string {
  if (item.kind === 'prompt') return 'You';
  if (item.kind === 'reply') return titled(modelShort);
  return item.tool ?? 'Tool';
}

function text(item: ActivityItem): string {
  if (item.preview) return item.preview;
  if (item.kind === 'prompt') return 'sent a prompt';
  if (item.kind === 'reply') return 'replied';
  return 'ran';
}

export function Activity({ items, modelShort, primary = false }: ActivityProps) {
  const newestFirst = [...items].reverse();
  return (
    <section class={`activity${primary ? ' activity-primary' : ''}`} aria-label="Recent activity">
      <h2 class="micro">Recent activity</h2>
      {newestFirst.length === 0 ? (
        <p class="section-unknown">Nothing has been said in this session yet.</p>
      ) : (
        <ol class="actlist">
          {newestFirst.map((item, i) => (
            <li class={`act act-${item.kind}`} key={`${item.at}-${i}`}>
              <time class="tnum" dateTime={item.at}>
                {clockTime(item.at, true)}
              </time>
              <span class="act-who">{label(item, modelShort)}</span>
              <span class="act-text">{text(item)}</span>
            </li>
          ))}
        </ol>
      )}
      <p class="act-note faint">
        One line per turn, read from this session's transcript on this machine. Set
        AIRLOCK_CONSOLE_PREVIEWS=off before starting the console to hide the text and keep
        only the shape.
      </p>
    </section>
  );
}
