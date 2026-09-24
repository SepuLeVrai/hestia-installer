"""Shared synthetic payload only, without HTTP fixtures or runtime side effects."""

def request(mode="fresh"):
    return {
        "version": 1, "mode": mode,
        "web": {"hostname": "hestia.example.test", "webroot": "/var/www/hestia", "service_user": "www-data"},
        "database": {"mode": "managed" if mode == "fresh" else "existing_local", "host": "localhost",
                     "port": 3306, "name": "hestia", "user": "hestia", "tls_ca_file": None},
        "administrator": {"first_name": "Bastien", "last_name": "D'Exemple & associés", "email": "admin@example.test"} if mode == "fresh" else None,
        "assistant": {"action": "disabled" if mode == "fresh" else "preserve"},
        "secrets": {"database_password": "DB-fixture-2026!", "admin_password": "Admin-fixture-2026!" if mode == "fresh" else "", "openai_api_key": ""},
    }


def local_request(mode="fresh"):
    value = request(mode)
    value["database"]["mode"] = "existing_local"
    return value
