from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


class ReleaseWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    def test_public_updater_channel_is_mandatory(self):
        self.assertIn("Require public updater channel credentials", self.workflow)
        self.assertIn("if: env.PUBLIC_RELEASE_TOKEN == ''", self.workflow)
        self.assertIn(
            "Production release blocked: AUTOPRINT_RELEASE_TOKEN is required",
            self.workflow,
        )
        self.assertNotIn("Public updater channel not configured", self.workflow)

    def test_public_release_is_published_unconditionally_after_gate(self):
        publish_start = self.workflow.index("- name: Publish public updater release")
        verify_start = self.workflow.index("- name: Verify public updater release")
        publish_block = self.workflow[publish_start:verify_start]

        self.assertNotIn("if: env.PUBLIC_RELEASE_TOKEN", publish_block)
        self.assertIn("AUTOPRINT_RELEASE_TOKEN", publish_block)
        self.assertIn("gh release upload", publish_block)
        self.assertIn("gh release create", publish_block)

    def test_public_release_verifies_both_installer_names_and_digest(self):
        self.assertIn("AutoPrint_Setup_$env:APP_VERSION.exe", self.workflow)
        self.assertIn("BarTenderGUI_Setup_$env:APP_VERSION.exe", self.workflow)
        self.assertIn(
            'gh api "repos/$env:PUBLIC_RELEASE_REPO/releases/tags/$tag"',
            self.workflow,
        )
        self.assertIn('if (-not $digest.StartsWith("sha256:"))', self.workflow)
        self.assertIn("$publishedHash -ne $env:INSTALLER_SHA256", self.workflow)

    def test_release_does_not_duplicate_published_installers_as_actions_artifact(self):
        self.assertNotIn("actions/upload-artifact", self.workflow)
        self.assertNotIn("AutoPrint-Windows-Installer-release", self.workflow)
        self.assertNotIn("retention-days: 30", self.workflow)


if __name__ == "__main__":
    unittest.main()
