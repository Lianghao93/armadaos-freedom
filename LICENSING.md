# Licensing and copyright

[简体中文](LICENSING.zh-CN.md)

## Project identity and original contributions

**armadaos-freedom (ArmadaOS Freedom)** is an independently maintained Linux
distribution project based on ArmadaOS and extended for ARM64 tablets.

Copyright (C) 2026 danyi and the armadaos-freedom contributors.

This notice covers original code and modifications in which danyi and the
respective armadaos-freedom contributors hold copyright. It does not claim
ownership of inherited ArmadaOS code or other third-party works. Their existing
copyright and attribution notices remain in effect.

Freedom's work includes tablet integration, device-specific configuration,
kShimC boot integration, and additions to input and control software. A feature
can contain both our changes and upstream code; its authorship and license are
determined by the actual files and their history.

## Default license

Unless a file or component has a different applicable license, software and
scripts originally written for armadaos-freedom are licensed under the
**GNU General Public License, version 2 or (at your option) any later version**
(`GPL-2.0-or-later`). The complete GPL version 2 text is in [LICENSE](LICENSE).

These works are distributed without warranty, as described in the GPL.
Modifications to existing components follow the licensing requirements of the
components being modified. The repository's default license does not replace
component-specific licenses or relicense third-party material.

## Included and referenced components

The following table is a guide to the principal components, not a replacement
for their notices. Consult each component's license files, SPDX identifiers,
source notices, and provenance records for its precise scope.

| Component | License and source of the applicable terms |
| --- | --- |
| ArmadaOS original software and scripts inherited by this fork | `GPL-2.0-or-later`; retain the Armada contributors' copyright. See the [upstream license](https://github.com/armada-os/armada/blob/main/LICENSE.md). |
| kShimC source snapshot | Original kShimC source: `GPL-3.0-only`; separately licensed code and assets retain their own terms. See the snapshot's [LICENSE](devices/lenovo-tb323fu/boot/kshim-success-source/LICENSE) and [license notice](devices/lenovo-tb323fu/boot/kshim-success-source/README.md#license--许可证). |
| Armada RGB | `GPL-3.0-or-later`, as declared in its [package manifest](packages/armada-rgb/Cargo.toml). |
| TB323FU board-support snapshot | Multiple licenses, including GPL, MIT, BSD, and documentation licenses. Retain Joonhoe Kim and other authors' notices; see the snapshot's [NOTICE](devices/lenovo-tb323fu/kernel/tb323fu-linux-11a25bf8/tb323fu-linux-11a25bf89b65fff48112a91a0b25bc247e7736d8/NOTICE), `REUSE.toml`, and `LICENSES/`. |
| Kernel patches and device trees | Follow each file's license and original authorship. Imported device support retains its provenance, including files inherited through ArmadaOS from ROCKNIX. See [kernel patch sources](packages/kernel/PATCHES.md) and [TB323FU sources](devices/lenovo-tb323fu/docs/SOURCES.md). |
| Build tooling derived from Universal Blue image-template and Bazzite | `Apache-2.0` for those upstream portions; retain their notices. See [image-template](https://github.com/ublue-os/image-template) and [Bazzite](https://github.com/ublue-os/bazzite). |
| Menu fonts | SIL Open Font License 1.1 and the notices shipped with each font. See [font sources](devices/lenovo-tb323fu/docs/SOURCES.md#字体) and the font asset directories. |
| Other packages, dependencies, firmware, and assets | Their own licenses and distribution terms; they are not all covered by the repository's default GPL license. |

Pinned versions and the scope of the kShimC license/FTM4 cleanup synchronization
are recorded in [component sources](devices/lenovo-tb323fu/docs/SOURCES.md).
The current boot menu uses kShimC; that does not change the provenance or license
of other inherited device-support files.

## Redistribution and contributions

Retain applicable copyright notices, license texts, attribution, and notices of
modifications. When distributing covered binaries or images, satisfy the source
availability and other requirements of the licenses for the included components.

For new Freedom-authored source files, identify the actual copyright holders
and applicable license. For modifications, preserve the existing notices and
identify the changes and their dates as required by the component's license.
Contributions do not automatically transfer their authors' copyright to danyi,
ArmadaOS, or another contributor.

## Proposed commercial authorization policy (not in effect)

This section records a proposal for independently licensable original components.
No covered files or versions have been designated; it grants no new license and
places no new conditions on the current repository.

For components later lawfully released under separate terms, the proposed scope is:

- Free use: personal noncommercial use and sharing, and a business's own internal use.
- Prior written authorization from the relevant rights holders: paid flashing,
  sale of devices preinstalled with a covered component, sale of images containing
  it, and related paid services to customers. Such customer-facing activities do
  not qualify as internal use.

The existing [LICENSE](LICENSE), component licenses, and rights already granted
under the GPL remain unchanged. Compliant commercial use of those GPL works does
not require separate permission under this proposal. Before adopting separate
terms, identify the covered files and versions, establish the necessary rights,
and publish the applicable license and authorization contact.
