(() => {
  'use strict';

  const shell = document.querySelector('[data-template-editor]');
  if (!shell) return;

  const canvas = document.getElementById('template-canvas');
  const layer = document.getElementById('template-elements-layer');
  const form = document.getElementById('template-editor-form');
  const hidden = document.getElementById('layout-json');
  const layoutNode = document.getElementById('template-layout-data');
  const previewNode = document.getElementById('template-preview-tokens');
  const assetUrlTemplate = canvas.dataset.assetUrlTemplate || '';
  let layout = JSON.parse(layoutNode.textContent || '{}');
  const previewTokens = JSON.parse(previewNode.textContent || '{}');
  let selectedId = null;
  let previewMode = false;
  let snapGrid = 5;
  let history = [JSON.stringify(layout)];
  let historyIndex = 0;

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const round = value => Math.round(value * 100) / 100;
  const byId = id => layout.elements.find(item => item.id === id);
  const makeId = prefix => `${prefix}-${globalThis.crypto?.randomUUID?.() || Date.now().toString(36)}`;

  const snap = value => snapGrid > 0 ? round(Math.round(Number(value) / snapGrid) * snapGrid) : round(Number(value));

  function updateHistoryControls() {
    const undo = document.querySelector('[data-action="undo"]');
    const redo = document.querySelector('[data-action="redo"]');
    if (undo) undo.disabled = historyIndex <= 0;
    if (redo) redo.disabled = historyIndex >= history.length - 1;
  }

  function commitHistory() {
    const snapshot = JSON.stringify(layout);
    if (history[historyIndex] === snapshot) {
      updateHistoryControls();
      return;
    }
    history = history.slice(0, historyIndex + 1);
    history.push(snapshot);
    if (history.length > 50) history.shift();
    historyIndex = history.length - 1;
    updateHistoryControls();
  }

  function restoreHistory(index) {
    if (index < 0 || index >= history.length) return;
    historyIndex = index;
    layout = JSON.parse(history[historyIndex]);
    if (selectedId && !byId(selectedId)) selectedId = null;
    renderRegions();
    renderElements();
    updateHistoryControls();
  }

  function undo() { restoreHistory(historyIndex - 1); }
  function redo() { restoreHistory(historyIndex + 1); }

  function tokenPreview(text) {
    let source = String(text);
    const seen = new Set([source]);
    for (let pass = 0; pass < 8; pass += 1) {
      const expanded = source.replace(/\{\{([a-z0-9_.-]{1,80})\}\}/gi, (match, token) => (
        Object.prototype.hasOwnProperty.call(previewTokens, token) ? previewTokens[token] : match
      ));
      if (expanded === source) return expanded;
      if (expanded.length > 10000 || seen.has(expanded)) throw new Error('invalid nested token expansion');
      seen.add(expanded);
      source = expanded;
    }
    if (/\{\{([a-z0-9_.-]{1,80})\}\}/i.test(source)) throw new Error('token expansion depth exceeded');
    return source;
  }

  function macroPreviewRuns(text) {
    const source = tokenPreview(text);
    const tagRe = /\[(\/?)(B|I|U|SIZE|COLOR|ALIGN)(?:=([^\]]+))?\]/gi;
    let state = {bold:false,italic:false,underline:false,fontSize:null,color:null,align:null};
    const stack = [];
    const runs = [];
    let position = 0;

    const append = fragment => {
      if (!fragment) return;
      runs.push({text:fragment, ...state});
    };
    for (const match of source.matchAll(tagRe)) {
      append(source.slice(position, match.index));
      const closing = match[1] === '/';
      const tag = match[2].toUpperCase();
      const argument = match[3];
      if (closing) {
        const previous = stack.pop();
        if (!previous || previous.tag !== tag) throw new Error('invalid macro nesting');
        state = previous.state;
      } else {
        const previous = {...state};
        if (tag === 'B') state.bold = true;
        else if (tag === 'I') state.italic = true;
        else if (tag === 'U') state.underline = true;
        else if (tag === 'SIZE') {
          const size = Number(argument);
          if (!Number.isFinite(size) || size < 6 || size > 72) throw new Error('invalid size');
          state.fontSize = size;
        } else if (tag === 'COLOR') {
          if (!/^#[0-9a-f]{6}$/i.test(argument || '')) throw new Error('invalid color');
          state.color = argument;
        } else if (tag === 'ALIGN') {
          if (!['left','center','right','justify'].includes(String(argument || '').toLowerCase())) throw new Error('invalid alignment');
          state.align = String(argument).toLowerCase();
        }
        stack.push({tag, state:previous});
      }
      position = match.index + match[0].length;
    }
    append(source.slice(position));
    if (stack.length) throw new Error('unclosed macro');
    return runs;
  }

  function renderElementText(node, text) {
    if (!previewMode) {
      node.appendChild(document.createTextNode(String(text || '')));
      return;
    }
    try {
      for (const run of macroPreviewRuns(text || '')) {
        const span = document.createElement('span');
        span.textContent = run.text;
        span.style.fontWeight = run.bold ? '700' : '';
        span.style.fontStyle = run.italic ? 'italic' : '';
        span.style.textDecoration = run.underline ? 'underline' : '';
        if (run.fontSize) span.style.fontSize = `${run.fontSize}pt`;
        if (run.color) span.style.color = run.color;
        if (run.align) {
          span.style.display = 'block';
          span.style.width = '100%';
          span.style.textAlign = run.align;
        }
        node.appendChild(span);
      }
    } catch (_error) {
      node.appendChild(document.createTextNode(tokenPreview(text || '')));
    }
  }

  function syncHidden() {
    hidden.value = JSON.stringify(layout);
  }

  function selected() {
    return selectedId ? byId(selectedId) : null;
  }

  function mmToPercent(value, axis) {
    return (Number(value) / (axis === 'x' ? 210 : 297)) * 100;
  }

  function renderRegions() {
    for (const name of ['header', 'footer', 'margin_left', 'margin_right']) {
      const node = canvas.querySelector(`[data-region="${name}"]`);
      if (node) node.classList.toggle('is-disabled', !layout.regions?.[name]?.enabled);
    }
    for (const input of document.querySelectorAll('[data-region-enabled]')) {
      const region = input.dataset.regionEnabled;
      input.checked = Boolean(layout.regions?.[region]?.enabled);
    }
  }

  function renderElements() {
    layer.replaceChildren();
    const ordered = [...layout.elements].sort((a, b) => Number(a.z_index || 0) - Number(b.z_index || 0));
    for (const element of ordered) {
      const node = document.createElement('div');
      node.className = 'template-element';
      node.dataset.elementId = element.id;
      node.dataset.type = element.type;
      node.tabIndex = 0;
      node.setAttribute('role', 'button');
      node.setAttribute('aria-label', `Vorlagenelement ${element.id}`);
      node.style.left = `${mmToPercent(element.x_mm, 'x')}%`;
      node.style.top = `${mmToPercent(element.y_mm, 'y')}%`;
      node.style.width = `${mmToPercent(element.width_mm, 'x')}%`;
      node.style.height = `${mmToPercent(element.height_mm, 'y')}%`;
      node.style.zIndex = String(element.z_index || 1);
      const style = element.style || {};
      node.style.fontSize = `${style.font_size || 10}pt`;
      node.style.fontFamily = style.font_family || 'Helvetica, Arial, sans-serif';
      node.style.fontWeight = style.bold ? '700' : '400';
      node.style.fontStyle = style.italic ? 'italic' : 'normal';
      node.style.textDecoration = style.underline ? 'underline' : 'none';
      node.style.color = style.color || '#111827';
      node.style.textAlign = style.align || 'left';
      node.classList.toggle('is-selected', selectedId === element.id);
      node.classList.toggle('is-locked', Boolean(element.locked));
      node.classList.toggle('is-hidden', element.visible === false);
      if (element.type === 'image') {
        node.classList.add('image-template-element');
        const image = document.createElement('img');
        image.alt = 'Vorlagenbild';
        image.draggable = false;
        image.src = assetUrlTemplate.replace('__ASSET__', encodeURIComponent(element.asset || ''));
        node.appendChild(image);
      } else if (element.type === 'invoice_lines') {
        node.classList.add('dynamic-template-element');
        node.textContent = 'Rechnungspositionen / Tabelle';
      } else if (element.type === 'invoice_totals') {
        node.classList.add('dynamic-template-element');
        node.textContent = 'Summen / Gesamtbetrag';
      } else if (element.type === 'body') {
        node.classList.add('dynamic-template-element');
        node.textContent = 'Dokumenttext / Fließtext';
      } else if (!['line', 'rect'].includes(element.type)) {
        renderElementText(node, element.text || '');
      }

      const handle = document.createElement('span');
      handle.className = 'resize-handle';
      handle.setAttribute('aria-hidden', 'true');
      node.appendChild(handle);
      layer.appendChild(node);
    }
    updateProperties();
    syncHidden();
  }

  function updateProperties() {
    const item = selected();
    const empty = document.getElementById('no-selection');
    const props = document.getElementById('element-properties');
    empty.hidden = Boolean(item);
    props.hidden = !item;
    document.querySelectorAll('[data-format],[data-align],[data-prop],[data-metric],[data-action="delete-element"],[data-action="duplicate-element"],[data-action="layer-up"],[data-action="layer-down"]').forEach(control => {
      control.disabled = !item;
    });
    if (!item) return;

    const textInput = document.querySelector('[data-element-text]');
    textInput.value = item.text || '';
    textInput.disabled = ['line','rect','image','invoice_lines','invoice_totals','body'].includes(item.type);
    document.querySelector('[data-element-region]').value = item.region || 'content';
    document.querySelector('[data-element-locked]').checked = Boolean(item.locked);
    document.querySelector('[data-element-visible]').checked = item.visible !== false;
    for (const input of document.querySelectorAll('[data-metric]')) {
      input.value = item[input.dataset.metric] ?? '';
    }
    const style = item.style || {};
    document.querySelector('[data-prop="color"]').value = style.color || '#111827';
    document.querySelector('[data-prop="font_family"]').value = style.font_family || 'Helvetica';
    document.querySelector('[data-prop="font_size"]').value = String(style.font_size || 10);
    for (const button of document.querySelectorAll('[data-format]')) {
      button.classList.toggle('active', Boolean(style[button.dataset.format]));
    }
    for (const button of document.querySelectorAll('[data-align]')) {
      button.classList.toggle('active', style.align === button.dataset.align);
    }
  }

  function selectElement(id) {
    selectedId = id;
    renderElements();
  }

  function setElementText(value) {
    const item = selected();
    if (!item) return;
    item.text = String(value).slice(0, 4000);
    renderElements();
  }

  function addToken(token) {
    const item = {
      id: makeId('token'),
      type: 'token',
      region: 'content',
      x_mm: 20,
      y_mm: 110 + (layout.elements.length % 8) * 10,
      width_mm: 80,
      height_mm: 9,
      text: `{{${token}}}`,
      style: {font_size:10,font_family:'Helvetica',bold:false,italic:false,underline:false,color:'#111827',align:'left'},
      z_index: Math.max(1, ...layout.elements.map(row => Number(row.z_index || 1))) + 1,
      locked: false,
      visible: true,
    };
    layout.elements.push(item);
    selectElement(item.id);
    commitHistory();
  }

  function addBasic(type) {
    const isLine = type === 'line';
    const dynamicSizes = {
      image: {width:45, height:24},
      invoice_lines: {width:174, height:68},
      invoice_totals: {width:80, height:30},
      body: {width:174, height:110},
    };
    const dynamic = dynamicSizes[type];
    const item = {
      id: makeId(type),
      type,
      region: 'content',
      x_mm: dynamic ? 18 : 25,
      y_mm: 120 + (layout.elements.length % 8) * 8,
      width_mm: dynamic ? dynamic.width : (type === 'rect' ? 55 : 80),
      height_mm: dynamic ? dynamic.height : (isLine ? 1 : (type === 'rect' ? 18 : 10)),
      text: type === 'text' ? 'Text' : '',
      asset: '',
      style: {font_size:10,font_family:'Helvetica',bold:false,italic:false,underline:false,color:'#111827',align:'left'},
      z_index: Math.max(1, ...layout.elements.map(row => Number(row.z_index || 1))) + 1,
      locked: false,
      visible: true,
    };
    layout.elements.push(item);
    selectElement(item.id);
    commitHistory();
  }

  function insertMacro(token) {
    const item = selected();
    if (!item || ['line', 'rect', 'image', 'invoice_lines', 'invoice_totals', 'body'].includes(item.type)) return;
    const text = `{{${token}}}`;
    item.text = `${item.text || ''}${item.text ? ' ' : ''}${text}`.slice(0, 4000);
    renderElements();
    commitHistory();
  }

  layer.addEventListener('pointerdown', event => {
    const node = event.target.closest('.template-element');
    if (!node) return;
    const item = byId(node.dataset.elementId);
    if (!item) return;
    selectedId = item.id;
    renderElements();
    if (item.locked) return;

    const start = {x:event.clientX, y:event.clientY, xMm:Number(item.x_mm), yMm:Number(item.y_mm), w:Number(item.width_mm), h:Number(item.height_mm)};
    const resizing = event.target.classList.contains('resize-handle');
    const bounds = canvas.getBoundingClientRect();
    event.preventDefault();

    const move = moveEvent => {
      const dx = (moveEvent.clientX - start.x) / bounds.width * 210;
      const dy = (moveEvent.clientY - start.y) / bounds.height * 297;
      if (resizing) {
        item.width_mm = clamp(snap(start.w + dx), 1, 210 - item.x_mm);
        item.height_mm = clamp(snap(start.h + dy), 1, 297 - item.y_mm);
      } else {
        item.x_mm = clamp(snap(start.xMm + dx), 0, 210 - item.width_mm);
        item.y_mm = clamp(snap(start.yMm + dy), 0, 297 - item.height_mm);
      }
      renderElements();
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      commitHistory();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up, {once:true});
  });

  layer.addEventListener('click', event => {
    const node = event.target.closest('.template-element');
    if (node) selectElement(node.dataset.elementId);
  });

  document.addEventListener('click', event => {
    const tokenButton = event.target.closest('[data-add-token]');
    if (tokenButton) return addToken(tokenButton.dataset.addToken);
    const basicButton = event.target.closest('[data-add-element]');
    if (basicButton) return addBasic(basicButton.dataset.addElement);
    const quickToken = event.target.closest('[data-quick-token]');
    if (quickToken) return insertMacro(quickToken.dataset.quickToken);

    const align = event.target.closest('[data-align]');
    if (align) {
      const item = selected(); if (!item) return;
      item.style.align = align.dataset.align; renderElements(); commitHistory(); return;
    }
    const format = event.target.closest('[data-format]');
    if (format) {
      const item = selected(); if (!item) return;
      const key = format.dataset.format;
      item.style[key] = !item.style[key]; renderElements(); commitHistory(); return;
    }
    const action = event.target.closest('[data-action]');
    if (!action) return;
    if (action.dataset.action === 'undo') {
      undo();
    } else if (action.dataset.action === 'redo') {
      redo();
    } else if (action.dataset.action === 'toggle-preview') {
      previewMode = !previewMode;
      canvas.classList.toggle('template-preview-mode', previewMode);
      action.classList.toggle('active', previewMode);
      renderElements();
    } else if (action.dataset.action === 'delete-element') {
      if (!selectedId) return;
      layout.elements = layout.elements.filter(row => row.id !== selectedId);
      selectedId = null; renderElements(); commitHistory();
    } else if (action.dataset.action === 'duplicate-element') {
      const item = selected(); if (!item) return;
      const clone = structuredClone(item);
      clone.id = makeId(item.type);
      clone.x_mm = round(clamp(Number(clone.x_mm) + 4, 0, 210 - Number(clone.width_mm)));
      clone.y_mm = round(clamp(Number(clone.y_mm) + 4, 0, 297 - Number(clone.height_mm)));
      clone.z_index = Math.max(1, ...layout.elements.map(row => Number(row.z_index || 1))) + 1;
      layout.elements.push(clone); selectElement(clone.id); commitHistory();
    } else if (action.dataset.action === 'layer-up' || action.dataset.action === 'layer-down') {
      const item = selected(); if (!item) return;
      const delta = action.dataset.action === 'layer-up' ? 1 : -1;
      item.z_index = clamp(Number(item.z_index || 1) + delta, 0, 999);
      renderElements(); commitHistory();
    } else if (action.dataset.action === 'insert-macro') {
      insertMacro(document.getElementById('macro-token').value);
    }
  });

  document.querySelector('[data-element-text]').addEventListener('input', event => setElementText(event.target.value));
  document.querySelector('[data-element-text]').addEventListener('change', () => commitHistory());
  document.querySelector('[data-element-region]').addEventListener('change', event => {
    const item = selected(); if (!item) return; item.region = event.target.value; renderElements(); commitHistory();
  });
  document.querySelector('[data-element-locked]').addEventListener('change', event => {
    const item = selected(); if (!item) return; item.locked = event.target.checked; renderElements(); commitHistory();
  });
  document.querySelector('[data-element-visible]').addEventListener('change', event => {
    const item = selected(); if (!item) return; item.visible = event.target.checked; renderElements(); commitHistory();
  });
  document.querySelector('[data-prop="color"]').addEventListener('input', event => {
    const item = selected(); if (!item) return; item.style.color = event.target.value; renderElements(); commitHistory();
  });
  document.querySelector('[data-prop="font_family"]').addEventListener('change', event => {
    const item = selected(); if (!item) return;
    item.style.font_family = event.target.value;
    renderElements(); commitHistory();
  });
  document.querySelector('[data-prop="font_size"]').addEventListener('change', event => {
    const item = selected(); if (!item) return; item.style.font_size = Number(event.target.value); renderElements(); commitHistory();
  });
  for (const input of document.querySelectorAll('[data-metric]')) {
    input.addEventListener('change', event => {
      const item = selected(); if (!item) return;
      const key = input.dataset.metric;
      const max = ['x_mm','width_mm'].includes(key) ? 210 : 297;
      item[key] = round(clamp(Number(event.target.value || 0), key.includes('width') || key.includes('height') ? 1 : 0, max));
      if (item.x_mm + item.width_mm > 210) item.x_mm = round(210 - item.width_mm);
      if (item.y_mm + item.height_mm > 297) item.y_mm = round(297 - item.height_mm);
      renderElements(); commitHistory();
    });
  }
  for (const input of document.querySelectorAll('[data-region-enabled]')) {
    input.addEventListener('change', event => {
      const region = event.target.dataset.regionEnabled;
      layout.regions[region].enabled = event.target.checked;
      renderRegions(); syncHidden(); commitHistory();
    });
  }
  for (const input of document.querySelectorAll('[data-free-text]')) {
    input.value = layout.free_text?.[input.dataset.freeText] || '';
    input.addEventListener('input', event => {
      layout.free_text[input.dataset.freeText] = event.target.value.slice(0, 500);
      previewTokens[input.dataset.freeText] = layout.free_text[input.dataset.freeText];
      syncHidden();
      if (previewMode) renderElements();
    });
    input.addEventListener('change', () => commitHistory());
  }

  const snapSelect = document.querySelector('[data-snap-grid]');
  if (snapSelect) {
    snapGrid = Number(snapSelect.value || 0);
    canvas.classList.toggle('snap-grid', snapGrid > 0);
    snapSelect.addEventListener('change', () => {
      snapGrid = Number(snapSelect.value || 0);
      canvas.classList.toggle('snap-grid', snapGrid > 0);
    });
  }

  document.addEventListener('keydown', event => {
    const target = event.target;
    const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      if (event.shiftKey) redo(); else undo();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
      event.preventDefault(); redo(); return;
    }
    if (editing) return;
    const item = selected();
    if (!item) return;
    if ((event.key === 'Delete' || event.key === 'Backspace') && !item.locked) {
      event.preventDefault();
      layout.elements = layout.elements.filter(row => row.id !== selectedId);
      selectedId = null; renderElements(); commitHistory(); return;
    }
    if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key) || item.locked) return;
    event.preventDefault();
    const step = event.shiftKey ? 5 : 1;
    if (event.key === 'ArrowLeft') item.x_mm = round(clamp(Number(item.x_mm) - step, 0, 210 - Number(item.width_mm)));
    if (event.key === 'ArrowRight') item.x_mm = round(clamp(Number(item.x_mm) + step, 0, 210 - Number(item.width_mm)));
    if (event.key === 'ArrowUp') item.y_mm = round(clamp(Number(item.y_mm) - step, 0, 297 - Number(item.height_mm)));
    if (event.key === 'ArrowDown') item.y_mm = round(clamp(Number(item.y_mm) + step, 0, 297 - Number(item.height_mm)));
    renderElements(); commitHistory();
  });

  const imageUpload = document.querySelector('[data-image-upload]');
  if (imageUpload) {
    imageUpload.addEventListener('change', async () => {
      const file = imageUpload.files?.[0];
      if (!file) return;
      const body = new FormData();
      body.append('image', file);
      imageUpload.disabled = true;
      try {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
        const response = await fetch(imageUpload.dataset.uploadUrl, {
          method: 'POST',
          body,
          credentials: 'same-origin',
          headers: {
            'Accept': 'application/json',
            'X-CSRF-Token': csrf,
          },
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || 'Bild konnte nicht hochgeladen werden');
        const item = {
          id: makeId('image'),
          type: 'image',
          region: 'header',
          x_mm: 145,
          y_mm: 12,
          width_mm: 45,
          height_mm: 24,
          text: '',
          asset: result.asset,
          style: {font_size:10,font_family:'Helvetica',bold:false,italic:false,underline:false,color:'#111827',align:'left'},
          z_index: Math.max(1, ...layout.elements.map(row => Number(row.z_index || 1))) + 1,
          locked: false,
          visible: true,
        };
        layout.elements.push(item);
        selectElement(item.id);
        commitHistory();
      } catch (error) {
        window.alert(error instanceof Error ? error.message : 'Bild konnte nicht hochgeladen werden');
      } finally {
        imageUpload.value = '';
        imageUpload.disabled = false;
      }
    });
  }

  form.addEventListener('submit', () => syncHidden());
  renderRegions();
  renderElements();
  updateHistoryControls();
})();
