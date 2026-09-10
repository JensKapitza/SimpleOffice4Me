"""Compatibility test module aggregating the split WebDAV test classes."""
from __future__ import annotations

if __package__:
    from .webdav_test_part_1 import WebDavDocumentTestPart1
    from .webdav_test_part_2 import WebDavDocumentTestPart2
    from .webdav_test_part_3 import WebDavDocumentTestPart3
    from .webdav_test_part_4 import WebDavDocumentTestPart4
    from .webdav_test_part_5 import WebDavDocumentTestPart5
else:
    from webdav_test_part_1 import WebDavDocumentTestPart1
    from webdav_test_part_2 import WebDavDocumentTestPart2
    from webdav_test_part_3 import WebDavDocumentTestPart3
    from webdav_test_part_4 import WebDavDocumentTestPart4
    from webdav_test_part_5 import WebDavDocumentTestPart5


if __name__ == "__main__":
    import unittest
    unittest.main()
