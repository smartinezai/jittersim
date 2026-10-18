"""Skips for the TLS tests that need a library able to check the local authority's certificates.

The authority limits itself to the Pi's private names and networks with a critical name constraint
of type IP. LibreSSL 2.8.3 (the library of Apple's Python 3.9, /usr/bin/python3) cannot check such a
constraint and refuses every certificate the authority issued: "unsupported name constraint type".
OpenSSL, which every other Python of the matrix uses, has done so for years, and so do the
browsers and operating systems that trust the authority.
"""

import ssl
import unittest

LIBRESSL = ssl.OPENSSL_VERSION.startswith("LibreSSL")

needs_openssl = unittest.skipIf(
    LIBRESSL, "this Python's TLS library is LibreSSL, which cannot check the authority's IP name constraints")
