const test = require('node:test');
const assert = require('node:assert/strict');

const {
  companySearchUrls,
  onlyEmptyContactFields,
  parseCompanyDetails,
} = require('../static/js/company-data-search.js');

test('API-free provider links encode company and city and contain no credentials', () => {
  const links = companySearchUrls('Muster & Söhne GmbH', 'München');
  assert.equal(
    links.googleMaps,
    'https://www.google.com/maps/search/?api=1&query=Muster%20%26%20S%C3%B6hne%20GmbH%20M%C3%BCnchen',
  );
  assert.equal(new URL(links.googleSearch).searchParams.get('q'), 'Muster & Söhne GmbH München');
  assert.equal(new URL(links.bingMaps).searchParams.get('q'), 'Muster & Söhne GmbH München');
  assert.equal(new URL(links.openStreetMap).searchParams.get('query'), 'Muster & Söhne GmbH München');
  for (const url of Object.values(links)) assert.doesNotMatch(url, /api[_-]?key|token=/i);
});

test('manual labelled text maps to structured fields and rejects conflicting duplicates', () => {
  assert.deepEqual(parseCompanyDetails([
    'Name: Musterwerke GmbH',
    'Straße: Musterstraße',
    'Hausnummer: 12',
    'PLZ: 12345',
    'Ort: Berlin',
    'E-Mail: info@muster.test',
    'Webseite: https://muster.test/impressum',
  ].join('\n')), {
    display_name: 'Musterwerke GmbH',
    street: 'Musterstraße',
    house_number: '12',
    postal: '12345',
    city: 'Berlin',
    email: 'info@muster.test',
    website: 'https://muster.test/impressum',
  });
  assert.throws(
    () => parseCompanyDetails('Firma: Muster GmbH\nFirma: Andere GmbH'),
    /unterschiedliche Werte/,
  );
});

test('manual paste limits and existing contact values are protected from overwrite', () => {
  assert.throws(() => parseCompanyDetails('x'.repeat(20001)), /zu lang/);
  assert.throws(() => parseCompanyDetails(Array(102).fill('Name: Firma').join('\n')), /zu viele Zeilen/);
  assert.throws(() => parseCompanyDetails(`Name: ${'x'.repeat(2001)}`), /zu lang/);
  assert.deepEqual(
    onlyEmptyContactFields(
      {display_name: 'New name', company: 'New company', email: 'new@example.test', website: 'https://example.test'},
      {display_name: 'Existing name', company: '', email: 'old@example.test', website: ''},
    ),
    {company: 'New company', website: 'https://example.test'},
  );
});
