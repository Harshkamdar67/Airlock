// The command palette and the shortcut sheet. Both are modal dialogs with a
// focus trap and focus restored to whatever opened them.

import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import type { ComponentChildren } from 'preact';
import { matchCommands, type Command } from '../lib/palette';

function useFocusTrap(onClose: () => void) {
  const ref = useRef<HTMLDivElement | null>(null);
  const opener = useRef<Element | null>(null);

  useLayoutEffect(() => {
    opener.current = document.activeElement;
    return () => {
      const el = opener.current;
      if (el instanceof HTMLElement) el.focus();
    };
  }, []);

  useEffect(() => {
    const selector =
      'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])';
    const first = ref.current?.querySelector<HTMLElement>(selector);
    if (first && !ref.current?.contains(document.activeElement)) first.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') ||
        (!e.ctrlKey && !e.metaKey && !e.altKey && e.key === '?')
      ) {
        // The global handler owns replacing one overlay with another.
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !ref.current) return;
      const focusable = ref.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  return ref;
}

function Modal({
  label,
  onClose,
  children,
}: {
  label: string;
  onClose: () => void;
  children: ComponentChildren;
}) {
  const ref = useFocusTrap(onClose);
  return (
    <div
      class="scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        class="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        ref={(node) => {
          ref.current = node;
          if (node && !node.contains(document.activeElement)) {
            queueMicrotask(() => {
              const first = node.querySelector<HTMLElement>(
                'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])',
              );
              (first ?? node).focus();
            });
          }
        }}
      >
        {children}
      </div>
    </div>
  );
}

export interface PaletteCommand extends Command {
  run(): void;
}

export function Palette({
  commands,
  onClose,
}: {
  commands: PaletteCommand[];
  onClose(): void;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const matches = matchCommands(commands, query);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  useEffect(() => {
    setActive(0);
  }, [query]);
  useEffect(() => {
    document
      .getElementById(`palette-option-${active}`)
      ?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const run = (i: number) => {
    const hit = matches[i];
    if (!hit) return;
    onClose();
    // Let the dialog unmount and restore focus first; a jump command may then
    // intentionally move it to the destination heading.
    setTimeout(() => hit.command.run(), 0);
  };

  return (
    <Modal label="Command palette" onClose={onClose}>
      <h2 class="micro" style="padding:var(--s5) var(--s6) 0">
        Command palette
      </h2>
      <input
        class="pinput"
        ref={inputRef as never}
        type="text"
        role="combobox"
        aria-expanded="true"
        aria-controls="palette-list"
        aria-activedescendant={matches.length ? `palette-option-${active}` : undefined}
        aria-label="Type a command or a session name"
        placeholder="Type a command or a session name"
        autocomplete="off"
        value={query}
        onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActive((a) => (matches.length ? (a + 1) % matches.length : 0));
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActive((a) => (matches.length ? (a - 1 + matches.length) % matches.length : 0));
          } else if (e.key === 'Enter') {
            e.preventDefault();
            run(active);
          }
        }}
      />
      {matches.length === 0 ? (
        <p class="pempty">Nothing matches that.</p>
      ) : (
        <ul class="plist" id="palette-list" role="listbox" aria-label="Commands">
          {matches.map((m, i) => (
            <li
              key={m.command.id}
              id={`palette-option-${i}`}
              role="option"
              aria-selected={i === active}
              class="pitem"
              onMouseEnter={() => setActive(i)}
              onClick={() => run(i)}
            >
              <span>
                {m.ranges.length
                  ? m.command.label.split('').map((ch, k) =>
                      m.ranges.includes(k) ? <mark key={k}>{ch}</mark> : <span key={k}>{ch}</span>,
                    )
                  : m.command.label}
              </span>
              {m.command.hint ? <span class="faint">{m.command.hint}</span> : null}
              <span class="g">{m.command.group}</span>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

const SHORTCUTS: [string, string][] = [
  ['Ctrl K, Cmd K', 'Open the command palette'],
  ['?', 'Show this list'],
  ['/', 'Filter the sessions rail'],
  ['Arrow up, Arrow down', 'Move through sessions in the rail'],
  ['j, k', 'Move through sessions from anywhere'],
  ['Home, End', 'First or last session'],
  ['Enter', 'Open the selected session and move focus to it'],
  ['g', 'Move focus to the session view'],
  ['r', 'Move focus to the route list'],
  ['t', 'Cycle theme: system, light, dark'],
  ['c', 'Copy the Markdown report for this session'],
  ['Escape', 'Close a dialog, or clear the rail filter'],
];

export function Shortcuts({ onClose }: { onClose(): void }) {
  return (
    <Modal label="Keyboard shortcuts" onClose={onClose}>
      <div class="sheet">
        <h2>Keyboard shortcuts</h2>
        <table>
          <tbody>
            {SHORTCUTS.map(([k, v]) => (
              <tr key={k}>
                <td>
                  <span class="kbd">{k}</span>
                </td>
                <td>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p class="faint" style="margin-top:var(--s7)">
          Single letter shortcuts are ignored while you are typing in a field.
        </p>
        <button type="button" class="chip" data-dialog-close style="margin-top:var(--s6)" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
