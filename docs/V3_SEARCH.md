# V3 Global Search and Command Palette

The palette is additive to the existing quick-search form. It opens with Ctrl/Cmd+K and has a visible mobile action.

Search is provider based. Providers receive only a bounded query plus actor/features and return navigation references; they do not duplicate domain records. Contact search uses ContactStore visibility. Document search explicitly applies the existing chat/document visibility rule even though providers run outside Flask request-local state. Project results are available only when the existing projects feature is granted.

Providers run with a short aggregate timeout. Failure or timeout of one provider is reported as temporarily unavailable and does not block other results. Commands in v1 are navigation-only; they do not execute destructive or mutating actions.

With v3.search disabled, the API returns 404, the palette is not rendered and the existing navigation/search stays unchanged.

## UI-Integration

Die Command Palette wird als bewusstes Template-Fragment `templates/v3_command_palette.html` einmalig aus `layout.html` eingebunden. Sie ist keine eigenständige Seite und darf daher keine zweite Layout-Shell erzeugen.
