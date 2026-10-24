terraform {
  required_version = ">= 1.17.0, < 2.0.0"
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "3.3.0"
    }
  }
}

provider "helm" {
  kubernetes = {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}

resource "helm_release" "cutback" {
  name             = var.release_name
  namespace        = var.namespace
  create_namespace = false
  chart            = abspath("${path.module}/../../helm/cutback")
  wait             = true
  wait_for_jobs    = true
  timeout          = 600
  # Automatic binary rollback after a schema migration is unsafe. Review the
  # compatibility window and restore drill before an explicit operator rollback.
  atomic          = false
  cleanup_on_fail = false
  values = [yamlencode(merge(var.reviewed_values, {
    image = {
      repository = var.image_repository
      digest     = var.image_digest
      pullPolicy = "IfNotPresent"
    }
    secrets = {
      runtime     = var.runtime_secret_name
      migration   = var.migration_secret_name
      experiments = var.experiment_secret_name
    }
  }))]
}
