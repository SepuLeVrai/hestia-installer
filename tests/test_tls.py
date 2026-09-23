import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from installer.tls import generate_ephemeral_certificate


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL requis pour le test d'intégration TLS")
class TLSTests(unittest.TestCase):
    def test_certificate_contains_ip_san_and_private_key_is_0600(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            material = generate_ephemeral_certificate(root, "127.0.0.1")
            self.assertEqual(os.stat(material.private_key).st_mode & 0o777, 0o600)
            output = subprocess.run(
                ["openssl", "x509", "-in", str(material.certificate), "-noout", "-ext", "subjectAltName"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.replace(" ", "")
            self.assertIn("IPAddress:127.0.0.1", output)


if __name__ == "__main__":
    unittest.main()
