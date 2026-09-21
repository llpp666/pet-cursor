# -*- coding: utf-8 -*-
import unittest
from unittest import mock

import matting


class ExternalProcessTests(unittest.TestCase):
    def test_popen_clears_and_restores_dll_directory(self):
        process = object()
        with mock.patch.object(
                matting, "_set_windows_dll_directory",
                side_effect=[r"C:\app\_internal", None]) as set_directory:
            with mock.patch.object(
                    matting.subprocess, "Popen", return_value=process) as popen:
                result = matting._popen_external(["python.exe", "worker.py"])

        self.assertIs(result, process)
        self.assertEqual(
            set_directory.call_args_list,
            [mock.call(None), mock.call(r"C:\app\_internal")],
        )
        popen.assert_called_once_with(["python.exe", "worker.py"])

    def test_popen_restores_dll_directory_after_launch_error(self):
        with mock.patch.object(
                matting, "_set_windows_dll_directory",
                side_effect=[r"C:\app\_internal", None]) as set_directory:
            with mock.patch.object(
                    matting.subprocess, "Popen", side_effect=OSError("failed")):
                with self.assertRaises(OSError):
                    matting._popen_external(["missing-python.exe"])

        self.assertEqual(
            set_directory.call_args_list,
            [mock.call(None), mock.call(r"C:\app\_internal")],
        )


if __name__ == "__main__":
    unittest.main()
