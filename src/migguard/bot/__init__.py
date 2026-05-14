"""PR webhook receiver and local web playground (FastAPI app).

Accepts pull-request events from GitHub and Azure DevOps on a single
``/webhook`` endpoint, and serves a paste-and-review UI at ``/playground``.
"""
