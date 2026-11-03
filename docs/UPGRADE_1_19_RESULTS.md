# Loom 1.19: normalization upgrade

Typed, versioned normalization now covers names, business names, identifiers, email, phone, dates/times, exact numbers and currencies, percentages, financial stages, formulas, blanks and addresses. Each view preserves the original value, parsing rule/profile, assumptions, scope and source dependencies. Existing records and reviewed identities stay separate.

New spreadsheet intake keeps number formats, cell types, formulas, merged ranges, hidden rows and column positions. Duplicate CSV headers can be retained positionally. Header labels remain labels; native percentage formats preserve exact ratios. Numeric identifier zero masks preserve display width in a distinct view.

Derived normalization views live in research space and become stale when their source revisions change. Opt-in normalized search returns original records with matching-view lineage; source-token constraints and scope filters remain in force. Default source ranking retains its existing route. The browser inspector compares raw/normalized values with kind, unit and parsing state and opens the original record.

Validation: 147 portable release groups passed with zero failures/skips; two fuzz groups passed; 76 normalization tests and four normalization API/workbook checks passed. Eleven retained regression cases passed (33 assertions), including the pinned mailbox-case and explicit phone-country repair. Six browser checks and eight installed-language assertions passed. Release/regression/fuzz ran on validation worker; API/job/access portability ran on validation worker. Receipts and hash-verified outputs are retained. Interactive edits, small native reads and the 0.23-second graph-view derivation ran on validation worker; that timing excludes extraction, staging and independent comprehension review. The installed runtime matches all 209 allowlisted source-file hashes.

Case test: 1,292 fields across 134 selected records received source-linked views. All prior source and case-annotation content hashes stayed unchanged. The retained development retrieval questions remain 12/12 top-one and top-five, MRR 1.0. The seven-statement case explanation stays current. Eleven native-data assertions passed. Currency views use an explicit declared USD profile; original roles and values remain available.

Three native balance values retain precision-review states. The normalizer preserves their full precision for review. Sixty-two formulas remain unevaluated. Native percentage formats separate fractional ratio values from monetary values in broadly labeled columns.

Coverage: this is the selected updated-budget cohort and same-agent designated expectations/development labels. Whole-case accuracy, independent comprehension and unsupported regional conventions remain open. The native case files, graph, field examples and audit outputs stay outside the source repository.

The required strict machine-wide integration scan passed with zero high findings and zero warnings.
