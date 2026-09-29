# CRM 3.0

CRM 3.0 adds relationship history and a joined working view without replacing ContactStore, CardDAV, projects, tasks, calendar or invoices.

## Relationship model

The implementation uses the V3 RelationStore with one undirected crm_link per endpoint pair. Direction and history live in bounded role records inside the relation metadata. This allows the same person/company pair to carry several roles and several historical periods without changing the generic relation schema.

Supported role keys are:

- works_at
- contact_for
- customer
- supplier
- employee
- parent_company
- subsidiary
- custom with a required human label

Each role can hold status (planned, active, former), start/end date, function/title and a short note. Ending a role never deletes it.

## Existing company links

Existing company_contact_id and company-name fields remain authoritative for CardDAV/vCard compatibility. The V3 workspace displays that existing assignment but does not silently rewrite it on GET.

## Merge safety

Before ContactManagement removes a duplicate contact, CRM role records are copied to the selected target and deduplicated by stable role_id. Only after the contact write succeeds are obsolete crm_link rows for the old contact cleaned up. If cleanup fails, the target copy already contains the relationship data and the stale source row can be repaired later.

## Workspace providers

The CRM workspace reads data from existing stores:

- ContactStore for contact/company identity and visible company people;
- RelationStore for historical CRM roles;
- ProjectStore only when the existing projects feature is granted;
- TodoStore using its existing contact filter;
- CalendarStore using its existing actor visibility;
- DocumentStore plus document_visible for linked documents;
- invoices only for a contact the actor may manage;
- legacy CRM activities/history by reference to the existing ContactCRMStore.

No provider copies its source records into CRM storage.

## Rollout

The UI and routes require SIMPLEOFFICE_V3_CRM_ENABLED=true. With the capability off, existing CRM, ContactStore, CardDAV and vCard paths are unchanged.
