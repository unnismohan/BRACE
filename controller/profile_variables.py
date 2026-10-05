"""Robot variable file: values arrive in the child environment, never argv."""

import json
import os


def get_variables():
    return json.loads(os.environ.get("BRACE_PROFILE_VARIABLES", "{}"))
