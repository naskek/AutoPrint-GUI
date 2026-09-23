from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import updater
from updater import ReleaseInfo, UpdateError


class FakeResponse:
    def __init__(self, data: bytes, *, content_length: str | None = None):
        self._stream = io.BytesIO(data)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


class FailingReadResponse(FakeResponse):
    def __init__(self, first_chunk: bytes):
        super().__init__(b"")
        self._first_chunk = first_chunk
        self._calls = 0

    def read(self, size=-1):
        self._calls += 1
        if self._calls == 1:
            return self._first_chunk
        raise OSError("stream failed")


def release_payload(
    *,
    tag: str = "v3.2.3",
    name: str = "AutoPrint_Setup_3.2.3.exe",
    url: str = "https://example.invalid/AutoPrint_Setup_3.2.3.exe",
    digest: str | None = None,
) -> bytes:
    asset = {
        "name": name,
        "browser_download_url": url,
    }
    if digest is not None:
        asset["digest"] = digest
    return json.dumps(
        {
            "tag_name": tag,
            "assets": [asset],
            "body": "notes",
            "html_url": "https://example.invalid/release",
        }
    ).encode("utf-8")


class FetchLatestReleaseTests(unittest.TestCase):
    @patch("updater.urlopen")
    def test_release_parses_valid_sha256_digest(self, urlopen):
        digest = "ab" * 32
        urlopen.return_value = FakeResponse(
            release_payload(digest=f"sha256:{digest}")
        )

        release = updater.fetch_latest_release()

        self.assertEqual(release.version, "3.2.3")
        self.assertEqual(release.installer_name, "AutoPrint_Setup_3.2.3.exe")
        self.assertEqual(release.sha256, digest)
        self.assertEqual(release.notes, "notes")

    @patch("updater.urlopen")
    def test_invalid_digest_is_ignored(self, urlopen):
        urlopen.return_value = FakeResponse(
            release_payload(digest="sha256:not-a-valid-digest")
        )

        release = updater.fetch_latest_release()

        self.assertIsNone(release.sha256)

    @patch("updater.urlopen")
    def test_http_404_has_public_channel_error(self, urlopen):
        urlopen.side_effect = HTTPError(
            updater.UPDATE_API_URL, 404, "Not Found", hdrs=None, fp=None
        )

        with self.assertRaisesRegex(UpdateError, "Канал обновлений пока не опубликован"):
            updater.fetch_latest_release()

    @patch("updater.urlopen")
    def test_other_http_error_is_reported(self, urlopen):
        urlopen.side_effect = HTTPError(
            updater.UPDATE_API_URL, 503, "Unavailable", hdrs=None, fp=None
        )

        with self.assertRaisesRegex(UpdateError, "HTTP 503"):
            updater.fetch_latest_release()

    @patch("updater.urlopen")
    def test_network_error_is_reported(self, urlopen):
        urlopen.side_effect = URLError("offline")

        with self.assertRaisesRegex(UpdateError, "offline"):
            updater.fetch_latest_release()

    @patch("updater.urlopen")
    def test_malformed_release_tag_is_rejected(self, urlopen):
        urlopen.return_value = FakeResponse(release_payload(tag="latest"))

        with self.assertRaisesRegex(UpdateError, "некорректный тег версии"):
            updater.fetch_latest_release()

    @patch("updater.urlopen")
    def test_missing_installer_asset_is_rejected(self, urlopen):
        payload = json.dumps(
            {
                "tag_name": "v3.2.3",
                "assets": [{"name": "notes.txt", "browser_download_url": "https://example.invalid/notes.txt"}],
            }
        ).encode("utf-8")
        urlopen.return_value = FakeResponse(payload)

        with self.assertRaisesRegex(UpdateError, "не найден установщик"):
            updater.fetch_latest_release()

    @patch("updater.urlopen")
    def test_non_https_installer_url_is_rejected(self, urlopen):
        urlopen.return_value = FakeResponse(
            release_payload(url="http://example.invalid/AutoPrint_Setup_3.2.3.exe")
        )

        with self.assertRaisesRegex(UpdateError, "HTTPS-ссылка"):
            updater.fetch_latest_release()


class DownloadReleaseTests(unittest.TestCase):
    def _release(self, data: bytes, *, sha256: str | None = None) -> ReleaseInfo:
        return ReleaseInfo(
            version="3.2.3",
            installer_name="AutoPrint_Setup_3.2.3.exe",
            download_url="https://example.invalid/AutoPrint_Setup_3.2.3.exe",
            sha256=sha256,
        )

    @patch("updater.urlopen")
    def test_known_length_reports_progress_and_atomically_replaces_target(self, urlopen):
        data = b"installer-bytes"
        urlopen.return_value = FakeResponse(data, content_length=str(len(data)))
        progress = Mock()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "updater.tempfile.gettempdir", return_value=tmp
        ):
            target = Path(tmp) / "AutoPrint_Update_3.2.3.exe"
            target.write_bytes(b"old")

            result = updater.download_release(self._release(data), progress=progress)

            self.assertEqual(result, target)
            self.assertEqual(target.read_bytes(), data)
            self.assertFalse(target.with_suffix(".download").exists())

        progress.assert_called_once_with(len(data), len(data))

    @patch("updater.urlopen")
    def test_unknown_length_reports_none_total(self, urlopen):
        data = b"installer-bytes"
        urlopen.return_value = FakeResponse(data)
        progress = Mock()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "updater.tempfile.gettempdir", return_value=tmp
        ):
            updater.download_release(self._release(data), progress=progress)

        progress.assert_called_once_with(len(data), None)

    @patch("updater.urlopen")
    def test_checksum_mismatch_removes_partial_download(self, urlopen):
        data = b"installer-bytes"
        urlopen.return_value = FakeResponse(data, content_length=str(len(data)))

        with tempfile.TemporaryDirectory() as tmp, patch(
            "updater.tempfile.gettempdir", return_value=tmp
        ):
            target = Path(tmp) / "AutoPrint_Update_3.2.3.exe"
            partial = target.with_suffix(".download")

            with self.assertRaisesRegex(UpdateError, "Контрольная сумма"):
                updater.download_release(self._release(data, sha256="00" * 32))

            self.assertFalse(partial.exists())
            self.assertFalse(target.exists())

    @patch("updater.urlopen")
    def test_matching_checksum_accepts_download(self, urlopen):
        data = b"installer-bytes"
        digest = hashlib.sha256(data).hexdigest()
        urlopen.return_value = FakeResponse(data)

        with tempfile.TemporaryDirectory() as tmp, patch(
            "updater.tempfile.gettempdir", return_value=tmp
        ):
            result = updater.download_release(self._release(data, sha256=digest))
            self.assertEqual(result.read_bytes(), data)

    @patch("updater.urlopen")
    def test_stream_failure_removes_partial_download(self, urlopen):
        urlopen.return_value = FailingReadResponse(b"partial")

        with tempfile.TemporaryDirectory() as tmp, patch(
            "updater.tempfile.gettempdir", return_value=tmp
        ):
            target = Path(tmp) / "AutoPrint_Update_3.2.3.exe"
            partial = target.with_suffix(".download")

            with self.assertRaisesRegex(UpdateError, "Не удалось сохранить обновление"):
                updater.download_release(self._release(b""))

            self.assertFalse(partial.exists())
            self.assertFalse(target.exists())


class LaunchInstallerTests(unittest.TestCase):
    @patch("updater.os.name", "posix")
    def test_non_windows_is_rejected_before_path_validation(self):
        with self.assertRaisesRegex(UpdateError, "только в Windows"):
            updater.launch_installer("does-not-matter.exe")

    @unittest.skipUnless(os.name == "nt", "Windows-only installer launch contract")
    @patch("updater.subprocess.Popen")
    def test_windows_launch_uses_silent_update_arguments(self, popen):
        with tempfile.TemporaryDirectory() as tmp:
            installer = Path(tmp) / "setup.exe"
            installer.write_bytes(b"fixture")

            updater.launch_installer(installer)

            popen.assert_called_once_with(
                [
                    str(installer),
                    "/SP-",
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/CLOSEAPPLICATIONS",
                ],
                cwd=str(installer.parent),
                close_fds=True,
            )

    @unittest.skipUnless(os.name == "nt", "Windows-only installer launch contract")
    def test_missing_windows_installer_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.exe"
            with self.assertRaisesRegex(UpdateError, "Установщик не найден"):
                updater.launch_installer(missing)


if __name__ == "__main__":
    unittest.main()
