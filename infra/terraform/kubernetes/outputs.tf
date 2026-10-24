output "release_name" {
  value = helm_release.cutback.name
}
output "namespace" {
  value = helm_release.cutback.namespace
}
output "reviewed_image_digest" {
  value = var.image_digest
}
