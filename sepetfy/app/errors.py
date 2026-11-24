"""Merkezî hata sınıfları."""


class SepetfyError(Exception):
    """Tüm Sepetfy hatalarının tabanı."""


class ConfigError(SepetfyError):
    """Eksik veya hatalı ayar."""


class ExternalServiceError(SepetfyError):
    """Harici servis hatası. ``retryable`` geçici hataları belirtir."""

    def __init__(self, service: str, message: str, *, retryable: bool = True):
        super().__init__(f"[{service}] {message}")
        self.service = service
        self.retryable = retryable


class ServiceUnavailable(ExternalServiceError):
    """Devre kesici açık: servis geçici olarak devre dışı."""

    def __init__(self, service: str, message: str = "devre kesici açık"):
        super().__init__(service, message, retryable=False)


class BudgetExceeded(SepetfyError):
    """Aylık bütçe limiti aşılacak; ücretli çağrı engellendi."""


class PublishNotAllowed(SepetfyError):
    """Onay kontrolü başarısız: içerik yayınlanamaz."""


class DuplicatePublication(PublishNotAllowed):
    """Bu içerik bu platformda zaten yayınlandı / yayınlanıyor."""
