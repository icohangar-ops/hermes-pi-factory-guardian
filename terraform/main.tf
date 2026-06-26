# Twingate provider configuration
# https://registry.terraform.io/providers/Twingate/twingate/latest/docs
terraform {
  required_providers {
    twingate = {
      source  = "Twingate/twingate"
      version = "~> 3.0"
    }
  }
}

provider "twingate" {
  api_token = var.twingate_api_token
  network   = var.twingate_network
}


# --- Remote Network ---

resource "twingate_remote_network" "factory_factory_a_line_line_1" {
  name = "factory-factory-a-line-line-1"
}

# --- Connectors (failover pair) ---

resource "twingate_connector" "connector_factory_a_line_1_a" {
  remote_network_id     = twingate_remote_network.factory_factory_a_line_line_1.id
  name                   = "connector-factory-a-line-1-a"
  status_updates_enabled = true
  tags {
    factory = "factory-a"
    line = "line-1"
    role = "primary"
  }
}

resource "twingate_connector" "connector_factory_a_line_1_b" {
  remote_network_id     = twingate_remote_network.factory_factory_a_line_line_1.id
  name                   = "connector-factory-a-line-1-b"
  status_updates_enabled = true
  tags {
    factory = "factory-a"
    line = "line-1"
    role = "failover"
  }
}

# --- IdP Groups ---

resource "twingate_group" "factory_factory_a_shift_leads" {
  name = "factory-factory-a-shift-leads"
  type = "manual"
}

resource "twingate_group" "factory_factory_a_maintenance" {
  name = "factory-factory-a-maintenance"
  type = "manual"
}

resource "twingate_group" "factory_factory_a_contractors" {
  name = "factory-factory-a-contractors"
  type = "manual"
}

resource "twingate_group" "factory_factory_a_plant_managers" {
  name = "factory-factory-a-plant-managers"
  type = "manual"
}

# --- Resources (machines + cameras) ---

resource "twingate_resource" "cnc_mill_01_guardian_factory_a_local" {
  name              = "cnc-mill-01-guardian.factory-a.local"
  address           = "factory-guardian:8080"
  remote_network_id = twingate_remote_network.factory_factory_a_line_line_1.id
  port = 8080
  tags {
  factory = "factory-a"
  line = "line-1"
  machine-id = "cnc_mill_01"
  machine-type = "cnc_mill"
  classification = "restricted"
}
}

resource "twingate_resource" "cnc_mill_02_guardian_factory_a_local" {
  name              = "cnc-mill-02-guardian.factory-a.local"
  address           = "factory-guardian:8080"
  remote_network_id = twingate_remote_network.factory_factory_a_line_line_1.id
  port = 8080
  tags {
  factory = "factory-a"
  line = "line-1"
  machine-id = "cnc_mill_02"
  machine-type = "cnc_mill"
  classification = "restricted"
}
}

resource "twingate_resource" "conveyor_a_guardian_factory_a_local" {
  name              = "conveyor-a-guardian.factory-a.local"
  address           = "factory-guardian:8080"
  remote_network_id = twingate_remote_network.factory_factory_a_line_line_1.id
  port = 8080
  tags {
  factory = "factory-a"
  line = "line-1"
  machine-id = "conveyor_a"
  machine-type = "conveyor"
  classification = "internal"
}
}

resource "twingate_resource" "press_brake_01_guardian_factory_a_local" {
  name              = "press-brake-01-guardian.factory-a.local"
  address           = "factory-guardian:8080"
  remote_network_id = twingate_remote_network.factory_factory_a_line_line_1.id
  port = 8080
  tags {
  factory = "factory-a"
  line = "line-1"
  machine-id = "press_brake_01"
  machine-type = "press_brake"
  classification = "restricted"
  risk = "high"
}
}

resource "twingate_resource" "cam_01_factory_a_local" {
  name              = "cam-01.factory-a.local"
  address           = "192.168.10.21"
  remote_network_id = twingate_remote_network.factory_factory_a_line_line_1.id
  port = 554
  tags {
  factory = "factory-a"
  line = "line-1"
  zone = "cnc-north"
  classification = "restricted"
}
}

resource "twingate_resource" "cam_02_factory_a_local" {
  name              = "cam-02.factory-a.local"
  address           = "192.168.10.22"
  remote_network_id = twingate_remote_network.factory_factory_a_line_line_1.id
  port = 554
  tags {
  factory = "factory-a"
  line = "line-1"
  zone = "loading-dock"
  classification = "internal"
}
}

# --- Security Policies ---

resource "twingate_security_policy" "policy_machine_restricted" {
  name = "policy-machine-restricted"
  resources = [
    twingate_resource.cnc_mill_01_guardian_factory_a_local.id,
    twingate_resource.cnc_mill_02_guardian_factory_a_local.id,
  ]
  groups = [
    twingate_group.factory_factory_a_shift_leads.id,
    twingate_group.factory_factory_a_maintenance.id,
  ]
  requires_mfa = false
}

resource "twingate_security_policy" "policy_machine_internal" {
  name = "policy-machine-internal"
  resources = [
    twingate_resource.conveyor_a_guardian_factory_a_local.id,
  ]
  groups = [
    twingate_group.factory_factory_a_shift_leads.id,
    twingate_group.factory_factory_a_maintenance.id,
    twingate_group.factory_factory_a_contractors.id,
  ]
  requires_mfa = false
}

resource "twingate_security_policy" "policy_machine_press_brake" {
  name = "policy-machine-press-brake"
  resources = [
    twingate_resource.press_brake_01_guardian_factory_a_local.id,
  ]
  groups = [
    twingate_group.factory_factory_a_shift_leads.id,
    twingate_group.factory_factory_a_maintenance.id,
  ]
  requires_mfa = true
}

resource "twingate_security_policy" "policy_restricted_footage" {
  name = "policy-restricted-footage"
  resources = [
    twingate_resource.cam_01_factory_a_local.id,
  ]
  groups = [
    twingate_group.factory_factory_a_shift_leads.id,
    twingate_group.factory_factory_a_plant_managers.id,
  ]
  requires_mfa = false
}

resource "twingate_security_policy" "policy_internal_footage" {
  name = "policy-internal-footage"
  resources = [
    twingate_resource.cam_02_factory_a_local.id,
  ]
  groups = [
    twingate_group.factory_factory_a_shift_leads.id,
    twingate_group.factory_factory_a_maintenance.id,
    twingate_group.factory_factory_a_contractors.id,
  ]
  requires_mfa = false
}
