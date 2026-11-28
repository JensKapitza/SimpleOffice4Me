import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install optional browser dependency")
class MotionTargetSelectionTest(unittest.TestCase):
    def test_native_resume_never_uses_target_order_or_a_hidden_last_page(self):
        from tools.android_motion_acceptance import choose_foreground

        older, current = object(), object()
        for entries in (
            [(older, False, False), (current, True, True)],
            [(current, True, True), (older, False, False)],
            [(older, True, False), (current, True, True)],
            [(current, True, False), (older, False, True)],
        ):
            self.assertIs(current, choose_foreground(entries))
        self.assertIsNone(choose_foreground([(older, False, False), (current, False, False)]))
        self.assertIsNone(choose_foreground([(older, True, False), (current, True, False)]))
        self.assertIsNone(choose_foreground([(older, True, True), (current, True, True)]))
