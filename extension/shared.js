// Shared by the service worker (background.js) and the review page (review.js).
//
// Stored in chrome.storage.local:
//   baseline  { id: snapshot }  the last version of each extension that was approved
//   pending   { id: { from, to, detectedAt, disabledByGuard } }  updates waiting for review
//   settings  see DEFAULT_SETTINGS
//   log       [{ at, id, name, event, from, to }]  newest last

export const DEFAULT_SETTINGS = {
  guardUnpacked: false, // also hold unpacked (developer mode) extensions
  trusted: {}, // extension id -> true: its updates are approved without asking
};

export async function loadState() {
  const stored = await chrome.storage.local.get(['baseline', 'pending', 'settings', 'log']);
  return {
    baseline: stored.baseline ?? null, // null until the first run has recorded every extension
    pending: stored.pending ?? {},
    settings: { ...DEFAULT_SETTINGS, ...stored.settings },
    log: stored.log ?? [],
  };
}

// The parts of chrome.management's ExtensionInfo worth comparing between versions.
export function snapshot(info) {
  return {
    name: info.name,
    version: info.version,
    description: info.description ?? '',
    permissions: [...(info.permissions ?? [])].sort(),
    hostPermissions: [...(info.hostPermissions ?? [])].sort(),
    homepageUrl: info.homepageUrl ?? '',
    updateUrl: info.updateUrl ?? '',
    installType: info.installType,
  };
}

// True if updates of this extension are approved without asking.
export function isExempt(info, settings) {
  return Boolean(settings.trusted[info.id]) || (info.installType === 'development' && !settings.guardUnpacked);
}

const COMPARED_FIELDS = [
  ['name', 'Name'],
  ['description', 'Description'],
  ['homepageUrl', 'Homepage'],
  ['updateUrl', 'Update source'],
  ['installType', 'Install type'],
];

export function diff(from, to) {
  const missingFrom = (list, other) => other.filter((item) => !list.includes(item));
  return {
    addedPermissions: missingFrom(from.permissions, to.permissions),
    removedPermissions: missingFrom(to.permissions, from.permissions),
    addedHosts: missingFrom(from.hostPermissions, to.hostPermissions),
    removedHosts: missingFrom(to.hostPermissions, from.hostPermissions),
    fields: COMPARED_FIELDS.filter(([key]) => from[key] !== to[key]).map(([key, label]) => ({
      label,
      from: from[key],
      to: to[key],
    })),
  };
}
