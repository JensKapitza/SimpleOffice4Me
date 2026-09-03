"""Immutable approval, PDF reports and tenant packages for rental billing."""
from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .file_lock import exclusive_file_lock
from .rental_calc import RentalCalculationStore
from .rental_types import (
    ALLOCATION_METHODS, CALCULATION_VERSION, LEDGER_KINDS, METRIC_TYPES, MONEY,
    allocate_money, intersection, money, parse_date, safe_name, sha256_bytes, utc_now,
)


class RentalBillingStore(RentalCalculationStore):
    def approval_directory(self, settlement_id: str) -> Path:
        settlement=self.settlement(settlement_id)
        return self.approval_root/settlement_id/f"v{int(settlement['version']):03d}"

    def approval_files(self, settlement_id: str) -> dict[str,Path]:
        directory=self.approval_directory(settlement_id)
        result={
            "snapshot":directory/"snapshot.json",
            "snapshot_sha256":directory/"snapshot.sha256",
            "approval_manifest":directory/"approval-manifest.json",
            "approval_pdf":directory/"Freigabe-und-Berechnungsnachweis.pdf",
            "landlord_pdf":directory/"Vermieter-Abrechnungsblatt.pdf",
        }
        for path in directory.glob("Mieterabrechnung-*.pdf"): result[path.name]=path
        for path in directory.glob("Mieterpaket-*.zip"): result[path.name]=path
        return result

    def build_snapshot(self, settlement_id: str) -> dict[str,Any]:
        calculation=self.calculate(settlement_id); settlement=calculation["settlement"]
        units=[]
        for unit in calculation["units"]:
            obj=unit["object"]
            units.append({"object_id":unit["object_id"],"label":unit["label"],"name":obj.get("name",""),"identifier":obj.get("identifier",""),"location":obj.get("location",""),"type":obj.get("type","")})
        start,end=parse_date(settlement["starts_on"]),parse_date(settlement["ends_on"])
        contacts={}; tenancies=[]; document_ids=set(); manual_inputs=[]; metrics=[]
        for unit in units:
            for tenancy in self.tenancies(unit["object_id"]):
                tenancy_start=parse_date(tenancy["starts_on"]); tenancy_end=parse_date(tenancy["ends_on"],optional=True) or date.max
                if not intersection(start,end,tenancy_start,tenancy_end): continue
                contact=self._contact(tenancy["contact_id"]); fields=contact.get("fields",{})
                contacts[tenancy["contact_id"]]={"contact_id":tenancy["contact_id"],"display_name":fields.get("display_name",""),"email":fields.get("email",""),"company":fields.get("company","")}
                row=dict(tenancy)
                if row.get("contract_document_id"):
                    row["contract_document"]=self._document_snapshot(row["contract_document_id"]); document_ids.add(row["contract_document_id"])
                tenancies.append(row)
            for metric in self.metrics(unit["object_id"]):
                metric_start=parse_date(metric["valid_from"]); metric_end=parse_date(metric["valid_to"],optional=True) or date.max
                if not intersection(start,end,metric_start,metric_end): continue
                row=dict(metric)
                if row.get("source_document_id"):
                    row["source_document"]=self._document_snapshot(row["source_document_id"]); document_ids.add(row["source_document_id"])
                if row["source_kind"]=="manual": manual_inputs.append({"type":"metric",**row})
                metrics.append(row)
        costs=[]
        for result in calculation["costs"]:
            row=json.loads(json.dumps(result,ensure_ascii=False,default=str)); cost=row["cost"]
            if cost.get("source_document_id"):
                cost["source_document"]=self._document_snapshot(cost["source_document_id"]); document_ids.add(cost["source_document_id"])
            if cost.get("source_kind")=="manual": manual_inputs.append({"type":"cost",**cost})
            for provenance in row.get("weight_provenance",[]):
                if provenance.get("source_document_id"):
                    provenance["source_document"]=self._document_snapshot(provenance["source_document_id"]); document_ids.add(provenance["source_document_id"])
                if provenance.get("source_kind")=="manual": manual_inputs.append({"type":provenance.get("kind","weight"),**provenance})
            costs.append(row)
        tenants=json.loads(json.dumps(calculation["tenants"],ensure_ascii=False))
        for tenant in tenants.values():
            for ledger in tenant.get("ledger",[]):
                if ledger.get("document_id"):
                    ledger["document"]=self._document_snapshot(ledger["document_id"]); document_ids.add(ledger["document_id"])
                if ledger.get("source_kind")=="manual": manual_inputs.append({"type":"ledger",**ledger})
        return {
            "schema":"simpleoffice-rental-settlement-snapshot-v1","calculation_version":CALCULATION_VERSION,
            "settlement":settlement,"units":units,"contacts":contacts,"tenancies":tenancies,"metrics":metrics,
            "costs":costs,"tenants":tenants,"vacancy":calculation["vacancy"],"total_effective_costs":calculation["total_effective_costs"],
            "manual_inputs":manual_inputs,"referenced_document_ids":sorted(document_ids),
            "rules":{
                "period_proration":"Kosten außerhalb des Abrechnungszeitraums werden taggenau nach Kalendertagen gekürzt.",
                "object_allocation":"Kosten werden mit dem dokumentierten Schlüssel auf Objekte verteilt; die Cent-Rundung bleibt summenerhaltend.",
                "tenant_allocation":"Objektanteile werden nach Überschneidung von Kosten- und Mietzeitraum auf Mieter verteilt. Nicht vermietete Anteile bleiben beim Vermieter.",
                "person_days":"Personentage = Summe(Personenzahl × Kalendertage der jeweiligen Gültigkeitsperiode).",
            },
        }

    def approve(self, settlement_id: str, actor: str) -> dict[str,Any]:
        lock_path=self.approval_root/f".{safe_name(settlement_id)}.approval.lock"
        with exclusive_file_lock(lock_path):
            # Re-read editable state only after the cross-process lock was acquired.
            # Otherwise two requests can render into the same immutable version.
            settlement=self._require_editable(settlement_id); snapshot=self.build_snapshot(settlement_id); approved_at=utc_now()
            snapshot["approval"]={"approved_at":approved_at,"approved_by":actor,"version":int(settlement["version"])}
            directory=self.approval_directory(settlement_id)
            staging=directory.parent/f".{directory.name}.staging-{uuid.uuid4().hex}"
            published=False
            try:
                staging.mkdir(parents=True,exist_ok=False)
                snapshot["frozen_evidence"]=self._freeze_documents(snapshot,staging)
                snapshot_path=staging/"snapshot.json"; snapshot_bytes=(json.dumps(snapshot,ensure_ascii=False,indent=2)+"\n").encode("utf-8"); snapshot_path.write_bytes(snapshot_bytes)
                digest=sha256_bytes(snapshot_bytes); (staging/"snapshot.sha256").write_text(f"{digest}  snapshot.json\n",encoding="ascii")
                snapshot["approval"]["snapshot_sha256"]=digest
                self._render_approval_pdf(snapshot,staging/"Freigabe-und-Berechnungsnachweis.pdf")
                self._render_landlord_pdf(snapshot,staging/"Vermieter-Abrechnungsblatt.pdf")
                for contact_id in sorted(snapshot["tenants"]):
                    tenant_pdf=staging/f"Mieterabrechnung-{safe_name(contact_id)}.pdf"; self._render_tenant_pdf(snapshot,contact_id,tenant_pdf)
                    self._build_tenant_zip(snapshot,contact_id,tenant_pdf,staging/f"Mieterpaket-{safe_name(contact_id)}.zip")
                self._write_manifest(snapshot,staging)

                # Editable settlements cannot have a valid approved directory.
                # Remove only stale output for this exact settlement/version, then
                # publish the freshly rendered tree with a same-filesystem rename.
                if directory.exists():
                    shutil.rmtree(directory)
                staging.replace(directory); published=True
                with self._db() as db:
                    cursor=db.execute("UPDATE rental_settlement SET status='approved',approved_at=?,approved_by=?,snapshot_sha256=?,updated_at=? WHERE settlement_id=? AND status IN ('draft','review')",(approved_at,actor,digest,approved_at,settlement_id))
                    if cursor.rowcount!=1: raise ValueError("Abrechnung konnte nicht atomar freigegeben werden")
            except Exception:
                shutil.rmtree(staging,ignore_errors=True)
                if published:
                    shutil.rmtree(directory,ignore_errors=True)
                raise
            self._revision("rental_settlement_approved",actor,"rental-settlements",settlement_id,{"snapshot_sha256":digest,"version":settlement["version"]})
            return {"settlement":self.settlement(settlement_id),"snapshot":snapshot,"files":{key:str(value) for key,value in self.approval_files(settlement_id).items()}}

    def clone_correction(self, settlement_id: str, actor: str) -> dict[str,Any]:
        old=self.settlement(settlement_id)
        if old["status"] not in {"approved","sent"}: raise ValueError("Nur freigegebene Abrechnungen können korrigiert werden")
        new=self.create_settlement(old["label"],old["year"],old["starts_on"],old["ends_on"],actor,group_id=old["group_id"],object_id=old["object_id"],supersedes_id=settlement_id)
        for cost in self.costs(settlement_id):
            copied=self.add_cost(new["settlement_id"],cost["cost_group"],cost["description"],cost["amount"],cost["starts_on"],cost["ends_on"],cost["allocation_method"],actor,direct_object_id=cost["direct_object_id"],source_kind=cost["source_kind"],source_note=cost["source_note"],source_document_id=cost["source_document_id"],tenant_visible=bool(cost["tenant_visible"]))
            for weight in self.manual_weights(cost["cost_id"]): self.set_manual_weight(new["settlement_id"],copied["cost_id"],weight["object_id"],weight["weight"],actor,source_kind=weight["source_kind"],source_note=weight["source_note"],source_document_id=weight["source_document_id"])
        with self._db() as db: db.execute("UPDATE rental_settlement SET status='corrected',updated_at=? WHERE settlement_id=?",(utc_now(),settlement_id))
        self._revision("rental_settlement_correction_created",actor,"rental-settlements",settlement_id,{"new_settlement_id":new["settlement_id"]})
        return self.settlement(new["settlement_id"])

    def tenant_package(self, settlement_id: str, contact_id: str) -> Path:
        settlement=self.settlement(settlement_id)
        if settlement["status"] not in {"approved","sent","corrected"}: raise ValueError("Mieterpakete dürfen erst nach Freigabe ausgegeben werden")
        path=self.approval_directory(settlement_id)/f"Mieterpaket-{safe_name(contact_id)}.zip"
        if not path.is_file(): raise ValueError("Freigegebenes Mieterpaket fehlt")
        return path

    def _snapshot_documents(self, snapshot: dict[str,Any]) -> dict[str,dict[str,Any]]:
        documents={}
        def add(value):
            if isinstance(value,dict) and value.get("document_id") and value.get("sha256"): documents[value["document_id"]]=value
        for tenancy in snapshot.get("tenancies",[]): add(tenancy.get("contract_document"))
        for metric in snapshot.get("metrics",[]): add(metric.get("source_document"))
        for result in snapshot.get("costs",[]):
            add(result.get("cost",{}).get("source_document"))
            for provenance in result.get("weight_provenance",[]): add(provenance.get("source_document"))
        for tenant in snapshot.get("tenants",{}).values():
            for ledger in tenant.get("ledger",[]): add(ledger.get("document"))
        return documents

    def _freeze_documents(self, snapshot: dict[str,Any], directory: Path) -> dict[str,dict[str,Any]]:
        evidence=directory/"Belege"; evidence.mkdir(parents=True,exist_ok=True); frozen={}
        for document_id,doc in self._snapshot_documents(snapshot).items():
            source=self._safe_document_path(doc["path"])
            if self._sha256_file(source)!=doc["sha256"]: raise ValueError(f"Beleg {document_id} hat sich während der Freigabe geändert")
            target=evidence/f"{safe_name(document_id)}-{safe_name(doc.get('name') or source.name)}"; shutil.copy2(source,target); digest=self._sha256_file(target)
            if digest!=doc["sha256"]: raise ValueError(f"Belegkopie {document_id} ist nicht identisch")
            frozen[document_id]={"relative_path":str(target.relative_to(directory)),"sha256":digest,"size":target.stat().st_size,"original_path":doc["path"]}
        return frozen

    def _write_manifest(self, snapshot: dict[str,Any], directory: Path) -> None:
        artifacts=[]
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.name!="approval-manifest.json": artifacts.append({"path":str(path.relative_to(directory)),"sha256":self._sha256_file(path),"size":path.stat().st_size})
        manifest={"schema":"simpleoffice-rental-approval-manifest-v1","settlement_id":snapshot["settlement"]["settlement_id"],"version":snapshot["settlement"]["version"],"approved_at":snapshot["approval"]["approved_at"],"approved_by":snapshot["approval"]["approved_by"],"snapshot_sha256":snapshot["approval"]["snapshot_sha256"],"artifacts":artifacts}
        (directory/"approval-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    @staticmethod
    def _styles():
        styles=getSampleStyleSheet(); styles.add(ParagraphStyle(name="RentalSmall",parent=styles["BodyText"],fontSize=8,leading=10)); return styles

    @staticmethod
    def _table(rows,widths=None,repeat_rows=1):
        table=Table(rows,colWidths=widths,repeatRows=repeat_rows,hAlign="LEFT"); table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e9ecef")),("GRID",(0,0),(-1,-1),.25,colors.HexColor("#adb5bd")),("VALIGN",(0,0),(-1,-1),"TOP"),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),8)])); return table

    @staticmethod
    def _doc(path: Path,title: str): return SimpleDocTemplate(str(path),pagesize=A4,rightMargin=12*mm,leftMargin=12*mm,topMargin=12*mm,bottomMargin=12*mm,title=title,author="SimpleOffice4Me")

    def _render_approval_pdf(self,snapshot: dict[str,Any],path: Path) -> None:
        styles=self._styles(); s=snapshot["settlement"]; a=snapshot["approval"]; story=[Paragraph("Freigabe- und Berechnungsnachweis",styles["Title"]),Paragraph(f"{s['label']} · Version {s['version']} · {s['starts_on']} bis {s['ends_on']}",styles["BodyText"]),Spacer(1,3*mm),self._table([["Freigegeben",a["approved_at"]],["Benutzer",a["approved_by"]],["Snapshot SHA-256",a["snapshot_sha256"]],["Algorithmus",snapshot["calculation_version"]]],[45*mm,135*mm],0),Spacer(1,3*mm),Paragraph("Berechnungsregeln",styles["Heading2"])]
        for rule in snapshot["rules"].values(): story.append(Paragraph("• "+rule,styles["RentalSmall"]))
        story += [Spacer(1,3*mm),Paragraph("Kosten, Schlüssel und Mieteranteile",styles["Heading2"])]
        labels={unit["object_id"]:unit["label"] for unit in snapshot["units"]}
        for result in snapshot["costs"]:
            cost=result["cost"]; story.append(Paragraph(f"<b>{cost['cost_group']}: {cost['description']}</b> — {cost['amount']} EUR, wirksam {result['effective_amount']} EUR, {cost['starts_on']} bis {cost['ends_on']}, Schlüssel {cost['allocation_method']}, Quelle {cost['source_kind']}: {cost.get('source_note','')}",styles["RentalSmall"]))
            rows=[["Objekt","Schlüsselwert","Objektanteil EUR"]]+[[labels.get(key,key),result.get("weights",{}).get(key,"0"),value] for key,value in result.get("object_allocations",{}).items()]; story.append(self._table(rows,[85*mm,45*mm,40*mm]))
            tenant_rows=[["Mieter","Objekt","Teilzeitraum","Basis","EUR"]]
            for line in result.get("tenant_allocations",[]):
                name=snapshot.get("contacts",{}).get(line["contact_id"],{}).get("display_name") or line["contact_id"]; basis=("Personentage" if line.get("tenant_time_basis")=="person_days" else "Tage")
                tenant_rows.append([name,labels.get(line["object_id"],line["object_id"]),f"{line['starts_on']} – {line['ends_on']}",f"{line.get('tenant_time_weight','0')}/{line.get('tenant_time_weight_total','0')} {basis}",line["amount"]])
            if len(tenant_rows)>1: story.append(self._table(tenant_rows,[38*mm,35*mm,38*mm,45*mm,24*mm]))
            story.append(Spacer(1,2*mm))
        story += [PageBreak(),Paragraph("Manuelle Eingaben",styles["Heading2"])]
        rows=[["Typ","Objekt/Position","Wert/Betrag","Herkunft/Begründung"]]
        for item in snapshot["manual_inputs"]: rows.append([item.get("type",""),item.get("object_id") or item.get("cost_group") or item.get("kind",""),item.get("value") or item.get("weight") or item.get("amount",""),item.get("source_note") or item.get("note","")])
        story.append(self._table(rows,[28*mm,45*mm,30*mm,77*mm])); story += [Spacer(1,3*mm),Paragraph("Belegnachweis",styles["Heading2"])]
        docs=self._snapshot_documents(snapshot); doc_rows=[["Dokument-ID","Name/Pfad","SHA-256"]]+[[doc_id,doc["path"],doc["sha256"]] for doc_id,doc in docs.items()]; story.append(self._table(doc_rows,[42*mm,78*mm,60*mm])); self._doc(path,"Freigabe- und Berechnungsnachweis").build(story)

    def _render_landlord_pdf(self,snapshot: dict[str,Any],path: Path) -> None:
        styles=self._styles(); s=snapshot["settlement"]; story=[Paragraph("Vermieter-Abrechnungsblatt",styles["Title"]),Paragraph(f"{s['label']} · {s['starts_on']} bis {s['ends_on']} · Version {s['version']}",styles["BodyText"]),Spacer(1,3*mm),Paragraph("Jahresübersicht Kosten",styles["Heading2"])]
        rows=[["Kostengruppe","Position","Original EUR","wirksam EUR","Schlüssel"]]+[[r["cost"]["cost_group"],r["cost"]["description"],r["cost"]["amount"],r["effective_amount"],r["cost"]["allocation_method"]] for r in snapshot["costs"]]; story.append(self._table(rows,[35*mm,65*mm,25*mm,25*mm,30*mm]))
        labels={unit["object_id"]:unit["label"] for unit in snapshot["units"]}; totals={key:Decimal("0") for key in labels}
        for result in snapshot["costs"]:
            for object_id,amount in result.get("object_allocations",{}).items(): totals[object_id]+=money(amount)
        story += [Spacer(1,3*mm),Paragraph("Aufteilung je Mietobjekt",styles["Heading2"]),self._table([["Objekt","Kosten EUR","Leerstand/Vermieter EUR"]]+[[labels[key],str(totals[key].quantize(MONEY)),snapshot["vacancy"].get(key,"0.00")] for key in labels],[95*mm,40*mm,45*mm]),Spacer(1,3*mm),Paragraph("Mieterabrechnungen",styles["Heading2"])]
        tenant_rows=[["Mieter","Kosten","Vorausz.","Vortrag","sonst. Konto","Saldo"]]
        for contact_id,tenant in snapshot["tenants"].items(): tenant_rows.append([snapshot["contacts"].get(contact_id,{}).get("display_name") or contact_id,tenant["costs"],tenant["advances"],tenant["opening_balance"],tenant["other_ledger"],tenant["balance"]])
        story.append(self._table(tenant_rows,[55*mm,25*mm,25*mm,25*mm,25*mm,25*mm])); story.append(Paragraph(f"Freigabe: {snapshot['approval']['approved_at']} · {snapshot['approval']['approved_by']} · SHA-256 {snapshot['approval']['snapshot_sha256']}",styles["RentalSmall"])); self._doc(path,"Vermieter-Abrechnungsblatt").build(story)

    def _render_tenant_pdf(self,snapshot: dict[str,Any],contact_id: str,path: Path) -> None:
        styles=self._styles(); tenant=snapshot["tenants"][contact_id]; name=snapshot["contacts"].get(contact_id,{}).get("display_name") or contact_id; s=snapshot["settlement"]; labels={unit["object_id"]:unit["label"] for unit in snapshot["units"]}
        story=[Paragraph("Mieterabrechnung",styles["Title"]),Paragraph(name,styles["Heading2"]),Paragraph(f"{s['label']} · {s['starts_on']} bis {s['ends_on']}",styles["BodyText"]),Spacer(1,3*mm)]
        rows=[["Kostengruppe","Objekt","Teilzeitraum","Berechnung","EUR"]]
        for line in tenant["lines"]:
            basis="Personentage" if line.get("tenant_time_basis")=="person_days" else "Tage"; calc=f"Objekt {line['object_allocated']} EUR; {line.get('tenant_time_weight','0')}/{line.get('tenant_time_weight_total','0')} {basis}"
            rows.append([line["cost_group"],labels.get(line["object_id"],line["object_id"]),f"{line['starts_on']} – {line['ends_on']}",calc,line["amount"]])
        story.append(self._table(rows,[32*mm,35*mm,36*mm,58*mm,19*mm])); story += [Spacer(1,3*mm),self._table([["Umlagefähige Kosten",tenant["costs"]+" EUR"],["Vorauszahlungen","- "+tenant["advances"]+" EUR"],["Offener Vortrag",tenant["opening_balance"]+" EUR"],["Weitere Buchungen",tenant["other_ledger"]+" EUR"],["Ergebnis",tenant["balance"]+" EUR"]],[120*mm,60*mm],0),Spacer(1,3*mm),Paragraph("Verteilungsschlüssel und Nachweise",styles["Heading2"])]
        for result in snapshot["costs"]:
            cost=result["cost"]
            if not any(line["cost_id"]==cost["cost_id"] for line in tenant["lines"]): continue
            weight_text=", ".join(f"{labels.get(key,key)}={value}" for key,value in result.get("weights",{}).items()); story.append(Paragraph(f"<b>{cost['cost_group']} – {cost['description']}</b>: {cost['allocation_method']} ({weight_text}); {cost['starts_on']} bis {cost['ends_on']}; Quelle {cost['source_kind']}: {cost.get('source_note','')}",styles["RentalSmall"]))
        story.append(Paragraph(f"Freigegebener Datenstand SHA-256: {snapshot['approval']['snapshot_sha256']}",styles["RentalSmall"])); self._doc(path,f"Mieterabrechnung {name}").build(story)

    def _tenant_documents(self,snapshot: dict[str,Any],contact_id: str) -> dict[str,dict[str,Any]]:
        tenant=snapshot["tenants"][contact_id]; documents={}
        for result in snapshot["costs"]:
            cost=result["cost"]
            if not cost.get("tenant_visible") or not any(line["cost_id"]==cost["cost_id"] for line in tenant["lines"]): continue
            doc=cost.get("source_document")
            if doc: documents[doc["document_id"]]=doc
            for provenance in result.get("weight_provenance",[]):
                doc=provenance.get("source_document")
                if doc: documents[doc["document_id"]]=doc
        return documents

    def _build_tenant_zip(self,snapshot: dict[str,Any],contact_id: str,tenant_pdf: Path,target: Path) -> None:
        documents=self._tenant_documents(snapshot,contact_id); manifest={"schema":"simpleoffice-rental-tenant-package-v1","settlement_id":snapshot["settlement"]["settlement_id"],"version":snapshot["settlement"]["version"],"contact_id":contact_id,"snapshot_sha256":snapshot["approval"]["snapshot_sha256"],"documents":list(documents.values())}
        with zipfile.ZipFile(target,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(tenant_pdf,"Mieterabrechnung.pdf"); archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
            for doc in documents.values():
                frozen=snapshot["frozen_evidence"].get(doc["document_id"],{}); source=target.parent/str(frozen.get("relative_path",""))
                if not source.is_file(): raise ValueError("Eingefrorener Beleg fehlt")
                archive.write(source,f"Belege/{safe_name(doc['document_id'])}-{safe_name(doc.get('name') or source.name)}")
