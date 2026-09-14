from unittest.mock import Mock

from django.test import SimpleTestCase

from altcoins.services.evidence import UnsafeSourceError, fetch_evidence


class EvidenceSecurityTest(SimpleTestCase):
    def test_rechaza_host_local(self):
        with self.assertRaises(UnsafeSourceError):
            fetch_evidence("https://127.0.0.1/private")

    def test_rechaza_tipo_de_contenido_no_permitido(self):
        session = Mock()
        response = Mock(url="https://example.com/file.exe")
        response.headers = {"Content-Type": "application/octet-stream"}
        response.iter_content.return_value = [b"data"]
        session.get.return_value = response
        with self.assertRaises(UnsafeSourceError):
            fetch_evidence("https://example.com/file.exe", session=session)
