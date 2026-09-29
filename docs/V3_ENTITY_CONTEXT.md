# V3 Entity Detail Shell

The V3 entity shell is a shared presentation layer, not a replacement for the owning domain pages.

Contacts, projects and documents resolve into the same small context model: identity, title, optional status/overview and a canonical URL back to the existing detail page. Existing deep links remain valid and authoritative.

Optional sections use registered providers. Missing or failing providers are hidden instead of turning the page into a 500 response. The Activity section is loaded only when both the capability and implementation are present.

The new route is available only when v3.entity_context is enabled. Disabling the capability restores the previous UI because no existing route is removed or redirected.
