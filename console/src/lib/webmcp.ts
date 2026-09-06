// WebMCP registration, feature detected defensively.
//
// Two rules decide everything here:
//   1. Register nothing unless navigator.modelContext really exists and is
//      shaped the way we expect. A missing or hostile shape is simply ignored.
//   2. Handlers may call POST /api/tools/call and nothing else. There is no
//      approve, reject, edit, pin, unpin, or execute tool, and the server has
//      no such endpoint either. If a manifest ever names one, it is dropped
//      here as well, so the page cannot become an approval bypass.

import { callTool } from './control';
import type { ToolDefinition } from '../types';

/** Names the page refuses to register even if the server offers them. */
const FORBIDDEN_FRAGMENTS = [
  'approve',
  'reject',
  'apply',
  'edit',
  'execute',
  'pin',
  'unpin',
  'resume',
  'restart',
  'delete',
  'control',
];

export function isRegisterableTool(name: string): boolean {
  if (typeof name !== 'string' || !name) return false;
  const lower = name.toLowerCase();
  return !FORBIDDEN_FRAGMENTS.some((fragment) => lower.includes(fragment));
}

/**
 * `propose_session_handoff` contains no forbidden fragment, and proposing is
 * explicitly an agent capability. Approval stays human-only.
 */
export function registerableTools(tools: ToolDefinition[]): ToolDefinition[] {
  return (Array.isArray(tools) ? tools : []).filter(
    (tool) => tool && typeof tool.name === 'string' && isRegisterableTool(tool.name),
  );
}

interface ModelContextLike {
  registerTool?: (definition: unknown) => unknown;
  provideContext?: (context: unknown) => unknown;
}

function modelContext(): ModelContextLike | null {
  if (typeof navigator === 'undefined') return null;
  const candidate = (navigator as unknown as { modelContext?: unknown }).modelContext;
  if (!candidate || typeof candidate !== 'object') return null;
  const ctx = candidate as ModelContextLike;
  if (typeof ctx.registerTool !== 'function' && typeof ctx.provideContext !== 'function') {
    return null;
  }
  return ctx;
}

export interface RegistrationResult {
  registered: number;
  skipped: string[];
}

/**
 * Returns how many tools were registered. Zero means either no WebMCP or
 * nothing safe to expose; the page shows the pill only above zero.
 */
export function registerTools(tools: ToolDefinition[]): RegistrationResult {
  const ctx = modelContext();
  const allowed = registerableTools(tools);
  const skipped = (Array.isArray(tools) ? tools : [])
    .map((tool) => tool?.name)
    .filter((name): name is string => typeof name === 'string' && !isRegisterableTool(name));

  if (!ctx || !allowed.length) return { registered: 0, skipped };

  const descriptors = allowed.map((tool) => ({
    name: tool.name,
    description: tool.description,
    inputSchema: tool.inputSchema,
    annotations: tool.annotations,
    async execute(args: Record<string, unknown>) {
      const result = await callTool(tool.name, args ?? {});
      return { content: [{ type: 'text', text: JSON.stringify(result) }] };
    },
  }));

  try {
    if (typeof ctx.provideContext === 'function') {
      ctx.provideContext({ tools: descriptors });
      return { registered: descriptors.length, skipped };
    }
    for (const descriptor of descriptors) {
      ctx.registerTool!(descriptor);
    }
    return { registered: descriptors.length, skipped };
  } catch {
    // A browser that offers the surface but refuses the call is treated as
    // absent. Nothing on the page depends on registration succeeding.
    return { registered: 0, skipped };
  }
}
