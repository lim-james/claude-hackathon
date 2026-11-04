const test = require('node:test');
const assert = require('node:assert/strict');
const {validateRequest} = require('../source-code/OpenSlides/desktop/anthropic.cjs');

const version = '2026-10-21';
const validBody = {
  model: 'claude-opus-5-5',
  max_tokens: 1024,
  system: 'Test',
  messages: [{role: 'user', content: 'hello'}]
};

test('rejects empty user message content', () => {
  const emptyContent = {...validBody, messages: [{role: 'user', content: ''}]};
  assert.throws(() => validateRequest(emptyContent, version), /Invalid design request/);
});

test('rejects whitespace-only user message content', () => {
  const whitespaceContent = {...validBody, messages: [{role: 'user', content: '   '}]};
  assert.throws(() => validateRequest(whitespaceContent, version), /Invalid design request/);
});

test('accepts valid non-empty user message content', () => {
  const validContent = {...validBody, messages: [{role: 'user', content: 'valid content'}]};
  assert.doesNotThrow(() => validateRequest(validContent, version));
});
