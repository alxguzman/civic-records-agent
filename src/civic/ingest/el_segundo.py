"""El Segundo adapter.

El Segundo publishes City Council agendas and minutes through CivicEngage's
calendar, and its documents are served as direct ``/home/showpublisheddocument``
PDFs. Discovery is therefore entirely the shared calendar walk plus the
configured adopted-budget PDFs — no city-specific parsing is needed yet.
"""

from civic.ingest.civicengage import CivicEngageAdapter


class ElSegundoAdapter(CivicEngageAdapter):
    pass
