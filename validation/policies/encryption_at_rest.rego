package vmwaremigration

# Enforce encryption-at-rest guardrails for generated storage resources
# across all supported providers.

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "resource \"aws_ebs_volume\"")
  not contains(content, "encrypted         = true")
  not contains(content, "encrypted = true")
  msg := sprintf("encryption_at_rest: aws_ebs_volume must set encrypted=true in %s", [file_path])
}

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "resource \"azurerm_managed_disk\"")
  contains(content, "encryption_settings_enabled = false")
  msg := sprintf("encryption_at_rest: azurerm_managed_disk must not disable encryption in %s", [file_path])
}

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "resource \"openstack_blockstorage_volume_v3\"")
  not contains(content, "volume_type = var.encrypted_volume_type")
  msg := sprintf("encryption_at_rest: openstack_blockstorage_volume_v3 must use encrypted volume type in %s", [file_path])
}
