package vmwaremigration

# Deny public ACL usage on aws_s3_bucket resources.
deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "resource \"aws_s3_bucket\"")
  contains(content, "acl = \"public-read\"")
  msg := sprintf("no_public_s3: public-read ACL is forbidden in %s", [file_path])
}

deny[msg] {
  some file_path
  content := input.generated_files[file_path]
  contains(content, "resource \"aws_s3_bucket\"")
  contains(content, "acl = \"public-read-write\"")
  msg := sprintf("no_public_s3: public-read-write ACL is forbidden in %s", [file_path])
}
