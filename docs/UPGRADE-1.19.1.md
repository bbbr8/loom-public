# Loom 1.19.1: larger native-data runs

This patch fixes three failures exposed while expanding a retained document cohort.

- Source search now excludes research annotations and structural nodes before applying the FTS candidate limit. A crowded analytical graph retains its source candidate slots. Genuine source fanout still reports its candidate limit; dependency and hard-clause checks remain active.
- Workbook version comparisons accept standalone native workbook hashes as well as embedded attachment hashes. An embedded workbook's own hash takes precedence over its enclosing email hash. Cell differences retain their source nodes and declared comparison scope.
- Normalizer 1.0.1 distinguishes calendar-date labels from accumulated financial headers such as `Cost to date` and `Updated budget`. Raw values, native formatting, source provenance and open financial states remain preserved.

Three pinned-baseline original/transfer/control cycles passed. The retained curriculum contains 14 cases. Validation passed 147 release groups with zero failures or skips, both fuzz groups, retained checks, authenticated API, access and job tests. Targeted suites contain 80 normalization tests, 31 case-layer tests and 22 studio tests.

The larger task-local run processed 369 distinct native files into 1,538 passages/rows, evolved a graph of 4,091 nodes and 10,701 structural/candidate edges, and verified journal replay and SQLite integrity. Source review and meaningful case interpretation were evaluated separately. These measurements describe that selected run; they are not a whole-corpus accuracy claim or a general row-capacity certification.

Native files, extracted case text, OCR, graphs, financial models and case findings stay outside this repository. The runtime's bounded graph, source-intake, candidate and execution limits remain explicit.
