"""Zero-ETL Federated Data Connectors package."""

from src.connectors.base import BaseDataConnector, NormalizedTelemetry, SchemaMapping
from src.connectors.sql_connector import DirectSQLConnector
from src.connectors.rest_connector import DirectRESTConnector
from src.connectors.manager import ConnectorRegistry, default_registry

__all__ = [
    "BaseDataConnector",
    "NormalizedTelemetry",
    "SchemaMapping",
    "DirectSQLConnector",
    "DirectRESTConnector",
    "ConnectorRegistry",
    "default_registry",
]
