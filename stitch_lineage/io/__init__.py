"""I/O boundary: dbt artifacts, the Metabase API, and graph.json persistence.

Seam rule (SPEC.md section 4): only io/metabase_client.py performs HTTP; resolve/
never imports this package.
"""
