(() => {
  const root = document.getElementById('main-content');
  const target = document.getElementById('chat-messages');
  const scroll = document.getElementById('chat-scroll');
  const files = document.getElementById('chat-files');
  const fileStatus = document.getElementById('chat-file-status');
  const body = document.getElementById('chat-body');
  const form = document.getElementById('chat-compose');
  const replyTo = document.getElementById('chat-reply-to');
  const replyPreview = document.getElementById('chat-reply-preview');
  const replySender = document.getElementById('chat-reply-sender');
  const replyText = document.getElementById('chat-reply-text');
  const replyCancel = document.getElementById('chat-reply-cancel');
  const fragmentUrl = root ? root.dataset.chatFragmentUrl : '';
  const androidApp = /SimpleOffice4Me-Android\//i.test(navigator.userAgent || '');
  let lastHtml = target ? target.innerHTML : '';

  const nearBottom = () => !scroll || scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 140;
  const toBottom = () => { if (scroll) scroll.scrollTop = scroll.scrollHeight; };
  const shareReplyFields = () => document.querySelectorAll('.chat-share-reply');

  function setReply(messageId, sender, text) {
    if (!replyTo || !replyPreview) return;
    replyTo.value = messageId || '';
    shareReplyFields().forEach(field => { field.value = replyTo.value; });
    if (!messageId) {
      replyPreview.classList.add('d-none');
      if (replySender) replySender.textContent = '';
      if (replyText) replyText.textContent = '';
      return;
    }
    if (replySender) replySender.textContent = `Antwort an ${sender || 'Nachricht'}`;
    if (replyText) replyText.textContent = text || 'Nachricht';
    replyPreview.classList.remove('d-none');
    if (body) body.focus();
  }

  function bindMessageActions() {
    document.querySelectorAll('.chat-reply').forEach(button => {
      if (button.dataset.bound === '1') return;
      button.dataset.bound = '1';
      button.addEventListener('click', () => setReply(button.dataset.messageId, button.dataset.sender, button.dataset.body));
    });
  }

  function bindAndroidCallHandoff() {
    if (!androidApp) return;
    document.querySelectorAll('.chat-call-form[data-sip-uri]').forEach(callForm => {
      if (callForm.dataset.androidBound === '1') return;
      callForm.dataset.androidBound = '1';
      callForm.addEventListener('submit', event => {
        const sipUri = String(callForm.dataset.sipUri || '').trim();
        if (!sipUri.startsWith('sip:')) return;
        event.preventDefault();
        const bridge = new URL('https://sip.simpleoffice.local/call');
        bridge.searchParams.set('target', sipUri);
        window.location.href = bridge.toString();
      });
    });
  }

  toBottom();
  bindMessageActions();
  bindAndroidCallHandoff();

  if (files && fileStatus) {
    files.addEventListener('change', () => {
      const count = files.files.length;
      fileStatus.textContent = count ? `${count} Datei${count === 1 ? '' : 'en'} gewählt` : 'Keine Datei gewählt';
    });
  }

  if (body && form) {
    body.addEventListener('keydown', event => {
      if (event.key === 'Enter' && event.ctrlKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
  }

  if (replyCancel) replyCancel.addEventListener('click', () => setReply('', '', ''));

  document.querySelectorAll('[data-open-share]').forEach(trigger => {
    trigger.addEventListener('click', () => {
      const panel = document.getElementById('chat-share-panel');
      if (panel) panel.open = true;
    });
  });

  if (!target || !fragmentUrl) return;
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const keepBottom = nearBottom();
      const response = await fetch(fragmentUrl, { headers: { Accept: 'text/html' }, cache: 'no-store' });
      if (!response.ok) return;
      const html = await response.text();
      if (html === lastHtml) return;
      target.innerHTML = html;
      lastHtml = html;
      bindMessageActions();
      bindAndroidCallHandoff();
      if (keepBottom) toBottom();
    } catch (_error) {
      // Polling is best-effort; normal navigation remains fully functional.
    }
  }, 2500);
})();
