variable "kubeconfig_path" {
  type        = string
  description = "Existing authorized kubeconfig path; credentials are not passed as values."
}
variable "kube_context" {
  type        = string
  description = "Exact authorized context; never infer a production context."
  validation {
    condition     = length(trimspace(var.kube_context)) > 0
    error_message = "An explicit kube_context is required."
  }
}
variable "namespace" {
  type        = string
  description = "Existing customer namespace, with Pod Security admission and enforcing CNI."
}
variable "release_name" {
  type    = string
  default = "cutback"
}
variable "image_repository" {
  type    = string
  default = "ghcr.io/cutbackai/cutback"
}
variable "image_digest" {
  type        = string
  description = "Reviewed immutable image digest."
  validation {
    condition     = can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
    error_message = "Supply an immutable sha256 registry digest."
  }
}
variable "runtime_secret_name" {
  type        = string
  description = "Existing Secret with database_url and admin_token; payload_key only if authorized."
}
variable "migration_secret_name" {
  type        = string
  description = "Existing separate Secret with database_url for the bounded migration Job."
}
variable "experiment_secret_name" {
  type    = string
  default = "cutback-experiments"
}
variable "reviewed_values" {
  type        = any
  default     = {}
  description = "Reviewed nonsecret chart values, including explicit networking peers. No key/token values."
}
