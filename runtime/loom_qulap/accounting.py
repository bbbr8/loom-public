"""Compile joint whole-payment allocation alternatives into QULAP models."""
from .model import ModelError, NAME


def allocation_model(packet):
    if packet.get("schema") != "qulap.allocations.v1":
        raise ModelError("expected qulap.allocations.v1")
    payments, invoices = packet.get("payments", []), packet.get("invoices", [])
    if not 1 <= len(payments) <= 12 or not 1 <= len(invoices) <= 12:
        raise ModelError("allocation compiler supports 1..12 payments and invoices")
    scope, currency, basis = packet.get("scope"), packet.get("currency"), packet.get("basis")
    if not all(isinstance(x, str) and x for x in (scope, currency, basis)):
        raise ModelError("declare common scope, currency and basis")
    invoice_ids = {x.get("id") for x in invoices}
    if len(invoice_ids) != len(invoices) or len({x.get("id") for x in payments}) != len(payments):
        raise ModelError("duplicate payment or invoice ID")
    variables, sources, constraints, metrics = [], [], [], []
    expressions = {x["id"]: [] for x in invoices}
    all_applied = []
    row_by_payment = {}
    source_ids = []
    for kind, items in (("pay", payments), ("inv", invoices)):
        for row in items:
            if not isinstance(row.get("id"), str) or not NAME.fullmatch(row["id"]):
                raise ModelError("payment/invoice IDs must be identifiers")
            if type(row.get("amount_cents")) is not int or row["amount_cents"] <= 0:
                raise ModelError("known positive integer-cent amounts required; unknowns belong in a general QULAP model")
            if any(row.get(k) != v for k, v in (("scope", scope), ("currency", currency), ("basis", basis))):
                raise ModelError("allocation compiler cannot cross scope, currency or amount basis")
            if row.get("stage") != ("paid" if kind == "pay" else "invoiced"):
                raise ModelError("payments must be paid and invoices invoiced")
            if not row.get("locator") or not row.get("lineage_id"):
                raise ModelError("every input needs a native locator and lineage ID")
            sid = kind + "_source_" + row["id"]
            source_ids.append(sid)
            sources.append({"id": sid, "locator": row["locator"], "lineage_id": row["lineage_id"],
                            "event_time": row.get("event_time"), "original": row})
            variables.append({"id": kind + "_amount_" + row["id"], "domain": [row["amount_cents"]],
                              "unit": "cents", "lane": {"scope": scope, "currency": currency, "basis": basis,
                                                         "stage": row["stage"]}, "source_ids": [sid]})
    bridge = {"reason": "Hypothetical whole-instrument application between supplied paid and invoiced records; actual remittance remains to inspect", "source_ids": source_ids}
    for payment in payments:
        options = payment.get("eligible_invoices", [])
        if not isinstance(options, list) or not options or len(set(options)) != len(options) or any(x not in invoice_ids for x in options):
            raise ModelError("each payment needs unique explicit candidate invoice IDs")
        choices = []
        for invoice in options:
            key = "link_" + payment["id"] + "_" + invoice
            choices.append(key)
            variables.append({"id": key, "domain": [0, 1], "unit": "integer", "source_ids": [],
                              "next_record": payment.get("next_record", "Remittance and recipient application ledger for " + payment["id"])})
            term = "pay_amount_" + payment["id"] + " * " + key
            expressions[invoice].append(term)
            all_applied.append(term)
        row_by_payment[payment["id"]] = choices
        constraints.append({"id": "single_use_" + payment["id"], "expr": " + ".join(choices) + " <= 1",
                            "source_ids": [], "assumption": "one whole payment may fund at most one candidate invoice"})
    if len(variables) > 40:
        raise ModelError("allocation graph exceeds the 40-variable model limit; narrow the joint question")
    for invoice in invoices:
        expr = " + ".join(expressions[invoice["id"]]) or "0"
        op = " == " if packet.get("require_exact_invoices", True) else " <= "
        constraints.append({"id": "invoice_capacity_" + invoice["id"],
                            "expr": "(" + expr + ")" + op + "inv_amount_" + invoice["id"],
                            "source_ids": ["inv_source_" + invoice["id"]], "bridge": bridge})
        metrics.append({"id": "applied_" + invoice["id"], "expr": expr, "unit": "cents"})
    paid_expr = " + ".join("pay_amount_" + p["id"] for p in payments)
    metrics.append({"id": "unapplied_paid", "expr": "(" + paid_expr + ") - (" + " + ".join(all_applied) + ")", "unit": "cents"})
    propositions = [{"id": key, "expr": key + " == 1", "category": "accounting_application_hypothesis"}
                    for options in row_by_payment.values() for key in options]
    return {"schema": "qulap.model.v1", "source_tier": packet.get("source_tier", "derived"),
            "question": packet.get("question", "Which joint whole-payment applications are possible?"),
            "variables": variables, "constraints": constraints, "metrics": metrics, "propositions": propositions,
            "sources": sources, "coverage_gaps": ["Whole-instrument model only; split applications are excluded by declared assumption.",
                                                      "Candidate remittances are hypotheses, not observed applications.",
                                                      "No refund/reversal netting is performed; supply reviewed net-capacity payments."]}
