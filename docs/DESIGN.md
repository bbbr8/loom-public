# Workbench contract

User job: send a bounded source or model packet, inspect the result and optionally retain research in a graph. Primary object: editable request beside source hits, model ranges or bundle rankings. First action: choose a synthetic example, supply the service key and run it. Real data is supplied deliberately through the request editor.

States: loading service → example ready → queued/running → returned/partial/error/interrupted. Graph mutations use explicit named storage. Access keys stay in DOM memory and are excluded from requests/results exported by the UI. Empty, invalid JSON, authentication, busy and timeout states have visible text. Every visible command loads an example, evaluates a request, opens structured detail or exports the last result.

Visual tone: Space Black, restrained mint accent, system typography, no decorative motion. Desktop uses two reading columns; phone stacks input then results. Controls are at least 44 pixels high, labels and focus rings remain visible, results use textContent rather than source-controlled HTML. Color never carries the only state meaning. The workbench contains no external fonts or third-party scripts.

With configured job storage, the browser submits a durable identity before polling. A lost connection retains that identity for explicit recovery; graph mutations are never automatically retried under a new identity. Page navigation preserves total graph counts, revision-bound cursors and a clear Export this page action. Configured queued results are retained, including for source searches.
