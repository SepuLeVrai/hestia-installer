import unittest

from installer.validation import normalize_network, normalize_networks, validate_fqdn


class ValidationTests(unittest.TestCase):
    def test_fqdn_is_normalized(self) -> None:
        self.assertEqual(validate_fqdn("HESTIA.Example.TLD."), "hestia.example.tld")

    def test_url_is_not_accepted_as_fqdn(self) -> None:
        with self.assertRaises(ValueError):
            validate_fqdn("https://hestia.example.tld")

    def test_ipv4_network_is_normalized(self) -> None:
        self.assertEqual(normalize_network("10.40.4.12/16"), "10.40.0.0/16")

    def test_ipv6_network_is_supported(self) -> None:
        self.assertEqual(normalize_network("2001:db8:1::1/48"), "2001:db8:1::/48")

    def test_duplicate_networks_are_removed(self) -> None:
        self.assertEqual(
            normalize_networks(["10.0.0.1/8", "10.0.0.0/8"]),
            ["10.0.0.0/8"],
        )


if __name__ == "__main__":
    unittest.main()
