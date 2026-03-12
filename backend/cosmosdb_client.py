"""Azure Cosmos DB client for persisting interview results.

Uses DefaultAzureCredential (managed identity / Azure CLI / env creds)
for authentication — no connection strings or keys needed.
"""

import logging

from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential

from config import settings

logger = logging.getLogger(__name__)

# Singleton client and container reference
_credential: DefaultAzureCredential | None = None
_client: CosmosClient | None = None
_container = None


async def _get_container():
    """Get (or lazily create) the Cosmos DB container client."""
    global _credential, _client, _container

    if _container is not None:
        return _container

    if not settings.AZURE_COSMOS_DB_ENDPOINT:
        logger.warning("AZURE_COSMOS_DB_ENDPOINT not set — Cosmos DB persistence disabled")
        return None

    _credential = DefaultAzureCredential()
    _client = CosmosClient(url=settings.AZURE_COSMOS_DB_ENDPOINT, credential=_credential)
    database = _client.get_database_client(settings.AZURE_COSMOS_DB_DATABASE)
    _container = database.get_container_client(settings.AZURE_COSMOS_DB_CONTAINER)

    logger.info(
        f"Cosmos DB client initialised: {settings.AZURE_COSMOS_DB_DATABASE}/{settings.AZURE_COSMOS_DB_CONTAINER}"
    )
    return _container


async def init():
    """Eagerly initialise the Cosmos DB client and cache credentials."""
    await _get_container()


async def save_result(result: dict) -> None:
    """Upsert an interview result document into Cosmos DB.

    The document must include an 'id' field (we use call_id).
    """
    container = await _get_container()
    if container is None:
        return

    await container.upsert_item(result)
    logger.info(f"Interview result persisted to Cosmos DB: {result['id']}")


async def list_results() -> list[dict]:
    """Query all interview results, newest first."""
    container = await _get_container()
    if container is None:
        return []

    query = "SELECT * FROM c ORDER BY c.timestamp DESC"
    items = []
    async for item in container.query_items(query=query):
        items.append(item)
    return items


async def get_result(call_id: str) -> dict | None:
    """Get a single interview result by call_id."""
    container = await _get_container()
    if container is None:
        return None

    query = "SELECT * FROM c WHERE c.call_id = @call_id"
    parameters = [{"name": "@call_id", "value": call_id}]
    async for item in container.query_items(
        query=query, parameters=parameters
    ):
        return item
    return None


async def close() -> None:
    """Close the Cosmos DB client and credential on shutdown."""
    global _credential, _client, _container
    if _client:
        await _client.close()
        _client = None
        _container = None
    if _credential:
        await _credential.close()
        _credential = None
    logger.info("Cosmos DB client closed")
