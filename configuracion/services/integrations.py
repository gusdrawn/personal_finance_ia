import logging

from configuracion.models import CredencialIntegracion


logger = logging.getLogger(__name__)


def get_integration_api_key(provider: str) -> str:
    """Lee una credencial activa desde Configuración; nunca consulta `.env`."""
    credential = CredencialIntegracion.objects.filter(
        provider=provider,
        active=True,
    ).first()
    if credential:
        try:
            value = credential.get_api_key()
            if value:
                return value
        except RuntimeError:
            logger.exception("No se pudo descifrar la credencial del proveedor %s", provider)
    return ""


def get_coinmarketcap_api_key() -> str:
    return get_integration_api_key(CredencialIntegracion.PROVIDER_COINMARKETCAP)


def get_coingecko_api_key() -> str:
    return get_integration_api_key(CredencialIntegracion.PROVIDER_COINGECKO)


def get_tokenomist_api_key() -> str:
    return get_integration_api_key(CredencialIntegracion.PROVIDER_TOKENOMIST)


def get_brave_search_api_key() -> str:
    return get_integration_api_key(CredencialIntegracion.PROVIDER_BRAVE_SEARCH)
