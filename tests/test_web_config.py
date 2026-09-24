"""Input-only Web contract: no simulated claim of an installed application."""
import contextlib
from copy import deepcopy
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from installer.model import ErrorCode, InstallerError
from installer.web_config import validate_web_configuration as validate
from github_fixture import DUMMY, confirm, make_service, plan_sources
import test_github_http


from web_configuration_fixture import request


def set_value(value, path, replacement):
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = replacement


class WebConfigurationTests(unittest.TestCase):
    def invalid(self, path, values, mode="fresh"):
        for replacement in values:
            with self.subTest(path=path, value=repr(replacement)[:60]):
                payload = request(mode); set_value(payload, path, replacement)
                with self.assertRaises(InstallerError): validate(payload)

    def test_fresh_input_never_means_installed(self):
        result = validate(request())
        self.assertEqual(result["scope"], "INPUT_ONLY")
        self.assertFalse(result["deployment_ready"])
        self.assertFalse(result["configuration"]["assistant"]["desired_enabled"])
        self.assertIn("WEB_EXECUTION_CONTRACT", result["pending_checks"])

    def test_upgrade_preserves_admin_and_assistant_without_guessing_runtime(self):
        result = validate(request("upgrade"))["configuration"]
        self.assertIsNone(result["administrator"])
        self.assertEqual(result["assistant"], {"action": "preserve", "desired_enabled": None})

    def test_no_key_forces_disabled_even_if_configuration_requested(self):
        payload = request(); payload["assistant"]["action"] = "configure"
        result = validate(payload)
        self.assertEqual(result["configuration"]["assistant"], {"action": "disabled", "desired_enabled": False})
        self.assertEqual(result["notices"], ["ASSISTANT_DISABLED_NO_KEY"])

    def test_configured_key_is_never_echoed_and_not_marked_runtime_valid(self):
        payload = request(); payload["assistant"]["action"] = "configure"
        payload["secrets"]["openai_api_key"] = "fixture-openai-" + "z" * 30
        result = validate(payload)
        self.assertTrue(result["configuration"]["assistant"]["desired_enabled"])
        self.assertFalse(result["deployment_ready"])
        for secret in payload["secrets"].values():
            self.assertNotIn(secret, json.dumps(result))

    def test_disabled_or_preserved_assistant_refuses_contradictory_key(self):
        for mode in ("fresh", "upgrade"):
            self.invalid(("secrets", "openai_api_key"), ["fixture-openai-" + "z" * 30], mode)
        self.invalid(("assistant", "action"), ["preserve"])

    def test_upgrade_can_explicitly_disable_or_replace_assistant(self):
        payload=request("upgrade"); payload["assistant"]["action"]="disabled"
        self.assertFalse(validate(payload)["configuration"]["assistant"]["desired_enabled"])
        payload["assistant"]["action"]="configure";payload["secrets"]["openai_api_key"]="fixture-key-"+"a"*25
        self.assertTrue(validate(payload)["configuration"]["assistant"]["desired_enabled"])

    def test_key_format_placeholders_and_long_values_are_rejected(self):
        for key in ("x", "x"*501, "COLLER_LA_CLE_OPENAI_ICI", "YOUR_OPENAI_API_KEY_HERE", "x"*20+"\n", " abcdefghijklmnopqrstuvwxyz", "x"*20+"'"):
            payload=request();payload["assistant"]["action"]="configure";payload["secrets"]["openai_api_key"]=key
            with self.subTest(key=key[:25]),self.assertRaises(InstallerError):validate(payload)

    def test_unknown_and_missing_fields_are_rejected_everywhere(self):
        for section in (None,"web","database","administrator","assistant","secrets"):
            payload=request();obj=payload if section is None else payload[section]
            obj["unexpected"]=False
            with self.subTest(section=section),self.assertRaises(InstallerError):validate(payload)
            payload=request();obj=payload if section is None else payload[section]
            obj.pop(next(iter(obj)))
            with self.subTest(missing=section),self.assertRaises(InstallerError):validate(payload)

    def test_empty_inputs_wrong_types_and_versions(self):
        for value in ({},None,[],"",True):
            with self.subTest(value=value),self.assertRaises(InstallerError):validate(value)
        self.invalid(("version",), [True,0,2,1.0,"1",None])
        self.invalid(("mode",), [[],True,"delete","",None])
        for section in ("web","database","administrator","assistant","secrets"):
            self.invalid((section,), [None,[],True,""])

    def test_hostname_dsn_and_shell_injections_are_rejected(self):
        self.invalid(("web","hostname"), ["", "localhost", "https://host.test", "host.test/", "host.test;id", "host.test\n", "-host.test", "host.test.", "host.test "])
        self.invalid(("database","host"), ["host;dbname=other", "host$(id)", "127.0.0.999", "a..b", "[::1]", "fe80::1%eth0", "0.0.0.0", "224.0.0.1", "::"])

    def test_path_traversal_root_control_characters_and_long_components_rejected(self):
        self.invalid(("web","webroot"), ["/", "/var/www", "/etc/hestia", "relative", "/var/www/../etc", "/var/www//hestia", "/var/www/hestia/", "/var/www/a\\b", "/var/www/"+"x"*256, "/var/www/x\x00y", "/var/www/x\u202ey"])

    def test_safe_unicode_paths_and_names_are_not_corrupted(self):
        payload=request();payload["web"]["webroot"]="/srv/Hestia équipe & été";payload["administrator"]["first_name"]="Élise"
        result=validate(payload)["configuration"]
        self.assertEqual(result["web"]["webroot"],payload["web"]["webroot"])
        self.assertEqual(result["administrator"]["first_name"],"Élise")
        self.assertIn("&",result["administrator"]["last_name"])

    def test_numeric_bounds_and_booleans(self):
        self.invalid(("database","port"), [True,False,-1,0,65536,3306.0,"3306",None,10**100])
        payload=request();payload["database"]["mode"]="existing_local"
        for port in (1,65535):
            payload["database"]["port"]=port
            self.assertEqual(validate(payload)["configuration"]["database"]["port"],port)

    def test_managed_database_cannot_be_adopted_in_upgrade(self):
        self.invalid(("database","mode"), ["managed"], "upgrade")
        self.invalid(("database","port"), [3307])

    def test_remote_database_requires_ca_path_and_rejects_webroot_ca(self):
        payload=request();payload["database"].update(mode="remote",host="db.example.test",tls_ca_file="/etc/hestia/ca.pem")
        self.assertEqual(validate(payload)["configuration"]["database"]["mode"],"remote")
        for ca in (None,"", "relative.pem", "/var/www/hestia/ca.pem", "/var/www/hestia", "/etc/../tmp/ca"):
            payload["database"]["tls_ca_file"]=ca
            with self.subTest(ca=ca),self.assertRaises(InstallerError):validate(payload)

    def test_local_and_remote_host_modes_cannot_be_confused(self):
        self.invalid(("database","host"), ["db.example.test","192.168.1.10"])
        payload=request();payload["database"].update(mode="remote",tls_ca_file="/etc/hestia/ca.pem")
        for host in ("localhost","127.0.0.1","::1"):
            payload["database"]["host"]=host
            with self.assertRaises(InstallerError):validate(payload)
        payload["database"]["host"]="2001:db8::1234"
        self.assertEqual(validate(payload)["configuration"]["database"]["host"],"2001:db8::1234")

    def test_database_names_and_unprivileged_users(self):
        self.invalid(("database","name"), ["mysql","SYS","information_schema","performance_schema","x"*65,"foo-bar","a;DROP",""])
        self.invalid(("database","user"), ["root","ROOT","x"*33,"a@b",""])
        self.invalid(("web","service_user"), ["root","nobody","a;id","-a","x"*33,""])

    def test_upgrade_never_recreates_or_resets_admin(self):
        self.invalid(("administrator",), [request()["administrator"]], "upgrade")
        self.invalid(("secrets","admin_password"), ["Reset-fixture-2026!"], "upgrade")

    def test_administrator_lengths_and_email(self):
        self.invalid(("administrator","first_name"), ["", "x"*101," name","name\n", "\ud800"])
        self.invalid(("administrator","email"), ["bad","x@localhost","x@bad..test","x@example.test\n","x@-host.test", ".x@example.test", "x..y@example.test", "x.@example.test", "x"*65+"@example.test"])
        self.invalid(("secrets","admin_password"), ["", "short", " "*12,"x"*73,"é"*37,"x"*12+"\x00"])
        payload=request();payload["secrets"]["admin_password"]="é"*36
        validate(payload)

    def test_credentials_are_not_normalized_mutated_or_retained(self):
        payload=request();payload["secrets"]["database_password"]="  DB-fixture-2026!  "
        before=deepcopy(payload);result=validate(payload)
        self.assertEqual(payload,before)
        self.assertNotIn("secrets",result["configuration"])
        result["configuration"]["web"]["hostname"]="other.example.test"
        self.assertEqual(payload,before)

    def test_secret_cannot_be_echoed_in_public_text_or_canonical_case(self):
        payload=request();payload["administrator"]["last_name"]=payload["secrets"]["database_password"]
        with self.assertRaises(InstallerError) as error:validate(payload)
        self.assertEqual(error.exception.code,ErrorCode.SECRET_REJECTED)
        payload=request();payload["secrets"]["database_password"]="HESTIA.EXAMPLE.TEST"
        with self.assertRaises(InstallerError):validate(payload)

    def test_validation_does_not_touch_network_processes_or_filesystem(self):
        with patch("socket.socket",side_effect=AssertionError("network")),patch("subprocess.run",side_effect=AssertionError("process")),patch("os.open",side_effect=AssertionError("filesystem")):
            validate(request());validate(request("upgrade"))

    def test_repeat_validation_does_not_create_journal_or_alter_plan(self):
        with tempfile.TemporaryDirectory() as root:
            service,fake=make_service(root)
            try:
                first=service.execute("web.config.validate",request())
                self.assertEqual(first,service.execute("web.config.validate",request()))
                self.assertIsNone(service.engine.report())
                self.assertFalse(service.engine.journal.path.parent.exists())
                plan=plan_sources(service)
                self.assertEqual(service.execute("web.config.validate",request()),first)
                self.assertEqual(service.engine.report(),plan)
                self.assertEqual(fake.archive_requests,[])
            finally:service.close()

    def test_known_github_secret_is_not_echoed_in_preview(self):
        with tempfile.TemporaryDirectory() as root:
            service,_=make_service(root)
            try:
                service.execute("github.validate",{"credential":DUMMY})
                payload=request();payload["administrator"]["last_name"]=DUMMY
                with self.assertRaises(InstallerError) as error:service.execute("web.config.validate",payload)
                self.assertEqual(error.exception.code,ErrorCode.SECRET_REJECTED)
            finally:service.close()


class WebConfigurationHTTPTests(unittest.TestCase):
    setUp=test_github_http.GitHubHTTPTests.setUp
    tearDown=test_github_http.GitHubHTTPTests.tearDown
    _connection=test_github_http.GitHubHTTPTests._connection
    _unlock=test_github_http.GitHubHTTPTests._unlock
    login=test_github_http.GitHubHTTPTests.login
    request=test_github_http.GitHubHTTPTests.request
    route="/api/web/config/validate"

    def test_authentication_is_required(self):
        self.assertEqual(self.request("POST",self.route,request())[0],401)
        self.assertFalse(self.service.engine.journal.path.parent.exists())

    def test_csrf_and_origin_are_required_before_validation(self):
        self.login()
        for headers in ({"X-Hestia-CSRF":""},{"Origin":"https://evil.invalid"}):
            self.assertEqual(self.request("POST",self.route,request(),headers=headers)[0],403)
        self.assertIsNone(self.service.engine.report())

    def test_https_fresh_upgrade_preview_has_no_secrets_or_persistence(self):
        self.login();capture=io.StringIO()
        with contextlib.redirect_stderr(capture):
            for mode in ("fresh","upgrade"):
                payload=request(mode);status,result,headers=self.request("POST",self.route,payload)
                self.assertEqual(status,200,result)
                self.assertFalse(result["web_configuration"]["deployment_ready"])
                self.assertIn("no-store",headers["Cache-Control"])
                for secret in payload["secrets"].values():
                    if secret:self.assertNotIn(secret,json.dumps(result)+capture.getvalue())
        self.assertFalse(self.service.engine.journal.path.parent.exists())
        self.assertEqual(self.fake.requests,[])

    def test_invalid_input_returns_only_fixed_error(self):
        self.login();payload=request();payload["database"]["host"]="x;password=PRIVATE-FIXTURE"
        status,result,_=self.request("POST",self.route,payload)
        self.assertEqual(status,400)
        self.assertEqual(result,{"error":"INVALID_DATA"})

    def test_credentials_are_not_accepted_through_existing_draft(self):
        self.login()
        self.assertEqual(self.request("POST","/api/wizard/draft",request())[0],400)
        self.assertFalse(self.service.engine.journal.path.parent.exists())

    def test_expired_session_refuses_secret_input(self):
        self.login();self.server.state.session_store.clear()
        self.assertEqual(self.request("POST",self.route,request())[0],401)
        self.assertFalse(self.service.engine.journal.path.parent.exists())

    def test_busy_engine_blocks_validation_without_state_changes(self):
        self.login();self.service._mutation_lock.acquire()
        try:
            self.assertEqual(self.request("POST",self.route,request())[0],409)
        finally:self.service._mutation_lock.release()
        self.assertFalse(self.service.engine.journal.path.parent.exists())

    def test_duplicate_json_and_excessive_body_are_rejected(self):
        self.login()
        for raw in (b'{"version":1,"version":1}', b"x"*70000):
            conn=self._connection()
            try:
                conn.request("POST",self.route,body=raw,headers={
                    "Content-Type":"application/json", "Cookie":self.cookie,
                    "Origin":f"https://127.0.0.1:{self.port}", "X-Hestia-CSRF":self.csrf})
                response=conn.getresponse();response.read()
                self.assertIn(response.status,(400,413))
            finally:conn.close()
        self.assertFalse(self.service.engine.journal.path.parent.exists())

    def test_query_secret_is_rejected_and_never_logged(self):
        self.login();capture=io.StringIO()
        with contextlib.redirect_stderr(capture):
            status,result,_=self.request("POST",self.route+"?key=PRIVATE-FIXTURE",request())
        self.assertEqual(status,400)
        self.assertNotIn("PRIVATE-FIXTURE",json.dumps(result)+capture.getvalue())
