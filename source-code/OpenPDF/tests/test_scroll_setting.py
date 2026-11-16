"""The Windows wheel preference that CanvasScroll scales its steps by."""
import ctypes
import unittest
from unittest.mock import Mock, patch
from scroll_tools import wheel_setting


class WheelSettingTests(unittest.TestCase):
    def windows(self, lines=None):
        """Pretend to be Windows; `lines` is what the system reports, None when the call fails."""
        def query(action, _param, pointer, _flags):
            if lines is None:
                return 0
            pointer._obj.value = lines
            return 1
        windll = Mock()
        windll.user32.SystemParametersInfoW.side_effect = query
        for patcher in (patch('scroll_tools.sys.platform', 'win32'), patch.object(ctypes, 'windll', windll, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return windll.user32.SystemParametersInfoW

    def test_other_platforms_scroll_three_lines(self):
        with patch('scroll_tools.sys.platform', 'linux'):
            self.assertEqual(wheel_setting(), 3)
            self.assertEqual(wheel_setting(horizontal=True), 3)

    def test_windows_vertical_preference_is_read(self):
        query = self.windows(lines=7)
        self.assertEqual(wheel_setting(), 7)
        self.assertEqual(query.call_args.args[0], 0x0068)  # SPI_GETWHEELSCROLLLINES

    def test_windows_horizontal_preference_is_read_separately(self):
        query = self.windows(lines=5)
        self.assertEqual(wheel_setting(horizontal=True), 5)
        self.assertEqual(query.call_args.args[0], 0x006C)  # SPI_GETWHEELSCROLLCHARS

    def test_windows_page_scrolling_and_disabled_wheel_are_passed_through(self):
        self.windows(lines=0xFFFFFFFF)
        self.assertEqual(wheel_setting(), 0xFFFFFFFF)
        self.windows(lines=0)
        self.assertEqual(wheel_setting(), 0)

    def test_windows_falls_back_to_three_lines_when_the_query_fails(self):
        self.windows(lines=None)
        self.assertEqual(wheel_setting(), 3)


if __name__ == '__main__':
    unittest.main()
