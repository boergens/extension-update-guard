// Update Guard service worker.
//
// It remembers the last approved version of every installed extension. Whenever
// an extension's version differs from that, the extension is turned off, the
// user is notified, and it stays off until the new version is approved on the
// review page.

import { loadState, snapshot, isExempt, diff } from './shared.js';

const SELF = chrome.runtime.id;
const LOG_LIMIT = 200;

// All state changes run one after another, so overlapping events can't
// overwrite each other's storage writes.
let queue = Promise.resolve();
function inTurn(task) {
  const run = queue.then(task);
  queue = run.catch((err) => console.error('Update Guard:', err));
  return run;
}

async function save(state) {
  await chrome.storage.local.set({
    baseline: state.baseline,
    pending: state.pending,
    settings: state.settings,
    log: state.log.slice(-LOG_LIMIT),
  });
}

async function showBadge(state) {
  const count = Object.keys(state.pending).length;
  await chrome.action.setBadgeBackgroundColor({ color: '#B3261E' });
  await chrome.action.setBadgeText({ text: count ? String(count) : '' });
  await chrome.action.setTitle({ title: count ? `Update Guard: ${count} waiting for review` : 'Update Guard' });
}

function addLog(state, event, id, name, from, to) {
  state.log.push({ at: Date.now(), id, name, event, from, to });
}

function notify(id, title, message) {
  chrome.notifications.create(id, {
    type: 'basic',
    iconUrl: chrome.runtime.getURL('icons/icon128.png'),
    title,
    message,
    buttons: [{ title: 'Review' }],
    requireInteraction: true,
    priority: 2,
  });
}

// Returns 'off', 'already-off', 'locked' (Chrome doesn't allow it, e.g. installed by policy) or 'failed'.
async function turnOff(info) {
  if (!info.enabled) return 'already-off';
  if (!info.mayDisable) return 'locked';
  try {
    await chrome.management.setEnabled(info.id, false);
    return 'off';
  } catch (err) {
    console.error(`Update Guard: could not turn off ${info.id}:`, err);
    return 'failed';
  }
}

// Stops holding an extension and turns it back on if Update Guard turned it off.
async function release(info, state) {
  const held = state.pending[info.id];
  if (!held) return;
  delete state.pending[info.id];
  chrome.notifications.clear(info.id);
  if (held.disabledByGuard && !info.enabled) {
    // Fails if Chrome wants the user to accept new permissions first; then it stays off.
    await chrome.management.setEnabled(info.id, true).catch(() => {});
  }
}

// Compares one extension with its approved version and acts on the difference.
async function examine(info, state) {
  if (info.id === SELF || info.type === 'theme') return;
  const id = info.id;
  const now = snapshot(info);
  const approved = state.baseline[id];
  const held = state.pending[id];

  if (!approved) {
    // Newly installed: the user just chose this version.
    state.baseline[id] = now;
    return;
  }

  if (now.version === approved.version) {
    if (held) {
      // The update was rolled back to the approved version.
      await release(info, state);
      addLog(state, 'reverted', id, now.name, held.to.version, now.version);
    }
    return;
  }

  if (isExempt(info, state.settings)) {
    await release(info, state);
    state.baseline[id] = now;
    addLog(state, 'auto-approved', id, now.name, approved.version, now.version);
    return;
  }

  if (held && held.to.version === now.version) {
    // Already waiting for review. Something else turned it on: turn it off again.
    if (info.enabled && (await turnOff(info)) === 'off') {
      held.disabledByGuard = true;
      addLog(state, 'held-again', id, now.name, approved.version, now.version);
      notify(
        id,
        `${now.name} is still waiting for review`,
        `It was turned on before version ${now.version} was approved, so Update Guard turned it off again.`,
      );
    }
    return;
  }

  // A version nobody has approved yet.
  const result = await turnOff(info);
  state.pending[id] = {
    from: approved,
    to: now,
    detectedAt: Date.now(),
    disabledByGuard: result === 'off' || Boolean(held?.disabledByGuard),
  };

  const change = `${approved.version} → ${now.version}.`;
  const changes = diff(approved, now);
  const gained = [...changes.addedPermissions, ...changes.addedHosts];
  const access = gained.length ? ` New access: ${gained.join(', ')}.` : '';

  if (result === 'off') {
    addLog(state, 'held', id, now.name, approved.version, now.version);
    notify(id, `${now.name} updated and was turned off`, `${change}${access} Review it before turning it back on.`);
  } else if (result === 'already-off' && info.disabledReason === 'permissions_increase') {
    // Chrome turns an extension off by itself when an update asks for more access.
    addLog(state, 'more-access', id, now.name, approved.version, now.version);
    notify(
      id,
      `${now.name} updated and wants more access`,
      `${change}${access} Chrome turned it off. Review it before accepting.`,
    );
  } else if (result === 'already-off') {
    // The user had it turned off: no ping, it is just listed on the review page.
    addLog(state, 'updated-while-off', id, now.name, approved.version, now.version);
  } else {
    addLog(state, 'still-running', id, now.name, approved.version, now.version);
    const why = info.installType === 'admin' ? ' because it is installed by policy' : '';
    notify(
      id,
      `${now.name} updated and is still running`,
      `${change}${access} Update Guard could not turn it off${why}. Review it now.`,
    );
  }
}

// Checks every installed extension. Safe to run at any time, any number of times.
function reconcile() {
  return inTurn(async () => {
    const state = await loadState();
    state.baseline ??= {};
    const before = JSON.stringify(state);

    const all = await chrome.management.getAll();
    for (const info of all) await examine(info, state);

    const installed = new Set(all.map((info) => info.id));
    for (const id of Object.keys(state.baseline)) {
      if (!installed.has(id)) delete state.baseline[id];
    }
    for (const [id, held] of Object.entries(state.pending)) {
      if (installed.has(id)) continue;
      delete state.pending[id];
      chrome.notifications.clear(id);
      addLog(state, 'removed', id, held.to.name, held.from.version, held.to.version);
    }

    if (JSON.stringify(state) !== before) await save(state);
    await showBadge(state);
  });
}

// Requests from the review page.
const actions = {
  // Approve the version the user was looking at. The page turns the extension
  // back on itself, because Chrome wants a user gesture for that in some cases.
  approve: ({ id, version }) =>
    inTurn(async () => {
      const state = await loadState();
      const held = state.pending[id];
      if (!held) return { ok: false, error: 'That extension is no longer waiting for review.' };
      const info = await chrome.management.get(id);
      if (info.version !== version) {
        return { ok: false, error: `${info.name} updated again, to ${info.version}. Review that version instead.` };
      }
      state.baseline[id] = snapshot(info);
      delete state.pending[id];
      chrome.notifications.clear(id);
      addLog(state, 'approved', id, info.name, held.from.version, info.version);
      await save(state);
      await showBadge(state);
      return { ok: true };
    }),

  setTrusted: ({ id, trusted }) => changeSettings((settings) => {
    if (trusted) settings.trusted[id] = true;
    else delete settings.trusted[id];
  }),

  setGuardUnpacked: ({ value }) => changeSettings((settings) => {
    settings.guardUnpacked = Boolean(value);
  }),
};

async function changeSettings(change) {
  await inTurn(async () => {
    const state = await loadState();
    state.baseline ??= {};
    change(state.settings);
    await save(state);
  });
  await reconcile(); // releases anything that is exempt now
  return { ok: true };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const action = actions[message?.type];
  if (!action) return false;
  action(message).then(sendResponse, (err) => sendResponse({ ok: false, error: String(err?.message ?? err) }));
  return true; // the response is sent later
});

function openReview(notificationId) {
  chrome.runtime.openOptionsPage();
  if (typeof notificationId === 'string') chrome.notifications.clear(notificationId);
}

chrome.management.onInstalled.addListener(() => reconcile()); // fires for updates too
chrome.management.onEnabled.addListener(() => reconcile());
chrome.management.onUninstalled.addListener(() => reconcile());
chrome.runtime.onStartup.addListener(() => reconcile());
chrome.action.onClicked.addListener(() => openReview());
chrome.notifications.onClicked.addListener(openReview);
chrome.notifications.onButtonClicked.addListener(openReview);

// Also check whenever the worker starts, which catches updates that happened
// while Update Guard was off or the browser was closed.
reconcile();
