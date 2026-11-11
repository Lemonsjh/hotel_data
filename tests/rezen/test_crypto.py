import unittest

from rezen_pms.crypto import aes_gcm_decrypt, aes_gcm_encrypt


class AesGcmTests(unittest.TestCase):
    def test_round_trip_and_known_empty_vector(self):
        key = bytes(range(32))
        nonce = bytes(range(12))
        ciphertext, tag = aes_gcm_encrypt(key, nonce, b"room-count")
        self.assertEqual(aes_gcm_decrypt(key, nonce, ciphertext, tag), b"room-count")
        self.assertNotEqual(ciphertext, b"room-count")

        empty_key = bytes(16)
        empty_tag = bytes.fromhex("58e2fccefa7e3061367f1d57a4e7455a")
        self.assertEqual(aes_gcm_decrypt(empty_key, bytes(12), b"", empty_tag), b"")

    def test_wrong_tag_is_rejected_without_partial_plaintext(self):
        key, nonce = bytes(16), bytes(12)
        ciphertext, tag = aes_gcm_encrypt(key, nonce, b"secret-room")
        broken = bytearray(tag)
        broken[0] ^= 1
        with self.assertRaises(RuntimeError):
            aes_gcm_decrypt(key, nonce, ciphertext, bytes(broken))
