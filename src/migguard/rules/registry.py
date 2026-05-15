"""Rule registry: discovers and loads all enabled rules."""

from __future__ import annotations

from migguard.rules.base import Rule
from migguard.rules.checks.compatibility import MergeOnSynapseRule
from migguard.rules.checks.data_loss import (
    DeleteWithoutWhereRule,
    DropSchemaRule,
    DropTableRule,
    TruncateRule,
    UpdateWithoutWhereRule,
)
from migguard.rules.checks.dbcc import DbccCommandRule
from migguard.rules.checks.idempotency import (
    AlterTableWithoutGuardRule,
    CreateTableWithoutIfNotExistsRule,
    DropWithoutIfExistsRule,
)
from migguard.rules.checks.indexes import IndexDropWithoutRecreateRule
from migguard.rules.checks.joins import (
    JoinFunctionOnKeyRule,
    JoinMissingOnRule,
    JoinOnNullableKeyRule,
    ManyToManyJoinRule,
)
from migguard.rules.checks.lifetimes import ObjectLifetimeRule
from migguard.rules.checks.locking import (
    CreateIndexWithoutOnlineRule,
    NotNullDefaultOnLargeTableRule,
)
from migguard.rules.checks.mysql_rules import (
    AlterTableWithoutAlgorithmRule,
    Utf8NotUtf8mb4Rule,
    ZeroDateDefaultRule,
)
from migguard.rules.checks.naming import NamingConventionRule
from migguard.rules.checks.permissions import GrantOrDenyRule
from migguard.rules.checks.rollback import IrreversibleWithoutDownScriptRule
from migguard.rules.checks.sequencing import VersionSequencingRule
from migguard.rules.checks.transaction import WriteWithoutTransactionRule


def all_rules() -> list[Rule]:
    """Return one instance of every enabled rule."""
    return [
        DeleteWithoutWhereRule(),
        UpdateWithoutWhereRule(),
        TruncateRule(),
        DropTableRule(),
        DropSchemaRule(),
        NotNullDefaultOnLargeTableRule(),
        CreateIndexWithoutOnlineRule(),
        DropWithoutIfExistsRule(),
        CreateTableWithoutIfNotExistsRule(),
        AlterTableWithoutGuardRule(),
        MergeOnSynapseRule(),
        GrantOrDenyRule(),
        WriteWithoutTransactionRule(),
        IrreversibleWithoutDownScriptRule(),
        NamingConventionRule(),
        VersionSequencingRule(),
        ObjectLifetimeRule(),
        AlterTableWithoutAlgorithmRule(),
        Utf8NotUtf8mb4Rule(),
        ZeroDateDefaultRule(),
        JoinMissingOnRule(),
        JoinOnNullableKeyRule(),
        JoinFunctionOnKeyRule(),
        ManyToManyJoinRule(),
        IndexDropWithoutRecreateRule(),
        DbccCommandRule(),
    ]
