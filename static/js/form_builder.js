(() => {
  'use strict';

  const form = document.getElementById('form-builder-form');
  if (!form) return;

  const steps = Array.from(form.querySelectorAll('[data-wizard-step]'));
  const status = document.getElementById('form-wizard-status');
  const progress = document.getElementById('form-wizard-progress');
  const progressBar = progress?.querySelector('.progress-bar');
  const back = document.getElementById('wizard-back');
  const next = document.getElementById('wizard-next');
  const save = document.getElementById('wizard-save');
  const add = document.getElementById('add-form-field');
  const list = document.getElementById('form-field-list');
  const template = document.getElementById('form-field-template');
  const titleField = document.getElementById('form-title-field');
  const jsonTarget = document.getElementById('form-definition-json');
  const review = document.getElementById('form-review');
  const jsonPreview = document.getElementById('form-json-preview');
  const nameInput = document.getElementById('form-name');
  const idInput = document.getElementById('form-id');
  const descriptionInput = document.getElementById('form-description');
  const definitionsNode = document.getElementById('form-definitions-data');
  const definitions = definitionsNode ? JSON.parse(definitionsNode.textContent || '[]') : [];
  let currentStep = 1;
  let idTouched = false;
  let currentLayout = '';

  const slug = value => String(value || '')
    .toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss')
    .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 64);

  const lines = value => String(value || '').split(/\r?\n/).map(v => v.trim()).filter(Boolean);

  function markerFromOptions(options, prefix) {
    const marker = (options || []).find(value => String(value).startsWith(prefix));
    return marker ? String(marker).slice(prefix.length) : '';
  }

  function visibleOptions(options) {
    return (options || []).filter(value => !String(value).startsWith('__'));
  }

  function addField(source = {}) {
    const fragment = template.content.cloneNode(true);
    const card = fragment.querySelector('.form-field-card');
    const label = fragment.querySelector('.field-label');
    const key = fragment.querySelector('.field-key');
    const type = fragment.querySelector('.field-type');
    const required = fragment.querySelector('.field-required');
    const options = fragment.querySelector('.field-options');
    const relation = fragment.querySelector('.field-relation');
    const relationWrap = fragment.querySelector('.field-relation-wrap');
    const defaultInput = fragment.querySelector('.field-default');
    const placeholder = fragment.querySelector('.field-placeholder');
    const help = fragment.querySelector('.field-help');

    label.value = source.label || '';
    key.value = source.key || '';
    type.value = source.type || 'text';
    required.checked = Boolean(source.required);
    options.value = visibleOptions(source.options).join('\n');
    relation.value = source.relation_form || 'contact';
    defaultInput.value = markerFromOptions(source.options, '__default__:');
    placeholder.value = markerFromOptions(source.options, '__placeholder__:');
    help.value = markerFromOptions(source.options, '__help__:');

    let keyTouched = Boolean(source.key);
    label.addEventListener('input', () => {
      if (!keyTouched) key.value = slug(label.value);
      refreshTitleFields();
    });
    key.addEventListener('input', () => { keyTouched = true; refreshTitleFields(); });

    const updateType = () => {
      relationWrap.hidden = type.value !== 'relation';
      options.closest('.field-options-wrap').hidden = type.value === 'relation';
      defaultInput.disabled = type.value === 'relation';
      if (type.value === 'select') {
        options.placeholder = 'Ein Wert pro Zeile, z. B. Neu\nIn Arbeit\nErledigt';
      } else {
        options.placeholder = 'Optional: Autocomplete-Werte, ein Wert pro Zeile';
      }
    };
    type.addEventListener('change', updateType);
    updateType();

    fragment.querySelector('.remove-field').addEventListener('click', () => {
      card.remove();
      renumber();
      refreshTitleFields();
    });

    list.appendChild(fragment);
    renumber();
    refreshTitleFields(source.key);
  }

  function renumber() {
    list.querySelectorAll('.form-field-card').forEach((card, index) => {
      card.querySelector('.field-number').textContent = `Feld ${index + 1}`;
    });
  }

  function refreshTitleFields(preferred = '') {
    const previous = preferred || titleField.value;
    titleField.innerHTML = '';
    list.querySelectorAll('.form-field-card').forEach(card => {
      const key = card.querySelector('.field-key').value.trim();
      const label = card.querySelector('.field-label').value.trim() || key;
      if (!key) return;
      const option = document.createElement('option');
      option.value = key;
      option.textContent = label;
      titleField.appendChild(option);
    });
    if ([...titleField.options].some(option => option.value === previous)) titleField.value = previous;
  }

  function fieldFromCard(card) {
    const field = {
      key: card.querySelector('.field-key').value.trim(),
      label: card.querySelector('.field-label').value.trim(),
      type: card.querySelector('.field-type').value,
      required: card.querySelector('.field-required').checked,
      options: lines(card.querySelector('.field-options').value),
      relation_form: card.querySelector('.field-relation').value
    };
    const defaultValue = card.querySelector('.field-default').value.trim();
    const placeholder = card.querySelector('.field-placeholder').value.trim();
    const help = card.querySelector('.field-help').value.trim();
    if (defaultValue) field.options.unshift(`__default__:${defaultValue}`);
    if (placeholder) field.options.unshift(`__placeholder__:${placeholder}`);
    if (help) field.options.unshift(`__help__:${help}`);
    return field;
  }

  function buildDefinition() {
    refreshTitleFields();
    const definition = {
      form_id: idInput.value.trim(),
      name: nameInput.value.trim(),
      description: descriptionInput.value.trim(),
      title_field: titleField.value,
      fields: Array.from(list.querySelectorAll('.form-field-card')).map(fieldFromCard)
    };
    if (currentLayout) definition.layout = currentLayout;
    return definition;
  }

  function validateStep() {
    if (currentStep === 1) {
      if (!nameInput.reportValidity() || !idInput.reportValidity()) return false;
      return true;
    }
    if (currentStep === 2) {
      const cards = Array.from(list.querySelectorAll('.form-field-card'));
      if (!cards.length) {
        alert('Bitte mindestens ein Feld anlegen.');
        return false;
      }
      for (const card of cards) {
        for (const input of card.querySelectorAll('input[required], select[required]')) {
          if (!input.reportValidity()) return false;
        }
      }
      const keys = cards.map(card => card.querySelector('.field-key').value.trim());
      if (new Set(keys).size !== keys.length) {
        alert('Jede Feldkennung darf nur einmal vorkommen.');
        return false;
      }
      return true;
    }
    return true;
  }

  function updateReview() {
    const definition = buildDefinition();
    jsonTarget.value = JSON.stringify(definition);
    jsonPreview.textContent = JSON.stringify(definition, null, 2);
    review.innerHTML = '';
    const title = document.createElement('div');
    title.className = 'fw-semibold fs-5';
    title.textContent = definition.name;
    review.appendChild(title);
    const meta = document.createElement('div');
    meta.className = 'text-secondary small mb-3';
    meta.textContent = `${definition.form_id} · ${definition.fields.length} Felder · Titel: ${definition.title_field}`;
    review.appendChild(meta);
    if (definition.description) {
      const p = document.createElement('p');
      p.textContent = definition.description;
      review.appendChild(p);
    }
    const ul = document.createElement('ul');
    ul.className = 'mb-0';
    definition.fields.forEach(field => {
      const li = document.createElement('li');
      li.textContent = `${field.label} · ${field.type}${field.required ? ' · Pflichtfeld' : ''}`;
      ul.appendChild(li);
    });
    review.appendChild(ul);
  }

  function showStep(step) {
    currentStep = Math.max(1, Math.min(3, step));
    steps.forEach(section => { section.hidden = Number(section.dataset.wizardStep) !== currentStep; });
    back.disabled = currentStep === 1;
    next.hidden = currentStep === 3;
    save.hidden = currentStep !== 3;
    const labels = ['Basisdaten', 'Felder', 'Prüfen'];
    status.textContent = `Schritt ${currentStep} von 3 · ${labels[currentStep - 1]}`;
    progress.setAttribute('aria-valuenow', String(currentStep));
    progressBar.style.width = `${currentStep * 33.333}%`;
    if (currentStep === 3) updateReview();
  }

  function loadDefinition(definition) {
    nameInput.value = definition.name || '';
    idInput.value = definition.form_id || '';
    descriptionInput.value = definition.description || '';
    currentLayout = definition.layout || '';
    idTouched = true;
    list.innerHTML = '';
    (definition.fields || []).forEach(addField);
    refreshTitleFields(definition.title_field);
    titleField.value = definition.title_field || titleField.value;
    showStep(1);
    document.getElementById('form-wizard').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function continueAfterSave() {
    const targetId = sessionStorage.getItem('simpleoffice-form-open-after-save');
    if (!targetId) return;
    const target = document.querySelector(`[data-form-open="${CSS.escape(targetId)}"]`);
    sessionStorage.removeItem('simpleoffice-form-open-after-save');
    if (target) window.location.replace(target.href);
  }

  nameInput.addEventListener('input', () => {
    if (!idTouched) idInput.value = slug(nameInput.value);
  });
  idInput.addEventListener('input', () => { idTouched = true; });
  add.addEventListener('click', () => addField());
  next.addEventListener('click', () => { if (validateStep()) showStep(currentStep + 1); });
  back.addEventListener('click', () => showStep(currentStep - 1));
  form.addEventListener('submit', event => {
    if (!validateStep()) { event.preventDefault(); return; }
    const definition = buildDefinition();
    jsonTarget.value = JSON.stringify(definition);
    sessionStorage.setItem('simpleoffice-form-open-after-save', definition.form_id);
  });

  document.querySelectorAll('[data-edit-form]').forEach(button => {
    button.addEventListener('click', () => {
      const definition = definitions.find(item => item.form_id === button.dataset.editForm);
      if (definition) loadDefinition(definition);
    });
  });

  addField({ label: 'Bezeichnung', key: 'bezeichnung', type: 'text', required: true });
  showStep(1);
  continueAfterSave();
})();
