"""Deterministic rental cost allocation calculations."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .rental_store import RentalStoreBase
from .rental_types import MONEY, allocate_money, days, intersection, iso, money, number, parse_date


class RentalCalculationStore(RentalStoreBase):
    def _metric_weight(self, object_id: str, metric_type: str, start: date, end: date, *, person_days: bool = False) -> Decimal:
        spans=[]
        for item in self.metrics(object_id):
            if item["metric_type"] != metric_type: continue
            overlap=intersection(start,end,parse_date(item["valid_from"]),parse_date(item["valid_to"],optional=True) or date.max)
            if overlap: spans.append((number(item["value"]),days(*overlap)))
        if not spans: return Decimal("0")
        integrated=sum((value*span_days for value,span_days in spans),Decimal("0"))
        return integrated if person_days else integrated/Decimal(days(start,end))

    def _settlement_units(self, settlement: dict[str, Any]) -> list[dict[str, Any]]:
        units=self.group(settlement["group_id"])["units"] if settlement.get("group_id") else [{"object_id":settlement["object_id"],"label":"","active":1}]
        result=[]
        for unit in units:
            obj=self._object(unit["object_id"])
            result.append({"object_id":unit["object_id"],"label":unit.get("label") or obj.get("name",unit["object_id"]),"object":obj})
        return result

    def _settlement_unit_ids(self, settlement_id: str) -> set[str]:
        return {row["object_id"] for row in self._settlement_units(self.settlement(settlement_id))}

    def _weights_for_cost(self, settlement: dict[str, Any], cost: dict[str, Any], start: date, end: date) -> tuple[dict[str, Decimal],list[dict[str,Any]]]:
        units=self._settlement_units(settlement); method=cost["allocation_method"]; provenance=[]
        if method=="direct": return ({unit["object_id"]:Decimal("1") if unit["object_id"]==cost["direct_object_id"] else Decimal("0") for unit in units},provenance)
        if method=="equal": return ({unit["object_id"]:Decimal("1") for unit in units},provenance)
        if method=="manual":
            rows={item["object_id"]:item for item in self.manual_weights(cost["cost_id"])}; weights={}
            for unit in units:
                row=rows.get(unit["object_id"]); weights[unit["object_id"]]=number(row["weight"]) if row else Decimal("0")
                if row: provenance.append({"kind":"manual_weight",**row})
            return weights,provenance
        metric_type="persons" if method=="person_days" else method; weights={}
        for unit in units:
            object_id=unit["object_id"]
            weights[object_id]=self._metric_weight(object_id,metric_type,start,end,person_days=method=="person_days")
            provenance.extend({"kind":"metric",**item} for item in self.metrics(object_id) if item["metric_type"]==metric_type and intersection(start,end,parse_date(item["valid_from"]),parse_date(item["valid_to"],optional=True) or date.max))
        return weights,provenance

    def calculate(self, settlement_id: str) -> dict[str, Any]:
        settlement=self.settlement(settlement_id); settlement_start=parse_date(settlement["starts_on"]); settlement_end=parse_date(settlement["ends_on"])
        units=self._settlement_units(settlement); unit_ids={unit["object_id"] for unit in units}
        tenant_totals={}; tenant_lines={}; vacancy_totals={object_id:Decimal("0.00") for object_id in unit_ids}; cost_results=[]
        for cost in self.costs(settlement_id):
            cost_start,cost_end=parse_date(cost["starts_on"]),parse_date(cost["ends_on"]); overlap=intersection(settlement_start,settlement_end,cost_start,cost_end)
            if not overlap:
                cost_results.append({"cost":cost,"effective_amount":"0.00","object_allocations":{},"tenant_allocations":[],"weight_provenance":[],"ignored":True}); continue
            overlap_days,full_cost_days=days(*overlap),days(cost_start,cost_end)
            effective_amount=(money(cost["amount"])*Decimal(overlap_days)/Decimal(full_cost_days)).quantize(MONEY,ROUND_HALF_UP)
            weights,provenance=self._weights_for_cost(settlement,cost,*overlap); object_allocations=allocate_money(effective_amount,weights); tenant_allocations=[]
            for object_id,object_amount in object_allocations.items():
                if object_amount==0: continue
                object_days=days(*overlap); relevant=[]; occupied_total_days=0
                for tenancy in self.tenancies(object_id):
                    tenancy_overlap=intersection(overlap[0],overlap[1],parse_date(tenancy["starts_on"]),parse_date(tenancy["ends_on"],optional=True) or date.max)
                    if tenancy_overlap:
                        occupied_days=days(*tenancy_overlap); occupied_total_days+=occupied_days; relevant.append((tenancy,tenancy_overlap,occupied_days))
                vacancy_days=max(0,object_days-occupied_total_days)
                if cost["allocation_method"]=="person_days":
                    time_weights={}; tenant_person_days=Decimal("0")
                    for tenancy,span,_ in relevant:
                        value=self._metric_weight(object_id,"persons",span[0],span[1],person_days=True); time_weights[tenancy["tenancy_id"]]=value; tenant_person_days+=value
                    object_person_days=self._metric_weight(object_id,"persons",overlap[0],overlap[1],person_days=True); vacancy_person_days=max(Decimal("0"),object_person_days-tenant_person_days)
                    if vacancy_person_days: time_weights["__vacancy__"]=vacancy_person_days
                else:
                    time_weights={tenancy["tenancy_id"]:Decimal(occupied_days) for tenancy,_span,occupied_days in relevant}
                    if vacancy_days: time_weights["__vacancy__"]=Decimal(vacancy_days)
                if not any(value>0 for value in time_weights.values()): vacancy_totals[object_id]+=object_amount; continue
                time_allocations=allocate_money(object_amount,time_weights)
                for tenancy,span,occupied_days in relevant:
                    amount=time_allocations.get(tenancy["tenancy_id"],Decimal("0.00")); contact_id=tenancy["contact_id"]; tenant_totals[contact_id]=tenant_totals.get(contact_id,Decimal("0"))+amount
                    line={"cost_id":cost["cost_id"],"cost_group":cost["cost_group"],"description":cost["description"],"object_id":object_id,"amount":str(amount),"occupied_days":occupied_days,"period_days":object_days,"starts_on":iso(span[0]),"ends_on":iso(span[1]),"allocation_method":cost["allocation_method"],"object_weight":str(weights.get(object_id,Decimal("0"))),"all_object_weights":{key:str(value) for key,value in weights.items()},"tenant_time_weight":str(time_weights.get(tenancy["tenancy_id"],Decimal("0"))),"tenant_time_weight_total":str(sum(time_weights.values(),Decimal("0"))),"tenant_time_basis":"person_days" if cost["allocation_method"]=="person_days" else "days","object_allocated":str(object_amount),"source_document_id":cost["source_document_id"],"tenant_visible":bool(cost["tenant_visible"])}
                    tenant_lines.setdefault(contact_id,[]).append(line); tenant_allocations.append({"contact_id":contact_id,**line})
                vacancy_totals[object_id]+=time_allocations.get("__vacancy__",Decimal("0.00"))
            cost_results.append({"cost":cost,"effective_amount":str(effective_amount),"cost_period_days":full_cost_days,"effective_days":overlap_days,"effective_starts_on":iso(overlap[0]),"effective_ends_on":iso(overlap[1]),"weights":{key:str(value) for key,value in weights.items()},"object_allocations":{key:str(value) for key,value in object_allocations.items()},"tenant_allocations":tenant_allocations,"weight_provenance":provenance,"ignored":False})
        contacts=set()
        for object_id in unit_ids:
            for tenancy in self.tenancies(object_id):
                if intersection(settlement_start,settlement_end,parse_date(tenancy["starts_on"]),parse_date(tenancy["ends_on"],optional=True) or date.max): contacts.add(tenancy["contact_id"])
        tenants={}
        for contact_id in sorted(contacts):
            ledger=[row for row in self.ledger(contact_id=contact_id) if row["object_id"] in unit_ids]; opening=advances=adjustments=Decimal("0.00"); included=[]
            for row in ledger:
                booked=parse_date(row["booked_on"]); value=money(row["amount"])
                if row["kind"]=="opening_balance" and booked<=settlement_start: opening+=value; included.append(row)
                elif settlement_start<=booked<=settlement_end:
                    included.append(row)
                    if row["kind"]=="advance": advances+=-value
                    else: adjustments+=value
            costs=tenant_totals.get(contact_id,Decimal("0.00")).quantize(MONEY,ROUND_HALF_UP); balance=(costs+opening+adjustments-advances).quantize(MONEY,ROUND_HALF_UP)
            tenants[contact_id]={"contact_id":contact_id,"costs":str(costs),"advances":str(advances),"opening_balance":str(opening),"other_ledger":str(adjustments),"balance":str(balance),"lines":tenant_lines.get(contact_id,[]),"ledger":included}
        return {"settlement":settlement,"units":units,"costs":cost_results,"tenants":tenants,"vacancy":{key:str(value.quantize(MONEY,ROUND_HALF_UP)) for key,value in vacancy_totals.items()},"total_effective_costs":str(sum((money(row["effective_amount"]) for row in cost_results),Decimal("0")).quantize(MONEY,ROUND_HALF_UP))}
