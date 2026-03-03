"""Serializer package — reverse of parser.

Converts DB records (NodeRecord, EdgeRecord) back into .kgn/.kge text format.
"""

from kgn.serializer.kge_serializer import serialize_edges
from kgn.serializer.kgn_serializer import serialize_node

__all__ = ["serialize_edges", "serialize_node"]
