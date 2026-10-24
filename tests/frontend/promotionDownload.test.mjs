import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/lib/promotionApi.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
});

function setup(response, { clickError = false } = {}) {
  const exports = {}, calls = [], events = [], timers = [];
  const anchor = {
    href: '', download: '',
    click() {
      events.push(['click', this.href, this.download]);
      if (clickError) throw new Error('download failed');
    },
    remove() { events.push(['remove']); },
  };
  runInNewContext(outputText, {
    exports, Error, decodeURIComponent,
    window: { location: { pathname: '/workspace/index.html' } },
    fetch: async (url, options) => { calls.push({ url, options }); return response; },
    URL: {
      createObjectURL(blob) { events.push(['create', blob]); return 'blob:download'; },
      revokeObjectURL(url) { events.push(['revoke', url]); },
    },
    document: {
      createElement(tag) { assert.equal(tag, 'a'); return anchor; },
      body: { appendChild(node) { assert.equal(node, anchor); events.push(['append']); } },
    },
    setTimeout(callback) { timers.push(callback); },
  });
  return { exports, calls, events, timers, anchor };
}

function zipResponse(disposition) {
  return {
    ok: true,
    headers: { get: name => name === 'Content-Disposition' ? disposition : null },
    blob: async () => 'zip-bytes',
  };
}

test('campaign export uses POST and downloads server ZIP with decoded filename', async () => {
  const view = setup(zipResponse("attachment; filename*=UTF-8''%E5%AE%A3%E4%BC%A0%E5%8C%85.zip"));
  await view.exports.exportCampaignZip('cmp_123');
  assert.equal(view.calls[0].url, '/workspace/api/promotion/campaigns/cmp_123/export-zip');
  assert.equal(view.calls[0].options.method, 'POST');
  assert.equal(view.anchor.download, '宣传包.zip');
  assert.deepEqual(view.events, [
    ['create', 'zip-bytes'], ['append'], ['click', 'blob:download', '宣传包.zip'], ['remove'],
  ]);
  assert.equal(view.timers.length, 1);
  view.timers[0]();
  assert.deepEqual(view.events.at(-1), ['revoke', 'blob:download']);
});

test('campaign export propagates backend error and never downloads an error body', async () => {
  const view = setup({ ok: false, status: 400, json: async () => ({ detail: '没有可导出的已审核内容' }) });
  await assert.rejects(view.exports.exportCampaignZip('cmp_123'), /没有可导出的已审核内容/);
  assert.equal(view.events.length, 0);
});

test('campaign export handles non-JSON error responses', async () => {
  const view = setup({ ok: false, status: 503, json: async () => { throw new Error('HTML'); } });
  await assert.rejects(view.exports.exportCampaignZip('cmp_123'), /导出失败（503）/);
});

test('campaign export falls back to campaign filename and cleans up failed clicks', async () => {
  const view = setup(zipResponse('attachment'), { clickError: true });
  await assert.rejects(view.exports.exportCampaignZip('cmp_123'), /download failed/);
  assert.equal(view.anchor.download, 'campaign-cmp_123.zip');
  assert.deepEqual(view.events.at(-1), ['remove']);
  view.timers[0]();
  assert.deepEqual(view.events.at(-1), ['revoke', 'blob:download']);
});

test('malformed extended filename keeps plain filename and strips directory components', async () => {
  const view = setup(zipResponse('attachment; filename="../package.zip"; filename*=UTF-8\'\'%bad'));
  await view.exports.exportCampaignZip('cmp_123');
  assert.equal(view.anchor.download, 'package.zip');
});

test('review requests identify the displayed revision for stale-review rejection', async () => {
  const view = setup({ ok: true, json: async () => ({ content: { id: 'cnt_1' } }) });
  await view.exports.reviewContent('cnt_1', true, 'rev_2');
  assert.deepEqual(JSON.parse(view.calls[0].options.body), { approved: true, revision_id: 'rev_2' });
});
