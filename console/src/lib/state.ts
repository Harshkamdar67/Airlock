// Small state transitions extracted from the page so the races they prevent
// have direct regressions without pretending a DOM unit test is a browser.

import type { TimelineFilters } from './sentences';

/** The API's three-valued context fit, including exact-window equality. */
export function contextFit(
  inputTokens: number | null | undefined,
  window: number | null | undefined,
): boolean | null {
  if (inputTokens == null || window == null) return null;
  return inputTokens <= window;
}

/** An initial snapshot may not overwrite a newer full snapshot from SSE. */
export function mayApplyInitialOverview(
  mounted: boolean,
  streamVersionWhenStarted: number,
  currentStreamVersion: number,
): boolean {
  return mounted && streamVersionWhenStarted === currentStreamVersion;
}

/** Model ids are session-local. Keep the kind filter, clear the model filter. */
export function filtersForSessionSwitch(filters: TimelineFilters): TimelineFilters {
  return { ...filters, model: null };
}

/**
 * A route may disappear while the session stays selected. Clear a model filter
 * that the current detail's events no longer know, or it would leave an empty
 * select and an apparently empty timeline. Historical event-only models stay.
 */
export function filtersForDetail(
  filters: TimelineFilters,
  eventModels: string[],
): TimelineFilters {
  if (!filters.model || eventModels.includes(filters.model)) return filters;
  return { ...filters, model: null };
}
