mock_provider "helm" {}

variables {
  kubeconfig_path       = "/not-used-with-mock/kubeconfig"
  kube_context          = "local-mock-only"
  namespace             = "cutback-test"
  runtime_secret_name   = "existing-runtime"
  migration_secret_name = "existing-migrator"
  image_digest          = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "private_reviewed_release" {
  command = plan
  assert {
    condition     = !helm_release.cutback.create_namespace && !helm_release.cutback.atomic
    error_message = "Do not create unreviewed infrastructure or automatically roll binaries back across migrations."
  }
  assert {
    condition     = yamldecode(helm_release.cutback.values[0]).image.digest == var.image_digest
    error_message = "The reviewed digest must be preserved."
  }
  assert {
    condition     = yamldecode(helm_release.cutback.values[0]).secrets.migration == "existing-migrator"
    error_message = "Migration credentials must remain a separate external reference."
  }
}
