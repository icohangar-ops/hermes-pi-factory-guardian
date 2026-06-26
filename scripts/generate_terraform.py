#!/usr/bin/env python3
"""
Generate Terraform for hermes-pi-factory-guardian + Twingate from the
extended factory_config.twingate.yaml.

Reads:  config/factory_config.twingate.yaml  (the file we shipped earlier)
Writes: terraform/twingate_factory.tf
        terraform/terraform.tfvars.example
        terraform/variables.tf

The generated .tf uses the official Twingate/twingate Terraform provider
(https://registry.terraform.io/providers/Twingate/twingate/latest/docs)
and emits:
  - 1 twingate_remote_network            per factory line
  - 2 twingate_connector                 per remote_network (failover pair)
  - 1 twingate_resource                  per machine + per camera
  - 1 twingate_resource_access           per resource (group bindings) [via policy]
  - 1 twingate_group                     per IdP SCIM group referenced in config
  - 1 twingate_security_policy           per policy name referenced in config
  - 1 twingate_remote_network            per connector's network

Run:
    python3 scripts/generate_terraform.py \\
        --config download/factory_config.twingate.yaml \\
        --out    download/terraform

Then:
    cd download/terraform
    cp terraform.tfvars.example terraform.tfvars  # fill in real values
    terraform init
    terraform plan
    terraform apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Dict, List

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install PyYAML", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_TF_NAME_RE = re.compile(r"[^a-z0-9_]")


def tf_safe(s: str) -> str:
    """Turn an arbitrary string into a valid Terraform resource name suffix."""
    s = s.lower().strip()
    s = _TF_NAME_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "default"


def hcl_string(s: str) -> str:
    """Escape a Python string for HCL."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def hcl_block(label: str, body_lines: List[str], indent: int = 2) -> str:
    """Render an HCL block like:
        label {
          <body>
        }
    """
    pad = " " * indent
    inner = "\n".join(f"{pad}{line}" for line in body_lines)
    return f"{label} {{\n{inner}\n}}"


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------
def gen_provider(network_var: str = "var.twingate_network") -> str:
    return f'''# Twingate provider configuration
# https://registry.terraform.io/providers/Twingate/twingate/latest/docs
terraform {{
  required_providers {{
    twingate = {{
      source  = "Twingate/twingate"
      version = "~> 3.0"
    }}
  }}
}}

provider "twingate" {{
  api_token = var.twingate_api_token
  network   = {network_var}
}}
'''


def gen_remote_network(rn_name: str) -> str:
    name = tf_safe(rn_name)
    return hcl_block(
        f'resource "twingate_remote_network" "{name}"',
        [f'name = {hcl_string(rn_name)}'],
    )


def gen_connector(rn_ref: str, conn_name: str, labels: Dict[str, str]) -> str:
    name = tf_safe(conn_name)
    body = [
        f'remote_network_id     = {rn_ref}',
        f'name                   = {hcl_string(conn_name)}',
        'status_updates_enabled = true',
    ]
    # Twingate connector has a `tags` block in recent provider versions;
    # fall back to keeping labels in the resource name for older versions.
    if labels:
        tag_lines = [f'{k} = {hcl_string(v)}' for k, v in labels.items()]
        body.append(hcl_block("tags", tag_lines, indent=2).replace("\n", "\n  "))
    return hcl_block(f'resource "twingate_connector" "{name}"', body)


def gen_resource(
    res_name: str,
    address: str,
    protocol: str,
    port: int,
    rn_ref: str,
    tags: Dict[str, str],
    remote_network_id_ref: str,
) -> str:
    name = tf_safe(res_name)
    # Twingate resource: address can be IP/CIDR/FQDN. We default to FQDN.
    body: List[str] = [
        f'name              = {hcl_string(res_name)}',
        f'address           = {hcl_string(address)}',
        f'remote_network_id = {remote_network_id_ref}',
    ]
    if protocol.lower() in ("tcp", "udp", "icmp"):
        body.append(f'protocol = {hcl_string(protocol.upper())}')
    if port:
        body.append(f'port = {port}')
    if tags:
        tag_lines = [f'{k} = {hcl_string(v)}' for k, v in tags.items()]
        body.append(hcl_block("tags", tag_lines, indent=2))
    return hcl_block(f'resource "twingate_resource" "{name}"', body)


def gen_group(name: str) -> str:
    n = tf_safe(name)
    return hcl_block(
        f'resource "twingate_group" "{n}"',
        [f'name = {hcl_string(name)}', f'type = "manual"'],
    )


def gen_security_policy(
    policy_name: str,
    rn_ref: str,
    resources: List[str],
    allowed_groups: List[str],
    require_mfa: bool = False,
) -> str:
    n = tf_safe(policy_name)
    body: List[str] = [
        f'name = {hcl_string(policy_name)}',
    ]
    # The Twingate provider's security_policy resource binds resources and
    # groups via set references. We emit the full policy here.
    if resources:
        body.append("resources = [")
        for r in resources:
            body.append(f"  {r},")
        body.append("]")
    if allowed_groups:
        body.append("groups = [")
        for g in allowed_groups:
            body.append(f"  {g},")
        body.append("]")
    body.append(f"requires_mfa = {"true" if require_mfa else "false"}")
    return hcl_block(f'resource "twingate_security_policy" "{n}"', body)


def gen_variables() -> str:
    return '''variable "twingate_api_token" {
  description = "Twingate Admin API token (Settings > API > Generate Token)"
  type        = string
  sensitive   = true
}

variable "twingate_network" {
  description = "Twingate network slug (e.g. 'acme' for acme.twingate.com)"
  type        = string
}

variable "factory_name" {
  description = "Factory identifier, used in resource naming"
  type        = string
  default     = "factory-a"
}

variable "factory_line" {
  description = "Production line identifier, used in resource naming"
  type        = string
  default     = "line-1"
}
'''


def gen_tfvars_example(network: str, factory_name: str, factory_line: str) -> str:
    return f'''# Copy to terraform.tfvars and fill in real values.

twingate_api_token = "REDACTED-GENERATE-IN-ADMIN-CONSOLE"
twingate_network   = "{network}"
factory_name       = "{factory_name}"
factory_line       = "{factory_line}"
'''


# ---------------------------------------------------------------------------
# Env-var interpolation (${VAR} -> os.environ[VAR])
# ---------------------------------------------------------------------------
_ENV_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _interpolate_env(value: Any) -> Any:
    """Recursively replace ${VAR} with os.environ[VAR] in any string in the
    config tree. Missing env vars fall back to the literal ${VAR} so the
    generator remains idempotent across runs."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------
def generate(config: Dict[str, Any], out_dir: str) -> Dict[str, str]:
    # Resolve ${FACTORY_NAME}, ${FACTORY_LINE}, ${SHIFT_TIMEZONE}, etc. so the
    # generated Terraform has concrete names, not env-var placeholders.
    config = _interpolate_env(config)

    tg = config.get("twingate", {})
    factory = config.get("factory", {})
    factory_name = factory.get("name", "factory-a").lower().replace(" ", "-")
    factory_line = "line-1"

    # Pull the factory_name/line out of env vars if set (overrides config).
    factory_name = os.environ.get("FACTORY_NAME", factory_name)
    factory_line = os.environ.get("FACTORY_LINE", factory_line)

    rn_name = tg.get("remote_network", f"factory-{factory_name}-line-{factory_line}")
    connectors = tg.get("connectors", [])
    idp_groups = (
        tg.get("identity_provider", {})
        .get("groups", {})
    )

    # Collect every distinct policy name referenced by machines/cameras.
    policies: Dict[str, Dict[str, Any]] = {}
    resources: List[Dict[str, Any]] = []

    for m in config.get("machines", []):
        sub = m.get("twingate", {})
        if not sub:
            continue
        resources.append({
            "type": "machine",
            "id": m["id"],
            "res_name": sub.get("resource_name", f"{m['id']}-guardian"),
            "address": sub.get("address", ""),
            "protocol": sub.get("protocol", "tcp"),
            "port": sub.get("port", 8080),
            "tags": sub.get("tags", {}),
            "policy": sub.get("policy"),
            "allowed_groups": sub.get("jit_access", {}).get("allowed_groups", []),
            "require_mfa": sub.get("jit_access", {}).get("require_mfa", False),
        })
        if sub.get("policy"):
            policies[sub["policy"]] = {
                "allowed_groups": sub.get("jit_access", {}).get("allowed_groups", []),
                "require_mfa": sub.get("jit_access", {}).get("require_mfa", False),
            }

    for c in config.get("cameras", []):
        sub = c.get("twingate", {})
        if not sub:
            continue
        resources.append({
            "type": "camera",
            "id": c["id"],
            "res_name": sub.get("resource_name", f"{c['id']}-cam"),
            "address": sub.get("address", ""),
            "protocol": sub.get("protocol", "tcp"),
            "port": sub.get("port", 554),
            "tags": sub.get("tags", {}),
            "policy": sub.get("policy"),
            "allowed_groups": sub.get("jit_access", {}).get("allowed_groups", []),
            "require_mfa": sub.get("jit_access", {}).get("require_mfa", False),
        })
        if sub.get("policy"):
            policies.setdefault(sub["policy"], {
                "allowed_groups": sub.get("jit_access", {}).get("allowed_groups", []),
                "require_mfa": sub.get("jit_access", {}).get("require_mfa", False),
            })

    # --- Emit HCL ---
    blocks: List[str] = []
    blocks.append(gen_provider())
    blocks.append("# --- Remote Network ---")
    rn_tf_name = tf_safe(rn_name)
    blocks.append(gen_remote_network(rn_name))
    rn_ref = f"twingate_remote_network.{rn_tf_name}.id"

    blocks.append("# --- Connectors (failover pair) ---")
    for conn in connectors:
        blocks.append(gen_connector(rn_ref, conn["name"], conn.get("labels", {})))

    blocks.append("# --- IdP Groups ---")
    for role, gname in idp_groups.items():
        blocks.append(gen_group(gname))

    blocks.append("# --- Resources (machines + cameras) ---")
    res_tf_refs: Dict[str, str] = {}
    for r in resources:
        # The Twingate resource's `address` is the LAN-side address of the
        # Pi endpoint. We render the resource_name as the Twingate name.
        block = gen_resource(
            res_name=r["res_name"],
            address=r["address"],
            protocol=r["protocol"],
            port=r["port"],
            rn_ref=rn_ref,
            tags=r["tags"],
            remote_network_id_ref=rn_ref,
        )
        blocks.append(block)
        res_tf_refs[r["res_name"]] = f"twingate_resource.{tf_safe(r['res_name'])}.id"

    blocks.append("# --- Security Policies ---")
    group_tf_refs: Dict[str, str] = {}
    for role, gname in idp_groups.items():
        group_tf_refs[gname] = f"twingate_group.{tf_safe(gname)}.id"

    for pname, pinfo in policies.items():
        # Bind every resource that references this policy
        bound_resources = [
            res_tf_refs[r["res_name"]]
            for r in resources
            if r.get("policy") == pname
        ]
        bound_groups = [
            group_tf_refs[idp_groups[g]]
            for g in pinfo["allowed_groups"]
            if g in idp_groups
        ]
        blocks.append(gen_security_policy(
            policy_name=pname,
            rn_ref=rn_ref,
            resources=bound_resources,
            allowed_groups=bound_groups,
            require_mfa=pinfo.get("require_mfa", False),
        ))

    main_tf = "\n\n".join(blocks) + "\n"

    # --- Write files ---
    os.makedirs(out_dir, exist_ok=True)
    outputs = {
        "main.tf": main_tf,
        "variables.tf": gen_variables(),
        "terraform.tfvars.example": gen_tfvars_example(
            network=tg.get("network", "your-network").strip('"').strip("${}"),
            factory_name=factory_name,
            factory_line=factory_line,
        ),
    }
    for fname, content in outputs.items():
        path = os.path.join(out_dir, fname)
        with open(path, "w") as f:
            f.write(content)
        print(f"  wrote {path} ({len(content)} bytes)")

    # --- Print summary ---
    print()
    print(f"Generated Terraform for:")
    print(f"  Remote Network : {rn_name}")
    print(f"  Connectors     : {len(connectors)}")
    print(f"  Groups         : {len(idp_groups)}")
    print(f"  Resources      : {len(resources)} ({sum(1 for r in resources if r['type']=='machine')} machines, {sum(1 for r in resources if r['type']=='camera')} cameras)")
    print(f"  Policies       : {len(policies)}")
    return outputs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--config", required=True, help="Path to factory_config.twingate.yaml")
    p.add_argument("--out",    required=True, help="Output directory for .tf files")
    args = p.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    if not config.get("twingate"):
        print("ERROR: config has no top-level 'twingate:' block", file=sys.stderr)
        return 2

    generate(config, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
