"""Fixed public error codes for APP-005 (never interpolate secrets)."""

from __future__ import annotations


class InitializerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InterruptStage(InitializerError):
    """Raised by test adapters to simulate crash between remote success and checkpoint."""


# Input / contract
ERR_INPUT = "input.invalid"
ERR_CONFIG = "config.invalid"
ERR_CONFIG_MODE = "config.mode_not_deployment_ready"
ERR_SCHEMA = "manifest.schema_invalid"
ERR_TEMPLATE_REV = "template.revision_mismatch"
ERR_TEMPLATE_MISSING = "template.revision_required"
ERR_TEMPLATE_ROOT = "template.root_invalid"
ERR_TEMPLATE_SNAPSHOT = "template.snapshot_unverified"
ERR_TEMPLATE_MARKER_ONLY = "template.marker_insufficient"
ERR_TEMPLATE_DIRTY = "template.content_dirty"
ERR_OUTPUT_ROOT = "output.root_invalid"
ERR_PATH_ESCAPE = "path.escape_rejected"
ERR_SYMLINK = "path.symlink_rejected"
ERR_OVERWRITE = "path.user_file_overwrite_rejected"
ERR_GENERATED_EDIT = "path.generated_content_modified"
ERR_LOCK = "lock.busy"
ERR_LOCK_IO = "lock.io_error"
ERR_CHECKPOINT_RESET = "checkpoint.resource_reset_forbidden"

# Plan / apply binding
ERR_PLAN_DIGEST = "plan.digest_mismatch"
ERR_PLAN_REQUIRED = "plan.digest_required"
ERR_PLAN_MISSING = "plan.artifact_missing"
ERR_DRIFT_CONFIG = "drift.config_hash"
ERR_DRIFT_TEMPLATE = "drift.template_revision"
ERR_DRIFT_PLAN = "drift.plan_digest"
ERR_STATUS = "manifest.status_invalid"

# DNS / providers
ERR_DNS_UNRESOLVED = "dns.unresolved_blocks_apply"
ERR_DNS_CLOUDFRONT = "dns.cloudfront_unsupported"
ERR_AMBIGUOUS = "resource.ambiguous_refused"
ERR_COLLISION = "resource.collision_refused"
ERR_ADOPT_BY_NAME = "resource.adopt_by_name_forbidden"
ERR_NO_AUTO_DELETE = "resource.auto_delete_forbidden"

# Live integration gate (honest incompleteness)
ERR_LIVE_MISSING = "apply.live_integration_missing"
ERR_LIVE_CLAIM = "apply.live_claim_rejected"
ERR_CAPABILITY_METADATA = "capability.metadata_not_proof"
ERR_CAPABILITY_UNVERIFIED = "capability.unverified"
ERR_CAPABILITY_BINDING = "capability.binding_mismatch"
ERR_TF_RAW_PERSIST = "terraform.raw_plan_persist_forbidden"
ERR_TF_GUARD = "terraform.plan_guard_failed"
ERR_SECRETS = "secrets.preflight_failed"
ERR_TRANSPORT = "transport.error"
ERR_REDACTION = "output.redaction_failed"

# Stage / resume
ERR_STAGE = "stage.failed"
ERR_RESUME = "resume.unavailable"
ERR_CONCURRENT = "runner.concurrent_rejected"
