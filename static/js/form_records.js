(() => {
  'use strict';

  const formNode = document.getElementById('form-records-form-data');
  const recordsNode = document.getElementById('form-records-data');
  if (!formNode || !recordsNode) return;

  const definition = JSON.parse(formNode.textContent || '{}');
  const records = JSON.parse(recordsNode.textContent || '[]');
  const fields = definition.fields || [];
  const analysis = document.getElementById('form-analysis-content');
  const entryForm = document.getElementById('form-record-entry');

  const markerValue = (options, prefix) => {
    const marker = (options || []).find(value => String(value).startsWith(prefix));
    return marker ? String(marker).slice(prefix.length) : '';
  };

  const visibleOptions = options => (options || []).filter(value => !String(value).startsWith('__'));

  function applyFieldConfig() {
    fields.forEach(field => {
      const control = entryForm?.querySelector(`[data-form-field="${CSS.escape(field.key)}"]`);
      if (!control) return;

      const defaultValue = markerValue(field.options, '__default__:');
      const placeholder = markerValue(field.options, '__placeholder__:');
      const help = markerValue(field.options, '__help__:');
      const suggestions = visibleOptions(field.options);

      if (defaultValue && !control.value) control.value = defaultValue;
      if (placeholder && 'placeholder' in control) control.placeholder = placeholder;
      const helpNode = entryForm.querySelector(`[data-form-help="${CSS.escape(field.key)}"]`);
      if (helpNode) {
        helpNode.textContent = help;
        helpNode.hidden = !help;
      }

      if (!['select', 'relation'].includes(field.type) && suggestions.length) {
        const listId = `form-suggestions-${field.key}`;
        const datalist = document.createElement('datalist');
        datalist.id = listId;
        suggestions.forEach(value => {
          const option = document.createElement('option');
          option.value = value;
          datalist.appendChild(option);
        });
        document.body.appendChild(datalist);
        control.setAttribute('list', listId);
      }
    });
  }

  const valuesFor = field => records.map(record => record.values?.[field.key]).filter(value => value !== undefined && value !== null && String(value).trim() !== '');

  function numberValue(value) {
    const normalized = String(value).trim().replace(/\s/g, '').replace(',', '.');
    const number = Number(normalized);
    return Number.isFinite(number) ? number : null;
  }

  function formatNumber(value, currency = false) {
    return new Intl.NumberFormat('de-DE', currency ? { style: 'currency', currency: 'EUR' } : { maximumFractionDigits: 2 }).format(value);
  }

  function card(title, value, detail = '') {
    const col = document.createElement('div');
    col.className = 'col-12 col-sm-6 col-xl-4';
    const box = document.createElement('div');
    box.className = 'border rounded h-100 p-3 bg-body-tertiary';
    const heading = document.createElement('div');
    heading.className = 'small text-secondary';
    heading.textContent = title;
    const main = document.createElement('div');
    main.className = 'fs-4 fw-semibold mt-1';
    main.textContent = value;
    box.append(heading, main);
    if (detail) {
      const small = document.createElement('div');
      small.className = 'small text-secondary mt-1';
      small.textContent = detail;
      box.appendChild(small);
    }
    col.appendChild(box);
    return col;
  }

  function renderAnalysis() {
    analysis.innerHTML = '';
    analysis.appendChild(card('Datensätze', String(records.length), `${fields.length} Felder im Formular`));

    fields.forEach(field => {
      const values = valuesFor(field);
      if (['number', 'currency'].includes(field.type)) {
        const numbers = values.map(numberValue).filter(value => value !== null);
        if (!numbers.length) return;
        const sum = numbers.reduce((a, b) => a + b, 0);
        const avg = sum / numbers.length;
        analysis.appendChild(card(
          `${field.label} · Summe`,
          formatNumber(sum, field.type === 'currency'),
          `Ø ${formatNumber(avg, field.type === 'currency')} · Min ${formatNumber(Math.min(...numbers), field.type === 'currency')} · Max ${formatNumber(Math.max(...numbers), field.type === 'currency')}`
        ));
        return;
      }

      if (field.type === 'select') {
        const counts = new Map();
        values.forEach(value => counts.set(String(value), (counts.get(String(value)) || 0) + 1));
        if (!counts.size) return;
        const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
        const top = sorted.slice(0, 4).map(([value, count]) => `${value}: ${count}`).join(' · ');
        analysis.appendChild(card(`${field.label} · Verteilung`, `${counts.size} Werte`, top));
        return;
      }

      if (field.type === 'date') {
        const dates = values.map(value => new Date(`${value}T00:00:00`)).filter(value => !Number.isNaN(value.getTime())).sort((a, b) => a - b);
        if (!dates.length) return;
        const formatter = new Intl.DateTimeFormat('de-DE');
        analysis.appendChild(card(`${field.label} · Zeitraum`, `${dates.length} Werte`, `${formatter.format(dates[0])} – ${formatter.format(dates[dates.length - 1])}`));
      }
    });

    if (analysis.children.length === 1 && records.length) {
      analysis.appendChild(card('Auswertung', 'Keine Zahlenfelder', 'Für Summen und Statistiken Zahl-, Geld-, Auswahl- oder Datumsfelder verwenden.'));
    }
  }

  function csvCell(value) {
    let text = String(value ?? '');
    if (/^[=+\-@]/.test(text)) text = `'${text}`;
    text = text.replace(/"/g, '""');
    return `"${text}"`;
  }

  function exportCsv() {
    const header = fields.map(field => field.label);
    const rows = records.map(record => fields.map(field => record.values?.[field.key] ?? ''));
    const csv = '\uFEFF' + [header, ...rows].map(row => row.map(csvCell).join(';')).join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${definition.form_id || 'formular'}-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function printAnalysis() {
    document.body.classList.add('form-analysis-print');
    window.print();
    window.setTimeout(() => document.body.classList.remove('form-analysis-print'), 250);
  }

  const style = document.createElement('style');
  style.textContent = `
    @media print {
      body.form-analysis-print header,
      body.form-analysis-print nav,
      body.form-analysis-print #data-entry,
      body.form-analysis-print main > .row,
      body.form-analysis-print main > .d-flex,
      body.form-analysis-print .alert,
      body.form-analysis-print #analysis .btn { display: none !important; }
      body.form-analysis-print #analysis { border: 0 !important; box-shadow: none !important; }
      body.form-analysis-print #analysis .card-body { padding: 0 !important; }
    }
  `;
  document.head.appendChild(style);

  document.getElementById('form-export-csv')?.addEventListener('click', exportCsv);
  document.getElementById('form-print')?.addEventListener('click', printAnalysis);
  applyFieldConfig();
  renderAnalysis();
})();
