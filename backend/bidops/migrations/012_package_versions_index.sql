CREATE INDEX IF NOT EXISTS nd_package_versions_version_package
    ON nd_package_versions(version_id, package_id);
