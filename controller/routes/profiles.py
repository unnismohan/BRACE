"""Manage project environments without returning secret variable values."""

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import authentication as auth
from db import database, decrypt_token
from profiles import validate_values, seal_secrets
from runtime import audit

router = APIRouter()


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    environment: dict[str, str] = Field(default_factory=dict)
    variables: dict[str, str] = Field(default_factory=dict)
    secret_variables: dict[str, str] = Field(default_factory=dict)
    remove_secrets: list[str] = Field(default_factory=list)
    enabled: bool = True


def public_profile(row):
    value = dict(row)
    encrypted = value.pop("secret_variables") or ""
    value["secret_names"] = (
        sorted(json.loads(decrypt_token(encrypted))) if encrypted else []
    )
    for key in ("environment", "variables"):
        value[key] = json.loads(value[key])
    return value


@router.get("/api/projects/{project_id}/profiles")
def list_profiles(project_id: int, user=Depends(auth._proj_viewer)):
    with database() as conn:
        if not conn.execute(
            "SELECT 1 FROM projects WHERE id=?", (project_id,)
        ).fetchone():
            raise HTTPException(404, "Project not found")
        return [
            public_profile(row)
            for row in conn.execute(
                "SELECT * FROM environment_profiles WHERE project_id=? ORDER BY name,id",
                (project_id,),
            )
        ]


def save(project_id, req, profile_id=None):
    validate_values(req.environment, environment=True)
    validate_values(req.variables)
    validate_values(req.secret_variables)
    with database() as conn:
        old = (
            conn.execute(
                "SELECT * FROM environment_profiles WHERE id=? AND project_id=?",
                (profile_id, project_id),
            ).fetchone()
            if profile_id
            else None
        )
        if profile_id and not old:
            raise HTTPException(404, "Profile not found")
        secrets = (
            json.loads(decrypt_token(old["secret_variables"]))
            if old and old["secret_variables"]
            else {}
        )
        secrets.update(req.secret_variables)
        for name in req.remove_secrets:
            secrets.pop(name, None)
        values = (
            req.name.strip(),
            json.dumps(req.environment),
            json.dumps(req.variables),
            seal_secrets(secrets),
            int(req.enabled),
        )
        if not values[0]:
            raise HTTPException(400, "Profile name cannot be blank")
        if profile_id:
            conn.execute(
                "UPDATE environment_profiles SET name=?,environment=?,variables=?,secret_variables=?,enabled=? WHERE id=? AND project_id=?",
                values + (profile_id, project_id),
            )
        else:
            profile_id = conn.execute(
                "INSERT INTO environment_profiles(name,environment,variables,secret_variables,enabled,project_id) VALUES (?,?,?,?,?,?)",
                values + (project_id,),
            ).lastrowid
    return {"id": profile_id}


@router.post("/api/projects/{project_id}/profiles")
def create_profile(project_id: int, req: ProfileInput, user=Depends(auth._proj_admin)):
    result = save(project_id, req)
    audit(user, "profile.create", project_id=project_id, target=result["id"])
    return result


@router.put("/api/projects/{project_id}/profiles/{profile_id}")
def update_profile(
    project_id: int, profile_id: int, req: ProfileInput, user=Depends(auth._proj_admin)
):
    result = save(project_id, req, profile_id)
    audit(user, "profile.update", project_id=project_id, target=profile_id)
    return result
