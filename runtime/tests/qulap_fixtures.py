"""Create synthetic examples. They contain no native case evidence."""
import contextlib
import json
import sqlite3
from pathlib import Path


def build(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    def save(name, packet):
        (root / name).write_text(json.dumps(packet, indent=2) + "\n")
    def lane(stage):
        return {"currency": "USD", "scope": "synthetic_project_A", "stage": stage, "basis": "face_value"}
    sources = [{"id": "S_credit", "locator": "SYNTHETIC credit memo C1", "lineage_id": "SYN_C1"},
               {"id": "S_assumption", "locator": "SYNTHETIC modeling assumptions", "lineage_id": "SYN_MODEL"}]
    bridge = {"reason": "Alternative dispositions of the same synthetic credit; face value across stages is reconciled rather than added as loss", "source_ids": ["S_assumption"]}
    credit = {"schema": "qulap.model.v1", "source_tier": "synthetic",
              "question": "Which credit dispositions remain possible, and what resolves the residual?",
              "variables": [
                  {"id": "issued", "domain": [60000], "unit": "cents", "lane": lane("credit_issued"), "source_ids": ["S_credit"]},
                  {"id": "applied", "domain": [0, 60000], "unit": "cents", "lane": lane("credit_applied"), "source_ids": [],
                   "next_record": "Vendor job subledger including reversals and the final balance"},
                  {"id": "refunded", "domain": [0, 60000], "unit": "cents", "lane": lane("cash_refunded"), "source_ids": [],
                   "next_record": "Refund instrument and recipient bank clearing"}],
              "constraints": [{"id": "single_disposition", "expr": "applied + refunded <= issued", "source_ids": ["S_assumption"], "bridge": bridge}],
              "metrics": [{"id": "unresolved_credit", "expr": "issued - applied - refunded", "unit": "cents", "bridge": bridge}],
              "propositions": [{"id": "credit_not_delivered", "expr": "issued - applied - refunded > 0", "category": "fraud_mechanism", "bridge": bridge},
                               {"id": "double_satisfaction", "expr": "applied + refunded > issued", "category": "accounting", "bridge": bridge}],
              "sources": sources, "coverage_gaps": ["Synthetic binary whole-credit choices; partial dispositions are outside the declared domain.",
                                                        "A positive residual does not establish intent, retention or net damages."]}
    save("credit.model.json", credit)
    common = {"currency": "USD", "scope": "synthetic_pooled_account", "stage": "hypothetical_related_payment_application", "basis": "net_cash"}
    mechanism = {"schema": "qulap.model.v1", "source_tier": "synthetic", "question": "Is owner funding forced into a related-party payment under the declared capacities?",
                 "variables": [
                     {"id": "owner_part", "domain": [0, 10000, 20000, 30000, 40000], "unit": "cents", "lane": common, "source_ids": [],
                      "next_record": "Full pooled-account statement, opening balance and linked source receipts"},
                     {"id": "lender_part", "domain": [0, 10000, 20000, 30000, 40000], "unit": "cents", "lane": common, "source_ids": []},
                     {"id": "actor_knew_use", "domain": [False, True], "unit": "boolean", "source_ids": [],
                      "next_record": "Actor-specific direction and acknowledgment before the payment"}],
                 "constraints": [{"id": "payment_total", "expr": "owner_part + lender_part == 40000", "source_ids": ["S_cash"]},
                                 {"id": "lender_capacity", "expr": "lender_part <= 10000", "source_ids": ["S_cash"]}],
                 "metrics": [{"id": "owner_related_application", "expr": "owner_part", "unit": "cents"}],
                 "propositions": [{"id": "owner_funding_required", "expr": "owner_part >= 30000", "category": "accounting"},
                                  {"id": "knowing_use_hypothesis", "expr": "owner_part > 0 and actor_knew_use", "category": "fraud_mechanism"}],
                 "sources": [{"id": "S_cash", "locator": "SYNTHETIC account and source-capacity fixture", "lineage_id": "SYN_CASH"}],
                 "coverage_gaps": ["Finite $100-step application domain; unmodeled deposits, opening cash or borrowing can change bounds.",
                                   "Related-party payment purpose and actor knowledge require separate native support."]}
    save("mechanism.model.json", mechanism)
    contradiction = {"schema": "qulap.model.v1", "source_tier": "synthetic",
                     "variables": [{"id": "version_charge", "domain": [0, 1, 2], "unit": "integer"}],
                     "constraints": [{"id": "earliest_copy", "expr": "version_charge == 0"},
                                     {"id": "later_copy", "expr": "version_charge == 2"},
                                     {"id": "nonnegative", "expr": "version_charge >= 0"}],
                     "metrics": [{"id": "charge", "expr": "version_charge", "unit": "integer"}],
                     "propositions": [{"id": "disputed", "expr": "version_charge > 0"}], "sources": []}
    save("contradiction.model.json", contradiction)
    rows = []
    for pid, amount in (("p1", 10000), ("p2", 10000)):
        rows.append({"id": pid, "amount_cents": amount, "scope": "A", "currency": "USD", "basis": "net_cash",
                     "stage": "paid", "locator": "SYNTHETIC bank " + pid, "lineage_id": "SYN_" + pid,
                     "event_time": "2020-01-02", "eligible_invoices": ["i1", "i2"]})
    invoices = [{"id": iid, "amount_cents": 10000, "scope": "A", "currency": "USD", "basis": "net_cash",
                 "stage": "invoiced", "locator": "SYNTHETIC invoice " + iid, "lineage_id": "SYN_" + iid,
                 "event_time": "2020-01-01"} for iid in ("i1", "i2")]
    save("allocations.json", {"schema": "qulap.allocations.v1", "source_tier": "synthetic", "scope": "A",
                              "currency": "USD", "basis": "net_cash", "payments": rows, "invoices": invoices,
                              "require_exact_invoices": True})
    annotations = [{"id": "E1", "actor": "Synthetic Actor A", "slot": "conduct_participation", "stance": "support",
                    "proposition": "Fixture: actor issued a payment direction", "locator": "SYNTHETIC message M1",
                    "lineage_id": "SYN_M1", "event_time": "2020-01-01", "knowledge_time": None},
                   {"id": "E2", "actor": "Synthetic Actor A", "slot": "conduct_participation", "stance": "contrary",
                    "proposition": "Fixture: later approval allocated authority elsewhere", "locator": "SYNTHETIC amendment A1",
                    "lineage_id": "SYN_A1", "event_time": "2020-01-03", "knowledge_time": "2020-01-03"}]
    save("rico-bridges.json", {"schema": "qulap.rico-bridges.v1", "subsection": "1962c", "source_tier": "synthetic",
                               "actors": ["Synthetic Actor A", "Synthetic Actor B"], "annotations": annotations,
                               "legal_coverage": "Evidence organization only. 2024 statutory text checked; current/event-date case-law application is UNREVIEWED."})
    db = root / "synthetic.sqlite"
    if db.exists():
        raise FileExistsError(db)
    with contextlib.closing(sqlite3.connect(db)) as con:
        con.execute("create table facts(event_id text, amount_cents integer, project text, stage text, version text, locator text, lineage_id text)")
        con.executemany("insert into facts values(?,?,?,?,?,?,?)", [
            ("a", 10001, "A", "paid", "v1", "SYNTHETIC page 1 row 1", "SYN_BANK"),
            ("b", -2002, "A", "paid", "v1", "SYNTHETIC page 1 row 2", "SYN_BANK"),
            ("c", None, "A", "paid", "v1", "SYNTHETIC page 1 row 3", "SYN_BANK"),
            ("d", 3333, "A", "requested", "v1", "SYNTHETIC draw row 1", "SYN_DRAW")])
        con.commit()
    contract = {"schema": "qulap.cube-contract.v1", "columns": {k: k for k in ("event_id", "amount_cents", "locator", "lineage_id")},
                "axes": {"scope": {"column": "project"}, "stage": {"column": "stage"}, "version": {"column": "version"},
                         "currency": {"fixed": "USD"}, "basis": {"fixed": "net_cash"}}}
    save("cube-contract.json", contract)
    (root / "cube.sql").write_text("select * from facts order by event_id\n")


if __name__ == "__main__":
    import sys
    build(sys.argv[1])
