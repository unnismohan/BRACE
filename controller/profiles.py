"""Project environment snapshots and a deliberately small runner environment."""

import json
from fastapi import HTTPException
from db import database, decrypt_token, encrypt_token, encryption_available
from environments import (
    RUNNER_ENV_KEYS,
    validate_values,
    runner_environment as clean_environment,
)


def snapshot_profile(project_id, profile_id):
    if profile_id is None:
        return {}, ""
    with database() as conn:
        row = conn.execute(
            "SELECT * FROM environment_profiles WHERE id=? AND project_id=? AND enabled=1",
            (profile_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(
            404, "Enabled environment profile not found in this project"
        )
    public = {
        "id": row["id"],
        "name": row["name"],
        "environment": json.loads(row["environment"]),
        "variables": json.loads(row["variables"]),
    }
    return public, row["secret_variables"] or ""


def runner_environment(public=None, encrypted_secrets=""):
    public = dict(public or {})
    if encrypted_secrets:
        public["variables"] = {
            **public.get("variables", {}),
            **json.loads(decrypt_token(encrypted_secrets)),
        }
    return clean_environment(public)


def seal_secrets(values):
    if values and not encryption_available():
        raise HTTPException(
            503, "Secret variables require configured credential encryption"
        )
    return encrypt_token(json.dumps(values)) if values else ""
