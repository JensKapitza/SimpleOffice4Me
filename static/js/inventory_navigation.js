(() => {
  'use strict';

  const safeLocalUrl = (value) => {
    try {
      const parsed = new URL(String(value || ''), window.location.origin);
      if (parsed.origin !== window.location.origin) return '';
      if (!['http:', 'https:'].includes(parsed.protocol)) return '';
      return parsed.href;
    } catch (_error) {
      return '';
    }
  };

  const safeMarketplaceUrl = (value) => {
    try {
      const parsed = new URL(String(value || ''));
      if (parsed.protocol !== 'https:') return '';
      const host = parsed.hostname.toLowerCase();
      const allowed = host === 'amazon.de' || host.endsWith('.amazon.de') || host === 'ebay.de' || host.endsWith('.ebay.de');
      return allowed ? parsed.href : '';
    } catch (_error) {
      return '';
    }
  };

  const navigateLocal = (value, setStatus) => {
    const target = safeLocalUrl(value);
    if (!target) {
      setStatus('Unsicheres Navigationsziel wurde blockiert.', 'danger');
      return false;
    }
    window.location.assign(target);
    return true;
  };

  window.SimpleOfficeInventoryNavigation = {safeLocalUrl, safeMarketplaceUrl, navigateLocal};
})();
