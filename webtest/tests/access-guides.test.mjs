import test from 'node:test';
import assert from 'node:assert/strict';
import { loadApp, settle } from '../lib/load-app.mjs';

test('access guide renders client specs, capabilities search, and credential hints', async (t) => {
  const app = await loadApp();
  t.after(app.close);
  app.router.put('/api/users/1/credentials', {
    id: 1, loginName: 'alice', chineseName: 'Alice', isGuest: false,
    gitTokenConfigured: true, codeBuddyApiKeyConfigured: true,
  });
  app.router.get('/api/users', [{
    id: 1, loginName: 'alice', chineseName: 'Alice', isGuest: false,
    gitTokenConfigured: true, gitTokenOAuth: true, codeBuddyApiKeyConfigured: true,
  }]);
  app.router.post('/api/users/1/git-token/verify', {
    valid: false, missing: ['write_repository'],
  });
  app.evaluate(`
    state.currentUser={id:1,loginName:'alice',chineseName:'Alice',isGuest:false};
    state.authRequired=true;
    state.accessClient='claude';
    state.revealedToken='tok-secret';
    state.accessCapabilitiesStatus='ready';
    state.accessCapabilities={operations:[
      {name:'issues_get',method:'GET',path:'/api/issues',description:'读取需求',readOnly:true},
      {name:'projects_save',method:'PUT',path:'/api/projects',description:'保存项目',readOnly:false}
    ]};
    state.accessTab='mcp';renderGuide();
  `);
  const body = app.document.querySelector('#accessBody');
  assert.match(body.textContent, /MCP 快速接入/);
  assert.match(body.textContent, /Claude Code/);
  const spec = app.evaluate(`accessMcpClientSpec('codex','http://example.test','abc')`);
  assert.match(spec.code, /mcp_servers\.agentflow/);
  assert.match(spec.code, /Bearer abc/);
  assert.match(app.evaluate(`accessMcpAgentPrompt(accessMcpClientSpec('cursor','http://x','abc'),'http://x','abc')`), /禁止使用 fetch/);
  assert.equal(app.evaluate(`credentialHint('工蜂 Authentication failed，需由责任人处理').field`), 'gitToken');
  assert.match(app.evaluate(`credentialActionHTML('CodeBuddy API Key 被拒绝')`), /codeBuddyApiKey/);

  app.evaluate(`state.accessTab='capabilities';renderGuide()`);
  const search = app.document.querySelector('#accessCapabilitySearch');
  assert.equal(body.querySelectorAll('tbody tr').length, 2);
  search.value = '保存';
  search.dispatchEvent(new app.window.Event('input', { bubbles: true }));
  assert.equal(body.querySelectorAll('tbody tr').length, 1);
  assert.match(body.querySelector('tbody').textContent, /项目与仓库/);
  assert.match(body.querySelector('tbody').textContent, /写入/);

  const credential = app.evaluate(`({
    pat:credentialSecretError(credentialKind('codexApiKey'),'any-key'),
    url:credentialExtraError(credentialKind('codexApiKey'),'ftp://bad'),
    save:credentialSavePayload(credentialKind('codeBuddyApiKey'),{secret:' ck_x ',extra:'ioa'}),
    clear:credentialClearPayload(credentialKind('codexApiKey')),
    next:credentialNextKind({gitTokenConfigured:true}).key,
    badge:credentialBadgeHTML('err')
  })`);
  assert.equal(credential.pat, '');
  assert.match(credential.url, /http/);
  assert.equal(credential.save.codeBuddyApiKey, 'ck_x');
  assert.equal(credential.clear.codexApiKeyClear, true);
  assert.equal(credential.next, 'codeBuddyApiKey');
  assert.match(credential.badge, /有错误/);

  app.evaluate(`
    state.data.users=[{...state.currentUser,id:1,gitTokenConfigured:true,codeBuddyApiKeyConfigured:true}];
    state.currentUser=state.data.users[0];
    renderPersonalSettings();
  `);
  await settle(4);
  const panel = app.document.querySelector('#credentialPanel');
  panel.querySelector('[data-credential-row="codeBuddyApiKey"] .credential-row-main').click();
  const secret = panel.querySelector('[data-credential-row="codeBuddyApiKey"] input[name="secret"]');
  secret.value = 'ck_replacement';
  secret.dispatchEvent(new app.window.Event('input', { bubbles: true }));
  panel.querySelector('[data-credential-row="codeBuddyApiKey"] [data-credential-eye]').click();
  assert.equal(secret.type, 'text');
  const save = panel.querySelector('[data-credential-row="codeBuddyApiKey"] [data-credential-act="save"]');
  assert.equal(save.disabled, false);
  save.click();
  await settle(6);
  assert.equal(app.router.calls.find(c => c.path === '/api/users/1/credentials').body.codeBuddyApiKey, 'ck_replacement');
  const configuredRow = app.document.querySelector('[data-credential-row="codeBuddyApiKey"]');
  configuredRow.querySelector('.credential-row-main').click();
  configuredRow.querySelector('[data-credential-act="delete"]').click();
  assert.equal(configuredRow.querySelector('[data-credential-pane="confirm"]').classList.contains('is-off'), false);
  configuredRow.querySelector('[data-credential-act="undelete"]').click();
  const gitRow = app.document.querySelector('[data-credential-row="gitToken"]');
  gitRow.querySelector('.credential-row-main').click();
  const gitSecret = gitRow.querySelector('input[name="secret"]');
  gitSecret.value = 'pat-without-scope';
  gitSecret.dispatchEvent(new app.window.Event('input', { bubbles: true }));
  gitRow.querySelector('[data-credential-act="save"]').click();
  await settle(6);
  assert.match(gitRow.querySelector('[data-git-verify-line]').textContent, /未保存/);
  gitRow.querySelector('[data-credential-act="delete"]').click();
  gitRow.querySelector('[data-credential-act="confirm-delete"]').click();
  await settle(6);
  assert.ok(app.router.calls.some(c => c.path === '/api/users/1/credentials' && c.body.gitTokenClear));
  await app.evaluate(`refreshGongfengGrant()`);
  assert.equal(app.evaluate(`state.data.users[0].gitTokenOAuth`), true);

  app.evaluate(`state.currentUser={isGuest:true,loginName:'guest'};renderUserProfilePopover()`);
  assert.match(app.document.querySelector('#userProfilePopover').textContent, /访客身份/);
  await app.evaluate(`renderPersonalSettings()`);
  assert.match(app.document.querySelector('#personalSettingsContent').textContent, /登录后即可管理自己的 Git Token 和 API Key/);
  app.evaluate(`state.accessTab='setup';renderGuide()`);
  assert.match(body.textContent, /请先登录/);
  app.evaluate(`openCommandPalette()`);
  const commandSearch = app.document.querySelector('#commandSearch');
  commandSearch.value = '项目配置';
  app.evaluate(`renderCommandResults()`);
  assert.match(app.document.querySelector('#commandResults').textContent, /项目配置/);
  app.evaluate(`moveCommandSelection(1);closeCommandPalette()`);
  assert.equal(app.document.querySelector('#commandPalette').hidden, true);

  app.evaluate(`
    state.currentUser={id:1,loginName:'alice',chineseName:'Alice',isGuest:false};
    state.data.users=[{...state.currentUser,gitTokenConfigured:true,codeBuddyApiKeyConfigured:false}];
    renderPersonalSettings();openCredentialWizard('codexApiKey');
  `);
  await settle(3);
  const wizardSecret = app.document.querySelector('#credentialWizard input[name="secret"]');
  wizardSecret.value = 'sk-valid';
  wizardSecret.dispatchEvent(new app.window.Event('input', { bubbles: true }));
  wizardSecret.dispatchEvent(new app.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  await settle(2);
  assert.match(app.document.querySelector('#credentialWizard').textContent, /确认并保存/);
  app.document.dispatchEvent(new app.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  await settle(2);
  assert.equal(app.evaluate(`credentialWizard===null`), true);

  app.router.post('/api/auth/logout', { ok: true });
  app.evaluate(`renderUserProfilePopover()`);
  app.document.querySelector('#uppLogoutBtn').click();
  await settle(4);
  assert.ok(app.router.calls.some(c => c.method === 'POST' && c.path === '/api/auth/logout'));
});

test('personal token UI creates and revokes tokens through the API', async (t) => {
  const app = await loadApp();
  t.after(app.close);
  const token = { id: 'token-1', name: 'Cursor', prefix: 'af_', createdAt: '2026-10-20T10:00:00Z',
    expiresAt: '2099-01-01T00:00:00Z', revokedAt: null };
  app.router.post('/api/auth/tokens', { token: 'af_secret' });
  app.router.get('/api/auth/tokens', [token]);
  app.router.delete('/api/auth/tokens/token-1', { ok: true });
  app.evaluate(`
    state.currentUser={id:1,loginName:'alice',isGuest:false};
    state.accessTab='tokens';state.accessTokensStatus='ready';state.accessTokens=${JSON.stringify([token])};
    renderGuide();
  `);
  const form = app.document.querySelector('#accessTokenForm');
  form.elements.name.value = 'Automation';
  form.elements.expiresInDays.value = '180';
  form.dispatchEvent(new app.window.Event('submit', { bubbles: true, cancelable: true }));
  await settle(8);
  const create = app.router.calls.find(c => c.method === 'POST' && c.path === '/api/auth/tokens');
  assert.deepEqual(create.body, { name: 'Automation', expiresInDays: 180 });
  assert.equal(app.evaluate(`state.revealedToken`), 'af_secret');
  app.evaluate(`renderAccessTokens()`);
  assert.match(app.document.querySelector('#accessBody').textContent, /af_secret/);
  await app.evaluate(`
    (() => {
      const b=document.createElement('button');b.textContent='生成并复制';
      return createQuickAccessPrompt(b);
    })()
  `);
  await settle(4);
  assert.match(app.evaluate(`state.revealedToken`), /af_secret/);

  app.evaluate(`state.accessTab='tokens';renderAccessTokens()`);
  app.document.querySelector('[data-revoke-token="token-1"]').click();
  await settle(2);
  app.document.querySelector('#confirmOkBtn').click();
  await settle(8);
  assert.ok(app.router.calls.some(c => c.method === 'DELETE' && c.path === '/api/auth/tokens/token-1'));
});

test('token-bearing access base never downgrades a secure deployment', async (t) => {
  const app = await loadApp();
  t.after(app.close);
  const base = (configured, protocol, host = 'page.example') => app.evaluate(
    `accessPublicBaseURL(${JSON.stringify(configured)}, ${JSON.stringify({ protocol, host })})`);
  // Bare APP_DOMAIN follows the studio page scheme.
  assert.equal(base('studio.example', 'https:'), 'https://studio.example');
  assert.equal(base('studio.example:8443', 'https:'), 'https://studio.example:8443');
  assert.equal(base('studio.example', 'http:'), 'http://studio.example');
  // An explicit https base wins, and an https page upgrades an explicit http base.
  assert.equal(base('https://studio.example/', 'http:'), 'https://studio.example');
  assert.equal(base('https://studio.example/lf/', 'http:'), 'https://studio.example/lf');
  assert.equal(base('http://studio.example', 'https:'), 'https://studio.example');
  assert.equal(base('http://studio.example', 'http:'), 'http://studio.example');
  // Without a usable configured base, the page origin is used as is.
  assert.equal(base('', 'https:'), 'https://page.example');
  assert.equal(base('', 'http:', '127.0.0.1:8080'), 'http://127.0.0.1:8080');
  assert.equal(base('javascript:alert(1)', 'https:'), 'https://page.example');
  assert.equal(base('ftp://studio.example', 'http:'), 'http://page.example');
});

test('access snippets copied from an https studio use https endpoints', async (t) => {
  const app = await loadApp({ origin: 'https://studio.example' });
  t.after(app.close);
  const writes = [];
  Object.defineProperty(app.window, 'isSecureContext', { configurable: true, value: true });
  Object.defineProperty(app.window.navigator, 'clipboard', { configurable: true, value: { writeText: async (value) => { writes.push(value); } } });
  app.router.post('/api/auth/tokens', { token: 'af_live_secret' });
  app.router.get('/api/auth/tokens', []);
  app.evaluate(`
    window.__AGENTFLOW_APP_DOMAIN__='studio.example';
    state.currentUser={id:1,loginName:'alice',chineseName:'Alice',isGuest:false};
    state.authRequired=true;state.accessClient='claude';state.revealedToken='';
    state.accessTab='mcp';renderGuide();
  `);
  assert.equal(app.evaluate('getPublicBaseURL()'), 'https://studio.example');
  await app.evaluate(`createQuickAccessPrompt(document.createElement('button'))`);
  await settle(4);
  const prompt = writes.find((value) => value.includes('af_live_secret'));
  assert.ok(prompt, 'quick access prompt was not copied');
  assert.match(prompt, /ENDPOINT = https:\/\/studio\.example\/mcp/);
  assert.match(prompt, /https:\/\/studio\.example\/mcp-setup\.txt/);
  for (const tab of ['mcp', 'api', 'setup']) {
    app.evaluate(`state.accessTab='${tab}';renderGuide()`);
    const text = app.document.querySelector('#accessBody').textContent + app.document.querySelector('#accessSummary')?.textContent;
    assert.doesNotMatch(text, /http:\/\/studio\.example/, `${tab} guide downgraded the endpoint`);
    if (tab !== 'setup') assert.match(text, /https:\/\/studio\.example/, `${tab} guide lost the endpoint`);
  }
});
