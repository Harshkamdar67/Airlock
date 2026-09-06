// Provider inference and the spoken form of a profile id.

import { describe, expect, it } from 'vitest';
import { inferProvider, modelDisplayName, profileLabel } from '../src/lib/names';

describe('modelDisplayName', () => {
  it('keeps the version a short name would drop', () => {
    expect(modelDisplayName('claude-fable-5-1')).toBe('Claude Fable 5.1');
    expect(modelDisplayName('claude-fable-5')).toBe('Claude Fable 5');
    expect(modelDisplayName('claude-opus-5[1m]')).toBe('Claude Opus 5 (1M)');
    expect(modelDisplayName('claude-opus-4-7')).toBe('Claude Opus 4.7');
    expect(modelDisplayName('claude-haiku-4-5-20251001')).toBe('Claude Haiku 4.5');
    expect(modelDisplayName('gpt-5.6-sol')).toBe('GPT-5.6 Sol');
    expect(modelDisplayName('gpt-5.6-luna-fast')).toBe('GPT-5.6 Luna Fast');
    expect(modelDisplayName('gpt-6-astra')).toBe('GPT-6 Astra');
    expect(modelDisplayName('gpt-5.3-codex-spark')).toBe('GPT-5.3 Codex Spark');
    expect(modelDisplayName('grok-4.6')).toBe('Grok 4.6');
    expect(modelDisplayName('grok-composer-2.5-fast')).toBe('Grok Composer 2.5 Fast');
    expect(modelDisplayName('deepseek/deepseek-v4-flash-0731')).toBe('DeepSeek V4 Flash 0731');
    expect(modelDisplayName('stealth/ox-alpha')).toBe('Ox Alpha');
    expect(modelDisplayName('openmodel/local-coder')).toBe('Local-coder (local)');
    expect(modelDisplayName(null)).toBe('unknown model');
  });
});

describe('inferProvider', () => {
  it('reads shipped ids by prefix and keeps the fallback otherwise', () => {
    expect(inferProvider('claude-opus-5[1m]', 'openai')).toBe('anthropic');
    expect(inferProvider('gpt-6-astra', 'anthropic')).toBe('openai');
    expect(inferProvider('grok-4.6', 'openai')).toBe('grok');
    expect(inferProvider('deepseek/deepseek-v4-flash-0731', 'openai')).toBe('openrouter');
    expect(inferProvider('openmodel/local-coder', 'openai')).toBe('openmodel');
    expect(inferProvider('mystery-9', 'grok')).toBe('grok');
    expect(inferProvider(null, 'openai')).toBe('openai');
  });
});

describe('profileLabel', () => {
  it('says a profile the way a person would', () => {
    expect(profileLabel('hybrid-openai-root')).toBe('Hybrid, OpenAI root');
    expect(profileLabel('hybrid-anthropic-root')).toBe('Hybrid, Anthropic root');
    expect(profileLabel('openai-pure')).toBe('OpenAI only');
    expect(profileLabel('grok-pure')).toBe('Grok only');
    expect(profileLabel('something-else')).toBe('something else');
    expect(profileLabel('')).toBe('unknown profile');
  });
});
