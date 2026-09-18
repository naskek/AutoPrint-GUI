# AutoPrint Licensing

This context describes the language used to decide whether a local AutoPrint installation may start its printing application.

## BarTender integration rule

Before changing BarTender printing, preview, COM lifecycle, or data-source integration, read and follow [the BarTender Automation technical reference](docs/bartender-automation.md).

## Language

**License Document**:
A human-readable `license.json` issued for one AutoPrint customer and one Windows installation, whose payload is protected by an issuer signature.
_Avoid_: License file, activation file

**License Payload**:
The customer, validity, machine binding, and entitlements asserted by a License Document.
_Avoid_: License data, claims

**Machine Code**:
The irreversible AutoPrint-specific hash that identifies the current Windows installation to the License Issuer without exposing its raw MachineGuid.
_Avoid_: MachineGuid, hardware ID, fingerprint

**License Decision**:
The allow-or-deny result produced after the License Document, current time, Machine Code, application identity, and required entitlements are evaluated.
_Avoid_: License status, activation status

**Entitlement**:
A named product capability granted by a License Payload; `print` is required to start AutoPrint-GUI.
_Avoid_: Feature flag, permission

**License Issuer**:
The trusted operator that creates and signs License Documents using a private key unavailable to AutoPrint-GUI.
_Avoid_: License server, administrator
