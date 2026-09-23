# AutoPrint Licensing

This context describes the language used to decide whether a local AutoPrint installation may start its printing application.

## BarTender integration rule

Before changing BarTender printing, preview, COM lifecycle, BTW handling, data-source integration, printers, copies/serialization, messages/results, events, or cancellation:

1. read and follow [the BarTender Automation technical reference](docs/bartender-automation.md);
2. verify the exact COM member/signature/semantics against the official Seagull Scientific documentation linked there;
3. follow [the BarTender testing strategy](docs/testing-bartender.md) and add/update COM-boundary contract coverage.

Official Seagull documentation is the baseline API contract. Do not guess overloads, properties, result semantics, or retry/fallback behavior. Any undocumented compatibility heuristic must be isolated, justified, and locally verified.

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
