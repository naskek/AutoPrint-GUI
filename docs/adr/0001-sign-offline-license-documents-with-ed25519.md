# Sign offline License Documents with Ed25519

AutoPrint accepts a readable local License Document only when its canonical payload verifies against an embedded Ed25519 public-key keyring. Issuer private keys live outside the application repository in the private license-tooling environment, so editing `license.json` cannot silently expand its validity or entitlements.
