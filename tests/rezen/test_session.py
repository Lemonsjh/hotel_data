import sqlite3
import tempfile
import unittest
from pathlib import Path

from rezen_pms.crypto import aes_gcm_encrypt
from rezen_pms.session import decrypt_cookie_value, discover_profile, load_auth, load_session


class SessionTests(unittest.TestCase):
    def test_plaintext_cookie_is_accepted_and_not_echoed_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            (profile / "Network").mkdir()
            (profile / "LocalPrefs.json").write_text("{}", encoding="utf-8")
            database = sqlite3.connect(profile / "Network" / "Cookies")
            database.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                             "encrypted_value BLOB, last_access_utc INTEGER)")
            database.execute("INSERT INTO cookies VALUES (?,?,?,?,?)",
                             ("one.rezen.tech", "rezenid", "session-value", b"", 2))
            database.commit()
            database.close()
            self.assertEqual(load_session(profile), "session-value")

    def test_rezensid_is_accepted_and_client_sid_is_not_a_hotel_session(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            (profile / "Network").mkdir()
            (profile / "LocalPrefs.json").write_text("{}", encoding="utf-8")
            database = sqlite3.connect(profile / "Network" / "Cookies")
            database.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                             "encrypted_value BLOB, last_access_utc INTEGER)")
            database.execute("INSERT INTO cookies VALUES (?,?,?,?,?)",
                             ("one.rezen.tech", "sid", "client-sid-value", b"", 3))
            database.execute("INSERT INTO cookies VALUES (?,?,?,?,?)",
                             ("one.rezen.tech", "rezensid", "session-value", b"", 2))
            database.commit()
            database.close()
            token, cookie = load_auth(profile)
            self.assertEqual(token, "session-value")
            self.assertIn("rezensid=session-value", cookie)
            self.assertNotIn("client-sid-value", token)

            database = sqlite3.connect(profile / "Network" / "Cookies")
            database.execute("DELETE FROM cookies WHERE name='rezensid'")
            database.commit()
            database.close()
            with self.assertRaisesRegex(ValueError, "填写会话"):
                load_auth(profile)

    def test_v10_cookie_uses_the_supplied_key(self):
        key = bytes(range(16))
        nonce = bytes(range(12))
        ciphertext, tag = aes_gcm_encrypt(key, nonce, b"session-value")
        self.assertEqual(decrypt_cookie_value(b"v10" + nonce + ciphertext + tag, key), "session-value")
        with self.assertRaisesRegex(ValueError, "配置中填写会话"):
            decrypt_cookie_value(b"v20" + nonce + ciphertext + tag, key)

    def test_older_profile_is_used_when_the_newest_has_no_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, token in (("RezenOne-new", None), ("RezenOne-old", "session-value")):
                profile = root / name
                (profile / "Network").mkdir(parents=True)
                (profile / "LocalPrefs.json").write_text("{}", encoding="utf-8")
                database = sqlite3.connect(profile / "Network" / "Cookies")
                database.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                                 "encrypted_value BLOB, last_access_utc INTEGER)")
                if token:
                    database.execute("INSERT INTO cookies VALUES (?,?,?,?,?)",
                                     ("one.rezen.tech", "rezenid", token, b"", 1))
                database.commit()
                database.close()
            (root / "RezenOne-new" / "Network" / "Cookies").write_bytes(
                (root / "RezenOne-new" / "Network" / "Cookies").read_bytes())
            self.assertEqual(load_session(root=root), "session-value")

    def test_running_client_is_used_when_cookie_files_have_no_hotel_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = root / "RezenOne-open"
            (profile / "Network").mkdir(parents=True)
            (profile / "LocalPrefs.json").write_text("{}", encoding="utf-8")
            database = sqlite3.connect(profile / "Network" / "Cookies")
            database.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                             "encrypted_value BLOB, last_access_utc INTEGER)")
            database.execute("INSERT INTO cookies VALUES (?,?,?,?,?)",
                             ("one.rezen.tech", "sid", "client-sid-value", b"", 1))
            database.commit()
            database.close()
            token, cookie = load_auth(root=root, reader=lambda: "process-session-value")
            self.assertEqual(token, "process-session-value")
            self.assertEqual(cookie, "")
            self.assertNotIn("client-sid-value", token)

    def test_explicit_profile_does_not_read_the_running_client(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            (profile / "Network").mkdir()
            (profile / "LocalPrefs.json").write_text("{}", encoding="utf-8")
            database = sqlite3.connect(profile / "Network" / "Cookies")
            database.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                             "encrypted_value BLOB, last_access_utc INTEGER)")
            database.execute("INSERT INTO cookies VALUES (?,?,?,?,?)",
                             ("one.rezen.tech", "sid", "client-sid-value", b"", 1))
            database.commit()
            database.close()

            def reader():
                raise AssertionError("should not read process")

            with self.assertRaisesRegex(ValueError, "填写会话"):
                load_auth(profile, reader=reader)

    def test_saved_cookie_is_used_before_the_running_client(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = root / "RezenOne-saved"
            (profile / "Network").mkdir(parents=True)
            (profile / "LocalPrefs.json").write_text("{}", encoding="utf-8")
            database = sqlite3.connect(profile / "Network" / "Cookies")
            database.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                             "encrypted_value BLOB, last_access_utc INTEGER)")
            database.execute("INSERT INTO cookies VALUES (?,?,?,?,?)",
                             ("one.rezen.tech", "rezenid", "session-value", b"", 1))
            database.commit()
            database.close()

            def reader():
                raise AssertionError("should not read process")

            self.assertEqual(load_auth(root=root, reader=reader)[0], "session-value")

    def test_profile_discovery_uses_only_rezen_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            older = root / "RezenOne-old"
            newer = root / "RezenOne-new"
            for profile in (older, newer):
                (profile / "Network").mkdir(parents=True)
                (profile / "LocalPrefs.json").write_text("{}", encoding="utf-8")
                (profile / "Network" / "Cookies").write_bytes(b"")
            (root / "other").mkdir()
            (newer / "Network" / "Cookies").write_bytes(b"x")
            self.assertEqual(discover_profile(root), newer)
