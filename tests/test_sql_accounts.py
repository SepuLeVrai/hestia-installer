"""Closed account-policy tests. Any PHP credentials below are synthetic fixtures."""
import copy
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch

from installer import php_transport as p
from installer import sql_accounts as accounts
from web_configuration_fixture import local_request

POLICY = Path(__file__).resolve().parents[1] / 'installer/private/sql_accounts_policy.php'
BRIDGE = POLICY.with_name('sql_accounts_bridge.php')


def response(identifier, **changes):
    value = {'version': 1, 'operation': 'audit_local_accounts', 'request_id': identifier,
             'ok': True, 'error': None, 'policy': 'local-dml-v1', 'application_installed': False}
    value.update(changes)
    return value


class SqlAccountContractTests(unittest.TestCase):
    def setUp(self):
        self.payload = local_request()
        self.credential = p.ProvisioningCredentials('setup_fixture', 'private-setup-fixture')
        self.wire = accounts._request(self.payload, self.credential)

    def test_mapping_omits_admin_assistant_and_paths(self):
        self.payload['assistant']['action'] = 'configure'
        self.payload['secrets']['openai_api_key'] = 'fixture-openai-' + 'x' * 30
        raw = p._json(accounts._request(self.payload, self.credential))
        for text in [self.payload['secrets']['admin_password'], self.payload['secrets']['openai_api_key'], '/var/www/hestia']:
            self.assertNotIn(text.encode(), raw)
        self.assertEqual(set(self.wire), {'version', 'operation', 'request_id', 'database', 'application', 'provisioning'})

    def test_explicit_separate_accounts_and_passwords(self):
        for name, password in [('hestia', 'separate-test-password'), ('setup', self.payload['secrets']['database_password'])]:
            with self.subTest(name=name), self.assertRaisesRegex(accounts.AccountConfigurationError, 'ACCOUNT_SEPARATION_REQUIRED'):
                accounts._request(self.payload, p.ProvisioningCredentials(name, password))
        with self.assertRaises(accounts.AccountConfigurationError): accounts._request(self.payload, object())

    def test_local_transport_is_narrow_and_default_port_only(self):
        for fields in [{'mode': 'managed'}, {'host': '127.0.0.2'}, {'host': '::1'}, {'port': 3307},
                       {'mode': 'remote', 'host': 'db.example.test', 'tls_ca_file': '/etc/hestia/ca.pem'}]:
            candidate = copy.deepcopy(self.payload); candidate['database'].update(fields)
            with self.subTest(fields=fields), self.assertRaises(accounts.AccountConfigurationError): accounts._request(candidate, self.credential)
        self.assertEqual(self.wire['database']['host'], '127.0.0.1')

    def test_unicode_long_password_exact_bytes_and_boundaries(self):
        password = ' é' + 'x' * 1016 + "'\\ "
        self.payload['secrets']['database_password'] = password
        self.assertEqual(accounts._request(self.payload, self.credential)['application']['password'], password)
        self.payload['secrets']['database_password'] = 'é' * 513
        with self.assertRaises(accounts.AccountConfigurationError): accounts._request(self.payload, self.credential)

    def test_bad_input_types_extra_fields_and_controls(self):
        for name, value in [('port', True), ('port', 0), ('port', float('nan')), ('user', 'root'), ('name', 'x;host=evil')]:
            candidate = copy.deepcopy(self.payload); candidate['database'][name] = value
            with self.subTest(name=name), self.assertRaises(accounts.AccountConfigurationError): accounts._request(candidate, self.credential)
        for value in ('', 'secret\nvalue', 'x\0y', '\ud800'):
            candidate = copy.deepcopy(self.payload); candidate['secrets']['database_password'] = value
            with self.assertRaises(accounts.AccountConfigurationError): accounts._request(candidate, self.credential)
        self.payload['credentials'] = 'not allowed'
        with self.assertRaises(accounts.AccountConfigurationError): accounts._request(self.payload, self.credential)

    def test_upgrade_observation_is_read_only_mapping(self):
        result = accounts._request(local_request('upgrade'), self.credential)
        self.assertEqual(result['operation'], 'audit_local_accounts')
        self.assertNotIn('administrator', result)
        with self.assertRaisesRegex(accounts.AccountConfigurationError, 'UPGRADE_CONFIGURATION_REFUSED'):
            accounts.local_configuration(local_request('upgrade'), fresh_only=True)

    def test_response_closed_and_not_installation_proof(self):
        identifier = self.wire['request_id']
        self.assertEqual(accounts._response(0, p._json(response(identifier)), identifier), accounts.VERIFIED)
        for changes in [{'version': True}, {'request_id': 'bad'}, {'ok': 1}, {'policy': 'anything'},
                        {'error': 'secret'}, {'application_installed': True}, {'extra': 'secret'}, {'operation': 'grant'}]:
            with self.subTest(changes=changes), self.assertRaises(accounts.AccountConfigurationError):
                accounts._response(0, p._json(response(identifier, **changes)), identifier)
        for code in (True, 20, 1, -9):
            with self.subTest(code=code), self.assertRaises(accounts.AccountConfigurationError):
                accounts._response(code, p._json(response(identifier)), identifier)

    def test_malformed_duplicate_oversize_and_unknown_errors_fail_closed(self):
        identifier = self.wire['request_id']
        for data in (b'', b'not-json', b'{"ok":true,"ok":false}', b'x' * 4097, b'[]', b'\xff'):
            with self.assertRaisesRegex(accounts.AccountConfigurationError, 'AUDIT_PROTOCOL_REJECTED'):
                accounts._response(0, data, identifier)
        for error in accounts.ERRORS:
            value = response(identifier, ok=False, error=error, policy=None)
            self.assertEqual(accounts._response(20, p._json(value), identifier)['state'], 'REFUSED')
        with self.assertRaises(accounts.AccountConfigurationError):
            accounts._response(20, p._json(response(identifier, ok=False, error='RAW SQL SECRET', policy=None)), identifier)

    def test_protocol_php_rejects_unsafe_inputs_before_any_connection(self):
        variants = [b'{}', b'{"version":1,"version":1}', b'x' * 16385, b'[]']
        for op in ('shell', 'fresh_database', 'upgrade', 'create_user'):
            item = copy.deepcopy(self.wire); item['operation'] = op; variants.append(p._json(item))
        for fields in ({'host': 'example.test'}, {'port': True}, {'port': 3307}, {'name': 'mysql'}):
            item = copy.deepcopy(self.wire); item['database'].update(fields); variants.append(p._json(item))
        for data in variants:
            run = subprocess.run(['php', '-n', '-d', 'display_errors=0', '-d', 'log_errors=0', str(BRIDGE)],
                                 input=data, capture_output=True, timeout=3)
            self.assertEqual(run.returncode, 20)
            self.assertFalse(run.stderr)
            self.assertEqual(json.loads(run.stdout)['error'], 'REQUEST_INVALID')


class SqlGrantPolicyTests(unittest.TestCase):
    def policy(self, grants=None, public=None, *, role=None, account='app@127.0.0.1', profile='application', database='hestia_prod'):
        raw = {'grants': self.grants() if grants is None else grants, 'public': [] if public is None else public,
               'account': account, 'role': role, 'user': 'app', 'db': database, 'profile': profile}
        script = ('require ' + json.dumps(str(POLICY)) + '; $v=json_decode(stream_get_contents(STDIN),true);'
                  'echo hestia_account_policy($v["grants"],$v["public"],$v["account"],$v["role"],$v["user"],$v["db"],$v["profile"]) ? "PASS" : "REFUSED";')
        run = subprocess.run(['php', '-n', '-r', script], input=json.dumps(raw).encode(), capture_output=True, timeout=3)
        self.assertEqual(run.returncode, 0); self.assertFalse(run.stderr)
        return run.stdout == b'PASS'

    def grants(self):
        return ["GRANT USAGE ON *.* TO `app`@`127.0.0.1` IDENTIFIED BY PASSWORD '*" + 'A' * 40 + "'",
                'GRANT SELECT, INSERT, UPDATE, DELETE ON `hestia\\_prod`.* TO `app`@`127.0.0.1`']

    def test_exact_dml_profile_and_schema_all_provisioning(self):
        self.assertTrue(self.policy())
        grants = self.grants(); grants[1] = grants[1].replace('SELECT, INSERT, UPDATE, DELETE', 'ALL PRIVILEGES')
        self.assertTrue(self.policy(grants, profile='provisioning'))
        self.assertFalse(self.policy(grants))
        self.assertFalse(self.policy(profile='provisioning'))

    def test_missing_excess_duplicate_and_global_grants(self):
        for replacement in ('SELECT', 'SELECT, INSERT, UPDATE, DELETE, ALTER', 'SELECT, INSERT, UPDATE, DELETE, SELECT'):
            grants = self.grants(); grants[1] = grants[1].replace('SELECT, INSERT, UPDATE, DELETE', replacement)
            self.assertFalse(self.policy(grants))
        grants = self.grants(); grants[1] = grants[1].replace('`hestia\\_prod`.*', '*.*')
        self.assertFalse(self.policy(grants))
        self.assertFalse(self.policy([]))
        self.assertFalse(self.policy(self.grants() + [self.grants()[1]]))

    def test_schema_wildcards_other_database_and_table_grants_refused(self):
        for scope in ('`hestia_prod`.*', '`hestia%`.*', '`other`.*', '`hestia\\_prod`.`UserInfo`'):
            grants = self.grants(); grants[1] = grants[1].replace('`hestia\\_prod`.*', scope)
            self.assertFalse(self.policy(grants))

    def test_roles_proxy_grant_option_and_unknown_authentication_refused(self):
        self.assertFalse(self.policy(role='role_x'))
        for extra in ('GRANT `role_x` TO `app`@`127.0.0.1`', 'SET DEFAULT ROLE NONE FOR `app`@`127.0.0.1`',
                      'GRANT PROXY ON `root`@`localhost` TO `app`@`127.0.0.1`'):
            self.assertFalse(self.policy(self.grants() + [extra]))
        grants = self.grants(); grants[1] += ' WITH GRANT OPTION'; self.assertFalse(self.policy(grants))
        grants = self.grants(); grants[0] = 'GRANT USAGE ON *.* TO `app`@`127.0.0.1` IDENTIFIED VIA unix_socket'
        self.assertFalse(self.policy(grants))

    def test_public_privileges_identity_alias_and_wildcard_host_refused(self):
        self.assertTrue(self.policy(public=['GRANT USAGE ON *.* TO PUBLIC']))
        self.assertFalse(self.policy(public=['GRANT SELECT ON `other`.* TO PUBLIC']))
        for account in ('app@%', 'root@localhost', 'app@::1', 'other@127.0.0.1'):
            self.assertFalse(self.policy(account=account))
        self.assertFalse(self.policy(self.grants(), public=['x'] * 2))

    def test_native_password_quote_formats_and_bounds(self):
        grants = [item.replace('`app`@`127.0.0.1`', "'app'@'127.0.0.1'") for item in self.grants()]
        self.assertTrue(self.policy(grants))
        grants[0] = grants[0].replace('IDENTIFIED BY PASSWORD', 'IDENTIFIED VIA mysql_native_password USING')
        self.assertTrue(self.policy(grants))
        self.assertFalse(self.policy([None, self.grants()[1]]))
        self.assertFalse(self.policy(['x' * 4097, self.grants()[1]]))
        self.assertFalse(self.policy(profile='unknown'))
