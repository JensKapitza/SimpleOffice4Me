(() => {
  'use strict';
  const root = document.querySelector('[data-screen-root]');
  if (!root) return;
  const byId = (id) => document.getElementById(id);
  const message = byId('screen-message');
  const roleLabel = byId('screen-session-role');
  const stateLabel = byId('screen-session-state');
  let session = null, role = '', code = '', pc = null, stream = null;
  let pollTimer = null, lastSequence = 0, stopping = false, pollFailures = 0, starting = false;
  const pendingCandidates = [];
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

  const show = (text, kind = 'secondary') => {
    message.className = `alert alert-${kind}`;
    message.textContent = text;
  };
  const setState = (value) => { stateLabel.textContent = value || 'nicht verbunden'; };
  const api = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf);
    if (options.body) headers.set('Content-Type', 'application/json');
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(url, {...options, headers, credentials: 'same-origin', signal: controller.signal});
      if (!response.ok) {
        const error = new Error(`Serverfehler HTTP ${response.status}`);
        error.status = response.status; throw error;
      }
      return await response.json();
    } finally { window.clearTimeout(timeout); }
  };
  const sendSignal = (type, payload) => {
    if (!session || !role) return Promise.reject(new Error('Keine aktive Session.'));
    return api(`/screen/api/sessions/${encodeURIComponent(session.session_id)}/signals`, {
      method: 'POST', body: JSON.stringify({role, code, type, payload})
    });
  };
  const signalFailure = (context, error) => {
    const detail = error instanceof Error ? error.message : String(error || 'unbekannt');
    show(`${context}: ${detail}`, 'danger'); setState('Fehler');
  };
  const flushCandidates = async () => {
    if (!pc?.remoteDescription) return;
    while (pendingCandidates.length) await pc.addIceCandidate(pendingCandidates.shift());
  };
  const createPeer = () => {
    const peer = new RTCPeerConnection({iceServers: []});
    peer.onicecandidate = (event) => {
      if (event.candidate) sendSignal('ice', event.candidate.toJSON())
        .catch((error) => signalFailure('ICE-Kandidat konnte nicht übertragen werden', error));
    };
    peer.onconnectionstatechange = () => {
      setState(peer.connectionState);
      if (peer.connectionState === 'failed') signalFailure('WebRTC-Verbindung fehlgeschlagen', 'ICE-Aushandlung ohne Ergebnis');
    };
    peer.onicecandidateerror = (event) => signalFailure('ICE-Fehler', event.errorText || event.errorCode);
    return peer;
  };
  const handleSignals = async (messages) => {
    for (const item of messages) {
      lastSequence = Math.max(lastSequence, Number(item.seq || 0));
      try {
        if (item.type === 'offer' && role === 'receiver') {
          await pc.setRemoteDescription(item.payload); await flushCandidates();
          const answer = await pc.createAnswer(); await pc.setLocalDescription(answer);
          await sendSignal('answer', pc.localDescription.toJSON());
        } else if (item.type === 'answer' && role === 'sender') {
          await pc.setRemoteDescription(item.payload); await flushCandidates();
        } else if (item.type === 'ice' && item.payload) {
          if (pc.remoteDescription) await pc.addIceCandidate(item.payload);
          else pendingCandidates.push(item.payload);
        } else if (item.type === 'bye') {
          show('Die Gegenstelle hat die Verbindung beendet.', 'secondary'); await stop(false, false);
        }
      } catch (error) {
        signalFailure(`WebRTC-${item.type}-Verarbeitung fehlgeschlagen`, error);
        await stop(true, false); return;
      }
    }
  };
  const poll = async () => {
    if (!session || !role || stopping) return;
    const polledSession = session;
    try {
      const suffix = `?role=${encodeURIComponent(role)}&after=${lastSequence}`;
      const data = await api(`/screen/api/sessions/${encodeURIComponent(session.session_id)}/signals${suffix}`, {headers: {'X-Screen-Code': code}});
      if (session !== polledSession || stopping) return;
      await handleSignals(data.messages || []);
      pollFailures = 0;
      lastSequence = Math.max(lastSequence, Number(data.last_sequence || 0));
    } catch (error) {
      if (session !== polledSession || stopping) return;
      signalFailure('Signaling-Verbindung fehlgeschlagen', error);
      pollFailures += 1;
      if ([403, 404, 410].includes(error.status) || pollFailures >= 3) {
        await stop(false, false); return;
      }
    }
    if (session === polledSession && !stopping) pollTimer = window.setTimeout(poll, pollFailures ? 700 * (2 ** pollFailures) : 700);
  };
  const stop = async (notify = true, resetMessage = true) => {
    if (stopping) return;
    stopping = true;
    if (pollTimer) window.clearTimeout(pollTimer); pollTimer = null;
    // Start notification while identifiers still exist, but release capture immediately.
    const notification = notify && session ? sendSignal('bye', null).catch(() => {}) : Promise.resolve();
    if (pc) pc.close(); pc = null;
    if (stream) stream.getTracks().forEach((track) => track.stop()); stream = null;
    try { window.SimpleOfficeNativeScreen?.stopShare?.(); } catch (_) { /* optional bridge */ }
    session = null; role = ''; code = ''; lastSequence = 0; pendingCandidates.length = 0;
    byId('screen-local-preview').srcObject = null; byId('screen-local-preview').classList.add('d-none');
    byId('screen-remote-video').srcObject = null; byId('screen-remote-video').classList.add('d-none');
    byId('screen-share-code-box').classList.add('d-none');
    byId('screen-share-code').value = '';
    const qr = byId('screen-share-qr'); qr.removeAttribute('src'); qr.classList.add('d-none');
    byId('screen-share-start').disabled = false;
    byId('screen-share-stop').disabled = true; byId('screen-receive-stop').disabled = true; byId('screen-fullscreen').disabled = true;
    roleLabel.textContent = '–'; setState('nicht verbunden'); pollFailures = 0; stopping = false;
    if (resetMessage) show('Freigabe beendet.', 'secondary');
    await notification;
  };
  const captureDisplay = async () => {
    if (window.SimpleOfficeNativeScreen?.startShare) {
      const nativeStream = await window.SimpleOfficeNativeScreen.startShare();
      if (nativeStream instanceof MediaStream && nativeStream.getVideoTracks().length) return nativeStream;
    }
    if (!navigator.mediaDevices?.getDisplayMedia) throw new Error('Bildschirmaufnahme wird von diesem Browser nicht unterstützt.');
    return navigator.mediaDevices.getDisplayMedia({video: {frameRate: {ideal: 30, max: 60}}, audio: true});
  };

  byId('screen-share-start').addEventListener('click', async () => {
    if (starting || session || stream) return;
    starting = true;
    const startButton = byId('screen-share-start');
    startButton.disabled = true;
    try {
      role = 'sender'; roleLabel.textContent = 'Sender'; setState('Code wird erzeugt');
      const created = await api('/screen/api/sessions', {method: 'POST', body: '{}'});
      session = created.session; code = session.join_code;
      byId('screen-share-code').value = code;
      byId('screen-share-code-box').classList.remove('d-none');
      const qr = byId('screen-share-qr');
      qr.src = `/screen/api/sessions/${encodeURIComponent(session.session_id)}/connect-qr.svg`;
      qr.classList.remove('d-none');
      byId('screen-share-stop').disabled = false;
      show(`Verbindungscode ${code} ist bereit. Jetzt Bildschirmfreigabe auswählen.`, 'primary');
      setState('Bildschirmauswahl');
      stream = await captureDisplay();
      const preview = byId('screen-local-preview'); preview.srcObject = stream; preview.classList.remove('d-none');
      pc = createPeer(); stream.getTracks().forEach((track) => pc.addTrack(track, stream));
      stream.getVideoTracks()[0]?.addEventListener('ended', () => stop(true));
      const offer = await pc.createOffer(); await pc.setLocalDescription(offer);
      await sendSignal('offer', pc.localDescription.toJSON()); poll();
      show(`Teilen aktiv. Verbindungscode: ${code}`, 'success'); setState('wartet auf Empfänger');
    } catch (error) {
      await stop(false, false);
      signalFailure('Bildschirmfreigabe konnte nicht gestartet werden', error);
    } finally {
      starting = false;
      if (!session) startButton.disabled = false;
    }
  });
  byId('screen-share-stop').addEventListener('click', () => stop(true));
  byId('screen-copy-code').addEventListener('click', async () => {
    const value = byId('screen-share-code').value;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      show('Verbindungscode kopiert.', 'success');
    } catch (_) {
      byId('screen-share-code').select();
      show('Verbindungscode ist markiert und kann kopiert werden.', 'secondary');
    }
  });
  byId('screen-receive-start').addEventListener('click', async () => {
    try {
      await stop(false, false); code = String(byId('screen-join-code').value || '').trim().toUpperCase();
      if (!code) throw new Error('Verbindungscode fehlt.');
      const resolved = await api('/screen/api/join', {method: 'POST', body: JSON.stringify({code})});
      session = resolved.session; role = 'receiver'; roleLabel.textContent = 'Empfänger'; setState('verbindet');
      pc = createPeer();
      pc.ontrack = (event) => {
        const video = byId('screen-remote-video'); video.srcObject = event.streams[0];
        video.classList.remove('d-none'); byId('screen-fullscreen').disabled = false;
      };
      byId('screen-receive-stop').disabled = false; poll(); show('Verbindung wird aufgebaut …', 'primary');
    } catch (error) { await stop(false, false); signalFailure('Verbindung fehlgeschlagen', error); }
  });
  byId('screen-receive-stop').addEventListener('click', () => stop(true));
  byId('screen-fullscreen').addEventListener('click', async () => {
    try { await byId('screen-remote-video').requestFullscreen?.(); }
    catch (error) { signalFailure('Vollbild konnte nicht geöffnet werden', error); }
  });
  byId('screen-android-cast')?.addEventListener('click', () => {
    const result = window.SimpleOfficeNativeScreen?.openCast?.();
    show(result === 'ok' ? 'Android Anzeige-Auswahl wurde geöffnet.' : 'Auf diesem Gerät ist keine System-Anzeige-Auswahl verfügbar.', result === 'ok' ? 'success' : 'warning');
  });
  document.querySelectorAll('[data-desktop-screen-action]').forEach((button) => button.addEventListener('click', async (event) => {
    if (!window.simpleOfficeDesktop?.screenAction) return;
    event.preventDefault();
    try {
      const result = await window.simpleOfficeDesktop?.screenAction(button.dataset.desktopScreenAction);
      show(result?.message || 'Systemdialog wurde geöffnet.', 'success');
    } catch (error) { signalFailure('Systemdialog konnte nicht geöffnet werden', error); }
  }));
  window.addEventListener('beforeunload', () => {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    try { window.SimpleOfficeNativeScreen?.stopShare?.(); } catch (_) { /* optional bridge */ }
  });
})();
