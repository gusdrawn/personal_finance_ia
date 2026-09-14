"""Excepciones del paquete bank_sync."""


class BankSyncError(Exception):
    """Base de todos los errores de extracción."""


class LoginError(BankSyncError):
    """Falló la autenticación contra el banco."""


class DeviceVerificationRequired(LoginError):
    """El banco pide validar un dispositivo nuevo (intervención manual)."""


class ExtractionError(BankSyncError):
    """Falló la obtención de datos ya autenticado (navegación/interceptación)."""


class ParseError(BankSyncError):
    """La respuesta del banco no tuvo la forma esperada."""


class ConnectorNotFound(BankSyncError):
    """No hay un connector registrado para el banco solicitado."""


class AuthError(BankSyncError):
    """Falló la autenticación de una fuente de activos (API key/secret inválidos)."""


class InteractiveAuthRequired(AuthError):
    """La fuente exige CAPTCHA, MFA o reautenticación manual."""
