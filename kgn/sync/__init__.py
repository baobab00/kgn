"""Sync package — DB ↔ file system bidirectional synchronization.

Provides export (DB → .kgn/.kge files) and import (files → DB) services
with content-hash based change detection for idempotent operations.
"""
