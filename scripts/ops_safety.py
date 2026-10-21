"""Correct rollback ordering while preserving the existing recovery tool contract."""


def restore_config_file(current, source, previous):
    """Read the rollback source before rotating its potentially identical backup path.

    Keep the current file's inode: nginx mounts this individual file read-only, so
    replacing the inode would leave an existing container reading the old file.
    """
    restored = source.read_text()
    replaced = current.read_text()
    previous.write_text(replaced)
    current.write_text(restored)
