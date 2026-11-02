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
        self.assertIn("gh release create", publish_block)
        self.assertNotIn("gh release upload", publish_block)
        self.assertNotIn("--clobber", publish_block)

    def test_release_preflight_before_any_publication(self):
        credentials = self.workflow.index("- name: Require public updater channel credentials")
        unique = self.workflow.index("- name: Reject reused release version in both repositories")
        build = self.workflow.index("- name: Build PyInstaller application")
        private = self.workflow.index("- name: Publish private source release")
        public = self.workflow.index("- name: Publish public updater release")
        self.assertLess(credentials, unique)
        self.assertLess(unique, build)
        self.assertLess(build, private)
        self.assertLess(private, public)

    def test_release_version_cannot_be_reused_or_downgraded(self):
        preflight = self.workflow.split(
            "- name: Reject reused release version in both repositories", 1
        )[1].split("- name: Install build dependencies", 1)[0]
        self.assertIn("gh release list --repo $env:GITHUB_REPOSITORY", preflight)
        self.assertIn("gh release list --repo $env:PUBLIC_RELEASE_REPO", preflight)
        self.assertIn("$env:GH_TOKEN = $env:PUBLIC_RELEASE_TOKEN", preflight)
        self.assertIn("$candidate -le $published", preflight)
        self.assertIn("never overwrite published assets", preflight)
        self.assertIn("if ($LASTEXITCODE -ne 0)", preflight)

    def test_release_does_not_replace_binary_of_existing_version(self):
        source = self.workflow.split("- name: Publish private source release", 1)[1]
        self.assertNotIn("gh release upload", source)
        self.assertNotIn("--clobber", source)
        self.assertIn("gh release create $tag $env:INSTALLER_PATH", source)
        self.assertIn("gh release create $tag $env:INSTALLER_PATH", source)
        self.assertIn("--repo $env:GITHUB_REPOSITORY", source)
        self.assertIn("--repo $env:PUBLIC_RELEASE_REPO", source)

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
