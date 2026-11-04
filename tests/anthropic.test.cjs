const test = require('node:test');
const assert = require('node:assert/strict');
const {callAnthropic} = require('../source-code/OpenSlides/desktop/anthropic.cjs');

const version = '2026-10-21';
const body = {
  model: 'claude-opus-5-5',
  max_tokens: 1024,
  system: 'Test',
  messages: [{role: 'user', content: 'hello'}]
};

test('response size limit error message is correct', async () => {
  await assert.rejects(
    callAnthropic('key', body, version, undefined, async () => {
      return {
        ok: true,
        status: 200,
        body: {
          getReader: () => ({
            read: async () => {
              return { done: false, value: new Uint8Array(1000001) };
            },
            cancel: async () => {}
          })
        }
      };
    }),
    /Claude response is too large/
  );
});
