"""GTPerf web tool: the calculation layer.

`core` runs each study and returns a `Section` (inputs, key results, tables and figures) with no user
interface code. The web tool in `web/` runs it in the browser through Pyodide; build it with `make web`.
"""
