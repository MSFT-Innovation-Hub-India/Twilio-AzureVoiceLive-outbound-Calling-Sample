"""Check and toggle Cosmos DB public network access via the Azure Management SDK."""

import logging
from azure.identity import DefaultAzureCredential
from azure.mgmt.cosmosdb import CosmosDBManagementClient
from azure.mgmt.cosmosdb.models import DatabaseAccountUpdateParameters

from config import settings

logger = logging.getLogger(__name__)

_client: CosmosDBManagementClient | None = None


def _get_client() -> CosmosDBManagementClient:
    global _client
    if _client is None:
        _client = CosmosDBManagementClient(
            credential=DefaultAzureCredential(),
            subscription_id=settings.AZURE_SUBSCRIPTION_ID,
        )
    return _client


def check_public_network_access() -> dict:
    """Return the current public network access status of the Cosmos DB account.

    Returns dict with keys: enabled (bool), raw_value (str), account_name (str).
    """
    client = _get_client()
    account = client.database_accounts.get(
        settings.AZURE_RESOURCE_GROUP,
        settings.AZURE_COSMOS_DB_ACCOUNT_NAME,
    )
    raw = account.public_network_access or "Disabled"
    return {
        "enabled": raw.lower() in ("enabled", "securedbyperimeter"),
        "raw_value": raw,
        "account_name": settings.AZURE_COSMOS_DB_ACCOUNT_NAME,
    }


def enable_public_network_access() -> dict:
    """Enable public network access on the Cosmos DB account (blocking LRO)."""
    client = _get_client()
    poller = client.database_accounts.begin_update(
        settings.AZURE_RESOURCE_GROUP,
        settings.AZURE_COSMOS_DB_ACCOUNT_NAME,
        DatabaseAccountUpdateParameters(public_network_access="Enabled"),
    )
    result = poller.result()          # blocks until ARM finishes
    raw = result.public_network_access or "Enabled"
    logger.info("Cosmos DB public network access set to %s", raw)
    return {
        "enabled": raw.lower() in ("enabled", "securedbyperimeter"),
        "raw_value": raw,
        "account_name": settings.AZURE_COSMOS_DB_ACCOUNT_NAME,
    }
