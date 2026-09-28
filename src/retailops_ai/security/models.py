"""Versioned local grant/credential file contracts, not an OAuth/OIDC provider."""

from typing import Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, Symbol, UtcTime, Versioned
from retailops_ai.domain.access import Capability, Channel, Role


class ResourceScope(Contract):
    product_ids: list[Symbol] = Field(min_length=1, max_length=200)
    selling_location_ids: list[Symbol] = Field(min_length=1, max_length=100)
    channels: list[Channel] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def no_duplicates(self) -> Self:
        for values in (self.product_ids, self.selling_location_ids, self.channels):
            if len(set(values)) != len(values):
                raise ValueError("duplicate_access_scope")
        return self


class AccessGrant(Contract):
    principal_id: Symbol
    roles: list[Role] = Field(min_length=1, max_length=3)
    capabilities: list[Capability] = Field(min_length=1, max_length=2)
    scope: ResourceScope | None

    @model_validator(mode="after")
    def explicit_capabilities(self) -> Self:
        if len(set(self.roles)) != len(self.roles) or len(set(self.capabilities)) != len(
            self.capabilities
        ):
            raise ValueError("duplicate_role_or_capability")
        if "access:admin" in self.capabilities and "admin" not in self.roles:
            raise ValueError("administrative_capability_requires_admin_role")
        if ("forecast:read" in self.capabilities) != (self.scope is not None):
            raise ValueError("forecast_capability_requires_explicit_scope")
        return self


class GrantTemplate(Versioned):
    policy_id: Symbol
    grants: list[AccessGrant] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_principals(self) -> Self:
        if len({g.principal_id for g in self.grants}) != len(self.grants):
            raise ValueError("duplicate_grant_principal")
        return self


class Credential(Contract):
    token_sha256: Sha256 = Field(repr=False)
    principal_id: Symbol
    not_before: UtcTime
    expires_at: UtcTime
    revoked: bool

    @model_validator(mode="after")
    def bounded_lifetime(self) -> Self:
        if not 0 < (self.expires_at - self.not_before).total_seconds() <= 86400:
            raise ValueError("credential_lifetime_outside_local_budget")
        return self


class AccessPolicy(GrantTemplate):
    credentials: list[Credential] = Field(min_length=1, max_length=64, repr=False)

    @model_validator(mode="after")
    def credential_references(self) -> Self:
        principals = {g.principal_id for g in self.grants}
        if len({c.token_sha256 for c in self.credentials}) != len(self.credentials):
            raise ValueError("duplicate_credential_fingerprint")
        if any(c.principal_id not in principals for c in self.credentials):
            raise ValueError("credential_principal_missing")
        return self
