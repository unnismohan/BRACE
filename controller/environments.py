"""Pure runner environment policy; no controller database dependency."""

import json
import os
import re
from fastapi import HTTPException

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RUNNER_ENV_KEYS = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "TZ",
    "DISPLAY",
    "CHROME_BIN",
    "CHROMEDRIVER",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "JAVA_HOME",
}
RESERVED_ENV = RUNNER_ENV_KEYS | {
    "PYTHONPATH",
    "PYTHONHOME",
    "HOME",
    "BRACE_PROFILE_VARIABLES",
}


def validate_values(values, environment=False):
    for name, value in values.items():
        if not IDENTIFIER.fullmatch(name) or len(name) > 100 or len(value) > 16384:
            raise HTTPException(400, "Invalid variable name or value (maximum 16 KiB)")
        if environment and (name.upper() in RESERVED_ENV or name.upper().startswith("BRACE_")):
            raise HTTPException(400, f"Environment key is reserved: {name}")


def runner_environment(public=None):
    public = public or {}
    result = {
        name: value for name, value in os.environ.items() if name in RUNNER_ENV_KEYS
    }
    result["DISPLAY"] = result.get("DISPLAY", ":99")
    result.update(public.get("environment", {}))
    variables = dict(public.get("variables", {}))
    result["BRACE_PROFILE_VARIABLES"] = json.dumps(variables)
    return result
