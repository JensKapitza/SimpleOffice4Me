# V3 Universal Inbox

The V3 inbox is provenance/workflow metadata around the existing document store. It does not create another blob store.

Initial real sources are browser uploads and the S3 inbox. Both end in the existing StoragePort/DocumentStore path. S3 keeps its existing signed SHA-256 validation and optional ClamAV quarantine. Browser upload uses the same AttachmentSecurity quarantine scanner before publish when upload scanning is enabled.

An inbox item records source, source key, original name, actor, content hash/size, malware status, resulting document ID and pipeline steps. Failed or quarantined inputs can exist without a document ID; this is intentional and prevents half-created domain records.

Typed assignments can point to contact/company/project/task after validating the target through its existing domain store. The assignment is copied to document metadata for compatibility; a future Relations adapter may materialize it without changing this inbox contract.

## Web-Upload-Adapter

Die bestehende Dokument-Upload-Route bleibt schlank. Der optionale Inbox-/Malware-Lifecycle liegt in `app/v3_inbox_web.py`; der bestehende Storage-Port bleibt für das eigentliche Dokument autoritativ.
