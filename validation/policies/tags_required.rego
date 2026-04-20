package vmwaremigration

# Enforce required metadata tags/labels across providers.
# Minimum required: Environment, Owner, MigratedFrom.

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  requires_metadata_block(content)
  not contains(content, "Environment")
  not contains(content, "environment")
  not has_inherited_required_metadata(content)
  msg := sprintf("tags_required: Environment tag/label missing in %s", [file_path])
}

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  requires_metadata_block(content)
  not contains(content, "Owner")
  not contains(content, "owner")
  not has_inherited_required_metadata(content)
  msg := sprintf("tags_required: Owner tag/label missing in %s", [file_path])
}

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  requires_metadata_block(content)
  not contains(content, "MigratedFrom")
  not contains(content, "migrated_from")
  not has_inherited_required_metadata(content)
  msg := sprintf("tags_required: MigratedFrom tag/label missing in %s", [file_path])
}

requires_metadata_block(content) {
  contains(content, "tags = {")
}

requires_metadata_block(content) {
  contains(content, "labels = {")
}

requires_metadata_block(content) {
  contains(content, "metadata = {")
}

# Accept inherited provider-agnostic metadata maps frequently used in root/module wiring,
# where required keys are defined in shared locals/variables rather than inline per block.
has_inherited_required_metadata(content) {
  contains(content, "common_tags")
}

has_inherited_required_metadata(content) {
  contains(content, "common_labels")
}

has_inherited_required_metadata(content) {
  contains(content, "var.labels")
}

has_inherited_required_metadata(content) {
  contains(content, "local.labels")
}

has_inherited_required_metadata(content) {
  contains(content, "labels = merge(")
}

has_inherited_required_metadata(content) {
  contains(content, "tags = merge(")
}

has_inherited_required_metadata(content) {
  contains(content, "metadata = merge(")
}
