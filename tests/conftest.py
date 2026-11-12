"""Values the application requires at import, supplied explicitly for the suite.

The service refuses to start without an audience and an allowlist, which is the behaviour #15 asks
for. Tests need that import to succeed, so the two values are set here — before any test module
imports the app — with names that are unmistakably test values. They are not defaulted in the
application, where a real deployment could inherit them without anyone deciding.
"""

import os

os.environ.setdefault("EXPECTED_AUDIENCE", "test-audience.apps.googleusercontent.com")
os.environ.setdefault("ALLOWED_SUBS", "sub-allowed")
