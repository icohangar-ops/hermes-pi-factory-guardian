#!/usr/bin/env python3
"""Validate the Twingate overlay: YAML parses, compose merges, schema is sane."""
import sys, subprocess, tempfile, os, shutil
from pathlib import Path

try:
    import yaml
except ImportError:
    raise SystemExit(
        "PyYAML is required to run this validator. Install it first, e.g. "
        "`pip install PyYAML` (or `pip install -r requirements.txt`)."
    )

# Resolve paths relative to the repo root so this script works on a fresh
# checkout (CI, Pi, anyone's laptop) without environment-specific paths.
ROOT         = Path(__file__).resolve().parents[1]
BASE_COMPOSE = str(ROOT / "docker-compose.yml")
OVERLAY      = str(ROOT / "docker-compose.twingate.yml")
CONFIG       = str(ROOT / "config" / "factory_config.twingate.yaml")
ENV_EXAMPLE  = str(ROOT / ".env.twingate.example")

print("=" * 70)
print("1) YAML parse checks")
print("=" * 70)

for label, path in [("overlay", OVERLAY), ("config", CONFIG), ("env", ENV_EXAMPLE)]:
    try:
        with open(path) as f:
            if path.endswith(('.yml','.yaml')):
                list(yaml.safe_load_all(f))
            else:
                f.read()
        print(f"  OK   {label:8s} {path}")
    except Exception as e:
        print(f"  FAIL {label:8s} {path}\n      {e}")
        sys.exit(1)

print()
print("=" * 70)
print("2) factory_config.twingate.yaml schema sanity")
print("=" * 70)

with open(CONFIG) as f:
    cfg = yaml.safe_load(f)

tg = cfg.get("twingate")
assert tg, "missing top-level twingate: block"
for key in ["network","remote_network","connectors","identity_provider",
            "device_posture_checks","jit_access","audit","egress_allowlist"]:
    assert key in tg, f"twingate: missing {key}"
    print(f"  OK   twingate.{key}")

for m in cfg["machines"]:
    sub = m.get("twingate", {})
    assert sub.get("resource_name"), f"machine {m['id']} missing twingate.resource_name"
    assert "policy" in sub, f"machine {m['id']} missing twingate.policy"
    assert "tags" in sub and "machine-id" in sub["tags"], f"machine {m['id']} missing tags.machine-id"
    print(f"  OK   machine {m['id']:14s} -> {sub['resource_name']}  policy={sub['policy']}")

for c in cfg["cameras"]:
    sub = c.get("twingate", {})
    assert sub.get("resource_name"), f"camera {c['id']} missing twingate.resource_name"
    print(f"  OK   camera {c['id']:7s} -> {sub['resource_name']}")

print()
print("=" * 70)
print("3) docker-compose overlay merges with base")
print("=" * 70)

if shutil.which("docker"):
    env_file = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
    env_file.write("FACTORY_NAME=factory-a\nFACTORY_LINE=line-1\n")
    env_file.write("TWINGATE_NETWORK=test\nTWINGATE_ACCESS_TOKEN=t\nTWINGATE_REFRESH_TOKEN=t\n")
    env_file.close()
    try:
        out = subprocess.run(
            ["docker","compose","--env-file",env_file.name,
             "-f",BASE_COMPOSE,"-f",OVERLAY,"config"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode == 0:
            print("  OK   `docker compose -f base -f overlay config` succeeded")
            merged = yaml.safe_load(out.stdout)
            svc = merged.get("services",{})
            assert "factory-guardian" in svc, "factory-guardian missing from merged"
            assert "twingate-connector" in svc, "twingate-connector missing from merged"
            assert "ot_net" in merged.get("networks",{}), "ot_net missing from merged networks"
            print("  OK   merged services: " + ", ".join(svc.keys()))
            print("  OK   merged networks: " + ", ".join(merged.get("networks",{}).keys()))
            fc = svc["factory-guardian"]
            assert "ot_net" in fc.get("networks",{}), "factory-guardian not on ot_net"
            print("  OK   factory-guardian joined ot_net")
            tc = svc["twingate-connector"]
            assert "TWINGATE_NETWORK" in " ".join(tc.get("environment",[]))
            print("  OK   twingate-connector env includes TWINGATE_NETWORK")
            print("  OK   twingate-connector image:", tc.get("image"))
            print("  OK   twingate-connector restart:", tc.get("restart"))
            print("  OK   twingate-connector sysctls:", tc.get("sysctls"))
            print("  OK   twingate-connector has NO ports: block:", "ports" not in tc)
        else:
            print("  FAIL docker compose config returned non-zero:")
            print(out.stderr[:1500])
            sys.exit(1)
    finally:
        os.unlink(env_file.name)
else:
    print("  SKIP docker not installed in this env; doing a YAML-merge sanity check instead")
    with open(BASE_COMPOSE) as f: base = yaml.safe_load(f)
    with open(OVERLAY) as f: over = yaml.safe_load(f)
    merged = {**base}
    for k, v in over.items():
        if k == "services":
            merged["services"] = {**base.get("services",{}), **v}
            fg = dict(base["services"]["factory-guardian"])
            for kk, vv in v["factory-guardian"].items():
                fg[kk] = vv
            merged["services"]["factory-guardian"] = fg
        elif k == "networks":
            merged["networks"] = {**base.get("networks",{}), **v}
        else:
            merged[k] = v
    svc = merged["services"]
    assert "factory-guardian" in svc and "twingate-connector" in svc
    assert "ot_net" in merged["networks"]
    assert "ot_net" in svc["factory-guardian"]["networks"]
    assert "ports" not in svc["twingate-connector"]
    print("  OK   YAML merge: factory-guardian + twingate-connector + ot_net present")
    print("  OK   factory-guardian.networks:", svc["factory-guardian"]["networks"])
    print("  OK   twingate-connector.image:", svc["twingate-connector"]["image"])
    print("  OK   twingate-connector.sysctls:", svc["twingate-connector"]["sysctls"])
    print("  OK   twingate-connector has NO ports: block (correct)")

print()
print("=" * 70)
print("ALL CHECKS PASSED")
print("=" * 70)
