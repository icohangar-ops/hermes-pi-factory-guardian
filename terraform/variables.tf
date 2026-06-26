variable "twingate_api_token" {
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
