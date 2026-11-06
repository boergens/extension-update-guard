// The review page. Extension names, descriptions and URLs come from other
// extensions and are untrusted, so everything is inserted as text, never as HTML.

import { loadState, diff, isExempt } from './shared.js';

const SELF = chrome.runtime.id;
const HISTORY_SHOWN = 50;
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, '');
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  node.append(...children.flat().filter((child) => child != null && child !== false));
  return node;
}

function notice(text) {
  $('notice').textContent = text ?? '';
  $('notice').hidden = !text;
}

async function send(message) {
  const reply = await chrome.runtime.sendMessage(message);
  if (!reply?.ok) notice(reply?.error ?? 'Update Guard did not answer. Reload this page and try again.');
  return Boolean(reply?.ok);
}

function icon(info) {
  const letter = el('span', { class: 'icon letter' }, (info.name || '?').trim().charAt(0).toUpperCase());
  const largest = (info.icons ?? []).reduce((best, next) => (!best || next.size > best.size ? next : best), null);
  if (!largest) return letter;
  const img = el('img', { class: 'icon', src: largest.url, alt: '' });
  img.addEventListener('error', () => img.replaceWith(letter));
  return img;
}

// A link, but only for ordinary web addresses.
function webLink(url, text) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
    return el('a', { href: parsed.href, target: '_blank', rel: 'noreferrer' }, text ?? parsed.href);
  } catch {
    return null;
  }
}

function chips(items, kind) {
  return items.map((item) => el('code', { class: `chip ${kind}` }, item));
}

function changeList(held) {
  const changes = diff(held.from, held.to);
  const list = el('ul', { class: 'changes' });
  const line = (label, items, kind) => {
    if (items.length) list.append(el('li', {}, el('span', { class: 'label' }, label), ...chips(items, kind)));
  };
  line('New permissions', changes.addedPermissions, 'added');
  line('New site access', changes.addedHosts, 'added');
  line('Dropped permissions', changes.removedPermissions, 'removed');
  line('Dropped site access', changes.removedHosts, 'removed');
  for (const field of changes.fields) {
    list.append(
      el('li', {},
        el('span', { class: 'label' }, field.label),
        el('span', { class: 'old' }, field.from || '(none)'),
        ' → ',
        el('span', {}, field.to || '(none)'),
      ),
    );
  }
  if (!list.children.length) {
    list.append(
      el('li', { class: 'quiet' },
        'Chrome reports the same permissions, site access, name and update source as before. The code can still have changed.'),
    );
  }
  return list;
}

function describeState(held, info) {
  if (info.enabled) {
    if (info.installType === 'admin') return 'Still running: it is installed by policy and cannot be turned off.';
    return info.mayDisable
      ? 'Still running: Update Guard could not turn it off.'
      : 'Still running: Chrome does not allow turning it off.';
  }
  if (info.disabledReason === 'permissions_increase') return 'Turned off by Chrome, because this version asks for more access.';
  if (held.disabledByGuard) return 'Turned off by Update Guard.';
  return 'It was already off when it updated, and stays off.';
}

async function approve(id, version, turnOn) {
  notice();
  if (!(await send({ type: 'approve', id, version }))) return;
  if (!turnOn) return;
  try {
    await chrome.management.setEnabled(id, true);
  } catch (err) {
    notice(`Approved, but Chrome did not turn it on (${err.message}). You can turn it on at chrome://extensions.`);
  }
}

function reviewCard(id, held, info) {
  const canTurnOn = !info.enabled && (held.disabledByGuard || info.disabledReason === 'permissions_increase');
  const storePage = held.to.updateUrl.includes('clients2.google.com')
    ? webLink(`https://chromewebstore.google.com/detail/${id}`, 'Chrome Web Store page')
    : null;
  const homepage = webLink(held.to.homepageUrl, 'Homepage');
  const source = info.installType === 'development'
    ? 'Unpacked extension: the code is in the folder it was loaded from.'
    : el('span', {},
        'Code on disk: ', el('code', {}, `Extensions/${id}/`),
        ' in your profile folder, one subfolder per version. The old version stays there until Chrome restarts.');

  return el('article', { class: 'card', 'data-id': id },
    el('div', { class: 'card-head' },
      icon(info),
      el('div', { class: 'card-title' },
        el('h3', {}, held.to.name),
        el('p', { class: 'meta' }, `${id} · noticed ${new Date(held.detectedAt).toLocaleString()}`),
      ),
      el('p', { class: 'versions' }, el('span', { class: 'old' }, held.from.version), ' → ', el('strong', {}, held.to.version)),
    ),
    el('p', { class: info.enabled ? 'state running' : 'state' }, describeState(held, info)),
    changeList(held),
    el('p', { class: 'where' },
      [storePage, homepage, source].filter(Boolean).flatMap((part, index) => (index ? [' · ', part] : [part])),
    ),
    el('div', { class: 'buttons' },
      el('button', { class: 'primary', onclick: () => approve(id, held.to.version, canTurnOn) },
        canTurnOn ? 'Approve and turn on' : 'Mark as reviewed'),
      el('button', { onclick: () => chrome.tabs.create({ url: `chrome://extensions/?id=${id}` }) }, 'Open in chrome://extensions'),
    ),
  );
}

function extensionRow(info, state) {
  const held = state.pending[info.id];
  const trusted = Boolean(state.settings.trusted[info.id]);
  const unpackedSkipped = info.installType === 'development' && !state.settings.guardUnpacked;

  const choice = unpackedSkipped
    ? el('span', { class: 'quiet' }, 'Not held (unpacked)')
    : el('select', {
        'aria-label': `When ${info.name} updates`,
        onchange: (event) => send({ type: 'setTrusted', id: info.id, trusted: event.target.value === 'allow' }),
      },
        el('option', { value: 'hold', selected: !trusted }, 'Turn off and ask me'),
        el('option', { value: 'allow', selected: trusted }, 'Allow without asking'),
      );

  return el('tr', { 'data-id': info.id },
    el('td', {}, el('div', { class: 'name' }, icon(info), el('span', {}, info.name))),
    el('td', {}, info.version),
    el('td', {}, held ? el('span', { class: 'waiting' }, 'Waiting for review') : info.enabled ? 'On' : 'Off'),
    el('td', {}, choice),
  );
}

const EVENT_TEXT = {
  held: 'updated and was turned off for review',
  'held-again': 'was turned on before review and turned off again',
  'more-access': 'updated and was turned off by Chrome because it asks for more access',
  'updated-while-off': 'updated while it was off',
  'still-running': 'updated and could not be turned off',
  approved: 'approved',
  'auto-approved': 'updated and was allowed without asking',
  reverted: 'went back to the approved version',
  removed: 'was removed while waiting for review',
};

function historyItem(entry) {
  return el('li', {},
    el('time', {}, new Date(entry.at).toLocaleString()),
    el('span', {}, `${entry.name} ${entry.from} → ${entry.to}: ${EVENT_TEXT[entry.event] ?? entry.event}`),
  );
}

async function render() {
  const [state, all] = await Promise.all([loadState(), chrome.management.getAll()]);
  const byId = new Map(all.map((info) => [info.id, info]));
  const watched = all
    .filter((info) => info.id !== SELF && info.type !== 'theme')
    .sort((a, b) => a.name.localeCompare(b.name));

  const waiting = Object.entries(state.pending).filter(([id]) => byId.has(id));
  const guarded = watched.filter((info) => !isExempt(info, state.settings)).length;
  $('status').textContent = waiting.length
    ? `${waiting.length} update${waiting.length === 1 ? ' is' : 's are'} waiting for your review.`
    : `No updates are waiting. Guarding ${guarded} of ${watched.length} installed extension${watched.length === 1 ? '' : 's'}.`;
  document.title = waiting.length ? `(${waiting.length}) Update Guard` : 'Update Guard';

  $('waiting').replaceChildren(
    ...(waiting.length
      ? waiting.map(([id, entry]) => reviewCard(id, entry, byId.get(id)))
      : [el('p', { class: 'quiet' }, 'Nothing is waiting. When an extension updates, it is turned off and listed here.')]),
  );
  $('extensions').replaceChildren(...watched.map((info) => extensionRow(info, state)));
  $('guard-unpacked').checked = state.settings.guardUnpacked;
  $('history').replaceChildren(
    ...(state.log.length
      ? state.log.slice(-HISTORY_SHOWN).reverse().map(historyItem)
      : [el('li', { class: 'quiet' }, 'No updates seen yet.')]),
  );
}

// Redraw one at a time, whenever stored state or any extension changes.
let drawing = Promise.resolve();
function redraw() {
  drawing = drawing.then(render).catch((err) => notice(`Could not load: ${err.message}`));
}

$('guard-unpacked').addEventListener('change', (event) => send({ type: 'setGuardUnpacked', value: event.target.checked }));
chrome.storage.onChanged.addListener(redraw);
for (const event of ['onInstalled', 'onUninstalled', 'onEnabled', 'onDisabled']) {
  chrome.management[event].addListener(redraw);
}
redraw();
