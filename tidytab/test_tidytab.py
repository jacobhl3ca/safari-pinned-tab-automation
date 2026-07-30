import unittest

import tidytab


class SplitTabsTests(unittest.TestCase):
    def test_explicit_accessibility_state_wins_over_width(self):
        items = [
            ("pinned", (10, 5), 300, True),
            ("unpinned", (20, 5), 30, False),
        ]

        pinned, unpinned = tidytab._split_tabs(items)

        self.assertEqual(pinned, [("pinned", (10, 5))])
        self.assertEqual(unpinned, [("unpinned", (20, 5))])

    def test_width_fallback_splits_narrow_prefix(self):
        items = [
            ("pinned-1", (10, 5), 37, None),
            ("pinned-2", (20, 5), 37, None),
            ("tab-1", (30, 5), 240, None),
            ("tab-2", (40, 5), 240, None),
        ]

        pinned, unpinned = tidytab._split_tabs(items)

        self.assertEqual(
            pinned,
            [("pinned-1", (10, 5)), ("pinned-2", (20, 5))],
        )
        self.assertEqual(
            unpinned,
            [("tab-1", (30, 5)), ("tab-2", (40, 5))],
        )


if __name__ == "__main__":
    unittest.main()
