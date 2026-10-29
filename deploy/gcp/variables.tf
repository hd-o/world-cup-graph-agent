variable "project" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "deployment" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.deployment))
    error_message = "Use a short lowercase deployment prefix."
  }
}

variable "model_environment" {
  type    = map(string)
  default = {}
  validation {
    condition     = alltrue([for name in keys(var.model_environment) : can(regex("^OPENROUTER_(SUPERVISOR|ENRICHER|RESEARCH)_(MODEL_ID|PROVIDERS|SORT|REASONING_EFFORT)$", name))])
    error_message = "Only model configuration, never keys or passwords, belongs in Terraform."
  }
}

variable "gateway_image" {
  type        = string
  description = "Immutable Artifact Registry digest built outside Terraform."
}

variable "agent_image" {
  type        = string
  description = "Immutable Artifact Registry digest built outside Terraform."
}

variable "database_machine" {
  type    = string
  default = "e2-medium"
}

provider "google" {
  project               = var.project
  region                = var.region
  billing_project       = var.project
  user_project_override = true
}

data "google_project" "target" {}

locals {
  labels = { deployment = var.deployment }
}
