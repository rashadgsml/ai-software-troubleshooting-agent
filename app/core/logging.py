"""Logging with an investigation id on every record, so one run's logs can be grepped together."""

import logging
from contextvars import ContextVar

investigation_id_var: ContextVar[str] = ContextVar("investigation_id", default="-")

_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    previous_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous_factory(*args, **kwargs)
        record.investigation_id = investigation_id_var.get()  # type: ignore[attr-defined]
        return record

    logging.setLogRecordFactory(factory)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [inv=%(investigation_id)s] %(name)s: %(message)s")
