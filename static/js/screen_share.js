(() => {
  'use strict';
  const root = document.querySelector('[data-screen-root]');
  if (!root) return;
  const byId = (id) => document.getElementById(id);
  const message = byId('screen-message');
  const roleLabel = byId('screen-session-role');
  const stateLabel = byId('screen-session-state');
  let session = null, role = '', code = '', pc = null, stream = null, pollTimer = null, lastSequence = 0;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const show = (text, kind = 'secondary') => { message.className = `alert alert-${kind}`; message.textContent = text; };
  const api = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf);
    if (options.body) headers.set('Content-Type', 'application/json');
    const response = await fetch(url, {...options, headers, credentials: 'same-origin'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  };
  const sendSignal = (type, payload) => api(`/screen/api/sessions/${encodeURIComponent(session.session_id)}/signals`, {method: 'POST', body: JSON.stringify({role, code, type, payload})});
  const createPeer = () => {
    const peer = new RTCPeerConnection({iceServers: []});
    peer.onicecandidate = (event) => { if (event.candidate) sendSignal('ice', event.candidate.toJSON()).catch(() => {}); };
    peer.onconnectionstatechange = () => { stateLabel.textContent = peer.connectionState; };
    return peer;
  };
  const handleSignals = async (messages) => {
    for (const item of messages) {
      lastSequence = Math.max(lastSequence, Number(item.seq || 0));
      if (item.type === 'offer' && role === 'receiver') {
        await pc.setRemoteDescription(item.payload); const answer = await pc.createAnswer(); await pc.setLocalDescription(answer); await sendSignal('answer', pc.localDescription.toJSON());
      } else if (item.type === 'answer' && role === 'sender') await pc.setRemoteDescription(item.payload);
      else if (item.type === 'ice' && item.payload) { try { await pc.addIceCandidate(item.payload); } catch (_) {} }
      else if (item.type === 'bye') await stop(false);
    }
  };
  const poll = async () => {
    if (!session || !role) return;
    try {
      const suffix = `?role=${encodeURIComponent(role)}&after=${lastSequence}&code=${encodeURIComponent(code)}`;
      const data = await api(`/screen/api/sessions/${encodeURIComponent(session.session_id)}/signals${suffix}`);
      lastSequence = Math.max(lastSequence, Number(data.last_sequence || 0)); await handleSignals(data.messages || []);
    } catch (error) { show(`Session-Verbindung: ${error.message}`, 'warning'); }
    if (session) pollTimer = window.setTimeout(poll, 700);
  };
  const stop = async (notify = true) => {
    if (pollTimer) window.clearTimeout(pollTimer); pollTimer = null;
    if (notify && session) { try { await sendSignal('bye', null); } catch (_) {} }
    if (pc) pc.close(); pc = null;
    if (stream) stream.getTracks().forEach((track) => track.stop()); stream = null;
    session = null; role = ''; code = ''; lastSequence = 0;
    byId('screen-local-preview').srcObject = null; byId('screen-local-preview').classList.add('d-none');
    byId('screen-remote-video').srcObject = null; byId('screen-remote-video').classList.add('d-none');
    byId('screen-share-code-box').classList.add('d-none'); byId('screen-share-stop').disabled = true; byId('screen-receive-stop').disabled = true; byId('screen-fullscreen').disabled = true;
    roleLabel.textContent = '–'; stateLabel.textContent = 'nicht verbunden';
  };
  byId('screen-share-start').addEventListener('click', async () => {
    try {
      if (!navigator.mediaDevices?.getDisplayMedia) throw new Error('Bildschirmaufnahme wird von diesem Browser nicht unterstützt.');
      await stop(false); role = 'sender'; roleLabel.textContent = 'Sender';
      stream = await navigator.mediaDevices.getDisplayMedia({video: {frameRate: {ideal: 30, max: 60}}, audio: true});
      const created = await api('/screen/api/sessions', {method: 'POST', body: '{}'}); session = created.session; code = session.join_code;
      byId('screen-share-code').value = code; byId('screen-share-code-box').classList.remove('d-none');
      const preview = byId('screen-local-preview'); preview.srcObject = stream; preview.classList.remove('d-none'); byId('screen-share-stop').disabled = false;
      pc = createPeer(); stream.getTracks().forEach((track) => pc.addTrack(track, stream)); stream.getVideoTracks()[0]?.addEventListener('ended', () => stop(true));
      const offer = await pc.createOffer(); await pc.setLocalDescription(offer); await sendSignal('offer', pc.localDescription.toJSON()); poll(); show(`Teilen aktiv. Code: ${code}`, 'success');
    } catch (error) { await stop(false); show(error.message || 'Bildschirmfreigabe konnte nicht gestartet werden.', 'danger'); }
  });
  byId('screen-share-stop').addEventListener('click', () => stop(true));
  byId('screen-receive-start').addEventListener('click', async () => {
    try {
      await stop(false); code = String(byId('screen-join-code').value || '').trim().toUpperCase(); if (!code) throw new Error('Verbindungscode fehlt.');
      const resolved = await api(`/screen/api/join/${encodeURIComponent(code)}`); session = resolved.session; role = 'receiver'; roleLabel.textContent = 'Empfänger';
      pc = createPeer(); pc.ontrack = (event) => { const video = byId('screen-remote-video'); video.srcObject = event.streams[0]; video.classList.remove('d-none'); byId('screen-fullscreen').disabled = false; };
      byId('screen-receive-stop').disabled = false; poll(); show('Verbindung wird aufgebaut …', 'primary');
    } catch (error) { await stop(false); show(error.message || 'Verbindung fehlgeschlagen.', 'danger'); }
  });
  byId('screen-receive-stop').addEventListener('click', () => stop(true));
  byId('screen-fullscreen').addEventListener('click', () => byId('screen-remote-video').requestFullscreen?.());
  byId('screen-android-cast')?.addEventListener('click', () => {
    const result = window.SimpleOfficeNativeScreen?.openCast?.(); show(result === 'ok' ? 'Android Cast-Auswahl wurde geöffnet.' : 'Android Cast-Auswahl ist auf diesem Gerät nicht verfügbar.', result === 'ok' ? 'success' : 'warning');
  });
  window.addEventListener('beforeunload', () => { if (stream) stream.getTracks().forEach((track) => track.stop()); });
})();
