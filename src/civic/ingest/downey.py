"""Downey adapter.

Downey's council agendas live in AgendaLink (horizon.agendalink.app), a
Next.js app whose listing API requires a bearer token embedded in its client
bundle — out of scope under the crawling policy's no-credential-extraction
rule. So Phase 1 discovery for Downey is the configured adopted-budget PDFs
only (config-driven, like everything else); ``cities/downey.yaml`` records the
situation and the planned follow-up source (the city's public Laserfiche
WebLink at lf.downeyca.org).
"""

from civic.ingest.civicengage import CivicEngageAdapter


class DowneyAdapter(CivicEngageAdapter):
    pass
