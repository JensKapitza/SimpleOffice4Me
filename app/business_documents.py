"""CRM links, storage and HTTP routes for business documents.

Generation, invoice calculation and ZUGFeRD/PDF helpers live in
``business_document_generation``. Existing imports from this module remain
compatible.
"""
from __future__ import annotations

from flask import Blueprint

from .business_document_generation import *  # noqa: F401,F403

bp = Blueprint("business_documents", __name__, url_prefix="/documents/business")


def _link_path(root: Path) -> Path:
    path=root/CONTROL_DIR/LINK_FILE; path.parent.mkdir(parents=True,exist_ok=True); return path


def contact_links(root: Path, contact_id: str) -> list[dict[str, Any]]:
    rows=_read_json(_link_path(root),{"links":[]}).get("links",[]); return sorted((row for row in rows if row.get("contact_id")==contact_id),key=lambda row:row.get("created_at",""),reverse=True)


def _customer_document_rows(root: Path, contact_id: str, invoice_rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Resolve only authoritative customer/document relations, without name guessing."""
    store = DocumentStore(root)
    linked: dict[str, dict[str, Any]] = {}
    for link in contact_links(root, contact_id):
        document_id = str(link.get("document_id", "")).strip()
        if not document_id:
            continue
        entry = linked.setdefault(document_id, {"document_id": document_id, "links": [], "invoice_ids": []})
        entry["links"].append(link)
    for row in invoice_rows if invoice_rows is not None else invoices(root):
        if row.get("contact_id") != contact_id:
            continue
        document_id = str(row.get("document_id", "")).strip()
        if document_id:
            entry = linked.setdefault(document_id, {"document_id": document_id, "links": [], "invoice_ids": []})
            entry["invoice_ids"].append(str(row.get("invoice_id", "")))
    result: list[dict[str, Any]] = []
    for document_id, relation in linked.items():
        try:
            document = store.get_document(document_id)
        except ValueError:
            result.append({**relation, "available": False, "error": "document_metadata_missing"})
            continue
        candidate = root / str(document.get("last_path", ""))
        path = candidate.resolve()
        try:
            path.relative_to(root)
            safe = candidate.is_file() and not candidate.is_symlink()
        except ValueError:
            safe = False
        result.append({**relation, "document": document, "path": path,
                       "filename": Path(str(document.get("last_path", ""))).name,
                       "available": safe,
                       **({} if safe else {"error": "document_file_missing_or_unsafe"})})
    return sorted(result, key=lambda item: str(item.get("document", {}).get("last_seen_at", "")), reverse=True)


def _document_provenance(document: dict[str, Any]) -> dict[str, Any]:
    attributes = document.get("attributes", {}) if isinstance(document.get("attributes"), dict) else {}
    origin_keys = {
        "attachment_origin", "copied_from", "email_origin", "import_origin",
        "mail_origin", "source", "webdav_origin",
    }
    origins = {
        key: value for key, value in attributes.items()
        if key in origin_keys or key.endswith("_origin")
    }
    return {
        "first_seen_at": document.get("first_seen_at", ""),
        "last_seen_at": document.get("last_seen_at", ""),
        "current_storage_path": document.get("last_path", ""),
        "location_history": document.get("location_history", []),
        "content_history": document.get("content_history", []),
        "origins": origins,
        "malware_scan": attributes.get("malware_scan", {}),
    }


def _archive_member_name(document: dict[str, Any], used: set[str]) -> str:
    original = Path(str(document.get("last_path", "document"))).name
    stem = _safe_filename(Path(original).stem) or "document"
    suffix = Path(original).suffix.lower()[:20]
    candidate = f"documents/{stem}{suffix}"
    if candidate in used:
        candidate = f"documents/{stem}-{str(document.get('document_id', ''))[:8]}{suffix}"
    counter = 2
    unique = candidate
    while unique in used:
        unique = f"documents/{stem}-{counter}{suffix}"
        counter += 1
    used.add(unique)
    return unique


def customer_document_archive(root: Path, contact: dict[str, Any], actor: str) -> tuple[BinaryIO, dict[str, Any]]:
    """Build an auditable customer archive with files, provenance and history."""
    contact_id = str(contact["contact_id"])
    store = DocumentStore(root)
    invoice_rows = [row for row in invoices(root) if row.get("contact_id") == contact_id]
    document_rows = _customer_document_rows(root, contact_id, invoice_rows)
    if not document_rows and not invoice_rows:
        raise ValueError("customer archive is empty")

    # Python 3.10's SpooledTemporaryFile does not expose ``seekable`` while
    # ZipFile expects that attribute when reopening an archive for reading.
    # TemporaryFile remains disk-backed, bounded in memory and compatible
    # with every supported Python version.
    target = tempfile.TemporaryFile(mode="w+b")
    export_id, exported_at = str(uuid.uuid4()), utc_now()
    manifest_documents: list[dict[str, Any]] = []
    used_names: set[str] = set()
    document_ids = {str(row["document_id"]) for row in document_rows}
    document_logbooks = {document_id: [] for document_id in document_ids}
    for event in store.logbook():
        if event.get("source") == "revision":
            related_document_id = str(event.get("key", ""))
        else:
            related_document_id = str(event.get("document_id") or event.get("source_document_id") or "")
        if related_document_id in document_logbooks:
            document_logbooks[related_document_id].append(event)
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for row in document_rows:
                document = row.get("document", {})
                record: dict[str, Any] = {
                    "document_id": row["document_id"],
                    "available": bool(row.get("available")),
                    "relations": row.get("links", []),
                    "invoice_ids": sorted(set(row.get("invoice_ids", []))),
                }
                if not row.get("available"):
                    record["error"] = row.get("error", "document_unavailable")
                    manifest_documents.append(record)
                    continue
                member_name = _archive_member_name(document, used_names)
                digest = hashlib.sha256()
                size = 0
                with row["path"].open("rb") as source, archive.open(member_name, "w", force_zip64=True) as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                actual_sha256 = digest.hexdigest()
                record.update({
                    "archive_path": member_name,
                    "filename": Path(str(document.get("last_path", ""))).name,
                    "size": size,
                    "sha256": actual_sha256,
                    "stored_sha256": document.get("sha256", ""),
                    "hash_matches_metadata": actual_sha256 == document.get("sha256"),
                    "state": document.get("state", ""),
                    "tags": document.get("tags", []),
                    "provenance": _document_provenance(document),
                })
                archive.writestr(
                    f"audit/documents/{row['document_id']}.json",
                    json.dumps(document_logbooks[row["document_id"]], ensure_ascii=False, indent=2) + "\n",
                )
                manifest_documents.append(record)

            export_record = {
                "export_id": export_id,
                "action": "customer_document_archive_exported",
                "exported_at": exported_at,
                "exported_by": actor,
                "contact_id": contact_id,
                "customer_name": contact.get("fields", {}).get("display_name", ""),
                "document_count": sum(1 for row in manifest_documents if row.get("available")),
                "unavailable_document_count": sum(1 for row in manifest_documents if not row.get("available")),
                "invoice_count": len(invoice_rows),
            }
            manifest = {
                "format": "SimpleOffice4Me customer document archive",
                "format_version": 1,
                "customer": {"contact_id": contact_id, "display_name": export_record["customer_name"]},
                "export": export_record,
                "scope": "Explicit customer-document links and invoices linked by contact_id; no name-based inference.",
                "documents": manifest_documents,
                "invoices": invoice_rows,
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("audit/export.json", json.dumps(export_record, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("invoices/invoices.json", json.dumps(invoice_rows, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("README.txt", (
                "KUNDENAKTE / CUSTOMER DOCUMENT ARCHIVE\n\n"
                "manifest.json enthält die Dokumentliste, SHA-256-Prüfsummen, Verknüpfungen und Herkunftsnachweise.\n"
                "audit/ enthält den protokollierten Export und die Historie jedes enthaltenen Dokuments.\n"
                "invoices/invoices.json enthält die gespeicherten Rechnungsdatensätze.\n"
                "Dokumente werden nur über explizite Kontaktverknüpfungen oder eine eindeutige contact_id der Rechnung zugeordnet.\n\n"
                "manifest.json contains the document list, SHA-256 checksums, relations and provenance.\n"
                "audit/ contains the recorded export and each included document's audit history.\n"
                "invoices/invoices.json contains the stored invoice records.\n"
                "Documents are included only through explicit contact links or an invoice's authoritative contact_id.\n"
            ))
        revision = store.history.record(
            "customer_document_archive_exported", actor, "customer-exports", export_id,
            {**export_record, "document_ids": [row["document_id"] for row in manifest_documents],
             "invoice_ids": [str(row.get("invoice_id", "")) for row in invoice_rows]},
        )
        export_record["audit_revision"] = revision
        target.seek(0)
        return target, export_record
    except Exception:
        target.close()
        raise


def attach_contact_document(root: Path, contact_id: str, document_id: str, actor: str, *, relation: str="correspondence", metadata: dict[str,Any]|None=None) -> dict[str,Any]:
    path=_link_path(root)
    with exclusive_file_lock(path.with_suffix(".lock")):
        payload=_read_json(path,{"links":[]}); existing=next((row for row in payload.get("links",[]) if row.get("contact_id")==contact_id and row.get("document_id")==document_id),None)
        if existing:return existing
        row={"link_id":str(uuid.uuid4()),"contact_id":contact_id,"document_id":document_id,"relation":relation,"metadata":metadata or {},"created_at":utc_now(),"created_by":actor}; payload.setdefault("links",[]).append(row); atomic_json_write(path,payload)
    DocumentStore(root).history.record("contact_document_attached",actor,"contacts",contact_id,row); return row


def _xml_values(root: ET.Element) -> dict[str,list[str]]:
    values:dict[str,list[str]]={}
    for element in root.iter():
        local=element.tag.rsplit("}",1)[-1]; value=(element.text or "").strip()
        if value:values.setdefault(local,[]).append(value)
    return values


def inspect_zugferd_pdf(path: Path) -> dict[str,Any]:
    result={"detected":False,"xml_filename":"","profile":"","invoice_id":"","issue_date":"","currency":"","seller":"","buyer":"","grand_total":"","tax_total":"","due_payable":"","raw_xml":"","validation":"not_validated"}
    try:reader=PdfReader(path)
    except Exception:return result
    try:attachments=dict(reader.attachments)
    except Exception:attachments={}
    for filename,payloads in attachments.items():
        if str(filename).casefold() not in ZUGFERD_FILENAMES:continue
        payload=payloads[0] if isinstance(payloads,list) and payloads else payloads
        if not isinstance(payload,(bytes,bytearray)):continue
        try:text=bytes(payload).decode("utf-8"); xml_root=DefusedElementTree.fromstring(text)
        except (UnicodeDecodeError,ET.ParseError,DefusedXmlException):continue
        values=_xml_values(xml_root); result.update({"detected":True,"xml_filename":str(filename),"raw_xml":text,"invoice_id":(values.get("ID") or [""])[0],"issue_date":(values.get("DateTimeString") or [""])[0],"currency":(values.get("InvoiceCurrencyCode") or [""])[0],"grand_total":(values.get("GrandTotalAmount") or [""])[0],"tax_total":(values.get("TaxTotalAmount") or [""])[0],"due_payable":(values.get("DuePayableAmount") or [""])[0]}); names=values.get("Name",[])
        if names:result["seller"]=names[0]
        if len(names)>1:result["buyer"]=names[1]
        result["profile"]="EN16931"; break
    return result


def _store_generated_pdf(root: Path, contact_id: str, subject: str, pdf: bytes, actor: str, kind: str, template_id: str, *, metadata: dict[str,Any]|None=None) -> dict[str,Any]:
    now=datetime.now(timezone.utc); directory=root/"generated"/kind/now.strftime("%Y")/contact_id; directory.mkdir(parents=True,exist_ok=True); path=directory/f"{now.strftime('%Y%m%d-%H%M%S')}-{_safe_filename(subject)}-{uuid.uuid4().hex[:8]}.pdf"; path.write_bytes(pdf)
    store=DocumentStore(root); document=store.get_document(path); document_id=document["document_id"]
    store.update_metadata(
        document_id, author=actor, tags=[kind,"crm"],
        attributes={
            "contact_id": contact_id, "business_document_kind": kind,
            "business_template_id": template_id,
            **{str(key): str(value) for key,value in (metadata or {}).items() if value is not None and not isinstance(value,(dict,list))},
        },
    )
    attach_contact_document(root,contact_id,document_id,actor,relation=kind,metadata={"subject":subject,"template_id":template_id,**(metadata or {})}); return store.get_document(document_id)


def _invoice_row_from_form(root: Path, contact_id: str, form, actor: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    contacts=ContactStore(root); contact=contacts.get(contact_id,actor)
    if not contacts.can_manage_contact(contact,actor):raise PermissionError
    crm=ContactCRMStore(root).record(contact_id); selected=form.get("address",""); label,candidates=address_labels(contact,crm,selected); candidate=next((item for item in candidates if item["label"]==label),None)
    if not candidate:raise ValueError("billing address is required")
    settings=business_settings(root)
    for required in ("seller_name","seller_street","seller_postal","seller_city"):
        if not settings.get(required):raise ValueError("invoice issuer data is incomplete; configure business document settings first")
    lines=_build_invoice_lines(root,form); totals=_invoice_totals(lines); issue_text=form.get("issue_date","") or date.today().isoformat(); service_text=form.get("service_date","") or issue_text
    try:issue=date.fromisoformat(issue_text); service=date.fromisoformat(service_text)
    except ValueError as exc:raise ValueError("invoice/service date must be valid ISO dates") from exc
    try:days=int(form.get("payment_days",crm.get("payment_days") or settings.get("default_payment_days","14")) or 0)
    except ValueError as exc:raise ValueError("payment days must be an integer") from exc
    due=issue+timedelta(days=max(0,days)); invoice_id=str(existing.get("invoice_id")) if existing else str(uuid.uuid4()); fields=contact.get("fields",{}); contact_names=_recipient_names(contact)
    recipient_type=str(form.get("recipient_type") or ("company" if fields.get("company") else "private")).strip().casefold()
    if recipient_type not in {"private","company"}: raise ValueError("recipient type must be private or company")
    company=str(form.get("recipient_company","")).strip(); person=str(form.get("recipient_contact","")).strip()
    if recipient_type == "private" and not person: raise ValueError("a private invoice requires a recipient name")
    if recipient_type == "company" and not company: raise ValueError("a company invoice requires a company name")
    known_names={str(value).strip().casefold() for value in (*contact_names,fields.get("display_name",""),fields.get("company","")) if str(value).strip()}
    address_lines=[line.strip() for line in label.splitlines() if line.strip() and line.strip().casefold() not in known_names]
    recipient_lines=[person] if recipient_type == "private" else [company,*([person] if person else [])]
    buyer_label="\n".join(recipient_lines+address_lines); buyer_name=person if recipient_type == "private" else company
    now=utc_now(); row={"invoice_id":invoice_id,"invoice_number":existing.get("invoice_number") if existing else _draft_invoice_number(root,issue),"contact_id":contact_id,"issue_date":issue.isoformat(),"service_date":service.isoformat(),"due_date":due.isoformat(),"currency":str(form.get("currency") or crm.get("currency") or settings.get("currency") or "EUR").upper()[:3],"payment_terms":str(form.get("payment_terms") or crm.get("payment_terms") or settings.get("payment_terms") or "").strip(),"seller":{"name":settings["seller_name"],"street":settings["seller_street"],"postal":settings["seller_postal"],"city":settings["seller_city"],"country":settings.get("seller_country") or "DE","email":settings.get("seller_email","") ,"vat_id":settings.get("seller_vat_id","") ,"tax_number":settings.get("seller_tax_number","") ,"iban":settings.get("seller_iban","") ,"bic":settings.get("seller_bic","") ,"bank":settings.get("seller_bank","")},"buyer":{"name":buyer_name,"label":buyer_label,"recipient_type":recipient_type,"company":company if recipient_type == "company" else "","contact_name":person,"address_id":candidate["id"],"street":candidate.get("street",label),"postal":candidate.get("postal","") ,"city":candidate.get("city","") ,"country":candidate.get("country") or "DE","vat_id":crm.get("vat_id","")},"lines":lines,"totals":totals,"status":"draft","payments":[],"history":list(existing.get("history",[])) if existing else [],"template_id":str(form.get("template_id","")).strip(),"zugferd":{"version":"2.5.2","profile":"EN16931","status":"not_created"},"created_at":existing.get("created_at",now) if existing else now,"created_by":existing.get("created_by",actor) if existing else actor,"updated_at":now,"updated_by":actor}
    available_credit = max(Decimal("0"), Decimal(CustomerCreditLedger(root).account(contact_id, row["currency"])["balance"]))
    planned_credit = min(Decimal(totals["gross"]), available_credit).quantize(MONEY)
    row["settlement"] = {"customer_credit": f"{planned_credit:.2f}", "bank_due": f"{(Decimal(totals['gross']) - planned_credit).quantize(MONEY):.2f}"}
    tpl=active_template(root,row["template_id"]); row["template_id"]=tpl["template_id"]
    row["history"].append({"type":"draft_updated" if existing else "draft_created","at":now,"actor":actor})
    return row


def save_invoice_draft(root: Path, contact_id: str, form, actor: str, invoice_id: str = "") -> dict[str, Any]:
    path = _invoice_store_path(root, invoice_id) if invoice_id else None
    lock_path = path.with_suffix(".lock") if path else root / CONTROL_DIR / INVOICE_DIR / ".create.lock"
    with exclusive_file_lock(lock_path):
        existing = invoice(root, invoice_id) if invoice_id else None
        if existing and (existing.get("status") != "draft" or existing.get("contact_id") != contact_id): raise ValueError("only a matching invoice draft can be edited")
        row = _invoice_row_from_form(root, contact_id, form, actor, existing)
        atomic_json_write(_invoice_store_path(root,row["invoice_id"]),row)
    DocumentStore(root).history.record("invoice_draft_saved",actor,"invoice",row["invoice_id"],{"contact_id":contact_id,"totals":row["totals"]})
    return row


def _draft_watermark(pdf: bytes) -> bytes:
    reader=PdfReader(io.BytesIO(pdf)); writer=PdfWriter()
    for source in reader.pages:
        overlay=io.BytesIO(); c=canvas.Canvas(overlay,pagesize=A4); c.saveState(); c.setFillColor(colors.Color(.75,.1,.1,alpha=.18)); c.setFont("Helvetica-Bold",42); c.translate(A4[0]/2,A4[1]/2); c.rotate(35); c.drawCentredString(0,0,"ENTWURF / DRAFT"); c.setFont("Helvetica-Bold",12); c.drawCentredString(0,-18*mm,"KEINE RECHNUNG / NOT AN INVOICE"); c.restoreState(); c.save(); overlay.seek(0); writer.add_page(source); writer.pages[-1].merge_page(PdfReader(overlay).pages[0])
    target=io.BytesIO(); writer.write(target); return target.getvalue()


def draft_invoice_pdf(root: Path, row: dict[str, Any]) -> bytes:
    if row.get("status") != "draft": raise ValueError("invoice is not a draft")
    tpl=active_template(root,row.get("template_id","")); visual=_merge_content_with_template(root,tpl,_invoice_content_pdf(row)); return _draft_watermark(visual)


def finalize_invoice(root: Path, invoice_id: str, actor: str) -> tuple[dict[str,Any],dict[str,Any]]:
    path = _invoice_store_path(root, invoice_id)
    timings: dict[str, float] = {}
    started_total = time.perf_counter()

    def timed(step: str, operation):
        started = time.perf_counter()
        try:
            return operation()
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            timings[step] = elapsed_ms
            log = logger.warning if elapsed_ms > 500 else logger.debug
            log("invoice_finalization invoice_id=%s step=%s duration_ms=%.1f", invoice_id, step, elapsed_ms)

    with exclusive_file_lock(path.with_suffix(".lock"), blocking=False) as acquired, exclusive_file_lock(root / CONTROL_DIR / ".project-billing.lock"):
        if not acquired:
            raise ValueError("invoice finalization is already in progress")
        row = timed("invoice_load", lambda: invoice(root, invoice_id))
        if row.get("status") != "draft":
            raise ValueError("invoice is already finalized")
        timed("contact_load", lambda: ContactStore(root).get(row["contact_id"], actor) if row.get("contact_id") else None)
        timed("project_positions_load", lambda: _validate_project_sources(root, row, actor))
        settings = timed("business_settings_load", lambda: business_settings(root))
        number = row.get("invoice_number", "")
        number = timed("invoice_number_assign", lambda: _invoice_number(root)) if not number or number.startswith("DRAFT-") else number
        row["invoice_number"] = number
        row["history"].append({"type": "number_assigned", "at": utc_now(), "actor": actor, "invoice_number": number})
        try:
            tpl = timed("template_load", lambda: active_template(root, row["template_id"]))
            content_pdf = timed("content_pdf_render", lambda: _invoice_content_pdf(row))
            visual = timed("template_pdf_render", lambda: _merge_content_with_template(root, tpl, content_pdf))
            pdfa, pdfa_details = timed("ghostscript_pdfa_convert", lambda: _pdfa3_convert_detailed(visual))
            row["zugferd"].update({"pdfa_pipeline": pdfa_details["status"], "pdfa_details": pdfa_details})
            try:
                xml = timed("zugferd_xml_generate", lambda: _cii_xml(row))
            except Exception:
                row["zugferd"]["status"] = "xml_generation_failed"
                raise
            try:
                hybrid = timed("zugferd_xml_embed", lambda: embed_invoice_xml(pdfa, xml, "factur-x.xml"))
            except Exception:
                row["zugferd"]["status"] = "embedding_failed"
                raise
            validation = timed("zugferd_validate", lambda: _validate_hybrid(hybrid, xml))
            technical_status = _zugferd_status(str(pdfa_details["status"]), validation)
            row["zugferd"].update({"validation": validation, "status": technical_status})
            if technical_status != "validated":
                raise ValueError("ZUGFeRD validation is required but PDF/A-3/XML validation did not pass: " + ", ".join(validation.get("details", [])))
            document = timed(
                "final_file_save_and_contact_link",
                lambda: _store_generated_pdf(
                    root, row["contact_id"], f"Rechnung-{number}", hybrid, actor, "invoice", tpl["template_id"],
                    metadata={"invoice_id": invoice_id, "invoice_number": number, "invoice_total": row["totals"]["gross"], "invoice_currency": row["currency"], "zugferd_status": technical_status, "zugferd_version": "2.5.2"},
                ),
            )
        except Exception as exc:
            row["status"] = "draft"
            row["finalization_error"] = str(exc)
            row["finalization_timings_ms"] = timings
            row["history"].append({"type": "finalization_failed", "at": utc_now(), "actor": actor, "error": str(exc)[:500]})
            atomic_json_write(path, row)
            raise
        row.pop("finalization_error", None)
        row["document_id"] = document["document_id"]
        row["status"] = "open"
        row["history"].append({"type": "issued", "at": utc_now(), "actor": actor})
        timed("invoice_and_project_links_save", lambda: atomic_json_write(path, row))
        timed("audit_history_save", lambda: DocumentStore(root).history.record("invoice_created", actor, "invoice", invoice_id, {"invoice_number": number, "contact_id": row["contact_id"], "document_id": document["document_id"], "totals": row["totals"], "zugferd": row["zugferd"]}))
        row["finalization_timings_ms"] = timings
        atomic_json_write(path, row)
    if Decimal(row.get("settlement", {}).get("customer_credit", "0")) > 0:
        row = timed("customer_credit_apply", lambda: apply_available_customer_credit(root, invoice_id, actor))
    total_ms = round((time.perf_counter() - started_total) * 1000, 1)
    row["finalization_timings_ms"] = {**timings, "total": total_ms}
    logger.info("invoice_finalization invoice_id=%s total_ms=%.1f status=%s timings=%s", invoice_id, total_ms, row["zugferd"]["status"], timings)
    return row, document


def _create_invoice(root: Path, contact_id: str, form, actor: str) -> tuple[dict[str,Any],dict[str,Any]]:
    draft=save_invoice_draft(root,contact_id,form,actor)
    return finalize_invoice(root,draft["invoice_id"],actor)


@bp.get("/templates")
@login_required
def template_manager():return render_template("documents/business_templates.html",templates=templates(_root()),is_admin=_is_admin(),business=business_settings(_root()),libreoffice=bool(shutil.which("libreoffice") or shutil.which("soffice")),ghostscript=bool(shutil.which("gs")),verapdf=bool(shutil.which("verapdf")))

@bp.get("/templates/din5008-guide.pdf")
@login_required
def din5008_template_guide():
    return send_file(io.BytesIO(din5008_template_guide_pdf()), mimetype="application/pdf", as_attachment=True, download_name="SimpleOffice-DIN5008-Vorlagenmuster.pdf")

@bp.post("/templates")
@login_required
def upload_template():
    if not _is_admin():abort(403)
    upload=request.files.get("template")
    if upload is None:abort(400)
    try:save_template(_root(),upload,request.form.get("name",""),_actor());flash("Corporate-Design-Vorlage gespeichert.")
    except ValueError as exc:flash(str(exc))
    return redirect(url_for(".template_manager"))

@bp.post("/templates/settings")
@login_required
def update_business_settings():
    if not _is_admin():abort(403)
    try:save_business_settings(_root(),request.form.to_dict(),_actor());flash("Rechnungssteller- und Dokumenteinstellungen gespeichert.")
    except ValueError as exc:flash(str(exc))
    return redirect(url_for(".template_manager"))

@bp.post("/templates/<template_id>/activate")
@login_required
def activate_template(template_id:str):
    if not _is_admin():abort(403)
    try:set_active_template(_root(),template_id,_actor());flash("Vorlage aktiviert.")
    except ValueError as exc:flash(str(exc))
    return redirect(url_for(".template_manager"))

@bp.get("/objects/invoice-catalog.json")
@login_required
def invoice_object_catalog():
    query=request.args.get("q","").strip(); store=ObjectStore(_root())
    if request.args.get("categories")=="1":
        items=[]
        for item in store.invoice_categories():
            if query and query.casefold() not in f"{item.get('display_id','')} {item.get('name','')} {item.get('invoice',{}).get('category','')}".casefold():continue
            effective=store.invoice_effective(item);items.append({"object_id":item["object_id"],"id":item["display_id"],"name":item["name"],"vat_rate":effective.get("default_vat_rate") or effective.get("vat_rate","") ,"net_price":effective.get("default_net_price") or effective.get("net_price","") ,"gross_price":effective.get("default_gross_price") or effective.get("gross_price","") ,"price_group":effective.get("default_price_group") or effective.get("price_group","")})
            if len(items)>=20:break
        return jsonify({"items":items})
    return jsonify({"items":store.invoice_candidates(query,20)})


@bp.get("/projects/invoice-candidates.json")
@login_required
def project_invoice_candidates():
    root,actor=_root(),_actor(); store=ProjectStore(root); billed=_billed_project_sources(root); items=[]
    for project in store.projects():
        if project.get("status") == "cancelled":continue
        projection=store.billing_projection(project["project_id"],actor)
        for line in projection["lines"]:
            ref=(project["project_id"],str(line["source_type"]),str(line["source_id"]))
            if ref in billed:continue
            minutes=int(line.get("minutes",0)); quantity=(Decimal(minutes)/Decimal(60)).quantize(QTY).normalize()
            items.append({"project_id":project["project_id"],"project_title":project["title"],"source_type":line["source_type"],"source_id":line["source_id"],"description":line["description"],"minutes":minutes,"quantity":format(quantity,"f"),"category":"","net_price":"","vat_rate":"19"})
    return jsonify({"items":items})


@bp.get("/contacts/<contact_id>/appointment-invoice-candidates.json")
@login_required
def appointment_invoice_candidates(contact_id: str):
    root, actor = _root(), _actor()
    contacts = ContactStore(root)
    if not contacts.can_manage(contact_id, actor):
        abort(403)
    billed = _billed_appointment_sources(root)
    items = []
    for event in CalendarStore(root).events(actor):
        billing = event.get("billing", {}) if isinstance(event.get("billing"), dict) else {}
        if event.get("contact_id") != contact_id or event.get("event_id") in billed or not billing.get("billable"):
            continue
        if event.get("status", "active") in {"cancelled", "deleted", "moved"}:
            continue
        appointment_type = str(event.get("appointment_type") or "").strip()
        description = str(billing.get("description") or "").strip()
        if not description:
            prefix = f"{appointment_type}: " if appointment_type else "Termin: "
            description = f"{prefix}{event.get('title', 'Leistung')} ({str(event.get('start', ''))[:10]})"
        items.append({
            "source_type": "calendar_event",
            "source_id": event["event_id"],
            "event_id": event["event_id"],
            "event_start": event.get("start", ""),
            "appointment_type": appointment_type,
            "attendance": event.get("attendance", ""),
            "description": description,
            "quantity": billing.get("quantity", "1"),
            "net_price": billing.get("net_price", "0.00"),
            "vat_rate": billing.get("vat_rate", "19"),
            "currency": billing.get("currency", "EUR"),
            "category": "Termin",
        })
    return jsonify({"items": sorted(items, key=lambda item: item["event_start"], reverse=True)})

@bp.route("/contacts/<contact_id>/letter",methods=("GET","POST"))
@login_required
def contact_letter(contact_id:str):
    root,actor=_root(),_actor();contacts=ContactStore(root);contact=contacts.get(contact_id,actor)
    if not contacts.can_manage(contact_id,actor):abort(403)
    crm=ContactCRMStore(root).record(contact_id);selected=request.form.get("address","") if request.method=="POST" else request.args.get("address","");address,addresses=address_labels(contact,crm,selected)
    if request.method=="POST" and request.form.get("body","").strip():
        try:
            if not address:raise ValueError("recipient address is required")
            tpl=active_template(root,request.form.get("template_id",""));subject=request.form.get("subject","").strip();pdf=render_business_pdf(root,tpl,recipient=address,subject=subject,markdown=request.form.get("body","").strip(),cover=request.form.get("cover")=="1");document=_store_generated_pdf(root,contact_id,subject or "Brief",pdf,actor,"letter",tpl["template_id"]);flash("Brief erzeugt und mit dem CRM-Kontakt verknüpft.");return redirect(url_for("documents.detail",document_id=document["document_id"]))
        except ValueError as exc:flash(str(exc))
    return render_template("documents/contact_letter.html",contact=contact,crm=crm,address=address,addresses=addresses,templates=templates(root),links=contact_links(root,contact_id))

@bp.route("/contacts/<contact_id>/invoice",methods=("GET","POST"))
@login_required
def contact_invoice(contact_id:str):
    root,actor=_root(),_actor();contacts=ContactStore(root)
    try:contact=contacts.get(contact_id,actor)
    except ValueError:abort(404)
    if not contacts.can_manage_contact(contact,actor):abort(403)
    draft_id=request.form.get("invoice_id","").strip() if request.method=="POST" else request.args.get("invoice_id","").strip(); draft=None
    if draft_id:
        try:draft=invoice(root,draft_id)
        except ValueError:abort(404)
        if draft.get("contact_id")!=contact_id or draft.get("status")!="draft":abort(409)
    crm=ContactCRMStore(root).record(contact_id);selected=request.form.get("address","") if request.method=="POST" else (draft.get("buyer",{}).get("address_id","") if draft else request.args.get("address",""));address,addresses=address_labels(contact,crm,selected);settings=business_settings(root)
    if request.method=="POST":
        try:
            row=save_invoice_draft(root,contact_id,request.form,actor,draft_id)
            if request.form.get("action")=="finalize":
                row,document=finalize_invoice(root,row["invoice_id"],actor)
                if row["zugferd"]["status"] == "validated":
                    message = f"Invoice {row['invoice_number']} finalized, linked and technically validated." if g.language == "en" else f"Rechnung {row['invoice_number']} finalisiert, verknüpft und technisch validiert."
                else:
                    message = f"Invoice {row['invoice_number']} was created, but technical validation failed: {row['zugferd']['status']}." if g.language == "en" else f"Rechnung {row['invoice_number']} wurde erzeugt, aber die technische Validierung ist fehlgeschlagen: {row['zugferd']['status']}."
                flash(message);return redirect(url_for(".invoice_detail",invoice_id=row["invoice_id"]))
            flash("Rechnungsentwurf schnell gespeichert. Es wurde noch keine endgültige Rechnungsnummer vergeben.");return redirect(url_for(".invoice_detail",invoice_id=row["invoice_id"]))
        except PermissionError:abort(403)
        except ValueError as exc:flash(str(exc))
    fields=contact.get("fields",{}); default_names=_recipient_names(contact); default_company=str(fields.get("company","")).strip(); default_person=next((name for name in default_names if name.casefold()!=default_company.casefold()),"")
    recipient={"type":draft.get("buyer",{}).get("recipient_type") if draft else ("company" if default_company else "private"),"company":draft.get("buyer",{}).get("company","") if draft else default_company,"contact":draft.get("buyer",{}).get("contact_name","") if draft else (default_person or str(fields.get("display_name","")).strip())}
    if request.method=="POST":recipient={"type":request.form.get("recipient_type","private"),"company":request.form.get("recipient_company",""),"contact":request.form.get("recipient_contact","")}
    payment_days=str((date.fromisoformat(draft["due_date"])-date.fromisoformat(draft["issue_date"])).days) if draft else str(crm.get("payment_days") or settings.get("default_payment_days") or "14");return render_template("documents/contact_invoice.html",contact=contact,crm=crm,address=address,addresses=addresses,templates=templates(root),business=settings,payment_days=payment_days,issue_date=draft.get("issue_date",date.today().isoformat()) if draft else date.today().isoformat(),service_date=draft.get("service_date",date.today().isoformat()) if draft else date.today().isoformat(),links=contact_links(root,contact_id),draft=draft,recipient=recipient)


@bp.get("/invoices/<invoice_id>/draft.pdf")
@login_required
def invoice_draft_preview(invoice_id: str):
    root,actor=_root(),_actor()
    try:row=invoice(root,invoice_id)
    except ValueError:abort(404)
    if not ContactStore(root).can_manage(row["contact_id"],actor):abort(403)
    try:pdf=draft_invoice_pdf(root,row)
    except ValueError:abort(409)
    return send_file(io.BytesIO(pdf),as_attachment=False,download_name=f"{_safe_filename(row['invoice_number'])}.pdf",mimetype="application/pdf")


@bp.get("/contacts/<contact_id>/billing")
@login_required
def customer_billing(contact_id: str):
    root, actor = _root(), _actor(); contacts = ContactStore(root)
    try: contact = contacts.get(contact_id, actor)
    except ValueError: abort(404)
    if not contacts.can_manage_contact(contact, actor): abort(403)
    rows = [row for row in invoices(root) if row.get("contact_id") == contact_id]
    candidates = [item for item in contacts.contacts(actor) if item.get("contact_id") != contact_id and contacts.can_manage_contact(item, actor)]
    customer_documents = _customer_document_rows(root, contact_id, rows)
    return render_template("documents/customer_billing.html", contact=contact, rows=rows,
                           customer_documents=customer_documents,
                           credit=CustomerCreditLedger(root).account(contact_id),
                           referrals=CustomerCreditLedger(root).referrals(contact_id),
                           candidates=candidates, today=date.today().isoformat())


@bp.post("/contacts/<contact_id>/credits")
@login_required
def add_customer_credit(contact_id: str):
    root, actor = _root(), _actor()
    if not ContactStore(root).can_manage(contact_id, actor): abort(403)
    try:
        CustomerCreditLedger(root).add(contact_id, request.form.get("amount", ""),
            kind=request.form.get("kind", "topup"), tax_treatment=request.form.get("tax_treatment", ""),
            actor=actor, note=request.form.get("note", ""), reference=request.form.get("reference", ""),
            currency=request.form.get("currency", "EUR"), related_contact_id=request.form.get("related_contact_id", ""))
        flash("Kundenguthaben wurde revisionssicher gebucht.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".customer_billing", contact_id=contact_id))


@bp.post("/contacts/<contact_id>/referrals")
@login_required
def add_customer_referral(contact_id: str):
    root, actor = _root(), _actor(); referred_id = request.form.get("referred_id", "").strip(); contacts = ContactStore(root)
    if not contacts.can_manage(contact_id, actor) or not contacts.can_manage(referred_id, actor): abort(403)
    try:
        ledger = CustomerCreditLedger(root); ledger.add_referral(contact_id, referred_id, actor, request.form.get("note", ""))
        reward = request.form.get("reward_amount", "").strip()
        if reward:
            ledger.add(contact_id, reward, kind="referral", tax_treatment=request.form.get("tax_treatment", "manual_review"), actor=actor, note=f"Prämie für geworbenen Kunden {referred_id}", related_contact_id=referred_id)
        flash("Kundenwerbung wurde gespeichert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".customer_billing", contact_id=contact_id))


@bp.post("/contacts/<contact_id>/credits/refund")
@login_required
def refund_customer_credit(contact_id: str):
    root, actor = _root(), _actor()
    if not ContactStore(root).can_manage(contact_id, actor): abort(403)
    try:
        CustomerCreditLedger(root).refund(contact_id, request.form.get("amount", ""), actor=actor,
            reference=request.form.get("reference", ""), note=request.form.get("note", ""), currency=request.form.get("currency", "EUR"))
        flash("Guthabenauszahlung wurde protokolliert.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".customer_billing", contact_id=contact_id))


def _invoice_pdf_path(root: Path, row: dict[str, Any]) -> Path:
    document = DocumentStore(root).get_document(row.get("document_id", ""))
    path = (root / str(document.get("last_path", ""))).resolve()
    try: path.relative_to(root)
    except ValueError as exc: raise ValueError("invoice PDF is outside document storage") from exc
    if not path.is_file(): raise ValueError("invoice PDF not found")
    return path


@bp.get("/invoices/<invoice_id>/download")
@login_required
def invoice_download(invoice_id: str):
    root, actor = _root(), _actor()
    try: row = invoice(root, invoice_id)
    except ValueError: abort(404)
    if not ContactStore(root).can_manage(row["contact_id"], actor): abort(403)
    if row.get("status") == "draft":
        return send_file(io.BytesIO(draft_invoice_pdf(root,row)),as_attachment=True,download_name=f"{_safe_filename(row['invoice_number'])}.pdf",mimetype="application/pdf")
    try: path = _invoice_pdf_path(root, row)
    except ValueError: abort(404)
    return send_file(path, as_attachment=True, download_name=f"Rechnung-{_safe_filename(row['invoice_number'])}.pdf", mimetype="application/pdf")


@bp.get("/contacts/<contact_id>/invoices.zip")
@login_required
def customer_invoice_archive(contact_id: str):
    root, actor = _root(), _actor()
    if not ContactStore(root).can_manage(contact_id, actor): abort(403)
    target = io.BytesIO(); count = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for row in invoices(root):
            if row.get("contact_id") != contact_id: continue
            try: path = _invoice_pdf_path(root, row)
            except ValueError: continue
            archive.writestr(f"Rechnung-{_safe_filename(row['invoice_number'])}.pdf", path.read_bytes()); count += 1
    if count == 0: abort(404)
    target.seek(0)
    return send_file(target, as_attachment=True, download_name=f"Rechnungen-{_safe_filename(contact_id)}.zip", mimetype="application/zip")


@bp.get("/contacts/<contact_id>/customer-documents.zip")
@login_required
def customer_document_archive_download(contact_id: str):
    root, actor = _root(), _actor()
    contacts = ContactStore(root)
    try:
        contact = contacts.get(contact_id, actor)
    except ValueError:
        abort(404)
    if not contacts.can_manage(contact_id, actor):
        abort(403)
    try:
        target, _summary = customer_document_archive(root, contact, actor)
    except ValueError:
        abort(404)
    response = send_file(
        target, as_attachment=True,
        download_name=f"Kundenakte-{_safe_filename(contact.get('fields', {}).get('display_name', contact_id))}.zip",
        mimetype="application/zip", conditional=False,
    )
    response.call_on_close(target.close)
    return response

@bp.get("/invoices")
@login_required
def invoice_overview():
    root,actor=_root(),_actor();contacts=ContactStore(root);query=request.args.get("q","").strip().casefold();selected_status=request.args.get("status","").strip()
    contact_map={item["contact_id"]:item for item in contacts.contacts(actor) if contacts.can_manage_contact(item,actor)}
    rows=[]
    for row in invoices(root):
        contact=contact_map.get(row.get("contact_id"))
        if contact is None:continue
        state=row["payment_state"]["status"]
        if selected_status and state!=selected_status:continue
        searchable=f"{row.get('invoice_number','')} {row.get('buyer',{}).get('name','')} {contact.get('fields',{}).get('display_name','')}"
        if query and query not in searchable.casefold():continue
        rows.append({**row,"contact":contact})
    stats={"total":len(rows),"open":sum(row["payment_state"]["status"] in {"open","partial"} for row in rows),"overdue":sum(row["payment_state"]["status"]=="overdue" for row in rows),"paid":sum(row["payment_state"]["status"] in {"paid","credited"} for row in rows),"written_off":sum(row["payment_state"]["status"]=="written_off" for row in rows)}
    return render_template("documents/invoice_overview.html",rows=rows,stats=stats,query=request.args.get("q","").strip(),selected_status=selected_status)

@bp.get("/invoices/<invoice_id>")
@login_required
def invoice_detail(invoice_id:str):
    try:row=invoice(_root(),invoice_id)
    except ValueError:abort(404)
    contacts=ContactStore(_root());contact=contacts.get(row["contact_id"],_actor())
    if not contacts.can_manage_contact(contact,_actor()):abort(403)
    return render_template("documents/invoice_detail.html",invoice={**row,"payment_state":invoice_state(row)},contact=contact,today=date.today().isoformat(),credit=CustomerCreditLedger(_root()).account(row["contact_id"],row.get("currency","EUR")),write_off_reasons=sorted(WRITE_OFF_REASONS))

@bp.post("/invoices/<invoice_id>/payments")
@login_required
def invoice_payment(invoice_id:str):
    root,actor=_root(),_actor()
    try:row=invoice(root,invoice_id)
    except ValueError:abort(404)
    if not ContactStore(root).can_manage(row["contact_id"],actor):abort(403)
    try:record_invoice_payment(root,invoice_id,request.form,actor);flash(translate(g.language,"invoice.payment.saved"))
    except ValueError as exc:
        keys={"invoice is already paid":"invoice.payment.error.paid","payment amount must be positive and not exceed the outstanding amount":"invoice.payment.error.amount","payment date must be a valid ISO date":"invoice.payment.error.date"}
        flash(translate(g.language,keys.get(str(exc),"invoice.payment.error.default")))
    return redirect(url_for(".invoice_detail",invoice_id=invoice_id))


@bp.post("/invoices/<invoice_id>/write-off")
@login_required
def invoice_write_off(invoice_id: str):
    root, actor = _root(), _actor()
    try:
        row = invoice(root, invoice_id)
    except ValueError:
        abort(404)
    if not ContactStore(root).can_manage(row["contact_id"], actor):
        abort(403)
    error_keys = {
        "a draft invoice cannot be written off": "writeoff.error.draft",
        "invoice has no collectible outstanding amount": "writeoff.error.no_outstanding",
        "write-off reason is invalid": "writeoff.error.reason",
        "a note is required for another write-off reason": "writeoff.error.note",
        "write-off amount must be positive and not exceed the collectible outstanding amount": "writeoff.error.amount",
        "write-off date must be a valid ISO date": "writeoff.error.date",
        "write-off date cannot precede the invoice date": "writeoff.error.date_before_invoice",
        "stopping collection requires writing off the full collectible outstanding amount": "writeoff.error.stop_requires_full",
    }
    try:
        write_off_invoice(root, invoice_id, request.form, actor)
        flash(translate(g.language, "writeoff.saved"))
    except ValueError as exc:
        flash(translate(g.language, error_keys.get(str(exc), "writeoff.error")))
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))


@bp.post("/invoices/<invoice_id>/apply-credit")
@login_required
def invoice_apply_credit(invoice_id: str):
    root, actor = _root(), _actor()
    try: row = invoice(root, invoice_id)
    except ValueError: abort(404)
    if not ContactStore(root).can_manage(row["contact_id"], actor): abort(403)
    try:
        updated = apply_available_customer_credit(root, invoice_id, actor)
        amount = updated.get("credit_applied", "0.00")
        flash("Kundenguthaben wurde auf die Rechnung angewendet." if amount != "0.00" else "Kein verrechenbares Kundenguthaben vorhanden.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))


@bp.post("/invoices/<invoice_id>/credit-notes")
@login_required
def invoice_credit_note(invoice_id: str):
    root, actor = _root(), _actor()
    try: row = invoice(root, invoice_id)
    except ValueError: abort(404)
    if not ContactStore(root).can_manage(row["contact_id"], actor): abort(403)
    try:
        note, _document = create_credit_note(root, invoice_id, request.form.get("amount", ""), request.form.get("reason", ""), actor)
        flash(f"Gutschrift {note['credit_note_number']} wurde erstellt.")
    except ValueError as exc: flash(str(exc))
    return redirect(url_for(".invoice_detail", invoice_id=invoice_id))

@bp.post("/contacts/<contact_id>/attach")
@login_required
def attach_existing(contact_id:str):
    root,actor=_root(),_actor();contacts=ContactStore(root)
    if not contacts.can_manage(contact_id,actor):abort(403)
    document_id=request.form.get("document_id","").strip();store=DocumentStore(root)
    try:document=store.get_document(document_id)
    except ValueError:abort(404)
    metadata:dict[str,Any]={};path=root/str(document.get("last_path",""))
    if path.suffix.casefold()==".pdf" and path.is_file():
        details=inspect_zugferd_pdf(path)
        if details.get("detected"):
            metadata["zugferd"]={key:value for key,value in details.items() if key!="raw_xml"};store.set_attribute(document_id,"zugferd_detected","yes",actor)
            for key in ("invoice_id","profile","currency","grand_total","due_payable"):
                if details.get(key):store.set_attribute(document_id,f"zugferd_{key}",str(details[key]),actor)
    attach_contact_document(root,contact_id,document_id,actor,relation=request.form.get("relation","correspondence"),metadata=metadata);flash("Dokument mit Kontakt verknüpft.");return redirect(url_for(".contact_letter",contact_id=contact_id))

@bp.get("/zugferd/<document_id>")
@login_required
def zugferd_details(document_id:str):
    root=_root();store=DocumentStore(root)
    try:document=store.get_document(document_id)
    except ValueError:abort(404)
    return render_template("documents/zugferd_details.html",document=document,details=inspect_zugferd_pdf(root/str(document.get("last_path",""))))
