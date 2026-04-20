package vmwaremigration

# Deny open ingress to the internet for all supported providers.

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "cidr_blocks = [\"0.0.0.0/0\"]")
  msg := sprintf("no_open_ingress: AWS open ingress is forbidden in %s", [file_path])
}

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "source_address_prefix = \"*\"")
  msg := sprintf("no_open_ingress: Azure open ingress '*' is forbidden in %s", [file_path])
}

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "source_address_prefix = \"0.0.0.0/0\"")
  msg := sprintf("no_open_ingress: Azure open ingress 0.0.0.0/0 is forbidden in %s", [file_path])
}

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "source_ranges = [\"0.0.0.0/0\"]")
  msg := sprintf("no_open_ingress: GCP open ingress is forbidden in %s", [file_path])
}

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "remote_ip_prefix = \"0.0.0.0/0\"")
  msg := sprintf("no_open_ingress: OpenStack open ingress is forbidden in %s", [file_path])
}
